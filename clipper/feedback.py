"""
clipper/feedback.py
Feedback loop — learns from thumbs up / thumbs down on clips.

How it works:
- Every clip shown to the user can receive a thumbs up or thumbs down
- Feedback is saved to feedback_data.json next to app.py
- Words and phrases from liked clips get a score boost next time
- Words from disliked clips get a score penalty next time
- The scorer reads these weights at startup and applies them automatically

This gives the app a memory — the more you use it, the smarter it gets
at finding clips that match your taste.
"""

import json
import os
from typing import Dict, List
from collections import Counter
from pathlib import Path

FEEDBACK_FILE = Path(__file__).parent.parent / "feedback_data.json"

# How much each thumbs up/down shifts word weights
LIKE_BOOST    =  2
DISLIKE_BOOST = -3

# Cap weights so one clip cannot dominate everything
MAX_WEIGHT =  15
MIN_WEIGHT = -10


def _load() -> Dict:
    """Load feedback data from disk. Returns empty structure if file missing."""
    if FEEDBACK_FILE.exists():
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"word_weights": {}, "total_likes": 0, "total_dislikes": 0, "clips_rated": 0}


def _save(data: Dict) -> None:
    """Save feedback data to disk."""
    try:
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save feedback: {e}")


def record_feedback(clip_text: str, liked: bool) -> None:
    """
    Record a thumbs up (liked=True) or thumbs down (liked=False) for a clip.
    Extracts words from the clip text and adjusts their weights accordingly.
    """
    data = _load()
    words = [w.lower().strip(".,!?\"'") for w in clip_text.split() if len(w) > 3]

    boost = LIKE_BOOST if liked else DISLIKE_BOOST

    for word in words:
        current = data["word_weights"].get(word, 0)
        new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, current + boost))
        data["word_weights"][word] = new_weight

    if liked:
        data["total_likes"] += 1
    else:
        data["total_dislikes"] += 1
    data["clips_rated"] += 1

    _save(data)


def get_feedback_score(text: str) -> int:
    """
    Calculate a feedback-adjusted bonus score for a piece of text.
    Called by the scorer to add learned preferences on top of base scoring.

    Returns a positive or negative integer.
    """
    data = _load()
    if not data["word_weights"]:
        return 0

    words = [w.lower().strip(".,!?\"'") for w in text.split() if len(w) > 3]
    if not words:
        return 0

    total = sum(data["word_weights"].get(word, 0) for word in words)
    # Normalise by word count so longer clips don't automatically win
    return round(total / max(len(words), 1))


def get_feedback_stats() -> Dict:
    """Return summary stats for display in the UI."""
    data = _load()
    top_liked = sorted(
        [(w, s) for w, s in data["word_weights"].items() if s > 0],
        key=lambda x: x[1], reverse=True
    )[:8]
    top_disliked = sorted(
        [(w, s) for w, s in data["word_weights"].items() if s < 0],
        key=lambda x: x[1]
    )[:8]
    return {
        "total_likes":    data["total_likes"],
        "total_dislikes": data["total_dislikes"],
        "clips_rated":    data["clips_rated"],
        "top_liked":      top_liked,
        "top_disliked":   top_disliked,
        "words_learned":  len(data["word_weights"]),
    }


def reset_feedback() -> None:
    """Wipe all learned feedback. Use with caution."""
    _save({"word_weights": {}, "total_likes": 0, "total_dislikes": 0, "clips_rated": 0})
