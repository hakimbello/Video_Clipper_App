"""
Video Clipper — Streamlit Web App
Run with: streamlit run app.py

Client-ready features in this version:
- Platform + content type targeting
- Custom hook phrases per client
- Aspect ratio selection (vertical / square / horizontal)
- Clip preview and approval before export
- SRT subtitle file exported alongside every clip
- All downloads stay available until next run
"""

import streamlit as st
import os
import tempfile
import subprocess
import shutil
from pathlib import Path
from clipper.transcriber import transcribe_video
from clipper.scorer import get_top_clips, calculate_target_clip_count, HOOK_PHRASES
from clipper.exporter import export_clips

st.set_page_config(
    page_title="Video Clipper",
    page_icon="🎬",
    layout="centered"
)

st.title("🎬 Video Clipper")
st.caption("Upload a long-form video → review the best moments → export with captions.")

# Permanent output folder next to app.py
OUTPUT_DIR = Path(__file__).parent / "clips_output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Upload ─────────────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader(
    "Upload your video",
    type=["mp4", "mov", "mkv", "avi"],
    help="Supported: MP4, MOV, MKV, AVI"
)

# ── Settings ───────────────────────────────────────────────────────────────────
st.subheader("⚙️ Settings")

col1, col2 = st.columns(2)
with col1:
    platform = st.selectbox(
        "Target platform",
        options=["general", "tiktok", "shorts", "reels", "facebook"],
        index=0,
        help="Sets ideal clip length targets per platform."
    )
with col2:
    content_type = st.selectbox(
        "Content type",
        options=["general", "podcast", "educational", "commentary",
                 "storytelling", "entertainment", "promo"],
        index=0,
        help="Helps find complete moments that fit your content style."
    )

col3, col4 = st.columns(2)
with col3:
    aspect_ratio = st.selectbox(
        "Aspect ratio",
        options=["vertical", "square", "horizontal"],
        index=0,
        help=(
            "Vertical (9:16) = TikTok, Reels, Shorts. "
            "Square (1:1) = LinkedIn, Facebook feed. "
            "Horizontal (16:9) = YouTube standard."
        )
    )
with col4:
    export_srt = st.checkbox(
        "Export SRT caption file",
        value=True,
        help=(
            "Saves a .srt subtitle file alongside each clip. "
            "Upload to TikTok, YouTube, or Instagram for custom caption styling."
        )
    )

mode = st.radio(
    "Clip selection mode",
    options=["conservative", "balanced", "aggressive"],
    index=1,
    horizontal=True,
    help=(
        "Conservative = fewer clips, strongest complete moments only. "
        "Balanced = smart default. "
        "Aggressive = more clips, slightly lower bar."
    )
)

use_manual_count = st.checkbox("Set clip count manually", value=False)
manual_top_n = None
if use_manual_count:
    manual_top_n = st.slider("How many clips?", min_value=1, max_value=25, value=5)

max_clips = st.slider(
    "Maximum clip cap",
    min_value=1, max_value=50, value=20,
    help="Hard ceiling on total clips produced."
)

# ── Custom hook phrases ────────────────────────────────────────────────────────
with st.expander("🎯 Custom hook phrases (optional — for client-specific content)"):
    st.caption(
        "Add words or phrases that signal a strong moment in this client's content. "
        "One per line. These are added on top of the built-in list."
    )
    custom_hooks_input = st.text_area(
        "Custom hook phrases",
        placeholder="the real reason\nnobody tells you this\nhere is what changed everything\nyour money\nhow to",
        label_visibility="collapsed",
    )
    if custom_hooks_input.strip():
        custom_hooks = [line.strip().lower() for line in custom_hooks_input.splitlines() if line.strip()]
        st.success(f"{len(custom_hooks)} custom phrases added: {', '.join(custom_hooks[:5])}{'...' if len(custom_hooks) > 5 else ''}")
    else:
        custom_hooks = []

st.info(
    "📏 **Minimum clip length is 15 seconds.** "
    "Only clips containing a complete thought — hook, point, payoff — are selected."
)

# ── Step 1: Find clips (transcribe + score) ───────────────────────────────────
if st.button("🔍 Find Best Clips", disabled=uploaded_file is None):

    # Clear any previous session state
    for key in ["clips", "ready", "approved", "video_bytes", "video_name"]:
        st.session_state.pop(key, None)

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Store video bytes in session so we can export later after approval
    st.session_state["video_bytes"] = uploaded_file.read()
    st.session_state["video_name"]  = uploaded_file.name

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, uploaded_file.name)
        with open(video_path, "wb") as f:
            f.write(st.session_state["video_bytes"])

        video_duration = 300.0
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 video_path],
                capture_output=True, text=True, check=True
            )
            video_duration = float(probe.stdout.strip())
        except Exception:
            st.warning("Could not detect video duration — using 5 minute estimate.")

        duration_str = f"{int(video_duration // 60)}m {int(video_duration % 60)}s"

        with st.spinner("Transcribing audio... (roughly the same time as your video length)"):
            result = transcribe_video(video_path)

        # Inject custom hook phrases into the scorer for this run
        if custom_hooks:
            import clipper.scorer as scorer_module
            original_hooks = list(scorer_module.HOOK_PHRASES)
            scorer_module.HOOK_PHRASES = original_hooks + custom_hooks
        
        auto_count = calculate_target_clip_count(
            video_duration_seconds=video_duration,
            segments=result["segments"],
            mode=mode,
            max_clips=max_clips,
        )

        st.info(
            f"Video: **{duration_str}** — targeting **{auto_count} clips** "
            f"({mode} mode · {platform} · {content_type})"
        )

        with st.spinner("Scoring and selecting best moments..."):
            top_clips = get_top_clips(
                whisper_result=result,
                video_duration=video_duration,
                mode=mode,
                top_n=manual_top_n,
                max_clips=max_clips,
                platform=platform,
                content_type=content_type,
            )

        # Restore hook phrases to original
        if custom_hooks:
            scorer_module.HOOK_PHRASES = original_hooks

        if not top_clips:
            st.error(
                "No clips passed the quality filter. "
                "Try **aggressive** mode, or check your video has clear speech."
            )
            st.stop()

        # Store everything in session for the approval + export step
        st.session_state["clips"]          = top_clips
        st.session_state["whisper_result"] = result
        st.session_state["video_duration"] = video_duration
        st.session_state["ready"]          = True

        st.success(f"Found **{len(top_clips)} clips**. Review them below and approve the ones you want exported.")


