#!/usr/bin/env python3
import json
import os
import threading
import time
from datetime import datetime

import stripe
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, g, jsonify, redirect, render_template, request

from ai_personalize import personalize
from apollo_client import search_contacts
from auth import require_auth
from email_sender import send_gmail
import db as DB

app = Flask(__name__)
stripe.api_key            = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET     = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID           = os.environ.get("STRIPE_PRICE_ID", "")
CLERK_PUBLISHABLE_KEY     = os.environ.get("CLERK_PUBLISHABLE_KEY", "")

# ── Per-user campaign state ───────────────────────────────────────────────────

_campaigns: dict = {}  # user_id → {"running": bool, "output": [], "stop": bool}

def campaign_state(user_id: str) -> dict:
    if user_id not in _campaigns:
        _campaigns[user_id] = {"running": False, "output": [], "stop": False}
    return _campaigns[user_id]

def campaign_log(user_id: str, msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    campaign_state(user_id)["output"].append({"ts": ts, "msg": msg, "level": level})

# ── Campaign runner ───────────────────────────────────────────────────────────

def run_campaign(user_id: str, dry_run: bool = False):
    state = campaign_state(user_id)
    state.update({"running": True, "stop": False, "output": []})

    cfg      = DB.get_config(user_id)
    data     = DB.get_data(user_id)
    filters  = data.get("filters", {})
    template = data.get("template", {})
    schedule = data.get("schedule", {})

    apollo_key  = os.environ.get("APOLLO_KEY") or cfg.get("apollo", "")
    groq_key    = cfg.get("groq_key", "")
    gmail       = cfg.get("gmail", "")
    gmailpw     = cfg.get("gmailpw", "")
    sender_name = cfg.get("sender_name", "")

    per_day         = int(schedule.get("perday", 5))
    per_company_max = int(schedule.get("per_company", 2))
    total_limit     = int(schedule.get("limit", 0))

    campaign_log(user_id, "▶  Campaign started", "success")

    sent_td      = DB.sent_today(user_id)
    co_counts    = DB.sent_today_per_company(user_id)
    total_ever   = DB.total_sent(user_id)

    if sent_td >= per_day:
        campaign_log(user_id, f"✓  Already sent {sent_td} emails today (daily limit: {per_day})", "info")
        state["running"] = False
        return

    if total_limit and total_ever >= total_limit:
        campaign_log(user_id, f"✓  Total limit of {total_limit} reached", "info")
        state["running"] = False
        return

    campaign_log(user_id, "→  Searching Apollo for contacts...")
    try:
        contacts = search_contacts(apollo_key, filters)
    except Exception as e:
        campaign_log(user_id, f"✗  Apollo error: {e}", "error")
        state["running"] = False
        return

    with_email = [c for c in contacts if c.get("email")]
    campaign_log(user_id, f"   Found {len(contacts)} people — {len(with_email)} have emails")

    if not with_email:
        campaign_log(user_id, "✗  No contacts with emails found. Broaden your filters.", "error")
        state["running"] = False
        return

    new_sent = 0
    for contact in with_email:
        if state["stop"]:
            campaign_log(user_id, "■  Campaign stopped.", "warn")
            break

        company_key = (contact.get("organization_name") or "").lower()

        if co_counts[company_key] >= per_company_max:
            campaign_log(user_id, f"   ⟳  Skipping {contact.get('organization_name')} — already sent {per_company_max} today")
            continue

        if sent_td + new_sent >= per_day:
            campaign_log(user_id, f"✓  Daily limit of {per_day} reached", "success")
            break

        if total_limit and total_ever + new_sent >= total_limit:
            campaign_log(user_id, f"✓  Total limit of {total_limit} reached", "success")
            break

        person, subject, body = personalize(
            template.get("subject", ""), template.get("body", ""),
            contact, filters, groq_key or None
        )
        campaign_log(user_id, f"   ✦  {person['first_name']} {person['last_name']} · {person['company']} · {person['email']}")

        if not dry_run:
            try:
                send_gmail(gmail, gmailpw, person["email"], subject, body, sender_name)
                status = "sent"
                campaign_log(user_id, f"   ✓  Sent to {person['email']}", "success")
            except Exception as e:
                status = "failed"
                campaign_log(user_id, f"   ✗  Failed: {e}", "error")
        else:
            status = "dry-run"
            campaign_log(user_id, f"   [dry-run] Would send to {person['email']}")

        DB.append_log(user_id, {
            "timestamp": datetime.now().isoformat(),
            "name":      f"{person['first_name']} {person['last_name']}",
            "company":   person["company"],
            "email":     person["email"],
            "title":     person["title"],
            "subject":   subject,
            "status":    status,
        })

        if status == "sent":
            new_sent += 1
            co_counts[company_key] += 1

        time.sleep(2)

    campaign_log(user_id, f"✓  Done — {new_sent} emails sent this run", "success")
    state["running"] = False

# ── Scheduler (runs every minute, fires campaign for each due user) ────────────

scheduler = BackgroundScheduler()

def check_scheduled_campaigns():
    now = datetime.now()
    for user in DB.get_subscribed_users():
        uid   = user["id"]
        state = campaign_state(uid)
        if state["running"]:
            continue
        sched     = DB.get_data(uid).get("schedule", {})
        send_time = sched.get("time", "")
        if not send_time:
            continue
        try:
            h, m = send_time.split(":")
            if now.hour == int(h) and now.minute == int(m):
                threading.Thread(target=run_campaign, args=(uid,), daemon=True).start()
        except Exception:
            pass

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/success")
def index():
    return render_template("index.html", clerk_pub_key=CLERK_PUBLISHABLE_KEY)

@app.route("/api/me", methods=["POST"])
@require_auth
def api_me():
    body = request.json or {}
    user = DB.get_or_create_user(
        clerk_user_id=g.clerk_user_id,
        email=body.get("email", ""),
        name=body.get("name", ""),
    )
    return jsonify({
        "user_id":    user["id"],
        "subscribed": user.get("subscribed", False),
        "config":     DB.get_config(user["id"]),
        "data":       DB.get_data(user["id"]),
    })

@app.route("/api/config", methods=["POST"])
@require_auth
def post_config():
    body = request.json or {}
    user = DB.get_or_create_user(g.clerk_user_id)
    DB.save_config(user["id"], {
        "gmail":       body.get("gmail", ""),
        "gmailpw":     body.get("gmailpw", ""),
        "groq_key":    body.get("groq", ""),
        "sender_name": body.get("name", ""),
    })
    return jsonify({"ok": True})

@app.route("/api/data", methods=["POST"])
@require_auth
def post_data():
    user = DB.get_or_create_user(g.clerk_user_id)
    DB.save_data(user["id"], request.json or {})
    return jsonify({"ok": True})

@app.route("/api/state")
@require_auth
def get_state():
    user  = DB.get_or_create_user(g.clerk_user_id)
    uid   = user["id"]
    state = campaign_state(uid)
    return jsonify({
        "running":    state["running"],
        "sent":       DB.total_sent(uid),
        "sent_today": DB.sent_today(uid),
        "subscribed": user.get("subscribed", False),
    })

@app.route("/api/run", methods=["POST"])
@require_auth
def api_run():
    user  = DB.get_or_create_user(g.clerk_user_id)
    uid   = user["id"]
    state = campaign_state(uid)
    if state["running"]:
        return jsonify({"error": "Campaign already running"}), 409
    if not DB.is_subscribed(uid):
        return jsonify({"error": "Subscription required"}), 402
    dry_run = (request.json or {}).get("dry_run", False)
    threading.Thread(target=run_campaign, args=(uid, dry_run), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
@require_auth
def api_stop():
    user = DB.get_or_create_user(g.clerk_user_id)
    campaign_state(user["id"])["stop"] = True
    return jsonify({"ok": True})

@app.route("/api/log")
@require_auth
def api_log():
    user = DB.get_or_create_user(g.clerk_user_id)
    return jsonify(DB.get_logs(user["id"]))

@app.route("/api/log/clear", methods=["POST"])
@require_auth
def clear_log():
    user = DB.get_or_create_user(g.clerk_user_id)
    DB.clear_logs(user["id"])
    return jsonify({"ok": True})

@app.route("/api/stream")
def api_stream():
    # EventSource can't set headers, so accept token via query param
    from auth import verify_token
    token = request.args.get("token") or request.headers.get("Authorization", "")[7:]
    try:
        payload = verify_token(token)
        clerk_user_id = payload["sub"]
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    user  = DB.get_or_create_user(clerk_user_id)
    uid   = user["id"]
    state = campaign_state(uid)
    cursor = [0]

    def generate():
        while True:
            out = state["output"]
            while cursor[0] < len(out):
                item = out[cursor[0]]; cursor[0] += 1
                yield f"data: {json.dumps(item)}\n\n"
            if not state["running"] and cursor[0] >= len(out):
                yield f"data: {json.dumps({'done': True})}\n\n"
                return
            time.sleep(0.1)
            yield ": ping\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Stripe ────────────────────────────────────────────────────────────────────

@app.route("/api/verify-session", methods=["POST"])
@require_auth
def api_verify_session():
    """Fallback: verify a Stripe checkout session directly and activate subscription."""
    session_id = (request.json or {}).get("session_id", "")
    if not session_id:
        return jsonify({"error": "No session_id"}), 400
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status in ("paid", "no_payment_required") and session.status == "complete":
            clerk_id = session.get("client_reference_id") or (session.get("metadata") or {}).get("clerk_user_id")
            if not clerk_id:
                clerk_id = g.clerk_user_id
            DB.set_subscription(clerk_id, True,
                                stripe_customer_id=session.get("customer"),
                                stripe_subscription_id=session.get("subscription"))
            return jsonify({"ok": True, "subscribed": True})
        return jsonify({"ok": True, "subscribed": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/subscribe", methods=["POST"])
@require_auth
def api_subscribe():
    base = request.host_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/",
        client_reference_id=g.clerk_user_id,
        metadata={"clerk_user_id": g.clerk_user_id},
    )
    return jsonify({"url": session.url})

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return jsonify({"error": "Invalid signature"}), 400

    etype = event["type"]

    if etype == "checkout.session.completed":
        session  = event["data"]["object"]
        clerk_id = session.get("client_reference_id") or session.get("metadata", {}).get("clerk_user_id")
        if clerk_id:
            DB.set_subscription(clerk_id, True,
                                stripe_customer_id=session.get("customer"),
                                stripe_subscription_id=session.get("subscription"))

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub  = event["data"]["object"]
        user = DB.get_user_by_stripe_customer(sub["customer"])
        if user:
            DB.set_subscription(user["clerk_user_id"], False)

    return jsonify({"ok": True})

# ── Launch ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scheduler.add_job(check_scheduled_campaigns, "cron", minute="*")
    scheduler.start()
    port = int(os.environ.get("PORT", 5055))
    print(f"\n  AutoReach → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
