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
# LIST AVAILABLE GEMINI MODELS
# ===============================

def list_gemini_models() -> str:
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
        if "generateContent" in methods:
            supported.append(name)

    if not supported:
        return "No models available for generateContent."

    return "\n".join(supported)


# ===============================
# GEMINI SUMMARY (CONDENSED ONLY)
# ===============================

def summarize_with_gemini(raw_text: str, tone: str = "professional") -> str:

    if not raw_text.strip():
        return "No content provided."

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    models_raw = list_gemini_models()
    if "No models available" in models_raw:
        raise RuntimeError("Your API key has no available Gemini models.")

    model = models_raw.split("\n")[0]

    url = f"https://generativelanguage.googleapis.com/v1/{model}:generateContent?key={api_key}"

    # 🔥 Condensed compression prompt
    prompt = f"""
Task: Condensed compression summary.

Strict Rules:
- Output ONLY the compressed summary.
- No title. No intro. No conclusion.
- Do NOT add action items.
- Do NOT add recommendations.
- Do NOT expand beyond the source.
- Do NOT infer missing information.
- Remove examples, background, pleasantries.
- Keep only core arguments and key facts.
- Be strictly objective.
- Use 4–6 bullet points ONLY.
- Each bullet ≤ 15 words.
- Target total length: 40–100 words.

Content:
{raw_text[:20000]}
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

    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ===============================
# SEND EMAIL (SendGrid)
# ===============================

def send_email_sendgrid(subject: str, body: str) -> None:

    api_key = os.environ.get("SENDGRID_API_KEY")
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")

    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY")
    if not email_from:
        raise RuntimeError("Missing EMAIL_FROM")
    if not email_to:
        raise RuntimeError("Missing EMAIL_TO")

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

    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID")

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
