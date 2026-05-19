import requests


def search_contacts(api_key, filters, page=1, per_page=25):
    body = {"page": page, "per_page": per_page}

    if filters.get("title"):
        body["person_titles"] = [t.strip() for t in filters["title"].split(",") if t.strip()]
    if filters.get("company"):
        body["q_organization_names"] = [c.strip() for c in filters["company"].split(",") if c.strip()]
    if filters.get("location"):
        body["person_locations"] = [l.strip() for l in filters["location"].split(",") if l.strip()]
    if filters.get("school"):
        body["person_schools"] = [s.strip() for s in filters["school"].split(",") if s.strip()]

    resp = requests.post(
        "https://api.apollo.io/v1/mixed_people/search",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return [p for p in data.get("people", []) if p.get("email")]
