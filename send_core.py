import os
import re
import json
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import PyPDF2
import docx
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO


MYT = timezone(timedelta(hours=8))
DB_PATH = os.getenv("HISTORY_DB_PATH", "history.db")


# ===============================
# TEXT EXTRACTION
# ===============================

def extract_text_from_upload(uploaded_file) -> str:
    if uploaded_file is None:
        return ""

    filename = uploaded_file.name.lower()

    try:
        if filename.endswith(".pdf"):
            reader = PyPDF2.PdfReader(uploaded_file)
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
            return text.strip()

        if filename.endswith(".docx"):
            document = docx.Document(uploaded_file)
            return "\n".join([p.text for p in document.paragraphs]).strip()

        if filename.endswith(".txt"):
            return uploaded_file.read().decode("utf-8", errors="ignore").strip()

        return ""
    except Exception as e:
        raise RuntimeError(f"File extraction failed: {e}")


# ===============================
# LANGUAGE DETECTION (ZH/EN)
# ===============================

def detect_language(text: str) -> str:
    """
    Simple heuristic:
    - if CJK chars ratio is high -> zh
    - else -> en
    """
    if not text:
        return "en"
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    total = max(len(text), 1)
    ratio = cjk / total
    return "zh" if ratio >= 0.08 else "en"


# ===============================
# GEMINI MODEL DISCOVERY
# ===============================

def list_gemini_models() -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ListModels error {r.status_code}: {r.text}")

    data = r.json()
    models = data.get("models", [])

    supported = []
    for m in models:
        name = m.get("name", "")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods and name:
            supported.append(name)

    if not supported:
        raise RuntimeError("No Gemini models available for generateContent under this API key.")
    return supported


def pick_model(preferred_keywords=("flash", "pro")) -> str:
    """
    Choose a good available model. Prefer 'flash' then 'pro', else first available.
    """
    models = list_gemini_models()
    lower = [(m, m.lower()) for m in models]

    # Prefer flash
    for m, ml in lower:
        if "flash" in ml:
            return m
    # Prefer pro
    for m, ml in lower:
        if "pro" in ml:
            return m
    return models[0]


# ===============================
# CHUNKING FOR LONG TEXT
# ===============================

def chunk_text(text: str, max_chars: int = 12000, overlap: int = 600) -> list[str]:
    """
    Split by paragraphs first; if still too large, hard-split.
    """
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
        else:
            flush()
            if len(p) <= max_chars:
                buf = p
            else:
                # hard split long paragraph
                start = 0
                while start < len(p):
                    part = p[start:start + max_chars]
                    chunks.append(part.strip())
                    start += max_chars - overlap

    flush()
    return chunks


# ===============================
# GEMINI CALL
# ===============================

