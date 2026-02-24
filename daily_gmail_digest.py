# daily_gmail_digest.py
from datetime import datetime, timezone, timedelta

from gmail_core import fetch_emails_unread_24h
from send_core import (
    mark_mentions,
    summarize_gmail_digest_with_gemini,
    send_selected,
    save_history,
)

MYT = timezone(timedelta(hours=8))

def main():
    # 1) fetch unread emails in last 24h
    emails = fetch_emails_unread_24h(max_results=20)

    # 2) mark mentions based on Subject+Snippet only
    emails = mark_mentions(emails)

    # 3) Gemini digest (task-focused)
    digest, lang = summarize_gmail_digest_with_gemini(emails, force_lang="en")

    # 4) send SMS (auto-split)
    today = datetime.now(MYT).strftime("%Y-%m-%d")
    title = f"[Daily Report] Gmail Digest ({today})"
    body = f"{title}\n\n{digest}"

    send_selected(
        subject=title,
        body=body,
        send_email=False,
        send_telegram_flag=False,
        send_sms_flag=True,
        summary_for_sms=digest,  # ✅ keep SMS compact (no long title)
    )

    # 5) store history
    save_history(
        title=title,
        summary=digest,
        lang=lang,
        send_email=False,
        send_telegram=False,
        send_sms=True,
        meta={"source": "gmail_digest", "count": len(emails)},
    )

if __name__ == "__main__":
    main()
