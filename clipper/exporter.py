"""
clipper/exporter.py
Cuts video clips, burns captions, exports SRT files.

Text mode support added:
  raw_transcript  — word-by-word yellow highlight at bottom (original behavior)
  clean_subtitle  — grouped lines, no highlight, at bottom
  viral_hook      — hook text at top + subtitles at bottom (two ASS layers)
  caption_summary — one static summary line at bottom

All mode logic lives in clipper/text_modes.py.
This file only handles rendering and ffmpeg.
"""

import os
import subprocess
from typing import List, Dict, Callable, Optional
from clipper.text_modes import build_text_layers, TEXT_MODES

# Face detection (optional)
try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

PADDING_SECONDS = 1.5

FONT_NAME      = "Arial"
FONT_SIZE      = 100
FONT_BOLD      = -1
WORDS_PER_LINE = 2
MARGIN_V       = 700
MARGIN_LR      = 80

TEXT_COLOR      = "&H00FFFFFF"   # white
OUTLINE_COLOR   = "&H00000000"   # black
HIGHLIGHT_COLOR = "&H0000FFFF"   # yellow
BOX_COLOR       = "&H00000000"
HOOK_COLOR      = "&H0000FFFF"   # yellow for hook text at top

ASPECT_RATIOS = {
    "vertical":   (1080, 1920, "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"),
    "square":     (1080, 1080, "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"),
    "horizontal": (1920, 1080, "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"),
}


# ── ASS helpers ───────────────────────────────────────────────────────────────

def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _to_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_header(play_res_x: int, play_res_y: int, styles: str) -> str:
    """Build an ASS file header with custom styles."""
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{styles}
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def _get_res_and_margins(aspect_ratio: str):
    """Return (play_res_x, play_res_y, bottom_margin_v, top_margin_v) per ratio."""
    if aspect_ratio == "square":
        return 1080, 1080, 380, 80
    elif aspect_ratio == "horizontal":
        return 1920, 1080, 100, 60
    else:  # vertical
        return 1080, 1920, MARGIN_V, 120


# ── Mode 1: Raw transcript (word-by-word yellow highlight) ────────────────────

def _build_raw_transcript_ass(
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    fallback_text: str,
    aspect_ratio: str,
) -> str:
    """Original Opus-style word-by-word highlighted captions."""
    rx, ry, margin_v, _ = _get_res_and_margins(aspect_ratio)

    style = (
        f"Style: Cap,{FONT_NAME},{FONT_SIZE},{TEXT_COLOR},&H000000FF,"
        f"{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,2,0,1,5,0,"
        f"2,{MARGIN_LR},{MARGIN_LR},{margin_v},1"
    )
    header = _ass_header(rx, ry, style)
    lines  = []

    if not words:
        lines.append(
            f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(clip_duration)},"
            f"Cap,,0,0,0,,{_escape_ass(fallback_text.upper())}"
        )
        return header + "\n".join(lines) + "\n"

    chunks = [words[i: i + WORDS_PER_LINE] for i in range(0, len(words), WORDS_PER_LINE)]
    for chunk in chunks:
        if not chunk:
            continue
        for active_idx, active_word in enumerate(chunk):
            w_start = max(0.0, active_word["start"] - clip_start)
            w_end   = min(clip_duration, active_word["end"] - clip_start)
            if w_end <= w_start:
                continue
            parts = []
            for j, w in enumerate(chunk):
                word_text = _escape_ass(w["word"].strip().upper())
                if j == active_idx:
                    parts.append(f"{{\\c{HIGHLIGHT_COLOR}&}}{word_text}{{\\c{TEXT_COLOR}&}}")
                else:
                    parts.append(word_text)
            lines.append(
                f"Dialogue: 0,{_to_ass_time(w_start)},{_to_ass_time(w_end)},"
                f"Cap,,0,0,0,,{'  '.join(parts)}"
            )

    return header + "\n".join(lines) + "\n"


# ── Mode 2: Clean subtitle (grouped lines, no highlight) ─────────────────────

