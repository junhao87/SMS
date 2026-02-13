import os
import requests
import PyPDF2
import docx


# ===============================
# TEXT EXTRACTION
# ===============================

def extract_text_from_upload(uploaded_file) -> str:
    """
    Extract text from PDF / DOCX / TXT uploaded via Streamlit.
    """
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
        raise RuntimeError(f"File extraction failed: {e}") from e


# ===============================
# GEMINI SUMMARY
# ===============================

def summarize_with_gemini(raw_text: str, tone: str = "professional") -> str:
    """
    Generate executive summary using Gemini API (Google AI Studio key).
    """
    if not raw_text.strip():
        return "No content provided.\n\n- Action: N/A\n- Issue: N/A\n- Next: N/A"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing in Secrets.")

    # Model: fast + cheap, good for summaries
    model = os.getenv("GEMINI_MODEL", "gemini-pro").strip()


    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Keep input bounded to avoid request too large
    clipped = raw_text[:40000]

    prompt = f"""
You are a senior executive assistant.
Tone: {tone}.

Summarize the content into:
1) One concise paragraph (2–4 sentences)
2) Exactly 5 bullet key takeaways
3) A section titled "Action Items" (max 3)

If the content is mixed-language, keep the summary in English.
Content:
{clipped}
""".strip()

    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    try:
        r = requests.post(url, json=payload, timeout=45)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")

        data = r.json()

        # Typical response path
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    except Exception as e:
        raise RuntimeError(f"Gemini request failed. Model={model}. Details: {e}") from e


# ===============================
# SEND EMAIL (SendGrid)
# ===============================

def send_email_sendgrid(subject: str, body: str) -> None:
    """
    Send email using SendGrid API.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    email_from = os.environ.get("EMAIL_FROM", "").strip()   # must be verified in SendGrid
    email_to = os.environ.get("EMAIL_TO", "").strip()       # comma-separated

    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY missing.")
    if not email_from:
        raise RuntimeError("EMAIL_FROM missing.")
    if not email_to:
        raise RuntimeError("EMAIL_TO missing.")

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

    try:
        r = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if r.status_code not in (200, 201, 202):
            raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")

    except Exception as e:
        raise RuntimeError(f"Email sending failed: {e}") from e


# ===============================
# SEND TELEGRAM
# ===============================

def send_telegram(message: str) -> None:
    """
    Send message to Telegram bot chat.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing.")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID missing.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        if r.status_code != 200:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")

    except Exception as e:
        raise RuntimeError(f"Telegram sending failed: {e}") from e


# ===============================
# SEND BOTH
# ===============================

def send_both(subject: str, body: str) -> None:
    """
    Send summary to both Email and Telegram.
    """
    send_email_sendgrid(subject, body)
    send_telegram(body)
