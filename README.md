# cleancut

Automatically remove awkward pauses and filler words from any video file — no manual editing required.

`cleancut` transcribes your video with [OpenAI Whisper](https://github.com/openai/whisper), detects silence gaps and phonetic fillers, and uses `ffmpeg` to splice out those moments in a single re-encode pass. The result is a tighter, more professional video with no dead air.

---

## Demo

```
Input  → lecture.mp4        (45:12)
Output → lecture_cleaned.mp4 (41:38)
Removed: 3:34 (7.9% shorter)
```

---

## Features

- **Pause removal** — cuts silence gaps longer than a configurable threshold (default: 0.5 s)
- **Filler word removal** — removes phonetic hesitations (`um`, `uh`, `hmm`, `er`, etc.) along with their surrounding micro-silences
- **Context-aware** — only removes unambiguous fillers; words like *like*, *so*, *basically*, *right* are preserved by default because they carry real meaning in sentences
- **Extensible** — add your own filler words via `--extra-fillers`
- **No API key needed** — Whisper runs entirely on your machine
- **Any language** — pass `--language fr`, `--language es`, etc.
- **Quality control** — tunable CRF and encoding preset

---

## Requirements

| Dependency | Install |
|---|---|
| Python 3.9+ | [python.org](https://www.python.org) |
| ffmpeg | `brew install ffmpeg` (macOS) · `apt install ffmpeg` (Linux) · [ffmpeg.org](https://ffmpeg.org) (Windows) |
| openai-whisper | Auto-installed on first run, or `pip install openai-whisper` |

No OpenAI account or API credits required — Whisper runs locally.

---

## Installation

```bash
git clone https://github.com/DoomDust7/cleancut.git
cd cleancut
# ffmpeg must be installed separately (see Requirements above)
# openai-whisper is installed automatically on first run
```

---

## Usage

```bash
python3 cleancut.py <video_file> [options]
```

### Basic

```bash
# Removes pauses > 0.5s and default filler words (um, uh, hmm, er, …)
python3 cleancut.py lecture.mp4
# Output: lecture_cleaned.mp4
```

### Custom output path

```bash
python3 cleancut.py interview.mp4 --output interview_final.mp4
```

### Stricter pause cutting

```bash
# Cut pauses longer than 0.3 seconds instead of 0.5
python3 cleancut.py talk.mp4 --pause-threshold 0.3
```

### More accurate transcription

```bash
# Use the medium model for better filler word detection (slower, ~1.5 GB)
python3 cleancut.py webinar.mp4 --model medium
```

### Add your own filler words

```bash
# Also remove "like", "so", and "basically" (in addition to defaults)
python3 cleancut.py podcast.mp4 --extra-fillers like so basically
```

### Non-English video

```bash
python3 cleancut.py conference.mp4 --language es   # Spanish
python3 cleancut.py lecture.mp4 --language fr       # French
```

### All options

```
positional arguments:
  video_path            Path to the input video file

options:
  --model {tiny,base,small,medium,large}
                        Whisper model size. Larger = more accurate, slower (default: small)
  --pause-threshold SECONDS
                        Remove pauses longer than this many seconds (default: 0.5)
  --extra-fillers WORD [WORD ...]
                        Additional words to remove, e.g. --extra-fillers like so basically
  --language CODE       Language code for transcription, e.g. en, es, fr (default: en)
  --crf N               Video quality 0–51, lower = better (default: 18)
  --preset {ultrafast,superfast,veryfast,faster,fast,medium,slow}
                        Encoding speed/size tradeoff (default: fast)
  --output PATH         Output file path (default: <input>_cleaned.<ext>)
```

---

## How It Works

```
Input video
    │
    ▼
ffmpeg — extract 16 kHz mono WAV
    │
    ▼
Whisper — transcribe with word-level timestamps
    │
    ▼
Detect cut regions
    ├── Pauses: gaps between words > threshold
    └── Fillers: phonetic hesitation words + surrounding silence
    │
    ▼
Merge overlapping cuts → invert to keep-ranges
    │
    ▼
ffmpeg filter_complex — trim + concat all keep segments
    │
    ▼
Output video (re-encoded, frame-accurate cuts)
```

**Why re-encode?** Stream copying (`-c copy`) can only cut at keyframe boundaries (every ~2 seconds), leaving up to 2 seconds of unwanted content at each cut point. Re-encoding with `libx264` gives frame-accurate cuts.

---

## Whisper Model Guide

| Model | Size | Speed (CPU) | Accuracy |
|---|---|---|---|
| `tiny` | 75 MB | Very fast | Low |
| `base` | 145 MB | Fast | OK |
| `small` | 244 MB | Moderate | Good ✓ |
| `medium` | 1.5 GB | Slow | Better |
| `large` | 3 GB | Very slow | Best |

`small` is the recommended default — good accuracy for most clear speech, fast enough for real use.

---

## Default Filler Words

The following words are removed by default. They were chosen because they are **never meaningful** in English — they are purely phonetic hesitations:

```
um  uh  uhh  umm  hmm  hm  mm  er  erm  mhm  uh-huh
```

Words like `like`, `so`, `basically`, `right`, `okay`, `well` are **not** removed by default because they often carry real meaning. Use `--extra-fillers` to add them if your speech patterns use them as fillers.

---

## Tips

- **Lectures / screencasts** — default settings work well
- **Podcasts / interviews** — try `--pause-threshold 0.3` for tighter pacing
- **Presentations** — use `--model medium` for better accuracy on technical vocabulary
- **Non-native speakers** — use `--model medium` or `--model large` for improved filler detection
- **Fast talkers** — increase `--pause-threshold` to `0.7` to avoid cutting natural breath pauses

---

## License

MIT
