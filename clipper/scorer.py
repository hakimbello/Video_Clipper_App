"""
clipper/scorer.py
Selects complete, compelling moments from long-form video transcripts.

Design philosophy:
- A clip must contain a complete idea: hook, point, payoff.
- Short fragments are rejected regardless of how punchy they sound.
- Platform targets inform length decisions, not just content scoring.
- Longer clips with complete thoughts beat shorter clips with fragments.
"""

from typing import List, Dict, Tuple
try:
    from clipper.feedback import get_feedback_score
except Exception:
    def get_feedback_score(text): return 0


# ── Platform and content type targets ────────────────────────────────────────
# These are the target ranges in seconds for finished clips.
# The system uses BALANCED defaults but respects these when scoring.

PLATFORM_TARGETS = {
    "tiktok":     {"min": 15, "sweet_low": 30, "sweet_high": 75,  "max": 180},
    "shorts":     {"min": 15, "sweet_low": 30, "sweet_high": 90,  "max": 180},
    "reels":      {"min": 15, "sweet_low": 25, "sweet_high": 60,  "max": 90},
    "facebook":   {"min": 15, "sweet_low": 30, "sweet_high": 60,  "max": 90},
    "general":    {"min": 15, "sweet_low": 30, "sweet_high": 90,  "max": 180},
}

CONTENT_TYPE_TARGETS = {
    "entertainment": {"min": 15, "sweet_low": 15, "sweet_high": 45,  "max": 60},
    "educational":   {"min": 30, "sweet_low": 45, "sweet_high": 90,  "max": 180},
    "storytelling":  {"min": 45, "sweet_low": 60, "sweet_high": 120, "max": 180},
    "commentary":    {"min": 30, "sweet_low": 45, "sweet_high": 75,  "max": 120},
    "promo":         {"min": 20, "sweet_low": 30, "sweet_high": 60,  "max": 90},
    "podcast":       {"min": 45, "sweet_low": 60, "sweet_high": 120, "max": 180},
    "general":       {"min": 15, "sweet_low": 30, "sweet_high": 90,  "max": 180},
}

# Default targets used when no specific platform/content type is chosen
DEFAULT_MIN_DURATION    = 15.0   # hard floor — nothing under 15s ever exported
DEFAULT_TARGET_LOW      = 30.0   # preferred minimum
DEFAULT_TARGET_HIGH     = 90.0   # preferred maximum
DEFAULT_MAX_DURATION    = 180.0  # hard ceiling

# Minimum words a clip must contain to be considered a complete thought
MIN_WORDS = 20


# ── Hook and filler word lists ────────────────────────────────────────────────

HOOK_PHRASES = [
    "you", "listen", "watch", "the truth", "nobody talks about",
    "real talk", "here's the thing", "let me tell you", "most people",
    "what nobody tells you", "the reason", "the problem is",
    "what i learned", "this is why", "stop doing", "you need to",
    "the biggest mistake", "here's what", "i used to", "i realized",
]

FILLER_WORDS = [
    "um", "uh", "like", "you know", "basically", "literally",
    "sort of", "kind of", "i mean", "right", "okay so",
]

SENTENCE_ENDINGS = {".", "?", "!", "…"}


# ── Mode settings ─────────────────────────────────────────────────────────────

MODE_SETTINGS = {
    "conservative": {
        "count_multiplier":   0.6,
        "min_score":          15,
        "min_gap":            45.0,
        "similarity_threshold": 0.45,
        "window":             6,     # more segments = longer, more complete clips
    },
    "balanced": {
        "count_multiplier":   1.0,
        "min_score":          8,
        "min_gap":            20.0,
        "similarity_threshold": 0.55,
        "window":             5,
    },
    "aggressive": {
        "count_multiplier":   1.4,
        "min_score":          4,
        "min_gap":            10.0,
        "similarity_threshold": 0.65,
        "window":             4,
    },
}


# ── Dynamic clip count (unchanged logic, still works well) ───────────────────

