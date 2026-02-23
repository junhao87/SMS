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

# Premium UI CSS (black/grey/white)
st.markdown("""
<style>
.block-container {padding-top: 2rem; padding-bottom: 2rem; max-width: 920px;}
h1, h2, h3 {letter-spacing: -0.02em;}
div[data-baseweb="input"], textarea {border-radius: 14px !important;}
.stButton > button {
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.14);
  background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
  color: #fff;
  padding: 0.6rem 1rem;
}
.stButton > button:hover {border: 1px solid rgba(255,255,255,0.28); transform: translateY(-1px);}
details {
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.03);
  padding: 0.25rem 0.75rem;
}
small {opacity: 0.8;}
</style>
""", unsafe_allow_html=True)

st.title("Daily Summary Bot")
st.caption("Upload PDF/DOCX/TXT or paste text → Condensed summary → Preview → Download PDF → Confirm → Send → Save History")

# View state
if "view" not in st.session_state:
    st.session_state["view"] = "main"  # main / history


# =========================
# HISTORY PAGE
# =========================
def render_history():
    st.subheader("History (Latest 50)")
    if st.button("← Back to Main", use_container_width=False):
        st.session_state["view"] = "main"
        st.rerun()

    try:
        rows = load_history(50)
        if not rows:
            st.info("No history yet.")
            return

        for r in rows:
            flags = f"email={r.get('send_email')} tg={r.get('send_telegram')} sms={r.get('send_sms')}"
            with st.expander(f"#{r['id']} | {r['created_at']} | {r['lang']} | {flags}"):
                st.write(r["title"])
                st.text(r["summary"])
                meta = r.get("meta")
                if meta:
                    st.caption(f"meta: {meta}")
    except Exception as e:
        st.error(f"History error: {e}")


# =========================
# MAIN PAGE
# =========================
if st.session_state["view"] == "history":
    render_history()
    st.stop()

# --- Input
tone = st.selectbox("Tone (minimal impact in condensed mode)", ["professional", "neutral", "friendly"], index=1)
uploaded = st.file_uploader("Upload a file (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
pasted = st.text_area("Or paste text / paragraph here", height=200)
subject_prefix = st.text_input("Subject Prefix", value="[Daily Report]")

# --- Output language
lang_mode = st.selectbox("Output language", ["Auto (detect)", "English", "中文"], index=0)
force_lang = None
if lang_mode == "English":
    force_lang = "en"
elif lang_mode == "中文":
    force_lang = "zh"

# --- Send toggles (Email / Telegram / SMS)
st.subheader("Send Options")
colA, colB, colC = st.columns(3)
with colA:
    send_email = st.toggle("Send Gmail (SendGrid)", value=True)
with colB:
    send_tg = st.toggle("Send Telegram", value=True)
with colC:
    send_sms = st.toggle("Send SMS (Twilio)", value=False)

st.caption("Tip: SMS is auto-shortened (~300 chars) to reduce multi-part SMS cost.")

# --- Buttons
col1, col2, col3 = st.columns(3)
gen_clicked = col1.button("Generate Summary", use_container_width=True)
discard_clicked = col2.button("Discard / Undo", use_container_width=True)
history_clicked = col3.button("View History", use_container_width=True)

if history_clicked:
    st.session_state["view"] = "history"
    st.rerun()

if discard_clicked:
    st.session_state.pop("summary", None)
    st.session_state.pop("lang", None)
    st.session_state.pop("meta", None)
    st.session_state.pop("sent", None)
    st.success("Cleared. Nothing will be sent.")

# --- Generate summary
if gen_clicked:
    file_text = extract_text_from_upload(uploaded)
    raw_text = (pasted.strip() + "\n\n" + file_text.strip()).strip()

    if not raw_text:
        st.warning("Please upload a file or paste some text first.")
    else:
        try:
            with st.spinner("Summarizing (auto-chunk if long)..."):
                summary, lang, meta = summarize_long_document(raw_text, force_lang=force_lang)

            st.session_state["summary"] = summary
            st.session_state["lang"] = lang
            st.session_state["meta"] = meta
            st.session_state["sent"] = False

            st.success(
                f"Summary generated. Lang={lang}. Chunks={meta.get('chunks')}, Model={meta.get('model')}"
            )
        except Exception as e:
            st.error(f"Gemini error: {e}")

# --- Preview + PDF + Send
if "summary" in st.session_state:
    st.subheader("Preview (Condensed)")
    st.text(st.session_state["summary"])

    today = datetime.now(MYT).strftime("%Y-%m-%d")
    title = f"{subject_prefix} Daily Summary ({today})"
    pdf_bytes = summary_to_pdf_bytes(title, st.session_state["summary"])

    st.download_button(
        "Download Summary as PDF",
        data=pdf_bytes,
        file_name=f"summary_{today}.pdf",
        mime="application/pdf",
        use_container_width=True
    )

    st.divider()
    st.subheader("Send Control")

    confirm = st.checkbox(
        "I confirm this condensed summary is correct and I want to send it.",
        value=False
    )

    send_clicked = st.button("Send Now", type="primary", use_container_width=True)

    if send_clicked:
        if st.session_state.get("sent"):
            st.info("Already sent. Generate a new summary to send again.")
        elif not confirm:
            st.warning("Please tick confirmation before sending.")
        elif not send_email and not send_tg and not send_sms:
            st.warning("All send options are OFF. Turn on Gmail / Telegram / SMS.")
        else:
            try:
                body = f"{title}\n\n{st.session_state['summary']}"

                with st.spinner("Sending..."):
                    send_selected(
                        title,
                        body,
                        send_email=send_email,
                        send_telegram_flag=send_tg,
                        send_sms_flag=send_sms,
                        summary_for_sms=st.session_state["summary"],
                        lang=st.session_state.get("lang", "en"),
                    )

                # Save history after successful send
                save_history(
                    title=title,
                    summary=st.session_state["summary"],
                    lang=st.session_state.get("lang", "en"),
                    send_email=send_email,
                    send_telegram=send_tg,
                    send_sms=send_sms,
                    meta=st.session_state.get("meta", {}),
                )

                st.session_state["sent"] = True
                st.success("Sent successfully ✅ and saved to history.")
            except Exception as e:
                st.error(f"Send error: {e}")
