# run_gmail_sms_digest.py
from gmail_core import fetch_emails_unread_24h
from digest_core import mark_mentions, summarize_gmail_digest_with_gemini, send_sms_twilio_multi


def main():
    emails = fetch_emails_unread_24h(max_results=20)
    emails = mark_mentions(emails)
    digest, _lang = summarize_gmail_digest_with_gemini(emails, force_lang="en")
    send_sms_twilio_multi(digest)


if __name__ == "__main__":
    main()