def calculate_target_clip_count(
    video_duration_seconds: float,
    segments: List[Dict],
    mode: str = "balanced",
    max_clips: int = 25,
) -> int:
    minutes = video_duration_seconds / 60.0

    if minutes <= 3:
        base = _lerp(1, 3, minutes / 3.0)
    elif minutes <= 10:
        base = _lerp(3, 7, (minutes - 3) / 7.0)
    elif minutes <= 30:
        base = _lerp(5, 12, (minutes - 10) / 20.0)
    elif minutes <= 60:
        base = _lerp(8, 20, (minutes - 30) / 30.0)
    else:
        base = 18 + (minutes - 60) * 0.15

    density = _content_density(segments, video_duration_seconds)
    density_factor = 0.7 + (density * 0.5)
    adjusted = base * density_factor

    multiplier = MODE_SETTINGS.get(mode, MODE_SETTINGS["balanced"])["count_multiplier"]
    final = adjusted * multiplier

    return max(1, min(max_clips, round(final)))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _content_density(segments: List[Dict], video_duration: float) -> float:
    if not segments or video_duration <= 0:
        return 0.5
    total_speech = sum(s["end"] - s["start"] for s in segments)
    return min(1.0, total_speech / video_duration)


# ── Segment scoring ───────────────────────────────────────────────────────────

def _score_segment(
    text: str,
    duration: float,
    gap_before: float = 0.0,
    gap_after: float = 0.0,
    platform: str = "general",
    content_type: str = "general",
) -> int:
    """
    Score a segment on narrative completeness first, content quality second.

    Key changes from old scorer:
    - Length bonuses reward clips IN the target range, not the shortest clips
    - Brevity no longer earns bonus points
    - Narrative completeness (punctuation, complete sentences) earns points
    - Clean in/out points (pauses) earn points
    - Filler words penalised harder
    - Ultra-short clips are penalised hard regardless of text quality
    """
    score = 0
    words = text.split()
    lower = text.lower()
    word_count = len(words)

    if word_count == 0:
        return 0

    target = PLATFORM_TARGETS.get(platform, PLATFORM_TARGETS["general"])

    # ── Length scoring (most important dimension) ─────────────────────────────

    # Hard penalty for ultra-short clips — these are almost always fragments
    if duration < 15:
        score -= 30
    elif duration < DEFAULT_TARGET_LOW:
        score -= 8   # mild penalty for being shorter than ideal

    # Reward clips that land in the target sweet spot
    if target["sweet_low"] <= duration <= target["sweet_high"]:
        score += 15
    elif duration <= target["max"]:
        score += 5   # acceptable length, not ideal

    # Penalty for being too long
    if duration > target["max"]:
        score -= 10

    # ── Narrative completeness ────────────────────────────────────────────────

    # Clip ends with a complete sentence — has a payoff
    if text.strip()[-1] in SENTENCE_ENDINGS:
        score += 8

    # Clip contains multiple complete sentences — full arc
    sentence_count = sum(1 for c in text if c in ".?!")
    if sentence_count >= 2:
        score += 6
    if sentence_count >= 3:
        score += 4

    # Enough words to contain a real thought
    if word_count >= 40:
        score += 5
    if word_count >= 60:
        score += 3

    # ── Clean entry and exit points ───────────────────────────────────────────

    # Natural pause before = clean place to start a clip
    if gap_before >= 0.5:
        score += 4
    if gap_before >= 1.5:
        score += 3

    # Natural pause after = clean place to end a clip
    if gap_after >= 0.3:
        score += 3
    if gap_after >= 1.0:
        score += 2

    # ── Hook quality ──────────────────────────────────────────────────────────

    for phrase in HOOK_PHRASES:
        if phrase in lower:
            score += 4
            break   # one hook phrase is enough — no stacking

    if "?" in text:
        score += 4

    if "!" in text:
        score += 2

    # ── Content quality ───────────────────────────────────────────────────────

    # Filler word density — penalise proportionally
    filler_count = sum(1 for f in FILLER_WORDS if f in lower)
    filler_density = filler_count / max(word_count, 1)
    if filler_density > 0.15:
        score -= 8
    elif filler_density > 0.08:
        score -= 4

    # Does NOT start mid-sentence — important for comprehension
    MID_STARTERS = {"and", "but", "or", "so", "because", "which", "that", "then", "also"}
    if words[0].lower() in MID_STARTERS:
        score -= 6

    return score


