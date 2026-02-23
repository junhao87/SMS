import streamlit as st
from datetime import datetime, timezone, timedelta

from send_core import (
    extract_text_from_upload,
    summarize_long_document,
    summary_to_pdf_bytes,
    save_history,
    load_history,
    send_selected,
)

MYT = timezone(timedelta(hours=8))

st.set_page_config(page_title="Daily Summary Bot", layout="centered")
st.title("Daily Summary Bot")

uploaded = st.file_uploader("Upload PDF/DOCX/TXT", type=["pdf", "docx", "txt"])
pasted = st.text_area("Or paste text", height=200)
subject_prefix = st.text_input("Subject Prefix", "[Daily Report]")

# Send toggles
st.subheader("Send Options")
send_email = st.toggle("Send Gmail", True)
send_tg = st.toggle("Send Telegram", True)
send_sms = st.toggle("Send SMS (Twilio)", False)

if st.button("Generate Summary"):
    raw_text = (pasted + "\n\n" + extract_text_from_upload(uploaded)).strip()
    if not raw_text:
        st.warning("Please provide content.")
    else:
        summary, lang, meta = summarize_long_document(raw_text)
        st.session_state["summary"] = summary
        st.session_state["lang"] = lang
        st.session_state["meta"] = meta

if "summary" in st.session_state:
    st.subheader("Preview")
    st.text(st.session_state["summary"])

    today = datetime.now(MYT).strftime("%Y-%m-%d")
    title = f"{subject_prefix} ({today})"

    pdf_bytes = summary_to_pdf_bytes(title, st.session_state["summary"])
    st.download_button("Download PDF", pdf_bytes, f"summary_{today}.pdf")

    confirm = st.checkbox("Confirm to send")

    if st.button("Send Now"):
        if not confirm:
            st.warning("Please confirm before sending.")
        else:
            body = f"{title}\n\n{st.session_state['summary']}"
            send_selected(title, body, send_email, send_tg, send_sms)
            save_history(title, st.session_state["summary"], st.session_state["lang"], send_email, send_tg, send_sms)
            st.success("Sent successfully.")

if st.button("View History"):
    rows = load_history()
    for r in rows:
        with st.expander(f"{r['created_at']} | SMS={r['send_sms']}"):
            st.text(r["summary"])
