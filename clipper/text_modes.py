"""
clipper/text_modes.py
─────────────────────
All text formatting logic for clip captions lives here.

The four modes:

  1. raw_transcript   — exact words as Whisper heard them, word-by-word
                        highlighted in yellow as the person speaks.
                        This is the current default behavior.

  2. clean_subtitle   — same words but grouped into readable lines of 6,
                        no highlight, clean white text. Looks like Netflix
                        subtitles. Good for talk shows, interviews, podcasts.

  3. viral_hook       — short punchy text generated from the clip content.
                        Shown as a big bold overlay in the center/upper area.
                        Designed for mobile — max 6 words, all caps, high
                        contrast. Combined with subtitles at the bottom.

  4. caption_summary  — the whole clip's meaning compressed into one short
                        sentence shown as a static overlay for the clip's
                        full duration. Good for educational and commentary.

HOW TO ADD A NEW MODE IN THE FUTURE:
  1. Add a new key to TEXT_MODES dict at the bottom of this file.
  2. Write a function that takes clip_text and returns a string.
  3. Add a case for it in build_text_layers().
  That is all — the exporter and app pick it up automatically.
"""

import re
from typing import List, Dict, Tuple


# ── Utility helpers ───────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Strip extra whitespace and normalize punctuation."""
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _split_into_lines(text: str, words_per_line: int = 6) -> List[str]:
    """Break text into lines of N words each for readable subtitles."""
    words = text.split()
    return [
        " ".join(words[i: i + words_per_line])
        for i in range(0, len(words), words_per_line)
    ]


# ── Hook text generator ───────────────────────────────────────────────────────

# Sentence starters that often signal a strong moment worth hooking
_HOOK_TRIGGERS = [
    "the truth", "nobody", "most people", "here's why", "the reason",
    "stop", "you need", "what i", "i realized", "the secret",
    "the biggest", "this is why", "real talk", "let me tell you",
    "the problem", "what nobody", "here's the thing",
]

def generate_viral_hook(clip_text: str, max_words: int = 6) -> str:
    """
    Generate a short viral hook from clip text.

    Strategy (in order):
    1. Find a sentence that starts with a known hook trigger phrase.
       Extract the first max_words words of that sentence.
    2. If no trigger found, find the shortest complete sentence.
    3. If no complete sentence, take the first max_words words of the clip.

    The result is always uppercased for visual impact.
    """
    text = _clean_text(clip_text)
    lower = text.lower()

    # Strategy 1: look for hook trigger phrases
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for trigger in _HOOK_TRIGGERS:
        for sentence in sentences:
            if trigger in sentence.lower():
                words = sentence.split()[:max_words]
                if len(words) >= 2:
                    return " ".join(words).upper().rstrip(".,;")

    # Strategy 2: shortest complete sentence (must end with punctuation)
    complete = [s for s in sentences if s and s[-1] in ".!?" and len(s.split()) >= 3]
    if complete:
        shortest = min(complete, key=lambda s: len(s.split()))
        words = shortest.split()[:max_words]
        return " ".join(words).upper().rstrip(".,;")

    # Strategy 3: just take first max_words words
    words = text.split()[:max_words]
    return " ".join(words).upper().rstrip(".,;")


def generate_caption_summary(clip_text: str, max_words: int = 12) -> str:
    """
    Compress the clip's meaning into a short summary line.

    Strategy:
    1. Find the sentence with the most 'signal' words.
    2. Truncate to max_words.
    3. Ensure it ends cleanly.
    """
    text = _clean_text(clip_text)
    sentences = re.split(r'(?<=[.!?])\s+', text)

    # Score each sentence by signal word density
    signal_words = {
        "because", "therefore", "always", "never", "most", "best",
        "worst", "only", "every", "truth", "secret", "key", "reason",
        "important", "critical", "essential", "mistake", "wrong", "right",
    }

    best_sentence = text  # fallback
    best_score = -1

    for sentence in sentences:
        words = sentence.lower().split()
        score = sum(1 for w in words if w in signal_words)
        if score > best_score and len(words) >= 4:
            best_score = score
            best_sentence = sentence

    words = best_sentence.split()[:max_words]
    result = " ".join(words)
    if result and result[-1] not in ".!?":
        result += "."
    return result


# ── Main builder function ─────────────────────────────────────────────────────

def build_text_layers(
    clip_text: str,
    words: List[Dict],
    clip_start: float,
    clip_duration: float,
    text_mode: str,
    aspect_ratio: str = "vertical",
) -> Tuple[str, str]:
    """
    Build the text content for a clip based on the selected mode.

    Returns a tuple of (bottom_text, top_text).
    - bottom_text: what appears at the bottom of the clip (always present)
    - top_text:    what appears at the top (empty string = nothing shown)

    The actual ASS file building still happens in exporter.py.
    This function just decides WHAT text to show and WHERE.

    Args:
        clip_text:     Full transcript text for this clip.
        words:         Word-level timestamps from Whisper.
        clip_start:    Clip start time in the source video.
        clip_duration: Duration of the clip in seconds.
        text_mode:     One of the TEXT_MODES keys.
        aspect_ratio:  'vertical', 'square', or 'horizontal'.

    Returns:
        (bottom_text, top_text) — both are plain strings.
        The exporter handles rendering these into ASS format.
    """
    text = _clean_text(clip_text)

    if text_mode == "raw_transcript":
        # Word-by-word highlighted captions at bottom, nothing at top
        # The exporter handles the highlight logic using word timestamps
        return (text, "")

    elif text_mode == "clean_subtitle":
        # Clean readable subtitles at bottom, no highlight, nothing at top
        return (text, "")

    elif text_mode == "viral_hook":
        # Short punchy hook at TOP, full subtitles at BOTTOM
        hook = generate_viral_hook(text)
        return (text, hook)

    elif text_mode == "caption_summary":
        # Short summary shown as static text at bottom for the whole clip
        summary = generate_caption_summary(text)
        return (summary, "")

    else:
        # Unknown mode — fall back to raw transcript
        return (text, "")


# ── Mode registry ─────────────────────────────────────────────────────────────
# This is what the UI reads to populate the dropdown.
# To add a new mode: add a new entry here. Nothing else needs changing.

TEXT_MODES = {
    "raw_transcript": {
        "label":       "Raw Transcript (word-by-word, highlighted)",
        "description": "Exact words as spoken. Active word turns yellow. Current default.",
        "has_top":     False,
    },
    "clean_subtitle": {
        "label":       "Clean Subtitle",
        "description": "Same words, grouped into readable lines. No highlight. Netflix style.",
        "has_top":     False,
    },
    "viral_hook": {
        "label":       "Viral Hook + Subtitles",
        "description": "Short bold hook text at top. Full subtitles at bottom. Best for TikTok.",
        "has_top":     True,
    },
    "caption_summary": {
        "label":       "Caption Summary",
        "description": "One compressed sentence shown for the whole clip. Good for educational content.",
        "has_top":     False,
    },
}
