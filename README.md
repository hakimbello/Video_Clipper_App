# 🎬 Video Clipper

Automatically transcribes a video, finds the best short clips, and exports them
with burned-in captions (top headline + bottom transcript text) in vertical format
(1080×1920 — perfect for TikTok / Reels / Shorts).

---

## What This Does

1. You upload a video
2. Whisper (free, runs locally) transcribes the speech
3. The scorer picks the best short moments based on punchiness and hook words
4. ffmpeg cuts each clip, crops to vertical, and burns in your headline + captions
5. You download the finished clips

---

## Requirements

You need these installed on your computer:

### 1. Python 3.9 or 3.10
Download from: https://www.python.org/downloads/

### 2. ffmpeg (the actual program, not just the Python package)
- **Mac:** `brew install ffmpeg`
- **Windows:** Download from https://ffmpeg.org/download.html, then add to PATH
- **Linux:** `sudo apt install ffmpeg`

To check it works: open a terminal and type `ffmpeg -version`

---

## Setup (Do This Once)

```bash
# 1. Create a virtual environment (keeps this project's packages separate)
python -m venv venv

# 2. Activate it
# Mac/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 3. Install Python packages
pip install -r requirements.txt
```

---

## Run the Web App

```bash
streamlit run app.py
```

This opens a browser tab at http://localhost:8501
Upload your video, choose how many clips, click the button, download your clips.

---

## Run From the Command Line (No Browser)

```bash
python run_cli.py path/to/your/video.mp4
python run_cli.py video.mp4 --clips 3
python run_cli.py video.mp4 --model small  # better accuracy, slower
```

Output goes to an `output/` folder.

---

## Whisper Model Sizes

| Model  | Speed  | Accuracy | RAM needed |
|--------|--------|----------|------------|
| tiny   | Fast   | OK       | ~1 GB      |
| base   | Good   | Good     | ~1 GB      |
| small  | Slower | Better   | ~2 GB      |
| medium | Slow   | Great    | ~5 GB      |

`base` is the default and works well for most videos.

---

## Folder Structure

```
video_clipper_app/
├── app.py              ← Streamlit web UI
├── run_cli.py          ← Command line version
├── requirements.txt    ← Python packages needed
├── README.md           ← This file
└── clipper/
    ├── __init__.py
    ├── transcriber.py  ← Whisper transcription
    ├── scorer.py       ← Clip selection logic
    └── exporter.py     ← ffmpeg clip cutting + caption burning
```

---

## Customising the Scoring

Open `clipper/scorer.py` and edit:

- `HOOK_PHRASES` — words that boost a clip's score
- `BONUS_PHRASES` — phrases that get extra score
- `_score_segment()` — the full scoring logic (easy to read)

---

## Deploying Online (Free Options)

### Option 1: Streamlit Community Cloud (Easiest, Free)
- Push this folder to a GitHub repo
- Go to https://share.streamlit.io
- Connect your repo and deploy
- **Limitation:** Free tier has limited compute. Video processing may time out for long videos.
  Works fine for clips under ~5 minutes.

### Option 2: Railway.app (~$5/month)
- More reliable than free Streamlit Cloud
- Handles longer videos
- Go to https://railway.app, connect GitHub repo, deploy

### Option 3: Run Locally Always (Zero Cost)
- Just use `streamlit run app.py` on your own machine
- Most reliable option — no upload limits, no timeouts
- You can access it from any device on your home network at http://YOUR_IP:8501

---

## Troubleshooting

**"ffmpeg not found"**
→ ffmpeg isn't installed or isn't in your PATH. See setup above.

**"No module named whisper"**
→ Run `pip install openai-whisper` inside your venv.

**Clip exports but no audio**
→ The source video codec might not copy cleanly. Change `-c:a copy` to `-c:a aac` in exporter.py.

**Captions look wrong / don't show**
→ Make sure ffmpeg was compiled with libass support. The brew/apt versions usually are.
