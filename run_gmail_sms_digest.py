# run_gmail_sms_digest.py
from datetime import datetime, timezone, timedelta

from gmail_core import fetch_emails_unread_24h
from digest_core import mark_mentions, summarize_gmail_digest_with_gemini, send_sms_twilio_multi

MYT = timezone(timedelta(hours=8))

def main():
    emails = fetch_emails_unread_24h(max_results=20)
    emails = mark_mentions(emails)

    digest, _lang = summarize_gmail_digest_with_gemini(emails, force_lang="en")

    # SMS: keep it compact (no long title)
    send_sms_twilio_multi(digest)

    # optional: if you want to log to DB later, call save_history(...) here

if __name__ == "__main__":
    main()
