import os
import re
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests
import PyPDF2
import docx

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from twilio.rest import Client


MYT = timezone(timedelta(hours=8))
DB_PATH = os.getenv("HISTORY_DB_PATH", "history.db")

# Your repo font (TTF)
DEFAULT_CJK_FONT_PATH = os.getenv("PDF_FONT_PATH", "assets/fonts/NotoSansSC-Regular.ttf")
DEFAULT_CJK_FONT_NAME = os.getenv("PDF_FONT_NAME", "NotoSansSC")


# ===============================
# HELPERS
# ===============================

def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def _clip(s: str, limit: int) -> str:
    return (s or "").strip()[:limit]


# ===============================
# TEXT EXTRACTION
# ===============================

def extract_text_from_upload(uploaded_file) -> str:
    """Extract text from PDF / DOCX / TXT uploaded via Streamlit."""
    if uploaded_file is None:
        return ""

    filename = (uploaded_file.name or "").lower()

    try:
        if filename.endswith(".pdf"):
            reader = PyPDF2.PdfReader(uploaded_file)
            parts = []
            for page in reader.pages:
                parts.append((page.extract_text() or "").strip())
            return "\n\n".join([p for p in parts if p]).strip()

        if filename.endswith(".docx"):
            document = docx.Document(uploaded_file)
            return "\n".join([p.text for p in document.paragraphs if p.text.strip()]).strip()

        if filename.endswith(".txt"):
            return uploaded_file.read().decode("utf-8", errors="ignore").strip()

        return ""
    except Exception as e:
        raise RuntimeError(f"File extraction failed: {e}") from e


# ===============================
# LANGUAGE DETECTION
# ===============================

def detect_language(text: str) -> str:
    """Heuristic: if CJK ratio >= 8% => zh, else en"""
    text = text or ""
    if not text.strip():
        return "en"
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ratio = cjk / max(len(text), 1)
    return "zh" if ratio >= 0.08 else "en"


# ===============================
# GEMINI
# ===============================

