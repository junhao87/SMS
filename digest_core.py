# digest_core.py
import json
import re

# 从 send_core.py 复用 Gemini & SMS 基础能力
from send_core import pick_model, gemini_generate, detect_language, send_sms_twilio


# ===== Mention matching: Donald / Donald Lim (case-insensitive) =====
def _mention_tokens() -> list[str]:
    return ["donald", "donald lim"]


def mark_mentions(emails: list[dict]) -> list[dict]:
    """
    Mention is ONLY based on Subject + Snippet text.
    (Ignore CC/BCC completely)
    """
    tokens = _mention_tokens()
    out = []
    for e in emails:
        text = f"{e.get('subject','')} {e.get('snippet','')}".lower()
        e2 = dict(e)
        e2["mention"] = any(tok in text for tok in tokens)
        out.append(e2)
    return out


def _digest_prompt(emails: list[dict], out_lang: str, unread_count: int, mention_count: int) -> str:
    """
    Output is SMS-friendly but not overly short.
    MAIN focus: itemised task list.
    """
    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."

    compact = []
    for e in emails[:20]:
        compact.append({
            "from": (e.get("from","") or "")[:70],
            "subject": (e.get("subject","") or "")[:110],
            "date": (e.get("date","") or "")[:50],
            "snippet": (e.get("snippet","") or "")[:220],
            "mention": bool(e.get("mention", False)),
        })

    return f"""
Task: Summarise unread Gmail emails from the last 24 hours for SMS.

You must:
- Create 3 key highlights (short, factual).
- Create an itemised task list (follow-up actions) as the MAIN focus.
- Mentions: ONLY if mention=true (name appears in subject/snippet). Ignore CC/BCC.

STRICT output format (no extra text):
LINE1: "24h Unread: {unread_count} | Mentions: {mention_count}"
SECTION A (3 lines):
KEY: ...
KEY: ...
KEY: ...
SECTION B (tasks, 3–6 lines; most important first):
TASK: ...
TASK: ...
TASK: ...
(You may output up to 6 TASK lines)
SECTION C (mentions; keep short):
MENTION: <from> | <subject> | <why it matters>
(if none) MENTION: None

Rules:
- Be factual. Do NOT invent details.
- Prefer deadlines/approval/action-needed.
- Keep each KEY <= 95 chars.
- Keep each TASK <= 95 chars.
- Keep total output under 1100 characters.
- {lang_rule}

Email data (JSON):
{json.dumps(compact, ensure_ascii=False)}
""".strip()


def _hard_trim(text: str, max_chars: int = 1100) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip()


def summarize_gmail_digest_with_gemini(emails: list[dict], force_lang: str = "en") -> tuple[str, str]:
    """
    Returns (digest_text, lang)
    """
    unread_count = len(emails)
    mention_count = sum(1 for e in emails if e.get("mention"))

    if unread_count == 0:
        digest = (
            "24h Unread: 0 | Mentions: 0\n"
            "KEY: No unread emails in last 24 hours.\n"
            "KEY: \n"
            "KEY: \n"
            "TASK: None\n"
            "MENTION: None"
        )
        return digest, force_lang

    detected = detect_language(" ".join([(e.get("subject","") + " " + e.get("snippet","")) for e in emails]))
    out_lang = force_lang if force_lang in ("zh", "en") else detected

    model = pick_model()
    prompt = _digest_prompt(emails, out_lang, unread_count, mention_count)
    text = gemini_generate(prompt, model)

    text = _hard_trim(text, 1100)

    # Safety: ensure TASK exists
    if "TASK:" not in text:
        text = (
            f"24h Unread: {unread_count} | Mentions: {mention_count}\n"
            "KEY: New unread emails received.\n"
            "KEY: Review key updates and deadlines.\n"
            "KEY: Check if any approvals/replies are needed.\n"
            "TASK: Reply to any action-required emails.\n"
            "TASK: Confirm meetings/deadlines mentioned.\n"
            "MENTION: None"
        )

    return text.strip(), out_lang


# ===== SMS splitting + sending =====
def split_sms(message: str, limit: int = 320) -> list[str]:
    message = (message or "").strip()
    if len(message) <= limit:
        return [message]

    parts = []
    buf = ""

    for line in message.splitlines():
        if not line.strip():
            continue
        candidate = (buf + "\n" + line).strip() if buf else line
        if len(candidate) <= limit:
            buf = candidate
        else:
            if buf:
                parts.append(buf)
                buf = ""
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
            buf = line

    if buf:
        parts.append(buf)

    return parts


def send_sms_twilio_multi(message: str) -> None:
    for chunk in split_sms(message, limit=320):
        send_sms_twilio(chunk)
