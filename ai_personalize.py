import requests
import json
import re


COMPANY_FOCUS = {
    "goldman sachs": "Goldman's M&A advisory work and the breadth of deals across every sector",
    "morgan stanley": "Morgan Stanley's institutional securities group and capital markets scale",
    "jpmorgan": "JPMorgan's global coverage across M&A and capital markets",
    "jp morgan": "JPMorgan's global coverage across M&A and capital markets",
    "bank of america": "BofA's investment banking division and strength in leveraged finance",
    "citigroup": "Citi's global banking and capital markets platform",
    "barclays": "Barclays' investment banking group and European coverage",
    "lazard": "Lazard's sovereign advisory and cross-border M&A work",
    "evercore": "Evercore's independent advisory model and restructuring practice",
    "pjt partners": "PJT's restructuring and strategic advisory work on complex deals",
    "centerview partners": "Centerview's elite independent advisory track record",
    "moelis & company": "Moelis's independent advisory and breadth of industry coverage",
    "houlihan lokey": "Houlihan Lokey's restructuring and financial advisory work",
    "blackstone": "Blackstone's private equity and real estate investment platforms",
    "kkr": "KKR's private equity deals and approach to long-term value creation",
    "apollo global management": "Apollo's credit, private equity, and real asset strategies",
    "carlyle group": "Carlyle's global private equity and infrastructure investments",
    "mckinsey": "McKinsey's strategy and management consulting work across industries",
    "bain": "Bain's results-driven consulting approach",
    "bcg": "BCG's strategic consulting and digital transformation work",
    "google": "Google's scale across ads, cloud, and AI infrastructure",
    "microsoft": "Microsoft's enterprise software, cloud, and AI investments",
    "amazon": "Amazon's AWS cloud platform and the breadth of their business",
}


def get_company_focus(company):
    return COMPANY_FOCUS.get(company.lower().strip(),
                              f"the work {company} does in their industry")


def fill_placeholders(template, person):
    return (template
            .replace("{first_name}",    person.get("first_name", ""))
            .replace("{last_name}",     person.get("last_name", ""))
            .replace("{company}",       person.get("company", ""))
            .replace("{title}",         person.get("title", ""))
            .replace("{school}",        person.get("school", ""))
            .replace("{company_focus}", person.get("company_focus", "")))


def polish_with_groq(groq_key, subject, body, person):
    prompt = (
        f"Polish this networking email so it reads naturally. "
        f"Keep same tone and length. Return ONLY JSON: {{\"subject\":\"...\",\"body\":\"...\"}}\n\n"
        f"Subject: {subject}\nBody:\n{body}\n\n"
        f"Recipient: {person['first_name']} {person['last_name']}, "
        f"{person['title']} at {person['company']}."
    )
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json={"model": "llama3-8b-8192", "max_tokens": 512,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        result = json.loads(match.group())
        return result.get("subject", subject), result.get("body", body)
    return subject, body


def personalize(template_subject, template_body, contact, filters, groq_key=None):
    person = {
        "first_name":    contact.get("first_name", ""),
        "last_name":     contact.get("last_name", ""),
        "company":       contact.get("organization_name", ""),
        "title":         contact.get("title", "") or (filters.get("title", "").split(",")[0].strip()),
        "school":        filters.get("school", "").split(",")[0].strip(),
        "company_focus": get_company_focus(contact.get("organization_name", "")),
        "email":         contact.get("email", ""),
    }
    subject = fill_placeholders(template_subject, person)
    body    = fill_placeholders(template_body, person)

    if groq_key:
        try:
            subject, body = polish_with_groq(groq_key, subject, body, person)
        except Exception:
            pass

    return person, subject, body
