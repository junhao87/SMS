# digest_core.py
import json

from send_core import pick_model, gemini_generate, detect_language, send_sms_twilio


def mark_mentions(emails: list[dict]) -> list[dict]:
    """
    Mention detection: ONLY Subject + Snippet.
    Match: "Donald" / "Donald Lim" (case-insensitive).
    Ignore CC/BCC.
    """
    tokens = ["donald", "donald lim"]
    out = []
    for e in emails:
        text = f"{e.get('subject','')} {e.get('snippet','')}".lower()
        e2 = dict(e)
        e2["mention"] = any(tok in text for tok in tokens)
        out.append(e2)
    return out


def _digest_prompt(emails: list[dict], out_lang: str, unread_count: int, mention_count: int) -> str:
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

Goals:
- Summarise the inbox in 3 key highlights.
- Create an itemised TASK list (follow-up actions) as the MAIN focus.
- Mentions: ONLY if mention=true (name appears in subject/snippet). Ignore CC/BCC.

STRICT output format (no extra text, no paragraphs):
LINE1: "24h Unread: {unread_count} | Mentions: {mention_count}"

KEY: <highlight 1 (<= 95 chars)>
KEY: <highlight 2 (<= 95 chars)>
KEY: <highlight 3 (<= 95 chars)>

TASK: <action 1 (<= 95 chars)>
TASK: <action 2 (<= 95 chars)>
TASK: <action 3 (<= 95 chars)>
(If needed, add up to 3 more TASK lines, max 6 TASK lines total)

MENTION: <from> | <subject> | <why it matters (<= 75 chars)>
(If none) MENTION: None

Rules:
- Be factual; do NOT invent details.
- Prefer deadlines/approvals/action-needed.
- Keep total output under 1100 characters.
- {lang_rule}

Email data (JSON):
{json.dumps(compact, ensure_ascii=False)}
""".strip()


def _hard_trim(text: str, max_chars: int = 1100) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip()


def summarize_gmail_digest_with_gemini(emails: list[dict], force_lang: str = "en") -> tuple[str, str]:
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
            "KEY: Review action-required items and deadlines.\n"
            "KEY: Check approvals/meeting updates.\n"
            "TASK: Reply to action-required emails.\n"
            "TASK: Confirm any meetings or deadlines mentioned.\n"
            "TASK: Follow up on approvals or client decisions.\n"
            "MENTION: None"
        )

    return text.strip(), out_lang


def split_sms(message: str, limit: int = 320) -> list[str]:
    """
    Split into multiple SMS chunks. Prefer splitting by lines.
    """
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
            # hard split long line
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