# ── Step 2: Preview and approve clips ────────────────────────────────────────
if st.session_state.get("ready") and not st.session_state.get("exported"):
    top_clips = st.session_state["clips"]

    st.subheader("👀 Review & Approve Clips")
    st.caption(
        "Check the clips you want to export. Uncheck any you don't want. "
        "Then click Export below."
    )

    approved_flags = []
    for i, clip in enumerate(top_clips):
        duration_val = clip.get("duration", clip["end"] - clip["start"])
        col_check, col_info = st.columns([0.08, 0.92])
        with col_check:
            checked = st.checkbox("", value=True, key=f"approve_{i}")
            approved_flags.append(checked)
        with col_info:
            with st.expander(
                f"Clip {i+1}  ·  {duration_val:.0f}s  ·  Score: {clip['score']}  ·  "
                f"{clip['start']:.1f}s → {clip['end']:.1f}s",
                expanded=False
            ):
                st.write(clip["text"])
                st.caption(
                    f"Pause before: {clip.get('gap_before', 0):.1f}s  |  "
                    f"Pause after: {clip.get('gap_after', 0):.1f}s"
                )

    approved_count = sum(approved_flags)
    st.write(f"**{approved_count} of {len(top_clips)} clips selected for export.**")

    if st.button(f"✂️ Export {approved_count} Approved Clips", disabled=approved_count == 0):

        approved_clips = [clip for clip, flag in zip(top_clips, approved_flags) if flag]
        st.session_state["approved_clips"] = approved_clips

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, st.session_state["video_name"])
            with open(video_path, "wb") as f:
                f.write(st.session_state["video_bytes"])

            progress = st.progress(0)
            with st.spinner(f"Exporting {len(approved_clips)} clips with captions..."):
                export_clips(
                    video_path=video_path,
                    clips=approved_clips,
                    output_dir=str(OUTPUT_DIR),
                    whisper_result=st.session_state["whisper_result"],
                    aspect_ratio=aspect_ratio,
                    export_srt=export_srt,
                    progress_callback=lambda i, total: progress.progress(i / total),
                )
            progress.progress(1.0)

        st.session_state["exported"] = True
        st.session_state["final_clips"] = approved_clips
        st.rerun()


# ── Step 3: Download finished clips ───────────────────────────────────────────
if st.session_state.get("exported"):
    final_clips = st.session_state.get("final_clips", [])

    st.subheader("📥 Download Your Clips")
    st.caption("All clips and SRT files are ready. Download in any order.")

    srt_label = " + SRT" if export_srt else ""
    any_found = False

    for i, clip in enumerate(final_clips):
        final_path = OUTPUT_DIR / f"clip_{i+1}_final.mp4"
        srt_path   = OUTPUT_DIR / f"clip_{i+1}_captions.srt"
        duration_val = clip.get("duration", clip["end"] - clip["start"])
        clip_text    = clip["text"][:55] + "..." if len(clip["text"]) > 55 else clip["text"]

        if final_path.exists():
            any_found = True
            col_vid, col_srt = st.columns([0.7, 0.3])

            with col_vid:
                with open(final_path, "rb") as f:
                    st.download_button(
                        label=f"⬇️ Clip {i+1} [{duration_val:.0f}s] — \"{clip_text}\"",
                        data=f.read(),
                        file_name=f"clip_{i+1}.mp4",
                        mime="video/mp4",
                        key=f"dl_mp4_{i}",
                    )

            with col_srt:
                if export_srt and srt_path.exists():
                    with open(srt_path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ SRT {i+1}",
                            data=f.read(),
                            file_name=f"clip_{i+1}_captions.srt",
                            mime="text/plain",
                            key=f"dl_srt_{i}",
                        )
        else:
            st.warning(f"Clip {i+1} did not export — check ffmpeg is installed.")

    if not any_found:
        st.error("No clips were exported. Check that ffmpeg is installed and working.")

    st.divider()
    if st.button("🔄 Start Over with a New Video"):
        for key in ["clips", "ready", "approved", "exported", "final_clips",
                    "video_bytes", "video_name", "whisper_result", "video_duration"]:
            st.session_state.pop(key, None)
        st.rerun()