def _build_clean_subtitle_ass(
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    fallback_text: str,
    aspect_ratio: str,
    words_per_line: int = 5,
) -> str:
    """Clean grouped subtitle lines. No word highlight. Readable on any background."""
    rx, ry, margin_v, _ = _get_res_and_margins(aspect_ratio)
    font_size = 80  # slightly smaller for grouped lines

    style = (
        f"Style: Sub,{FONT_NAME},{font_size},{TEXT_COLOR},&H000000FF,"
        f"{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,1,0,1,4,0,"
        f"2,{MARGIN_LR},{MARGIN_LR},{margin_v},1"
    )
    header = _ass_header(rx, ry, style)
    lines  = []

    if not words:
        lines.append(
            f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(clip_duration)},"
            f"Sub,,0,0,0,,{_escape_ass(fallback_text)}"
        )
        return header + "\n".join(lines) + "\n"

    chunks = [words[i: i + words_per_line] for i in range(0, len(words), words_per_line)]
    for chunk in chunks:
        if not chunk:
            continue
        c_start = max(0.0, chunk[0]["start"]  - clip_start)
        c_end   = min(clip_duration, chunk[-1]["end"] - clip_start)
        if c_end <= c_start:
            continue
        text = _escape_ass(" ".join(w["word"].strip() for w in chunk))
        lines.append(
            f"Dialogue: 0,{_to_ass_time(c_start)},{_to_ass_time(c_end)},"
            f"Sub,,0,0,0,,{text}"
        )

    return header + "\n".join(lines) + "\n"


# ── Mode 3: Viral hook (hook at top + subtitles at bottom) ───────────────────

def _build_viral_hook_ass(
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    fallback_text: str,
    hook_text: str,
    aspect_ratio: str,
) -> str:
    """
    Two text layers in one ASS file:
    - TOP: short hook text in yellow, bold, large — shown for first 2/3 of clip
    - BOTTOM: word-by-word subtitles in white
    """
    rx, ry, margin_v, top_margin = _get_res_and_margins(aspect_ratio)
    hook_font_size = 110

    styles = (
        f"Style: Hook,{FONT_NAME},{hook_font_size},{HOOK_COLOR},&H000000FF,"
        f"{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,4,0,1,6,0,"
        f"8,{MARGIN_LR},{MARGIN_LR},{top_margin},1\n"   # alignment 8 = top-center
        f"Style: Sub,{FONT_NAME},80,{TEXT_COLOR},&H000000FF,"
        f"{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,1,0,1,4,0,"
        f"2,{MARGIN_LR},{MARGIN_LR},{margin_v},1"       # alignment 2 = bottom-center
    )
    header = _ass_header(rx, ry, styles)
    lines  = []

    # Hook line shown for the first 2/3 of the clip
    hook_end = clip_duration * 0.67
    if hook_text:
        escaped_hook = _escape_ass(hook_text.upper())
        lines.append(
            f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(hook_end)},"
            f"Hook,,0,0,0,,{escaped_hook}"
        )

    # Bottom subtitles — word by word
    if not words:
        lines.append(
            f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(clip_duration)},"
            f"Sub,,0,0,0,,{_escape_ass(fallback_text.upper())}"
        )
    else:
        chunks = [words[i: i + WORDS_PER_LINE] for i in range(0, len(words), WORDS_PER_LINE)]
        for chunk in chunks:
            if not chunk:
                continue
            for active_idx, active_word in enumerate(chunk):
                w_start = max(0.0, active_word["start"] - clip_start)
                w_end   = min(clip_duration, active_word["end"] - clip_start)
                if w_end <= w_start:
                    continue
                parts = []
                for j, w in enumerate(chunk):
                    word_text = _escape_ass(w["word"].strip().upper())
                    if j == active_idx:
                        parts.append(f"{{\\c{HIGHLIGHT_COLOR}&}}{word_text}{{\\c{TEXT_COLOR}&}}")
                    else:
                        parts.append(word_text)
                lines.append(
                    f"Dialogue: 0,{_to_ass_time(w_start)},{_to_ass_time(w_end)},"
                    f"Sub,,0,0,0,,{'  '.join(parts)}"
                )

    return header + "\n".join(lines) + "\n"


# ── Mode 4: Caption summary (static single line) ──────────────────────────────

