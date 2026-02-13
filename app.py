import streamlit as st
from datetime import datetime, timezone, timedelta
from send_core import extract_text_from_upload, summarize_with_gemini, send_both

MYT = timezone(timedelta(hours=8))

st.set_page_config(page_title="Daily Summary Bot", layout="centered")
st.title("Daily Summary Bot")
st.caption("Upload PDF/DOCX/TXT or paste text → Preview → Confirm → Send (Email + Telegram)")

tone = st.selectbox("Tone", ["professional", "neutral", "friendly"], index=0)
uploaded = st.file_uploader("Upload a file (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
pasted = st.text_area("Or paste text / paragraph here", height=220)

subject_prefix = st.text_input("Email Subject Prefix", value="[Daily Report]")

# ---- Buttons row (Generate / Discard)
col1, col2 = st.columns(2)
with col1:
    gen_clicked = st.button("Generate Summary", use_container_width=True)
with col2:
    discard_clicked = st.button("Discard / Undo (Do not send)", use_container_width=True)

# ---- Discard: clear preview + reset sent flag
if discard_clicked:
    st.session_state.pop("summary", None)
    st.session_state.pop("raw_text", None)
    st.session_state["sent"] = False
    st.success("Cleared preview. Nothing will be sent.")

# ---- Generate summary
if gen_clicked:
    file_text = extract_text_from_upload(uploaded)
    raw_text = (pasted.strip() + "\n\n" + file_text.strip()).strip()

    if not raw_text:
        st.warning("Please upload a file or paste some text first.")
    else:
        try:
            with st.spinner("Summarizing with Gemini..."):
                summary = summarize_with_gemini(raw_text, tone=tone)

            st.session_state["raw_text"] = raw_text
            st.session_state["summary"] = summary
            st.session_state["sent"] = False
            st.success("Summary generated. Review below, then confirm to send or discard.")
        except Exception as e:
            st.error(f"Gemini error: {e}")

# ---- Preview + Confirm + Send
if "summary" in st.session_state:
    st.subheader("Preview")
    st.text(st.session_state["summary"])

    st.divider()
    st.subheader("Send Control")

    # Safety: confirm checkbox before sending
    confirm = st.checkbox("I confirm this summary is correct and I want to send it.", value=False)

    send_clicked = st.button("Send Now (Email + Telegram)", type="primary", use_container_width=True)

    if send_clicked:
        if st.session_state.get("sent"):
            st.info("Already sent. If you want to send again, please generate a new summary.")
        elif not confirm:
            st.warning("Please tick the confirmation checkbox before sending.")
        else:
            try:
                with st.spinner("Sending..."):
                    today = datetime.now(MYT).strftime("%Y-%m-%d")
                    subject = f"{subject_prefix} Daily Summary ({today})"
                    body = f"{subject}\n\n{st.session_state['summary']}"
                    send_both(subject, body)

                st.session_state["sent"] = True
                st.success("Sent successfully ✅")
            except Exception as e:
                st.error(f"Send error: {e}")
