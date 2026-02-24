# gmail_core.py
import os
import requests

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def get_access_token() -> str:
    client_id = _require_env("GMAIL_CLIENT_ID")
    client_secret = _require_env("GMAIL_CLIENT_SECRET")
    refresh_token = _require_env("GMAIL_REFRESH_TOKEN")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    r = requests.post(GMAIL_TOKEN_URL, data=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Gmail token error {r.status_code}: {r.text}")
    return r.json()["access_token"]


def _gmail_get(path: str, params: dict | None = None) -> dict:
    access_token = get_access_token()
    url = f"{GMAIL_API_BASE}/{path.lstrip('/')}"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Gmail API error {r.status_code}: {r.text}")
    return r.json()


def _parse_headers(headers: list[dict]) -> dict:
    out = {}
    for h in headers or []:
        out[(h.get("name") or "").lower()] = h.get("value", "")
    return out


def fetch_emails_unread_24h(max_results: int = 20) -> list[dict]:
    """
    Fetch unread emails in last 24 hours.
    Returns list of {id, from, subject, date, snippet}
    """
    user_id = "me"
    q = "is:unread newer_than:1d"

    data = _gmail_get(f"users/{user_id}/messages", params={"q": q, "maxResults": max_results})
    msgs = data.get("messages", []) or []

    results = []
    for m in msgs:
        mid = m.get("id")
        if not mid:
            continue

        detail = _gmail_get(
            f"users/{user_id}/messages/{mid}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"],
            },
        )

        headers = _parse_headers(detail.get("payload", {}).get("headers", []))
        results.append({
            "id": mid,
            "from": (headers.get("from", "") or "").strip(),
            "subject": (headers.get("subject", "") or "").strip(),
            "date": (headers.get("date", "") or "").strip(),
            "snippet": (detail.get("snippet") or "").strip(),
        })

    return results