def _build_caption_summary_ass(
    clip_start: float,
    clip_duration: float,
    summary_text: str,
    aspect_ratio: str,
) -> str:
    """One static summary sentence shown for the full clip duration."""
    rx, ry, margin_v, _ = _get_res_and_margins(aspect_ratio)
    font_size = 85

    style = (
        f"Style: Sum,{FONT_NAME},{font_size},{TEXT_COLOR},&H000000FF,"
        f"{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,1,0,1,4,0,"
        f"2,{MARGIN_LR},{MARGIN_LR},{margin_v},1"
    )
    header = _ass_header(rx, ry, style)
    text   = _escape_ass(summary_text)
    line   = (
        f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(clip_duration)},"
        f"Sum,,0,0,0,,{text}"
    )
    return header + line + "\n"


# ── ASS router — picks the right builder based on mode ───────────────────────

def build_ass_file(
    text_mode: str,
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    clip_text: str,
    aspect_ratio: str,
) -> str:
    """
    Route to the correct ASS builder based on text_mode.
    This is the single entry point from export_clips().
    """
    bottom_text, top_text = build_text_layers(
        clip_text=clip_text,
        words=words,
        clip_start=clip_start,
        clip_duration=clip_duration,
        text_mode=text_mode,
        aspect_ratio=aspect_ratio,
    )

    if text_mode == "raw_transcript":
        return _build_raw_transcript_ass(
            words=words, clip_start=clip_start, clip_duration=clip_duration,
            fallback_text=bottom_text, aspect_ratio=aspect_ratio,
        )

    elif text_mode == "clean_subtitle":
        return _build_clean_subtitle_ass(
            words=words, clip_start=clip_start, clip_duration=clip_duration,
            fallback_text=bottom_text, aspect_ratio=aspect_ratio,
        )

    elif text_mode == "viral_hook":
        return _build_viral_hook_ass(
            words=words, clip_start=clip_start, clip_duration=clip_duration,
            fallback_text=bottom_text, hook_text=top_text, aspect_ratio=aspect_ratio,
        )

    elif text_mode == "caption_summary":
        return _build_caption_summary_ass(
            clip_start=clip_start, clip_duration=clip_duration,
            summary_text=bottom_text, aspect_ratio=aspect_ratio,
        )

    else:
        # Unknown mode — fall back to raw
        return _build_raw_transcript_ass(
            words=words, clip_start=clip_start, clip_duration=clip_duration,
            fallback_text=clip_text, aspect_ratio=aspect_ratio,
        )


# ── SRT export (unchanged) ────────────────────────────────────────────────────

def _to_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(words, clip_start, clip_duration, fallback_text="", words_per_block=6):
    if not words:
        return f"1\n{_to_srt_time(0)} --> {_to_srt_time(clip_duration)}\n{fallback_text.strip()}\n\n"

    blocks = []
    chunks = [words[i: i + words_per_block] for i in range(0, len(words), words_per_block)]
    for idx, chunk in enumerate(chunks):
        if not chunk:
            continue
        b_start = max(0.0, chunk[0]["start"]  - clip_start)
        b_end   = min(clip_duration, chunk[-1]["end"] - clip_start)
        if b_end <= b_start:
            continue
        text = " ".join(w["word"].strip() for w in chunk)
        blocks.append(f"{idx+1}\n{_to_srt_time(b_start)} --> {_to_srt_time(b_end)}\n{text}\n")

    return "\n".join(blocks) + "\n"


# ── Face detection (unchanged) ────────────────────────────────────────────────

