import streamlit as st
from datetime import datetime, timezone, timedelta
from send_core import extract_text_from_upload, summarize_with_gemini, send_both, list_gemini_models

MYT = timezone(timedelta(hours=8))

st.set_page_config(page_title="Daily Summary Bot", layout="centered")
st.title("Daily Summary Bot")

# Debug section
if st.button("Debug: List Available Gemini Models"):
    try:
        st.text(list_gemini_models())
    except Exception as e:
        st.error(f"Debug error: {e}")

tone = st.selectbox("Tone", ["professional", "neutral", "friendly"], index=0)
uploaded = st.file_uploader("Upload a file (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
pasted = st.text_area("Or paste text / paragraph here", height=220)
subject_prefix = st.text_input("Email Subject Prefix", value="[Daily Report]")

col1, col2 = st.columns(2)
with col1:
    gen_clicked = st.button("Generate Summary")
with col2:
    discard_clicked = st.button("Discard / Undo")

if discard_clicked:
    st.session_state.clear()
    st.success("Cleared.")

if gen_clicked:
    file_text = extract_text_from_upload(uploaded)
    raw_text = (pasted.strip() + "\n\n" + file_text.strip()).strip()

    if not raw_text:
        st.warning("Please upload or paste content.")
    else:
        try:
            summary = summarize_with_gemini(raw_text, tone=tone)
            st.session_state["summary"] = summary
        except Exception as e:
            st.error(f"Gemini error: {e}")

if "summary" in st.session_state:
    st.subheader("Preview")
    st.text(st.session_state["summary"])

    confirm = st.checkbox("Confirm before sending")

    if st.button("Send Now"):
        if not confirm:
            st.warning("Please confirm first.")
        else:
            today = datetime.now(MYT).strftime("%Y-%m-%d")
            subject = f"{subject_prefix} Daily Summary ({today})"
            body = f"{subject}\n\n{st.session_state['summary']}"
            try:
                send_both(subject, body)
                st.success("Sent successfully.")
            except Exception as e:
                st.error(f"Send error: {e}")
