"""
clipper/transcriber.py
Transcribes video audio using faster-whisper.
faster-whisper is 4x faster than standard whisper on the same hardware,
with identical accuracy. It's a drop-in replacement.

Install once:
    pip install faster-whisper
"""

from faster_whisper import WhisperModel

# Cache the model so it only loads once per session
_model = None


def _get_model(model_size: str = "base"):
    global _model
    if _model is None:
        print(f"Loading faster-whisper model ({model_size})...")
        # device="cpu" works on all computers
        # compute_type="int8" is the fastest option on CPU — no quality loss
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print("Model loaded.")
    return _model


def transcribe_video(video_path: str, model_size: str = "base") -> dict:
    """
    Transcribe a video file using faster-whisper.

    Returns a dict that matches the openai-whisper output format exactly,
    so the rest of the app (scorer, exporter) works without any changes.

    Dict keys:
        text     — full transcript as one string
        segments — list of segment dicts, each with a 'words' list
        language — detected language code e.g. 'en'
    """
    model = _get_model(model_size)
    print(f"Transcribing: {video_path}")

    # word_timestamps=True gives per-word start/end times
    # needed for the Opus-style word-by-word caption highlighting
    segments_generator, info = model.transcribe(
        video_path,
        word_timestamps=True,
        beam_size=5,
    )

    # faster-whisper returns a generator — we consume it fully here
    # and reshape into the same dict format openai-whisper uses
    segments = []
    full_text_parts = []

    for seg in segments_generator:
        words = []
        for w in (seg.words or []):
            words.append({
                "word":  w.word,
                "start": w.start,
                "end":   w.end,
            })

        segments.append({
            "id":    seg.id,
            "start": seg.start,
            "end":   seg.end,
            "text":  seg.text.strip(),
            "words": words,
        })
        full_text_parts.append(seg.text.strip())

    result = {
        "text":     " ".join(full_text_parts),
        "segments": segments,
        "language": info.language,
    }

    print(f"Transcription done. Language: {info.language}. Segments: {len(segments)}")
    return result