def _detect_face_crop(video_path: str, aspect_ratio: str) -> str:
    if not OPENCV_AVAILABLE or aspect_ratio != "vertical":
        return ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS["vertical"])[2]
    try:
        cap = cv2.VideoCapture(video_path)
        fps    = cap.get(cv2.CAP_PROP_FPS) or 25
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        interval     = max(1, int(fps * 2))
        face_x = []
        for fi in range(0, min(total, int(fps * 60)), interval):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, frame = cap.read()
            if not ret:
                continue
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(60, 60))
            for (x, y, w, h) in faces:
                face_x.append(x + w // 2)
        cap.release()
        if not face_x:
            return ASPECT_RATIOS["vertical"][2]
        avg_x    = int(sum(face_x) / len(face_x))
        scale    = 1920 / orig_h
        scaled_w = int(orig_w * scale)
        crop_x   = max(0, min(int(avg_x * scale) - 540, scaled_w - 1080))
        print(f"  Face reframe: avg_x={avg_x}px → crop_x={crop_x}")
        return f"scale={scaled_w}:1920,crop=1080:1920:{crop_x}:0"
    except Exception as e:
        print(f"  Face detection error ({e}), using center crop.")
        return ASPECT_RATIOS["vertical"][2]


def _run_ffmpeg(command: str) -> bool:
    try:
        subprocess.run(command, shell=True, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ffmpeg error: {e.stderr.decode(errors='replace')}")
        return False


def _ffmpeg_path(path: str) -> str:
    path = path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        path = path[0] + "\\:" + path[2:]
    return path


def _extract_words(whisper_result, clip_start, clip_end):
    words = []
    for segment in whisper_result.get("segments", []):
        for w in segment.get("words", []):
            if w["end"] > clip_start and w["start"] < clip_end:
                words.append({"word": w["word"], "start": w["start"], "end": w["end"]})
    return words


# ── Main export function ──────────────────────────────────────────────────────

def export_clips(
    video_path: str,
    clips: List[Dict],
    output_dir: str,
    whisper_result: dict = None,
    headlines=None,
    aspect_ratio: str = "vertical",
    export_srt: bool = True,
    smart_reframe: bool = True,
    text_mode: str = "raw_transcript",
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """
    Cut, caption, and export each clip.

    Args:
        text_mode: One of 'raw_transcript', 'clean_subtitle',
                   'viral_hook', 'caption_summary'.
                   Controls how captions appear on the exported video.
    """
    os.makedirs(output_dir, exist_ok=True)

    if smart_reframe and OPENCV_AVAILABLE and aspect_ratio == "vertical":
        print("  Detecting face for smart reframing...")
        vf_filter = _detect_face_crop(video_path, aspect_ratio)
    else:
        vf_filter = ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS["vertical"])[2]

    exported = []
    total    = len(clips)

    for i, clip in enumerate(clips):
        clip_num   = i + 1
        start      = max(0.0, clip["start"] - PADDING_SECONDS)
        end        = clip["end"] + PADDING_SECONDS
        duration   = end - start

        prefix     = os.path.join(output_dir, f"clip_{clip_num}")
        raw_path   = f"{prefix}_raw.mp4"
        ass_path   = f"{prefix}_captions.ass"
        final_path = f"{prefix}_final.mp4"
        srt_path   = f"{prefix}_captions.srt"

        print(f"[{clip_num}/{total}] Cutting clip ({aspect_ratio}, {text_mode})...")
        cut_cmd = (
            f'ffmpeg -y -i "{video_path}" '
            f'-ss {start:.3f} -t {duration:.3f} '
            f'-vf "{vf_filter}" '
            f'-c:a copy "{raw_path}"'
        )
        if not _run_ffmpeg(cut_cmd):
            print(f"  ✗ Cut failed, skipping clip {clip_num}.")
            continue

        words = _extract_words(whisper_result, start, end) if whisper_result else []

        # Build the ASS subtitle file using the selected text mode
        ass_content = build_ass_file(
            text_mode=text_mode,
            words=words,
            clip_start=start,
            clip_duration=duration,
            clip_text=clip.get("text", ""),
            aspect_ratio=aspect_ratio,
        )
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        print(f"[{clip_num}/{total}] Burning captions ({text_mode})...")
        burn_cmd = (
            f'ffmpeg -y -i "{raw_path}" '
            f'-vf "subtitles=\'{_ffmpeg_path(ass_path)}\'" '
            f'-c:a copy "{final_path}"'
        )
        if not _run_ffmpeg(burn_cmd):
            print(f"  ✗ Caption burn failed, skipping clip {clip_num}.")
            continue

        if export_srt:
            srt_content = _build_srt(
                words=words, clip_start=start,
                clip_duration=duration, fallback_text=clip.get("text", ""),
            )
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)

        for temp in [raw_path, ass_path]:
            try:
                os.remove(temp)
            except OSError:
                pass

        print(f"  ✓ Clip {clip_num} done → {final_path}")
        exported.append(final_path)

        if progress_callback:
            progress_callback(clip_num, total)

    return exported