def pick_model() -> str:
    """Pick a model that supports generateContent. Prefer flash, then pro."""
    api_key = _require_env("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Model list error {r.status_code}: {r.text}")

    models = r.json().get("models", [])
    candidates = [
        m.get("name", "")
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    candidates = [c for c in candidates if c]

    if not candidates:
        raise RuntimeError("No Gemini models available for generateContent under this API key.")

    for m in candidates:
        if "flash" in m.lower():
            return m
    for m in candidates:
        if "pro" in m.lower():
            return m
    return candidates[0]


def gemini_generate(prompt: str, model: str) -> str:
    api_key = _require_env("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, json=payload, timeout=90)

    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")

    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        raise RuntimeError(f"Gemini response parsing failed: {data}") from None


# ===============================
# CHUNKING
# ===============================

def chunk_text(text: str, max_chars: int = 12000, overlap: int = 500) -> list[str]:
    """Split by paragraphs; if a paragraph is too long, hard-split."""
    text = (text or "").strip()
    if not text:
        return []

    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    buf = ""

    def flush():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}".strip()
            continue

        flush()

        if len(p) <= max_chars:
            buf = p
            continue

        start = 0
        step = max_chars - overlap
        if step <= 0:
            step = max_chars

        while start < len(p):
            part = p[start:start + max_chars].strip()
            if part:
                chunks.append(part)
            start += step

    flush()
    return chunks


# ===============================
# SUMMARY (CONDENSED COMPRESSION)
# ===============================

def _condensed_prompt(content: str, out_lang: str) -> str:
    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."
    return f"""
Task: Condensed compression summary.

Strict rules:
- Output ONLY 4–6 bullet points.
- Each bullet ≤ 15 words.
- No title, no intro, no conclusion.
- No action items, no recommendations.
- No extra reasoning, no implications.
- Do NOT infer missing information.
- Keep only core arguments and key facts. Remove filler.
- Be strictly objective.
- {lang_rule}

Content:
{content}
""".strip()


def _chunk_prompt(chunk: str, out_lang: str, idx: int, total: int) -> str:
    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."
    return f"""
Task: Ultra-short chunk compression.

Rules:
- Output ONLY 2–3 bullet points.
- Each bullet ≤ 12 words.
- No conclusions, no action items, no expansion.
- Strictly objective and faithful.
- {lang_rule}

Chunk {idx}/{total}:
{chunk}
""".strip()


def summarize_long_document(raw_text: str, force_lang: str | None = None):
    """
    Returns: (summary, lang, meta)
    meta includes: chunks (app won't display model)
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return "No content provided.", "en", {"chunks": 0}

    detected = detect_language(raw_text)
    out_lang = force_lang if force_lang in ("zh", "en") else detected
    model = pick_model()

    chunks = chunk_text(raw_text, max_chars=12000, overlap=500)

    if len(chunks) <= 1:
        final = gemini_generate(_condensed_prompt(_clip(raw_text, 20000), out_lang), model)
        return final, out_lang, {"chunks": len(chunks)}

    partials = []
    for i, ch in enumerate(chunks, start=1):
        partials.append(gemini_generate(_chunk_prompt(_clip(ch, 14000), out_lang, i, len(chunks)), model))

    merged = "\n".join(partials)
    final = gemini_generate(_condensed_prompt(_clip(merged, 20000), out_lang), model)
    return final, out_lang, {"chunks": len(chunks)}


# ===============================
# PDF (NO WEIRD EN WORD BREAKS + CJK OK)
# ===============================

def _register_cjk_font() -> str:
    """
    1) Prefer your repo TTF font
    2) Fallback to built-in CID CJK font (STSong-Light)
    3) Final fallback Helvetica
    """
    # TTF first
    try:
        if os.path.exists(DEFAULT_CJK_FONT_PATH):
            if DEFAULT_CJK_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(DEFAULT_CJK_FONT_NAME, DEFAULT_CJK_FONT_PATH))
            return DEFAULT_CJK_FONT_NAME
    except Exception:
        pass

    # CID fallback
    try:
        fallback_name = "STSong-Light"
        if fallback_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(fallback_name))
        return fallback_name
    except Exception:
        return "Helvetica"


def _parse_bullets(summary_text: str) -> list[str]:
    lines = []
    for raw in (summary_text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        # Normalize bullets
        s = re.sub(r"^[•\-\*\u2022]\s*", "", s)
        lines.append(s)
    return lines


def summary_to_pdf_bytes(title: str, summary_text: str) -> bytes:
    font_name = _register_cjk_font()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Daily Summary Bot",
    )

    styles = getSampleStyleSheet()

    TitleStyle = ParagraphStyle(
        "TitleStyle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=16,
        leading=20,
        textColor=colors.black,
        spaceAfter=10,
    )

    SubtleStyle = ParagraphStyle(
        "SubtleStyle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#333333"),
        spaceAfter=8,
    )

    BulletStyle = ParagraphStyle(
        "BulletStyle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=12,
        leading=17,
        textColor=colors.black,
    )

    story = []
    story.append(Paragraph(title, TitleStyle))
    story.append(Paragraph("Condensed compression (objective, no expansion).", SubtleStyle))
    story.append(Spacer(1, 8))

    bullets = _parse_bullets(summary_text)
    if not bullets:
        story.append(Paragraph("No summary content.", BulletStyle))
    else:
        lf = ListFlowable(
            [ListItem(Paragraph(b, BulletStyle), leftIndent=10) for b in bullets],
            bulletType="bullet",
            bulletFontName=font_name,
            bulletFontSize=10,
            bulletDedent=4,
            leftIndent=14,
        )
        story.append(lf)

    doc.build(story)
    return buf.getvalue()


# ===============================
# HISTORY (SQLite)
# ===============================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            lang TEXT,
            title TEXT,
            summary TEXT,
            send_email INTEGER,
            send_telegram INTEGER,
            send_sms INTEGER,
            meta TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_history(title, summary, lang, send_email, send_telegram, send_sms, meta=None):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO history (created_at, lang, title, summary, send_email, send_telegram, send_sms, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S"),
        lang,
        title,
        summary,
        int(bool(send_email)),
        int(bool(send_telegram)),
        int(bool(send_sms)),
        json.dumps(meta or {}, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()


def load_history(limit=50):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, created_at, lang, title, summary, send_email, send_telegram, send_sms, meta FROM history ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id": r[0],
            "created_at": r[1],
            "lang": r[2],
            "title": r[3],
            "summary": r[4],
            "send_email": bool(r[5]),
            "send_telegram": bool(r[6]),
            "send_sms": bool(r[7]),
            "meta": r[8] or "{}",
        })
    return result


# ===============================
# SENDERS
# ===============================

def send_email_sendgrid(subject: str, body: str) -> None:
    api_key = _require_env("SENDGRID_API_KEY")
    email_from = _require_env("EMAIL_FROM")
    email_to = _require_env("EMAIL_TO")

    recipients = [{"email": e.strip()} for e in email_to.split(",") if e.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO has no valid recipients.")

    payload = {
        "personalizations": [{"to": recipients}],
        "from": {"email": email_from, "name": "Daily Summary Bot"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
        "reply_to": {"email": email_from},
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )

    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")


def send_telegram(message: str) -> None:
    token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def _normalize_my_number(n: str) -> str:
    n = (n or "").strip().replace(" ", "").replace("-", "")
    if not n:
        return ""
    if n.startswith("+"):
        return n
    if n.startswith("0"):
        return "+60" + n[1:]
    return n


def sms_ultra_short(summary_text: str) -> str:
    summary_text = (summary_text or "").strip()
    if not summary_text:
        return ""
    lines = [ln.strip() for ln in summary_text.splitlines() if ln.strip()]
    compact = "\n".join(lines)
    return compact[:320].rstrip() if len(compact) > 320 else compact


def send_sms_twilio(message: str) -> None:
    sid = _require_env("TWILIO_ACCOUNT_SID")
    token = _require_env("TWILIO_AUTH_TOKEN")
    from_num = _require_env("TWILIO_FROM")
    to_nums = _require_env("SMS_TO")

    client = Client(sid, token)
    numbers = [_normalize_my_number(n) for n in to_nums.split(",") if n.strip()]
    if not numbers:
        raise RuntimeError("SMS_TO has no valid numbers.")

    for n in numbers:
        client.messages.create(body=message, from_=from_num, to=n)


def send_selected(
    subject: str,
    body: str,
    send_email: bool,
    send_telegram_flag: bool,
    send_sms_flag: bool,
    *,
    summary_for_sms: str | None = None
) -> None:
    if send_email:
        send_email_sendgrid(subject, body)
    if send_telegram_flag:
        send_telegram(body)
    if send_sms_flag:
        send_sms_twilio(sms_ultra_short(summary_for_sms or body))
