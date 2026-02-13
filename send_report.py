import os
from datetime import datetime, timezone, timedelta
import requests
from openai import OpenAI
import PyPDF2
import docx

INPUT_FILE = os.getenv("INPUT_FILE", "daily.txt")
MYT = timezone(timedelta(hours=8))


# -------- Extract Text --------
def extract_text(file_path):
    if not os.path.exists(file_path):
        return ""

    if file_path.endswith(".pdf"):
        text = ""
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ""
        return text

    elif file_path.endswith(".docx"):
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])

    elif file_path.endswith(".txt"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    else:
        return ""


# -------- AI Summarize --------
def summarize(text):
    if not text:
        return "No content found."

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_prompt = """
You are a senior executive assistant.
Summarize the content into:
1. One concise paragraph
2. 5 key bullet points
3. Clear action items
Keep it professional and structured.
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text[:12000]},  # prevent too long input
        ],
    )

    return resp.output_text.strip()


# -------- Send Email (SendGrid) --------
def send_email(subject, body):
    api_key = os.environ["SENDGRID_API_KEY"]
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]

    payload = {
        "personalizations": [
            {"to": [{"email": x.strip()} for x in email_to.split(",")]}
        ],
        "from": {"email": email_from},
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
    )

    if r.status_code not in (200, 201, 202):
        raise Exception(r.text)


# -------- Send Telegram --------
def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})


def main():
    today = datetime.now(MYT).strftime("%Y-%m-%d")

    text = extract_text(INPUT_FILE)
    summary = summarize(text)

    title = f"Daily AI Summary ({today})"
    full_message = f"{title}\n\n{summary}"

    send_email(title, full_message)
    send_telegram(full_message)

    print("Done.")


if __name__ == "__main__":
    main()
