import streamlit as st
from datetime import datetime

from send_core import extract_text_from_upload, summarize_with_openai, send_both

st.set_page_config(page_title="Daily Summary Bot", layout="centered")

st.title("Daily Summary Bot")
st.caption("Upload PDF/DOCX/TXT or paste text → AI summary → Send to Email + Telegram")

tone = st.selectbox("Tone", ["professional", "neutral", "friendly"], index=0)

uploaded = st.file_uploader("Upload a file (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
pasted = st.text_area("Or paste text / paragraph here", height=200)

subject_prefix = st.text_input("Email Subject Prefix", value="[Daily Report]")

if st.button("Generate Summary"):
    with st.spinner("Extracting & summarizing..."):
        file_text = extract_text_from_upload(uploaded)
        raw_text = (pasted.strip() + "\n\n" + file_text.strip()).strip()
        summary = summarize_with_openai(raw_text, tone=tone)
        st.session_state["summary"] = summary

if "summary" in st.session_state:
    st.subheader("Preview")
    st.text(st.session_state["summary"])

    if st.button("Send to Email + Telegram"):
        with st.spinner("Sending..."):
            today = datetime.now().strftime("%Y-%m-%d")
            subject = f"{subject_prefix} Daily Summary ({today})"
            body = f"{subject}\n\n{st.session_state['summary']}"
            send_both(subject, body)
        st.success("Sent successfully ✅")