def gemini_generate(text_prompt: str, model_name: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1/{model_name}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {"parts": [{"text": text_prompt}]}
        ]
    }

    r = requests.post(url, json=payload, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")

    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ===============================
# SUMMARY (CONDENSED + LONG DOC SUPPORT)
# ===============================

def summarize_condensed(text: str, out_lang: str, model_name: str) -> str:
    """
    Condensed compression only:
    - 4–6 bullets
    - <= 15 words each
    - 40–100 words total
    - no action items, no conclusion, no expansion
    """
    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."
    prompt = f"""
Task: Condensed compression summary.

Strict rules:
- Output ONLY the compressed summary.
- No title. No intro. No conclusion.
- Do NOT add action items, recommendations, implications, or extra reasoning.
- Do NOT infer missing information. Do NOT expand beyond the source.
- Keep only core arguments and key facts. Remove examples/background/filler.
- Be strictly objective.
- Use 4–6 bullet points ONLY.
- Each bullet ≤ 15 words.
- Target total length: 40–100 words.
- {lang_rule}

Content:
{text}
""".strip()
    return gemini_generate(prompt, model_name)


def summarize_long_document(raw_text: str, force_lang: str | None = None) -> tuple[str, str, dict]:
    """
    If long: chunk → summarize each chunk very short → merge → final condensed compression.
    Returns: (final_summary, lang, meta)
    """
    if not raw_text.strip():
        return "No content provided.", "en", {"chunks": 0}

    detected = detect_language(raw_text)
    out_lang = force_lang if force_lang in ("zh", "en") else detected

    model_name = pick_model()
    chunks = chunk_text(raw_text, max_chars=12000, overlap=600)

    # Short docs: one pass condensed
    if len(chunks) <= 1:
        final = summarize_condensed(raw_text[:20000], out_lang, model_name)
        return final, out_lang, {"chunks": len(chunks), "model": model_name}

    # Step 1: summarize each chunk into 2-3 bullets (super short)
    partials = []
    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."
    for idx, ch in enumerate(chunks, start=1):
        prompt = f"""
Task: Ultra-short chunk compression.

Rules:
- Output ONLY 2–3 bullet points.
- Each bullet ≤ 12 words.
- No conclusions, no action items, no extra reasoning.
- Strictly objective and faithful.
- {lang_rule}

Chunk {idx}/{len(chunks)}:
{ch[:14000]}
""".strip()
        partials.append(gemini_generate(prompt, model_name))

    merged = "\n".join(partials)

    # Step 2: final condensed compression from merged partials
    final = summarize_condensed(merged[:20000], out_lang, model_name)
    return final, out_lang, {"chunks": len(chunks), "model": model_name}


# ===============================
# PDF GENERATION (SUMMARY -> PDF BYTES)
# ===============================

def summary_to_pdf_bytes(title: str, summary_text: str) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x = 40
    y = height - 60

    c.setFont("Helvetica-Bold", 14)
    c.drawString(x, y, title)
    y -= 24

    c.setFont("Helvetica", 11)

    # simple wrapping
    lines = []
    for raw_line in summary_text.splitlines():
        raw_line = raw_line.rstrip()
        if not raw_line:
            lines.append("")
            continue
        # wrap long line
        while len(raw_line) > 95:
            lines.append(raw_line[:95])
            raw_line = raw_line[95:]
        lines.append(raw_line)

    for line in lines:
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 60
        c.drawString(x, y, line)
        y -= 14

    c.save()
    return buffer.getvalue()


# ===============================
# HISTORY (SQLite)
# ===============================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            lang TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            send_email INTEGER NOT NULL,
            send_telegram INTEGER NOT NULL,
            meta TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_history(title: str, summary: str, lang: str, send_email: bool, send_telegram: bool, meta: dict):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO history (created_at, lang, title, summary, send_email, send_telegram, meta) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S"),
            lang,
            title,
            summary,
            1 if send_email else 0,
            1 if send_telegram else 0,
            json.dumps(meta, ensure_ascii=False),
        )
    )
    conn.commit()
    conn.close()


def load_history(limit: int = 50) -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, created_at, lang, title, summary, send_email, send_telegram, meta FROM history ORDER BY id DESC LIMIT ?", (limit,))
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
            "meta": r[7] or "{}",
        })
    return result


# ===============================
# SENDERS
# ===============================

def send_email_sendgrid(subject: str, body: str) -> None:
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    email_from = os.environ.get("EMAIL_FROM", "").strip()
    email_to = os.environ.get("EMAIL_TO", "").strip()

    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY")
    if not email_from:
        raise RuntimeError("Missing EMAIL_FROM")
    if not email_to:
        raise RuntimeError("Missing EMAIL_TO")

    recipients = [{"email": e.strip()} for e in email_to.split(",") if e.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO has no valid recipients.")

    payload = {
        "personalizations": [{"to": recipients}],
        "from": {"email": email_from},
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
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=30,
    )

    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def send_selected(subject: str, body: str, send_email: bool, send_telegram_flag: bool) -> None:
    if send_email:
        send_email_sendgrid(subject, body)
    if send_telegram_flag:
        send_telegram(body)
