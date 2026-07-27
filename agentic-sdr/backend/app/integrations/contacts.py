"""Contact discovery: Hunter.io first (verified emails ONLY — spec §7.2), then
Apollo.io. Returning unverified guesses is how a sender domain earns a spam
reputation, so Hunter results without verification.status == 'valid' are discarded."""
from typing import Dict, Optional

import httpx

from app.config import settings

PRIORITY_ROLES = ("ceo", "founder", "president", "vp", "director", "head", "cto", "coo", "cmo")


def _extract_domain(website: Optional[str]) -> Optional[str]:
    if not website:
        return None
    domain = website.replace("https://", "").replace("http://", "").split("/")[0]
    return domain.replace("www.", "") or None


def hunter_verified_contact(domain: str, timeout: float = 20.0) -> Optional[Dict]:
    if not settings.HUNTER_API_KEY:
        return None
    resp = httpx.get(
        "https://api.hunter.io/v2/domain-search",
        params={"domain": domain, "api_key": settings.HUNTER_API_KEY, "limit": 10},
        timeout=timeout,
    )
    resp.raise_for_status()
    emails = resp.json().get("data", {}).get("emails", [])

    verified = [
        e for e in emails
        if (e.get("verification") or {}).get("status") == "valid" and e.get("value")
    ]
    if not verified:
        return None

    def _mk(e: Dict) -> Dict:
        return {
            "contact_name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
            "contact_email": e["value"],
            "contact_role": e.get("position") or "",
            "contact_source": "hunter",
        }

    for e in verified:
        role = (e.get("position") or "").lower()
        if any(r in role for r in PRIORITY_ROLES):
            return _mk(e)
    return _mk(verified[0])


def apollo_contact(company_name: str, domain: Optional[str], timeout: float = 20.0) -> Optional[Dict]:
    if not settings.APOLLO_API_KEY:
        return None
    payload = {
        "api_key": settings.APOLLO_API_KEY,
        "q_organization_name": company_name,
        "person_titles": ["CEO", "Founder", "VP", "Director", "Head", "CTO"],
        "page": 1,
        "per_page": 5,
    }
    if domain:
        payload["q_organization_domains"] = [domain]
    resp = httpx.post("https://api.apollo.io/v1/mixed_people/search", json=payload, timeout=timeout)
    resp.raise_for_status()
    for person in resp.json().get("people", []):
        email = person.get("email")
        if email and "@" in email and not email.startswith("email_not_unlocked"):
            return {
                "contact_name": person.get("name") or "",
                "contact_email": email,
                "contact_role": person.get("title") or "",
                "contact_source": "apollo",
            }
    return None


def find_contact(company_name: str, website: Optional[str]) -> Optional[Dict]:
    domain = _extract_domain(website)
    if domain:
        result = hunter_verified_contact(domain)
        if result:
            return result
    return apollo_contact(company_name, domain)
