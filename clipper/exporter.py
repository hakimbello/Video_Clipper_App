"""
clipper/exporter.py
Cuts video clips, burns in Opus-style captions, and exports SRT files.

New in this version:
- Aspect ratio options: vertical (9:16), square (1:1), horizontal (16:9)
- SRT subtitle file exported alongside every video clip
- No headlines
"""

import os
import subprocess
from typing import List, Dict, Callable, Optional

PADDING_SECONDS = 1.5

FONT_NAME      = "Arial"
FONT_SIZE      = 100
FONT_BOLD      = -1
WORDS_PER_LINE = 2
MARGIN_V       = 700
MARGIN_LR      = 80

TEXT_COLOR      = "&H00FFFFFF"
OUTLINE_COLOR   = "&H00000000"
HIGHLIGHT_COLOR = "&H0000FFFF"
BOX_COLOR       = "&H00000000"

# Aspect ratio presets: (output_width, output_height, ffmpeg_scale_crop)
ASPECT_RATIOS = {
    "vertical":   (1080, 1920, "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"),
    "square":     (1080, 1080, "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"),
    "horizontal": (1920, 1080, "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"),
}


# ── ASS subtitle helpers ──────────────────────────────────────────────────────

def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _to_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_opus_captions_ass(
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    fallback_text: str = "",
    aspect_ratio: str = "vertical",
) -> str:
    """Build ASS subtitle file with Opus-style word-by-word highlighting."""

    # Adjust resolution and caption position per aspect ratio
    if aspect_ratio == "square":
        play_res_x, play_res_y = 1080, 1080
        margin_v = 400
    elif aspect_ratio == "horizontal":
        play_res_x, play_res_y = 1920, 1080
        margin_v = 120
    else:
        play_res_x, play_res_y = 1080, 1920
        margin_v = MARGIN_V

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Cap,{FONT_NAME},{FONT_SIZE},{TEXT_COLOR},&H000000FF,{OUTLINE_COLOR},{BOX_COLOR},{FONT_BOLD},0,0,0,100,100,2,0,1,5,0,2,{MARGIN_LR},{MARGIN_LR},{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = []

    if not words:
        text = _escape_ass(fallback_text.strip().upper())
        lines.append(
            f"Dialogue: 0,{_to_ass_time(0)},{_to_ass_time(clip_duration)},"
            f"Cap,,0,0,0,,{text}"
        )
        return header + "\n".join(lines) + "\n"

    chunks = [words[i: i + WORDS_PER_LINE] for i in range(0, len(words), WORDS_PER_LINE)]

    for chunk in chunks:
        if not chunk:
            continue
        chunk_start = max(0.0, chunk[0]["start"] - clip_start)
        chunk_end   = min(clip_duration, chunk[-1]["end"] - clip_start)
        if chunk_end <= chunk_start:
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


# ── SRT export ────────────────────────────────────────────────────────────────

def _to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    fallback_text: str = "",
    words_per_block: int = 6,
) -> str:
    """
    Build a standard SRT subtitle file for a clip.
    Groups words into blocks of words_per_block for readable subtitle chunks.
    This file can be uploaded directly to TikTok, YouTube, or Instagram.

    Args:
        words:          Word-level timestamps from Whisper.
        clip_start:     When the clip starts in the original video (for offsetting).
        clip_duration:  Length of the clip in seconds.
        fallback_text:  Used if no word timestamps exist.
        words_per_block: How many words per subtitle line (6 is readable on mobile).
    """
    if not words:
        # Fallback: one subtitle block for the whole clip
        return (
            f"1\n"
            f"{_to_srt_time(0)} --> {_to_srt_time(clip_duration)}\n"
            f"{fallback_text.strip()}\n\n"
        )

    blocks = []
    chunks = [words[i: i + words_per_block] for i in range(0, len(words), words_per_block)]

    for idx, chunk in enumerate(chunks):
        if not chunk:
            continue
        block_start = max(0.0, chunk[0]["start"] - clip_start)
        block_end   = min(clip_duration, chunk[-1]["end"] - clip_start)
        if block_end <= block_start:
            continue
        text = " ".join(w["word"].strip() for w in chunk)
        blocks.append(
            f"{idx + 1}\n"
            f"{_to_srt_time(block_start)} --> {_to_srt_time(block_end)}\n"
            f"{text}\n"
        )

    return "\n".join(blocks) + "\n"


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def _run_ffmpeg(command: str) -> bool:
    try:
        subprocess.run(
            command, shell=True, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ffmpeg error: {e.stderr.decode(errors='replace')}")
        return False


def _ffmpeg_path(path: str) -> str:
    path = path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        path = path[0] + "\\:" + path[2:]
    return path


def _extract_words(whisper_result: dict, clip_start: float, clip_end: float) -> List[Dict]:
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
    headlines=None,                              # ignored, kept for compatibility
    aspect_ratio: str = "vertical",             # vertical | square | horizontal
    export_srt: bool = True,                    # whether to also save .srt files
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """
    For each clip:
    1. Cut from source video and crop to chosen aspect ratio
    2. Burn Opus-style captions into video
    3. Export an SRT subtitle file alongside the video
    4. Clean up temp files

    Args:
        video_path:    Source video file.
        clips:         List of clip dicts from get_top_clips().
        output_dir:    Where to save finished files.
        whisper_result: Full transcription output (for word timestamps).
        aspect_ratio:  'vertical' (9:16), 'square' (1:1), 'horizontal' (16:9).
        export_srt:    If True, saves a .srt file for each clip.
        progress_callback: Called with (clips_done, total) after each clip.

    Returns:
        List of paths to finished .mp4 files.
    """
    os.makedirs(output_dir, exist_ok=True)

    ratio_config = ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS["vertical"])
    vf_filter    = ratio_config[2]

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

        # ── Step 1: Cut + crop ────────────────────────────────────────────────
        print(f"[{clip_num}/{total}] Cutting clip ({aspect_ratio})...")
        cut_cmd = (
            f'ffmpeg -y -i "{video_path}" '
            f'-ss {start:.3f} -t {duration:.3f} '
            f'-vf "{vf_filter}" '
            f'-c:a copy "{raw_path}"'
        )
        if not _run_ffmpeg(cut_cmd):
            print(f"  ✗ Clip {clip_num} cut failed, skipping.")
            continue

        # ── Step 2: Extract word timestamps ──────────────────────────────────
        words = _extract_words(whisper_result, start, end) if whisper_result else []

        # ── Step 3: Build and burn ASS captions ──────────────────────────────
        ass_content = _build_opus_captions_ass(
            words=words,
            clip_start=start,
            clip_duration=duration,
            fallback_text=clip.get("text", ""),
            aspect_ratio=aspect_ratio,
        )
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        print(f"[{clip_num}/{total}] Burning captions...")
        burn_cmd = (
            f'ffmpeg -y -i "{raw_path}" '
            f'-vf "subtitles=\'{_ffmpeg_path(ass_path)}\'" '
            f'-c:a copy "{final_path}"'
        )
        if not _run_ffmpeg(burn_cmd):
            print(f"  ✗ Clip {clip_num} caption burn failed, skipping.")
            continue

        # ── Step 4: Export SRT file ───────────────────────────────────────────
        if export_srt:
            srt_content = _build_srt(
                words=words,
                clip_start=start,
                clip_duration=duration,
                fallback_text=clip.get("text", ""),
            )
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
            print(f"  ✓ SRT saved → {srt_path}")

        # ── Cleanup temp files ────────────────────────────────────────────────
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
