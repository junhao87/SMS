import os
import requests
from openai import OpenAI
import PyPDF2
import docx

def extract_text_from_upload(uploaded_file) -> str:
    if uploaded_file is None:
        return ""

    name = uploaded_file.name.lower()

    if name.endswith(".pdf"):
        reader = PyPDF2.PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return text.strip()

    if name.endswith(".docx"):
        d = docx.Document(uploaded_file)
        return "\n".join([p.text for p in d.paragraphs]).strip()

    if name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="ignore").strip()

    return ""


def summarize_with_openai(raw_text: str, tone: str = "professional") -> str:
    if not raw_text.strip():
        return "No content provided.\n- Action: N/A\n- Issue: N/A\n- Next: N/A"

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    system_prompt = f"""
You are an executive assistant.
Tone: {tone}.
Summarize into:
1) One short paragraph (2-4 sentences)
2) Exactly 5 bullet points (key takeaways)
3) A final section: Action Items (max 3)
Keep it concise and clear.
"""

    # 简单防超长：只取前 50k 字符（后续可升级为分段总结）
    text = raw_text[:50000]

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )
    return resp.output_text.strip()


def send_email_sendgrid(subject: str, body: str) -> None:
    api_key = os.environ["SENDGRID_API_KEY"]
    email_from = os.environ["EMAIL_FROM"]   # must be verified in SendGrid
    email_to = os.environ["EMAIL_TO"]       # comma-separated

    to_list = [x.strip() for x in email_to.split(",") if x.strip()]
    if not to_list:
        raise ValueError("EMAIL_TO is empty.")

    payload = {
        "personalizations": [{"to": [{"email": t} for t in to_list]}],
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


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def send_both(subject: str, body: str) -> None:
    send_email_sendgrid(subject, body)
    send_telegram(body)
