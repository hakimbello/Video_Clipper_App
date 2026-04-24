"""
Video Clipper — Streamlit Web App
Run with: streamlit run app.py

Features:
- Platform + content type targeting
- Custom hook phrases per client
- Aspect ratio selection with smart face-detection reframing
- Clip preview and approval before export
- SRT subtitle file alongside every clip
- Feedback loop: thumbs up/down teaches the app your preferences
- Batch input: upload multiple files OR paste a YouTube URL
- Job history: see all previous exports in one place
"""

import streamlit as st
import os
import tempfile
import subprocess
import shutil
import json
from pathlib import Path
from datetime import datetime
from clipper.transcriber import transcribe_video
from clipper.scorer import get_top_clips, calculate_target_clip_count, HOOK_PHRASES
from clipper.exporter import export_clips, OPENCV_AVAILABLE
from clipper.text_modes import TEXT_MODES
from clipper.feedback import record_feedback, get_feedback_stats, reset_feedback

st.set_page_config(
    page_title="Video Clipper",
    page_icon="🎬",
    layout="centered"
)

OUTPUT_DIR   = Path(__file__).parent / "clips_output"
HISTORY_FILE = Path(__file__).parent / "job_history.json"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Job history helpers ────────────────────────────────────────────────────────

def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(entry: dict):
    history = load_history()
    history.insert(0, entry)
    history = history[:50]  # keep last 50 jobs
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ── Navigation tabs ────────────────────────────────────────────────────────────

