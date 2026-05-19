import os
from collections import defaultdict
from datetime import date

from supabase import create_client, Client

_client = None

def db() -> Client:
    global _client
    if not _client:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client


# ── Users ─────────────────────────────────────────────────────────────────────

def get_or_create_user(clerk_user_id: str, email: str = "", name: str = "") -> dict:
    r = db().table("users").select("*").eq("clerk_user_id", clerk_user_id).execute()
    if r.data:
        return r.data[0]
    r = db().table("users").insert({
        "clerk_user_id": clerk_user_id,
        "email": email,
        "name": name,
    }).execute()
    return r.data[0]

def is_subscribed(user_id: str) -> bool:
    r = db().table("users").select("subscribed").eq("id", user_id).execute()
    return bool(r.data and r.data[0].get("subscribed"))

def set_subscription(clerk_user_id: str, subscribed: bool,
                     stripe_customer_id: str = None,
                     stripe_subscription_id: str = None):
    update = {"subscribed": subscribed}
    if stripe_customer_id:     update["stripe_customer_id"]     = stripe_customer_id
    if stripe_subscription_id: update["stripe_subscription_id"] = stripe_subscription_id
    db().table("users").update(update).eq("clerk_user_id", clerk_user_id).execute()

def get_subscribed_users() -> list:
    r = db().table("users").select("id, clerk_user_id, email, name").eq("subscribed", True).execute()
    return r.data or []

def get_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    r = db().table("users").select("clerk_user_id").eq("stripe_customer_id", stripe_customer_id).execute()
    return r.data[0] if r.data else None


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(user_id: str) -> dict:
    r = db().table("configs").select("*").eq("user_id", user_id).execute()
    return r.data[0] if r.data else {}

def save_config(user_id: str, data: dict):
    existing = db().table("configs").select("id").eq("user_id", user_id).execute()
    if existing.data:
        db().table("configs").update(data).eq("user_id", user_id).execute()
    else:
        db().table("configs").insert({"user_id": user_id, **data}).execute()


# ── User data (filters / template / schedule) ─────────────────────────────────

def get_data(user_id: str) -> dict:
    r = db().table("user_data").select("*").eq("user_id", user_id).execute()
    if r.data:
        row = r.data[0]
        return {
            "filters":  row.get("filters")  or {},
            "template": row.get("template") or {},
            "schedule": row.get("schedule") or {},
        }
    return {"filters": {}, "template": {}, "schedule": {}}

def save_data(user_id: str, data: dict):
    existing = db().table("user_data").select("id").eq("user_id", user_id).execute()
    payload = {k: data[k] for k in ("filters", "template", "schedule") if k in data}
    if existing.data:
        db().table("user_data").update(payload).eq("user_id", user_id).execute()
    else:
        db().table("user_data").insert({"user_id": user_id, **payload}).execute()


# ── Logs ──────────────────────────────────────────────────────────────────────

def append_log(user_id: str, row: dict):
    db().table("logs").insert({"user_id": user_id, **row}).execute()

def get_logs(user_id: str, limit: int = 200) -> list:
    r = (db().table("logs").select("*")
         .eq("user_id", user_id)
         .order("timestamp", desc=True)
         .limit(limit)
         .execute())
    return r.data or []

def clear_logs(user_id: str):
    db().table("logs").delete().eq("user_id", user_id).execute()

def sent_today(user_id: str) -> int:
    today = date.today().isoformat()
    r = (db().table("logs").select("id", count="exact")
         .eq("user_id", user_id).eq("status", "sent")
         .gte("timestamp", today).execute())
    return r.count or 0

def sent_today_per_company(user_id: str) -> dict:
    today = date.today().isoformat()
    r = (db().table("logs").select("company")
         .eq("user_id", user_id).eq("status", "sent")
         .gte("timestamp", today).execute())
    counts = defaultdict(int)
    for row in (r.data or []):
        counts[(row.get("company") or "").lower()] += 1
    return counts

def total_sent(user_id: str) -> int:
    r = (db().table("logs").select("id", count="exact")
         .eq("user_id", user_id).eq("status", "sent").execute())
    return r.count or 0
