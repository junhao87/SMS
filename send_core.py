import os
import requests
import PyPDF2
import docx

# =========================================================
# 1) Extract text from uploaded files (PDF / DOCX / TXT)
# =========================================================

def extract_text_from_upload(uploaded_file) -> str:
    """
    Extract text from a Streamlit uploaded file object.
    Supports: .pdf, .docx, .txt
    """
    if uploaded_file is None:
        return ""

    filename = (uploaded_file.name or "").lower()

    try:
        if filename.endswith(".pdf"):
            reader = PyPDF2.PdfReader(uploaded_file)
            chunks = []
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
            return "\n".join(chunks).strip()

        if filename.endswith(".docx"):
            d = docx.Document(uploaded_file)
            return "\n".join([p.text for p in d.paragraphs]).strip()

        if filename.endswith(".txt"):
            return uploaded_file.read().decode("utf-8", errors="ignore").strip()

        return ""
    except Exception as e:
        raise RuntimeError(f"File extraction failed: {e}") from e


# =========================================================
# 2) AI Summary (Gemini API)
# =========================================================

def summarize_with_gemini(raw_text: str, tone: str = "professional") -> str:
    """
    Uses Gemini (Google Generative Language API) to produce a structured summary.
    Requires env var: GEMINI_API_KEY
    Optional env var: GEMINI_MODEL (default: gemini-1.5-flash)
    """
    if not raw_text or not raw_text.strip():
        return "No content provided.\n\n- Key Points: N/A\n- Action Items: N/A"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing in Secrets.")

    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

    # Keep prompts consistent for bosses: short, structured, action-focused
    prompt = f"""
You are a senior executive assistant.
Tone: {tone}.

Summarize the content into:
1) One concise paragraph (2–4 sentences)
2) Exactly 5 bullet key takeaways
3) A section titled "Action Items" (max 3)

Be clear and practical. If something is unclear, state assumptions briefly.
Content:
{raw_text[:40000]}
""".strip()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    r = requests.post(url, json=payload, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text}")

    data = r.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        # Fallback in case response shape changes
        return str(data)


# =========================================================
# 3) Send Email via SendGrid
# =========================================================

def send_email_sendgrid(subject: str, body: str) -> None:
    """
    Send email using SendGrid v3 Mail Send API.
    Requires env vars:
      SENDGRID_API_KEY, EMAIL_FROM, EMAIL_TO
    EMAIL_TO can be comma-separated.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    email_from = os.environ.get("EMAIL_FROM", "").strip()
    email_to = os.environ.get("EMAIL_TO", "").strip()

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
        "reply_to": {"email": email_from},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    # SendGrid success is commonly 202 Accepted
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")


# =========================================================
# 4) Send Telegram
# =========================================================

def send_telegram(message: str) -> None:
    """
    Send a message to Telegram bot chat.
    Requires env vars:
      TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing.")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID missing.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


# =========================================================
# 5) Convenience: send to both channels
# =========================================================

def send_both(subject: str, body: str) -> None:
    """
    Send the same content to Email + Telegram.
    """
    send_email_sendgrid(subject, body)
    send_telegram(body)