# ── Quality filter ────────────────────────────────────────────────────────────

def _passes_quality_filter(
    clip: Dict,
    min_score: int,
    platform: str = "general",
) -> Tuple[bool, str]:
    """
    Hard rules. A clip must pass ALL of these or it is rejected entirely.
    No exceptions. These are not suggestions.
    """
    text = clip["text"].strip()
    words = text.split()
    duration = clip["end"] - clip["start"]

    # 1. Minimum duration — the most important rule
    if duration < DEFAULT_MIN_DURATION:
        return False, f"too short ({duration:.1f}s, minimum {DEFAULT_MIN_DURATION}s)"

    # 2. Minimum word count — must contain a real thought
    if len(words) < MIN_WORDS:
        return False, f"too few words ({len(words)}, minimum {MIN_WORDS})"

    # 3. Score threshold
    if clip["score"] < min_score:
        return False, f"score {clip['score']} below minimum {min_score}"

    # 4. Does not start mid-sentence
    MID_STARTERS = {"and", "but", "or", "so", "because", "which", "that", "then", "also"}
    if words[0].lower() in MID_STARTERS:
        return False, f"starts mid-sentence ('{words[0]}')"

    # 5. Must contain at least one sentence boundary (has a complete thought)
    has_sentence_end = any(c in text[:-1] for c in ".?!")
    if not has_sentence_end and len(words) < 35:
        return False, "no complete sentence found"

    return True, "ok"


# ── Segment combining ─────────────────────────────────────────────────────────

def _combine_segments(segments: List[Dict], window: int = 5) -> List[Dict]:
    """
    Merge consecutive transcript segments into overlapping windows.

    Window of 5 means each candidate covers ~5 Whisper segments.
    At typical speaking pace that gives 20–60 seconds of content per candidate —
    enough to find a complete thought instead of just a fragment.

    gap_before and gap_after track silence around each chunk,
    used to find clean cut points.
    """
    combined = []
    n = len(segments)

    for i in range(n):
        chunk = segments[i: i + window]

        gap_before = 0.0
        if i > 0:
            gap_before = max(0.0, chunk[0]["start"] - segments[i - 1]["end"])

        gap_after = 0.0
        last_in_chunk = i + len(chunk) - 1
        if last_in_chunk + 1 < n:
            gap_after = max(0.0, segments[last_in_chunk + 1]["start"] - chunk[-1]["end"])

        combined.append({
            "start":      chunk[0]["start"],
            "end":        chunk[-1]["end"],
            "text":       " ".join(s["text"].strip() for s in chunk),
            "gap_before": round(gap_before, 2),
            "gap_after":  round(gap_after, 2),
        })

    return combined


# ── Spacing and dedup filters ─────────────────────────────────────────────────

def _filter_spaced_clips(clips: List[Dict], min_gap: float = 20.0) -> List[Dict]:
    """Ensure clips are spread across the video, not clustered in one section."""
    filtered = []
    for clip in clips:
        if all(abs(clip["start"] - s["start"]) > min_gap for s in filtered):
            filtered.append(clip)
    return filtered


def _filter_duplicate_text(clips: List[Dict], similarity_threshold: float = 0.55) -> List[Dict]:
    """Remove clips that repeat substantially the same content."""
    unique = []
    for clip in clips:
        words = set(clip["text"].lower().split())
        is_duplicate = any(
            len(words & set(kept["text"].lower().split())) / max(len(words), 1) > similarity_threshold
            for kept in unique
        )
        if not is_duplicate:
            unique.append(clip)
    return unique