tab_clip, tab_history, tab_feedback = st.tabs([
    "✂️ Clip Videos",
    "📁 Job History",
    "🧠 Feedback & Learning"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CLIP VIDEOS
# ══════════════════════════════════════════════════════════════════════════════

with tab_clip:
    st.title("🎬 Video Clipper")
    st.caption("Upload a video or paste a YouTube URL → review moments → export with captions.")

    # ── Input method ──────────────────────────────────────────────────────────
    input_method = st.radio(
        "How do you want to add your video?",
        options=["Upload a file", "YouTube URL"],
        horizontal=True,
    )

    uploaded_files = []
    youtube_url    = ""

    if input_method == "Upload a file":
        uploaded_files = st.file_uploader(
            "Upload one or more videos",
            type=["mp4", "mov", "mkv", "avi"],
            accept_multiple_files=True,
            help="Upload multiple files to batch process them one after another."
        )
        if uploaded_files:
            st.caption(f"{len(uploaded_files)} file(s) ready: {', '.join(f.name for f in uploaded_files)}")
    else:
        youtube_url = st.text_input(
            "Paste YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
            help="Requires yt-dlp to be installed. Run: pip install yt-dlp"
        )

    # ── Settings ──────────────────────────────────────────────────────────────
    st.subheader("⚙️ Settings")

    col1, col2 = st.columns(2)
    with col1:
        platform = st.selectbox(
            "Target platform",
            options=["general", "tiktok", "shorts", "reels", "facebook"],
            index=0,
        )
    with col2:
        content_type = st.selectbox(
            "Content type",
            options=["general", "podcast", "educational", "commentary",
                     "storytelling", "entertainment", "promo"],
            index=0,
        )

    col3, col4 = st.columns(2)
    with col3:
        aspect_ratio = st.selectbox(
            "Aspect ratio",
            options=["vertical", "square", "horizontal"],
            index=0,
            help="Vertical (9:16) = TikTok/Reels/Shorts. Square (1:1) = LinkedIn. Horizontal (16:9) = YouTube."
        )
    with col4:
        export_srt = st.checkbox("Export SRT caption file", value=True)

    col5, col6 = st.columns(2)
    with col5:
        smart_reframe = st.checkbox(
            "Smart face reframing",
            value=OPENCV_AVAILABLE,
            disabled=not OPENCV_AVAILABLE,
            help=(
                "Detects where the speaker's face is and keeps them in frame. "
                "Requires opencv-python. Install with: pip install opencv-python"
                if not OPENCV_AVAILABLE else
                "Detects face position and crops around it instead of center-cropping."
            )
        )
    with col6:
        mode = st.selectbox(
            "Clip selection mode",
            options=["conservative", "balanced", "aggressive"],
            index=1,
        )

    # ── Text mode selector ────────────────────────────────────────────────────
    mode_keys    = list(TEXT_MODES.keys())
    mode_labels  = [TEXT_MODES[k]["label"] for k in mode_keys]
    mode_descs   = [TEXT_MODES[k]["description"] for k in mode_keys]

    selected_label = st.selectbox(
        "📝 Caption style",
        options=mode_labels,
        index=0,
        help="Choose how text appears on your exported clips."
    )
    selected_mode_idx = mode_labels.index(selected_label)
    text_mode = mode_keys[selected_mode_idx]
    st.caption(f"ℹ️ {mode_descs[selected_mode_idx]}")

    use_manual_count = st.checkbox("Set clip count manually", value=False)
    manual_top_n = None
    if use_manual_count:
        manual_top_n = st.slider("How many clips?", min_value=1, max_value=25, value=5)

    max_clips = st.slider("Maximum clip cap", min_value=1, max_value=50, value=20)

    with st.expander("🎯 Custom hook phrases (optional)"):
        st.caption("Words or phrases that signal a strong moment. One per line.")
        custom_hooks_input = st.text_area(
            "Custom hook phrases",
            placeholder="the real reason\nnobody tells you this\nyour money",
            label_visibility="collapsed",
        )
        custom_hooks = [
            line.strip().lower()
            for line in custom_hooks_input.splitlines()
            if line.strip()
        ] if custom_hooks_input.strip() else []
        if custom_hooks:
            st.success(f"{len(custom_hooks)} custom phrases added.")

    st.info("📏 **Minimum clip length is 15 seconds.** Only complete thoughts are selected.")

    # ── Determine if ready to process ─────────────────────────────────────────
    has_input = bool(uploaded_files) or bool(youtube_url.strip())

    if st.button("🔍 Find Best Clips", disabled=not has_input):

        for key in ["clips", "ready", "exported", "video_bytes", "video_name"]:
            st.session_state.pop(key, None)

        if OUTPUT_DIR.exists():
            shutil.rmtree(OUTPUT_DIR)
        OUTPUT_DIR.mkdir(exist_ok=True)

        # ── Handle YouTube URL ─────────────────────────────────────────────
        if input_method == "YouTube URL" and youtube_url.strip():
            try:
                with st.spinner("Downloading video from YouTube..."):
                    yt_out = OUTPUT_DIR / "yt_download.mp4"
                    result = subprocess.run(
                        ["yt-dlp", "-f", "mp4", "-o", str(yt_out), youtube_url.strip()],
                        capture_output=True, text=True
                    )
                    if result.returncode != 0:
                        st.error(f"YouTube download failed: {result.stderr}")
                        st.stop()
                    uploaded_files = [yt_out]
                    st.success("YouTube video downloaded.")
            except FileNotFoundError:
                st.error(
                    "yt-dlp is not installed. Run this in PowerShell to install it:\n\n"
                    "`pip install yt-dlp`\n\nThen restart the app."
                )
                st.stop()

        # ── Process each file ──────────────────────────────────────────────
        all_clips = []

        for file_item in uploaded_files:
            # Handle both UploadedFile objects and Path objects (from yt-dlp)
            is_path = isinstance(file_item, Path)
            file_name = file_item.name if is_path else file_item.name

            st.markdown(f"**Processing: {file_name}**")

            with tempfile.TemporaryDirectory() as tmpdir:
                video_path = str(file_item) if is_path else os.path.join(tmpdir, file_name)

                if not is_path:
                    file_bytes = file_item.read()
                    with open(video_path, "wb") as f:
                        f.write(file_bytes)
                    st.session_state["video_bytes"] = file_bytes
                else:
                    with open(video_path, "rb") as f:
                        st.session_state["video_bytes"] = f.read()

                st.session_state["video_name"] = file_name

                # Get duration
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
                    st.warning("Could not detect duration — using 5 min estimate.")

                duration_str = f"{int(video_duration // 60)}m {int(video_duration % 60)}s"

                with st.spinner(f"Transcribing {file_name}..."):
                    result = transcribe_video(video_path)

                # Inject custom hooks
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
                st.info(f"{file_name}: **{duration_str}** — targeting **{auto_count} clips**")

                with st.spinner("Scoring moments..."):
                    top_clips = get_top_clips(
                        whisper_result=result,
                        video_duration=video_duration,
                        mode=mode,
                        top_n=manual_top_n,
                        max_clips=max_clips,
                        platform=platform,
                        content_type=content_type,
                    )

                if custom_hooks:
                    scorer_module.HOOK_PHRASES = original_hooks

                for clip in top_clips:
                    clip["source_file"] = file_name

                all_clips.extend(top_clips)
                st.session_state["whisper_result"] = result
                st.session_state["video_duration"] = video_duration

        if not all_clips:
            st.error("No clips passed the quality filter. Try aggressive mode.")
            st.stop()

        st.session_state["clips"] = all_clips
        st.session_state["ready"] = True
        st.success(f"Found **{len(all_clips)} clips** across all videos. Review below.")

    # ── Step 2: Review and approve ────────────────────────────────────────────
    if st.session_state.get("ready") and not st.session_state.get("exported"):
        top_clips = st.session_state["clips"]

        st.subheader("👀 Review & Approve Clips")
        st.caption("Check the clips you want to export. Uncheck any you want to skip.")

        # ── Clip cards — Opus Clip inspired ───────────────────────────────────
        st.markdown("""
        <style>
        .clip-card {
            border: 1px solid #e0e0e0;
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 14px;
            background: #fafafa;
        }
        .clip-card-selected {
            border: 2px solid #1D9E75;
            background: #f0faf6;
        }
        .score-badge {
            display: inline-block;
            background: #1D9E75;
            color: white;
            font-weight: 700;
            font-size: 18px;
            border-radius: 50%;
            width: 48px;
            height: 48px;
            line-height: 48px;
            text-align: center;
        }
        .score-badge-low {
            background: #e0e0e0;
            color: #666;
        }
        .clip-title {
            font-size: 15px;
            font-weight: 600;
            color: #1a1a1a;
            margin: 0;
        }
        .clip-meta {
            font-size: 12px;
            color: #888;
            margin-top: 3px;
        }
        .clip-transcript {
            font-size: 13px;
            color: #444;
            margin-top: 10px;
            line-height: 1.6;
            border-left: 3px solid #1D9E75;
            padding-left: 10px;
        }
        .duration-pill {
            display: inline-block;
            background: #EEF2FF;
            color: #3730A3;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 9px;
            border-radius: 99px;
        }
        .platform-pill {
            display: inline-block;
            background: #FEF3C7;
            color: #92400E;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 9px;
            border-radius: 99px;
            margin-left: 5px;
        }
        </style>
        """, unsafe_allow_html=True)

        import re as _re

        approved_flags = []

        # Initialize edited transcripts in session state if not already there
        # This stores any edits the user makes so they survive Streamlit reruns
        if "edited_transcripts" not in st.session_state:
            st.session_state["edited_transcripts"] = {
                i: clip.get("text", "") for i, clip in enumerate(top_clips)
            }

        for i, clip in enumerate(top_clips):
            duration_val = clip.get("duration", clip["end"] - clip["start"])
            source       = clip.get("source_file", "")
            score        = clip.get("score", 0)
            text         = clip.get("text", "")

            # Auto-title from first sentence
            sentences      = _re.split(r"(?<=[.!?])\s+", text.strip())
            first_sentence = sentences[0][:55] if sentences else text[:55]
            clip_title     = first_sentence.rstrip(".,") if first_sentence else f"Clip {i+1}"

            score_display = min(99, max(1, score + 50))
            score_class   = "score-badge" if score_display >= 55 else "score-badge score-badge-low"
            duration_str  = f"{duration_val:.0f}s"

            col_check, col_card = st.columns([0.07, 0.93])

            with col_check:
                st.markdown("<div style='margin-top:18px'></div>", unsafe_allow_html=True)
                checked = st.checkbox("", value=True, key=f"approve_{i}")
                approved_flags.append(checked)

            with col_card:
                card_class   = "clip-card clip-card-selected" if checked else "clip-card"
                source_pill  = f'<span class="platform-pill">📁 {source}</span>' if source else ""

                st.markdown(f"""
                <div class="{card_class}">
                    <div style="display:flex; align-items:flex-start; gap:14px;">
                        <div class="{score_class}">{score_display}</div>
                        <div style="flex:1">
                            <p class="clip-title">#{i+1} {clip_title}</p>
                            <p class="clip-meta">
                                <span class="duration-pill">⏱ {duration_str}</span>
                                {source_pill}
                                <span style="margin-left:8px; color:#aaa">{clip["start"]:.1f}s → {clip["end"]:.1f}s</span>
                            </p>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # ── Transcript editor ──────────────────────────────────────
                # Shown in an expander so the card stays clean by default.
                # When the user edits and clicks Save, the new text is stored
                # in session state and will be used when burning captions.
                with st.expander("✏️ Edit transcript for this clip", expanded=False):
                    st.caption(
                        "Edit the text below to fix transcription errors or rewrite it. "
                        "This is what gets burned into the captions."
                    )
                    edited = st.text_area(
                        label="Transcript",
                        value=st.session_state["edited_transcripts"].get(i, text),
                        height=120,
                        key=f"transcript_edit_{i}",
                        label_visibility="collapsed",
                    )

                    col_save, col_reset = st.columns([0.3, 0.7])
                    with col_save:
                        if st.button("💾 Save edit", key=f"save_edit_{i}",
                                     use_container_width=True):
                            st.session_state["edited_transcripts"][i] = edited
                            # Update the clip text in session state immediately
                            st.session_state["clips"][i]["text"] = edited
                            st.toast(f"Clip {i+1} transcript updated!")

                    with col_reset:
                        if st.button("↩️ Reset to original", key=f"reset_edit_{i}",
                                     use_container_width=True):
                            st.session_state["edited_transcripts"][i] = text
                            st.session_state["clips"][i]["text"] = text
                            st.toast(f"Clip {i+1} reset to original transcript.")

                    # Show character count so user knows if text is too long
                    current_text = st.session_state["edited_transcripts"].get(i, text)
                    was_edited   = current_text.strip() != text.strip()
                    if was_edited:
                        st.success("✓ This transcript has been edited.")
                    st.caption(f"{len(current_text)} characters · "
                               f"{len(current_text.split())} words")

        approved_count = sum(approved_flags)
        st.markdown(f"**{approved_count} of {len(top_clips)} clips selected for export.**")

        if st.button(f"✂️ Export {approved_count} Clips", disabled=approved_count == 0):
            approved_clips = [c for c, f in zip(top_clips, approved_flags) if f]
            st.session_state["approved_clips"] = approved_clips

            with tempfile.TemporaryDirectory() as tmpdir:
                video_path = os.path.join(tmpdir, st.session_state["video_name"])
                with open(video_path, "wb") as f:
                    f.write(st.session_state["video_bytes"])

                progress = st.progress(0)
                with st.spinner(f"Exporting {len(approved_clips)} clips..."):
                    export_clips(
                        video_path=video_path,
                        clips=approved_clips,
                        output_dir=str(OUTPUT_DIR),
                        whisper_result=st.session_state["whisper_result"],
                        aspect_ratio=aspect_ratio,
                        export_srt=export_srt,
                        smart_reframe=smart_reframe,
                        text_mode=text_mode,
                        progress_callback=lambda i, total: progress.progress(i / total),
                    )
                progress.progress(1.0)

            # Save to job history
            save_history({
                "date":       datetime.now().strftime("%Y-%m-%d %H:%M"),
                "file":       st.session_state["video_name"],
                "clips":      len(approved_clips),
                "platform":   platform,
                "mode":       mode,
                "text_mode":  text_mode,
                "duration":   f"{int(st.session_state['video_duration'] // 60)}m",
            })

            st.session_state["exported"]    = True
            st.session_state["final_clips"] = approved_clips
            st.rerun()

    # ── Step 3: Preview + Download + Feedback ────────────────────────────────
    if st.session_state.get("exported"):
        final_clips = st.session_state.get("final_clips", [])

        st.subheader("🎬 Your Clips")
        st.caption("Watch each clip, download what you want, and rate them to improve future selections.")

        any_found = False
        for i, clip in enumerate(final_clips):
            final_path   = OUTPUT_DIR / f"clip_{i+1}_final.mp4"
            srt_path     = OUTPUT_DIR / f"clip_{i+1}_captions.srt"
            duration_val = clip.get("duration", clip["end"] - clip["start"])
            clip_text    = clip["text"][:80] + "..." if len(clip["text"]) > 80 else clip["text"]
            score        = clip.get("score", 0)
            score_display = min(99, max(1, score + 50))

            # Auto-title from first sentence
            import re as _re
            sentences    = _re.split(r"(?<=[.!?])\s+", clip.get("text","").strip())
            clip_title   = (sentences[0][:50].rstrip(".,") if sentences else f"Clip {i+1}")

            if final_path.exists():
                any_found = True

                # ── Card wrapper ───────────────────────────────────────────
                st.markdown(f"""
                <div style="border:1px solid #e0e0e0; border-radius:14px;
                            padding:18px 20px; margin-bottom:20px; background:#fafafa;">
                    <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">
                        <div style="background:#1D9E75; color:white; font-weight:700;
                                    font-size:16px; border-radius:50%; width:42px; height:42px;
                                    line-height:42px; text-align:center; flex-shrink:0;">
                            {score_display}
                        </div>
                        <div>
                            <div style="font-size:15px; font-weight:600; color:#1a1a1a;">
                                #{i+1} {clip_title}
                            </div>
                            <div style="font-size:12px; color:#888; margin-top:2px;">
                                ⏱ {duration_val:.0f}s &nbsp;·&nbsp;
                                {clip["start"]:.1f}s → {clip["end"]:.1f}s
                            </div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # ── Video preview + transcript side by side ────────────────
                col_preview, col_info = st.columns([0.42, 0.58])

                with col_preview:
                    # Inline video player — plays right in the app
                    with open(final_path, "rb") as vf:
                        video_bytes = vf.read()
                    st.video(video_bytes)

                with col_info:
                    st.markdown("**Transcript**")
                    st.markdown(
                        f"<div style='font-size:13px; color:#444; line-height:1.7; "
                        f"border-left:3px solid #1D9E75; padding-left:10px; "
                        f"margin-bottom:14px;'>{clip_text}</div>",
                        unsafe_allow_html=True
                    )

                    # Download buttons
                    with open(final_path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download Clip {i+1} (.mp4)",
                            data=f.read(),
                            file_name=f"clip_{i+1}.mp4",
                            mime="video/mp4",
                            key=f"dl_mp4_{i}",
                            use_container_width=True,
                        )

                    if export_srt and srt_path.exists():
                        with open(srt_path, "rb") as f:
                            st.download_button(
                                label="⬇️ Download SRT captions",
                                data=f.read(),
                                file_name=f"clip_{i+1}.srt",
                                mime="text/plain",
                                key=f"dl_srt_{i}",
                                use_container_width=True,
                            )

                    # Feedback buttons
                    st.markdown("**Was this a good clip?**")
                    fb_col1, fb_col2 = st.columns(2)
                    with fb_col1:
                        if st.button("👍 Great clip", key=f"like_{i}",
                                     use_container_width=True):
                            record_feedback(clip["text"], liked=True)
                            st.toast("Noted — will prioritize similar moments!")
                    with fb_col2:
                        if st.button("👎 Not good", key=f"dislike_{i}",
                                     use_container_width=True):
                            record_feedback(clip["text"], liked=False)
                            st.toast("Noted — will avoid similar moments.")

                st.divider()

            else:
                st.warning(f"Clip {i+1} did not export — check ffmpeg is installed.")

        if not any_found:
            st.error("No clips exported. Check ffmpeg is installed.")

        if st.button("🔄 Start Over with a New Video"):
            for key in ["clips", "ready", "exported", "final_clips",
                        "video_bytes", "video_name", "whisper_result", "video_duration"]:
                st.session_state.pop(key, None)
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — JOB HISTORY
# ══════════════════════════════════════════════════════════════════════════════

with tab_history:
    st.title("📁 Job History")
    st.caption("A record of every video you have processed.")

    history = load_history()

    if not history:
        st.info("No jobs yet. Process your first video in the Clip Videos tab.")
    else:
        for job in history:
            with st.container():
                col_a, col_b, col_c, col_d = st.columns([0.35, 0.25, 0.2, 0.2])
                with col_a:
                    st.write(f"**{job.get('file', 'Unknown')}**")
                    st.caption(job.get("date", ""))
                with col_b:
                    st.metric("Clips exported", job.get("clips", 0))
                with col_c:
                    st.write(f"**Platform:** {job.get('platform', '—')}")
                    st.write(f"**Mode:** {job.get('mode', '—')}")
                with col_d:
                    st.write(f"**Duration:** {job.get('duration', '—')}")
                st.divider()

        if st.button("🗑️ Clear History"):
            HISTORY_FILE.unlink(missing_ok=True)
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — FEEDBACK & LEARNING
# ══════════════════════════════════════════════════════════════════════════════

with tab_feedback:
    st.title("🧠 Feedback & Learning")
    st.caption(
        "Every thumbs up and thumbs down you give on clips is saved here. "
        "The app uses this to score future clips — words from clips you liked "
        "get a boost, words from clips you disliked get penalised."
    )

    stats = get_feedback_stats()

    if stats["clips_rated"] == 0:
        st.info(
            "No feedback recorded yet. After exporting clips, use the 👍 and 👎 "
            "buttons next to each clip to start teaching the app your preferences."
        )
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Clips rated", stats["clips_rated"])
        with col2:
            st.metric("👍 Liked", stats["total_likes"])
        with col3:
            st.metric("👎 Disliked", stats["total_dislikes"])

        st.metric("Words learned", stats["words_learned"])

        if stats["top_liked"]:
            st.subheader("Words boosted by your likes")
            liked_str = "  ·  ".join(
                f"**{w}** (+{s})" for w, s in stats["top_liked"]
            )
            st.markdown(liked_str)

        if stats["top_disliked"]:
            st.subheader("Words penalised by your dislikes")
            disliked_str = "  ·  ".join(
                f"**{w}** ({s})" for w, s in stats["top_disliked"]
            )
            st.markdown(disliked_str)

        st.divider()
        st.caption(
            "Resetting feedback clears all learned preferences. "
            "Use this if you switch to a very different type of content."
        )
        if st.button("🗑️ Reset All Feedback"):
            reset_feedback()
            st.success("Feedback cleared. The app will start learning fresh.")
            st.rerun()
