import os
import requests
import PyPDF2
import docx


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
# GEMINI SUMMARY (REST v1 – FIXED)
# ===============================

def summarize_with_gemini(raw_text: str, tone: str = "professional") -> str:

    if not raw_text.strip():
        return "No content provided."

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    model = "gemini-1.5-flash"   # stable model

    url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={api_key}"

    prompt = f"""
You are a senior executive assistant.
Tone: {tone}.

Summarize into:
1) One concise paragraph
2) 5 key bullet points
3) Action Items (max 3)

Content:
{raw_text[:30000]}
"""

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    response = requests.post(url, json=payload, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(f"Gemini error {response.status_code}: {response.text}")

    data = response.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]


# ===============================
# SEND EMAIL (SendGrid)
# ===============================

def send_email_sendgrid(subject: str, body: str) -> None:

    api_key = os.environ.get("SENDGRID_API_KEY")
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")

    recipients = [{"email": e.strip()} for e in email_to.split(",") if e.strip()]

    payload = {
        "personalizations": [{"to": recipients}],
        "from": {"email": email_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
        "reply_to": {"email": email_from},
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")


# ===============================
# SEND TELEGRAM
# ===============================

def send_telegram(message: str) -> None:

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    r = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
    )

    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


# ===============================
# SEND BOTH
# ===============================

def send_both(subject: str, body: str) -> None:
    send_email_sendgrid(subject, body)
    send_telegram(body)
