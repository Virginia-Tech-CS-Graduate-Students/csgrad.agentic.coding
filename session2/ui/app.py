"""Run with: python -m streamlit run app.py"""

import os

import streamlit as st

from api import APIError, FORMATS, TranscriptionAPI, validate_file

st.set_page_config(page_title="Lecture to Transcript", page_icon="🎙️", layout="centered")
st.html("""
<style>
.stApp {background: #f8fafc; color: #17243b;}
.block-container {max-width: 900px; padding-top: 3rem;}
h1 {letter-spacing: -0.045em; font-weight: 750 !important;}
.eyebrow {font-size: .76rem; letter-spacing: .16em; font-weight: 700; color: #5267b0;}
.soundmark {float: right; display: flex; align-items: center; justify-content: center;
 gap: 5px; width: 64px; height: 64px; border-radius: 20px; background: #e7eaff;
 box-shadow: 0 6px 20px #5668c21a; transform: rotate(5deg);}
.soundmark i {width: 5px; border-radius: 5px; background: #5967d8; height: 17px;}
.soundmark i:nth-child(2), .soundmark i:nth-child(4) {height: 29px;}
.soundmark i:nth-child(3) {height: 41px;}
div[data-testid="stVerticalBlockBorderWrapper"] {background: white; border-radius: 18px;}
button:focus-visible, input:focus-visible, textarea:focus-visible {outline: 3px solid #5967d8 !important; outline-offset: 3px;}
@media (max-width: 600px) {.block-container {padding-top: 1.5rem;} .soundmark {width: 48px; height: 48px;}}
</style>
<div class="soundmark" aria-label="Audio waveform" role="img"><i></i><i></i><i></i><i></i><i></i></div>
<p class="eyebrow">LESS REPLAYING. MORE LEARNING.</p>
""")
st.title("Lecture to Transcript")
st.write("Turn your recordings into text you can read, search, and keep.")
st.caption("01  Upload a recording     /     02  Generate your transcript     /     03  Download & keep")
st.write("")

for key, default in {"job": None, "poll_error": None, "upload_error": None,
                     "download": None, "generation": 0, "submitting": False}.items():
    st.session_state.setdefault(key, default)
s = st.session_state
api = TranscriptionAPI(os.environ.get("TRANSCRIPT_API_URL", "http://localhost:8000"))


def reset():
    for key in ("job", "poll_error", "upload_error", "download"):
        s[key] = None
    s.submitting = False
    s.generation += 1


with st.container(border=True):
    st.subheader("Your recording")
    st.caption("Audio or video, one file at a time. For video, we transcribe the audio.")
    file = st.file_uploader(
        "Upload audio or video", type=FORMATS, max_upload_size=500,
        key=f"recording_{s.generation}", disabled=s.job is not None or s.submitting,
        help="MP3, WAV, M4A, MP4, or WebM · Up to 500 MiB",
    )
    validation = validate_file(file.name, file.size) if file else None
    if file:
        st.text(f"{file.name} · {file.size / (1024 * 1024):.2f} MiB")
    if validation:
        st.error(validation)
    if st.button("Generate transcript", type="primary", icon=":material/auto_awesome:",
                 disabled=file is None or bool(validation) or s.job is not None or s.submitting,
                 width="stretch"):
        s.submitting = True
        s.upload_error = None
        st.rerun()
    if s.submitting:
        try:
            with st.spinner("Uploading your recording…"):
                s.job = api.upload(file)
        except APIError as exc:
            s.upload_error = str(exc)
        finally:
            s.submitting = False
        st.rerun()
    if s.upload_error:
        st.error(s.upload_error)
    if s.job or s.upload_error:
        st.button("Transcribe another file", on_click=reset)


@st.fragment(run_every=2 if s.job and s.job["status"] in {"queued", "processing"} and not s.poll_error else None)
def result():
    job = s.job
    with st.container(border=True):
        st.subheader("Your transcript")
        if not job:
            st.markdown("**A little less note-taking.**")
            st.caption("Upload a recording and select Generate transcript. Your text will appear here when it’s ready.")
            return
        if job["status"] in {"queued", "processing"} and not s.poll_error:
            try:
                s.job = job = api.status(job["id"])
                if job["status"] in {"completed", "failed"}:
                    st.rerun()
            except APIError as exc:
                s.poll_error = str(exc)
                st.rerun()
        if s.poll_error:
            st.error(s.poll_error)
            st.caption("Your job is saved in this session. Retry the status check without uploading again.")
            if st.button("Retry status check"):
                s.poll_error = None
                st.rerun()
            return
        if job["status"] in {"queued", "processing"}:
            label = "Queued — waiting to start" if job["status"] == "queued" else "Transcribing your recording…"
            st.status(label, state="running")
            st.caption("You can leave this page open. Longer recordings may take a few minutes.")
        elif job["status"] == "failed":
            st.error(job["error"]["message"])
            st.caption("Choose Transcribe another file above to try again.")
        else:
            text = job["transcript"]["text"]
            st.success("Completed — your transcript is ready.")
            st.caption(f"{len(text.split()):,} words")
            st.code(text, language=None, wrap_lines=True, height=340)
            if s.download is None:
                try:
                    s.download = api.download(job["id"])
                except APIError as exc:
                    st.error(str(exc))
                    if st.button("Retry download"):
                        st.rerun()
            if s.download is not None:
                st.download_button("Download transcript (.txt)", data=s.download,
                                   file_name=f"transcript-{job['id']}.txt", mime="text/plain",
                                   icon=":material/download:", type="primary", on_click="ignore")


result()
st.caption("Made for lectures, conversations, and the ideas worth keeping.")
