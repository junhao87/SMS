import os
from datetime import datetime, timezone, timedelta

import requests
from openai import OpenAI

# -----------------------------
# Config / Input
# -----------------------------
INPUT_FILE = os.getenv("INPUT_FILE", "daily.txt")

# Malaysia time (UTC+8)
MYT = timezone(timedelta(hours=8))


def read_input_text() -> str:
    """
    Reads raw daily content from a text file.
    You can later upgrade this to read from PDF/Word/Notion, etc.
    """
    if not os.path.exists(INPUT_FILE):
        return ""
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


# -----------------------------
# Process: AI Summarize (OpenAI)
# -----------------------------
def summarize_with_openai(raw_text: str) -> str:
    """
    Produces a concise executive summary:
    - 1 short paragraph (2-4 sentences)
    - Exactly 3 bullets: Action, Issue, Next
    """
    if not raw_text:
        return "No updates today.\n- Action: N/A\n- Issue: N/A\n- Next: N/A"

    api_key = os.environ["OPENAI_API_KEY"]
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    client = OpenAI(api_key=api_key)

    system_prompt = (
        "You are an executive assistant.\n"
        "Summarize the user's content into:\n"
        "1) One short paragraph (2-4 sentences)\n"
        "2) Exactly 3 bullet points labeled: Action, Issue, Next\n"
        "Keep it concise, professional, and clear.\n"
        "Do not add extra bullets beyond the three required."
    )

    # Using OpenAI Responses API (SDK)
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ],
    )

    return resp.output_text.strip()


# -----------------------------
# Output 1: Send Email via SendGrid
# -----------------------------
def send_email_sendgrid(subject: str, body: str) -> None:
    """
    Sends email through SendGrid v3 Mail Send endpoint.
    Success commonly returns HTTP 202 Accepted.
    """
    api_key = os.environ["SENDGRID_API_KEY"]
    email_from = os.environ["EMAIL_FROM"]  # MUST be verified in SendGrid
    email_to = os.environ["EMAIL_TO"]      # comma-separated supported

    to_list = [x.strip() for x in email_to.split(",") if x.strip()]
    if not to_list:
        raise ValueError("EMAIL_TO is empty. Please set EMAIL_TO to at least one recipient.")

    payload = {
        "personalizations": [
            {
                "to": [{"email": t} for t in to_list],
            }
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
        timeout=30,
    )

    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"SendGrid error {r.status_code}: {r.text}")


# -----------------------------
# Output 2: Send Telegram message
# -----------------------------
def send_telegram(text: str) -> None:
    """
    Sends a message to a specific Telegram chat id.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


# -----------------------------
# Main
# -----------------------------
def main():
    today = datetime.now(MYT).strftime("%Y-%m-%d")

    raw_text = read_input_text()
    summary = summarize_with_openai(raw_text)

    title = f"Daily Summary ({today})"
    full_message = f"{title}\n\n{summary}"

    # Send both outputs
    send_email_sendgrid(subject=title, body=full_message)
    send_telegram(full_message)

    print("Done: Sent email (SendGrid) + telegram.")


if __name__ == "__main__":
    main()