# ── Main entry point ──────────────────────────────────────────────────────────

def get_top_clips(
    whisper_result: dict,
    video_duration: float,
    mode: str = "balanced",
    top_n: int = None,
    max_clips: int = 25,
    window: int = None,
    platform: str = "general",
    content_type: str = "general",
) -> List[Dict]:
    """
    Full pipeline: combine → score → quality filter → sort → space → dedupe → top N.

    Key changes:
    - window now defaults to mode-specific value (larger = longer clips)
    - scoring is narrative-first not brevity-first
    - quality filter enforces 15s minimum and 20-word minimum
    - gap_after is now tracked and used in scoring

    Args:
        whisper_result: Output from faster-whisper transcription.
        video_duration:  Video length in seconds.
        mode:            'conservative', 'balanced', or 'aggressive'.
        top_n:           Manual clip count override. None = auto.
        max_clips:       Hard cap on output clips.
        window:          Segments to merge per candidate. None = use mode default.
        platform:        'tiktok', 'shorts', 'reels', 'facebook', 'general'.
        content_type:    'entertainment', 'educational', 'storytelling',
                         'commentary', 'promo', 'podcast', 'general'.
    """
    settings = MODE_SETTINGS.get(mode, MODE_SETTINGS["balanced"])
    segments = whisper_result["segments"]

    # Clip count target
    if top_n is not None:
        target = min(top_n, max_clips)
    else:
        target = calculate_target_clip_count(
            video_duration_seconds=video_duration,
            segments=segments,
            mode=mode,
            max_clips=max_clips,
        )

    # Window size: use mode default if not explicitly passed
    effective_window = window if window is not None else settings["window"]

    # For long-form videos, automatically increase window so clips cover
    # more content and are more likely to contain complete thoughts
    if video_duration > 600 and effective_window < 6:   # over 10 minutes
        effective_window = 6
    if video_duration > 1800 and effective_window < 7:  # over 30 minutes
        effective_window = 7

    candidates = _combine_segments(segments, window=effective_window)

    scored = []
    for c in candidates:
        duration = c["end"] - c["start"]
        seg_score = _score_segment(
            text=c["text"].strip(),
            duration=duration,
            gap_before=c["gap_before"],
            gap_after=c["gap_after"],
            platform=platform,
            content_type=content_type,
        )
        scored.append({
            "start":      round(c["start"], 2),
            "end":        round(c["end"], 2),
            "text":       c["text"].strip(),
            "gap_before": c["gap_before"],
            "gap_after":  c["gap_after"],
            "score":      seg_score + get_feedback_score(c["text"].strip()),
            "duration":   round(duration, 2),
        })

    # Quality gate
    min_score = settings["min_score"]
    passed = []
    rejected_reasons = {}
    for clip in scored:
        ok, reason = _passes_quality_filter(clip, min_score, platform)
        if ok:
            passed.append(clip)
        else:
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    if rejected_reasons:
        print(f"  Quality filter rejections: {rejected_reasons}")
    print(f"  {len(passed)} of {len(scored)} candidates passed quality filter.")

    passed.sort(key=lambda x: x["score"], reverse=True)
    spaced = _filter_spaced_clips(passed, min_gap=settings["min_gap"])
    unique = _filter_duplicate_text(spaced, similarity_threshold=settings["similarity_threshold"])
    result = unique[:target]

    if result:
        avg_dur = sum(c["duration"] for c in result) / len(result)
        print(
            f"  Final: {len(result)} clips selected. "
            f"Avg duration: {avg_dur:.1f}s. "
            f"Range: {min(c['duration'] for c in result):.1f}s – "
            f"{max(c['duration'] for c in result):.1f}s. "
            f"(mode={mode}, platform={platform}, content={content_type})"
        )
    else:
        print("  No clips passed all filters. Try 'aggressive' mode or check your video has enough speech.")

    return result
