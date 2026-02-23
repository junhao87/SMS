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
from reportlab.pdfgen import canvas
from twilio.rest import Client


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
# LANGUAGE DETECTION
# ===============================

def detect_language(text: str) -> str:
    if not text:
        return "en"
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ratio = cjk / max(len(text), 1)
    return "zh" if ratio >= 0.08 else "en"


# ===============================
# GEMINI MODEL
# ===============================

def pick_model() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Model list error {r.status_code}: {r.text}")

    models = r.json().get("models", [])
    candidates = [
        m["name"] for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]

    if not candidates:
        raise RuntimeError("No Gemini models available")

    for m in candidates:
        if "flash" in m.lower():
            return m
    for m in candidates:
        if "pro" in m.lower():
            return m

    return candidates[0]


def gemini_generate(prompt: str, model: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1/{model}:generateContent?key={api_key}"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    r = requests.post(url, json=payload, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")

    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ===============================
# CHUNKING
# ===============================

def chunk_text(text: str, max_chars=12000, overlap=600):
    text = text.strip()
    if not text:
        return []

    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    buf = ""

    for p in paras:
        if len(buf) + len(p) < max_chars:
            buf += "\n\n" + p
        else:
            chunks.append(buf.strip())
            buf = p

    if buf.strip():
        chunks.append(buf.strip())

    return chunks


# ===============================
# SUMMARY
# ===============================

def summarize_long_document(raw_text: str, force_lang=None):

    if not raw_text.strip():
        return "No content provided.", "en", {"chunks": 0}

    detected = detect_language(raw_text)
    out_lang = force_lang if force_lang in ("zh", "en") else detected
    model = pick_model()

    chunks = chunk_text(raw_text)

    lang_rule = "Respond in Chinese (简体中文)." if out_lang == "zh" else "Respond in English."

    # Single chunk
    if len(chunks) == 1:
        prompt = f"""
Condensed compression summary.

Rules:
- Output ONLY 4–6 bullet points.
- Each bullet ≤ 15 words.
- No action items. No conclusion. No expansion.
- Strictly objective.
- {lang_rule}

Content:
{chunks[0][:20000]}
"""
        final = gemini_generate(prompt, model)
        return final, out_lang, {"chunks": 1, "model": model}

    # Multi-chunk
    partials = []
    for ch in chunks:
        prompt = f"""
Ultra-short compression.

Rules:
- 2–3 bullet points.
- ≤ 12 words each.
- Strictly objective.
- {lang_rule}

Content:
{ch[:15000]}
"""
        partials.append(gemini_generate(prompt, model))

    merged = "\n".join(partials)

    final_prompt = f"""
Final condensed compression.

Rules:
- Output ONLY 4–6 bullet points.
- ≤ 15 words each.
- No action items. No expansion.
- Strictly objective.
- {lang_rule}

Content:
{merged}
"""
    final = gemini_generate(final_prompt, model)
    return final, out_lang, {"chunks": len(chunks), "model": model}


# ===============================
# PDF
# ===============================

def summary_to_pdf_bytes(title: str, text: str):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 60
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, title)
    y -= 30

    c.setFont("Helvetica", 11)
    for line in text.splitlines():
        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 60
        c.drawString(40, y, line)
        y -= 15

    c.save()
    return buffer.getvalue()


# ===============================
# HISTORY
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
            send_sms INTEGER
        )
    """)
    conn.commit()
    conn.close()


def save_history(title, summary, lang, send_email, send_telegram, send_sms):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO history (created_at, lang, title, summary, send_email, send_telegram, send_sms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S"),
        lang, title, summary,
        int(send_email),
        int(send_telegram),
        int(send_sms)
    ))
    conn.commit()
    conn.close()


def load_history(limit=50):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
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
        })
    return result


# ===============================
# SENDERS
# ===============================

def send_email_sendgrid(subject, body):
    api_key = os.getenv("SENDGRID_API_KEY")
    email_from = os.getenv("EMAIL_FROM")
    email_to = os.getenv("EMAIL_TO")

    recipients = [{"email": e.strip()} for e in email_to.split(",")]

    payload = {
        "personalizations": [{"to": recipients}],
        "from": {"email": email_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload
    )

    if r.status_code not in (200, 201, 202):
        raise RuntimeError(r.text)


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": message})

    if r.status_code != 200:
        raise RuntimeError(r.text)


def send_sms_twilio(message):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_num = os.getenv("TWILIO_FROM")
    to_nums = os.getenv("SMS_TO")

    client = Client(sid, token)

    numbers = [n.strip() for n in to_nums.split(",") if n.strip()]
    for n in numbers:
        if n.startswith("0"):
            n = "+60" + n[1:]
        client.messages.create(body=message, from_=from_num, to=n)


def send_selected(subject, body, send_email, send_telegram_flag, send_sms_flag):
    if send_email:
        send_email_sendgrid(subject, body)
    if send_telegram_flag:
        send_telegram(body)
    if send_sms_flag:
        send_sms_twilio(body)
