#!/usr/bin/env python3
"""
cleancut — Automatically remove pauses and filler words from any video file.

Usage:
    python3 cleancut.py <video_path> [options]

Examples:
    python3 cleancut.py lecture.mp4
    python3 cleancut.py interview.mp4 --model medium --pause-threshold 0.4
    python3 cleancut.py talk.mp4 --output talk_final.mp4 --extra-fillers like so basically
"""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

# ── Defaults (override via CLI flags) ────────────────────────────
WHISPER_MODEL    = "small"   # tiny | base | small | medium | large
PAUSE_THRESHOLD  = 0.5       # seconds: gaps longer than this are removed
MIN_SEGMENT      = 0.1       # seconds: keep segments shorter than this are dropped
FILLER_PADDING   = 0.15      # seconds of surrounding silence absorbed with a filler word

# Only pure phonetic hesitations — words that are never meaningful in English.
# Words like "like", "so", "basically", "right", "okay" are intentionally excluded
# because they can carry real meaning depending on context.
DEFAULT_FILLERS = {
    "um", "uh", "uhh", "umm",
    "hmm", "hm", "mm",
    "er", "erm",
    "mhm", "uh-huh",
}
# ─────────────────────────────────────────────────────────────────


def ensure_whisper() -> None:
    try:
        import whisper  # noqa: F401
        print("[OK] openai-whisper is installed.")
    except ImportError:
        print("[INFO] openai-whisper not found — installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "openai-whisper"],
            check=True,
        )
        importlib.invalidate_caches()
        print("[OK] openai-whisper installed.")


def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def extract_audio(video_path: str, tmp_dir: str) -> str:
    wav_path = os.path.join(tmp_dir, "audio_16k.wav")
    print("[1/6] Extracting audio...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            wav_path,
        ],
        check=True, capture_output=True,
    )
    return wav_path


def transcribe(wav_path: str, model_name: str, language: str) -> list[dict]:
    import whisper

    print(f"[2/6] Loading Whisper '{model_name}' model...")
    if model_name in ("medium", "large"):
        print("      (First run will download the model weights — this may take a minute)")

    device = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        pass

    model = whisper.load_model(model_name, device=device)
    lang_arg = {"language": language} if language else {}

    print(f"      Transcribing on {device}. This may take a few minutes...")
    result = model.transcribe(
        wav_path,
        word_timestamps=True,
        fp16=(device == "cuda"),
        verbose=False,
        **lang_arg,
    )

    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            words.append({
                "word":  w["word"].strip().lower(),
                "start": float(w["start"]),
                "end":   float(w["end"]),
            })

    print(f"      Done — {len(words)} words in {len(result['segments'])} segments.")
    return words


def build_cut_regions(
    words: list[dict],
    video_duration: float,
    pause_threshold: float,
    filler_words: set[str],
    filler_padding: float,
) -> list[list[float]]:
    cuts: list[list[float]] = []

    if not words:
        return []

    # Leading silence
    if words[0]["start"] > pause_threshold:
        cuts.append([0.0, words[0]["start"]])

    # Trailing silence
    if words[-1]["end"] < video_duration - pause_threshold:
        cuts.append([words[-1]["end"], video_duration])

    # Inter-word pauses
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > pause_threshold:
            cuts.append([words[i]["end"], words[i + 1]["start"]])

    # Filler words (absorb adjacent short silences)
    for i, w in enumerate(words):
        if w["word"] in filler_words:
            cut_start = w["start"]
            cut_end   = w["end"]

            if i > 0:
                prev_gap = w["start"] - words[i - 1]["end"]
                if 0 < prev_gap <= filler_padding:
                    cut_start = words[i - 1]["end"]

            if i < len(words) - 1:
                next_gap = words[i + 1]["start"] - w["end"]
                if 0 < next_gap <= filler_padding:
                    cut_end = words[i + 1]["start"]

            cuts.append([cut_start, cut_end])

    return cuts


def merge_cuts_to_keep_ranges(
    cuts: list[list[float]],
    video_duration: float,
    min_segment: float,
) -> list[list[float]]:
    if not cuts:
        return [[0.0, video_duration]]

    sorted_cuts = sorted(cuts, key=lambda r: r[0])

    merged: list[list[float]] = [sorted_cuts[0][:]]
    for start, end in sorted_cuts[1:]:
        prev = merged[-1]
        if start <= prev[1]:
            prev[1] = max(prev[1], end)
        else:
            merged.append([start, end])

    keep: list[list[float]] = []
    cursor = 0.0
    for cut_start, cut_end in merged:
        if cut_start > cursor and (cut_start - cursor) >= min_segment:
            keep.append([cursor, cut_start])
        cursor = cut_end

    if cursor < video_duration and (video_duration - cursor) >= min_segment:
        keep.append([cursor, video_duration])

    return keep


def write_filter_script(keep_ranges: list[list[float]], path: str) -> None:
    n = len(keep_ranges)
    lines = []
    for i, (s, e) in enumerate(keep_ranges):
        lines.append(f"[0:v]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[v{i}];")
        lines.append(f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[a{i}];")
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    lines.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def run_ffmpeg(video_path: str, filter_script: str, output_path: str, crf: int, preset: str) -> None:
    n_lines = open(filter_script).read().count("\n") + 1
    print(f"[5/6] Assembling {n_lines // 2} segments with ffmpeg...")
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex_script", filter_script,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("[ERROR] ffmpeg failed:")
        print(result.stderr[-3000:])
        raise RuntimeError("ffmpeg encoding failed")


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove pauses and filler words from any video file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 cleancut.py lecture.mp4
  python3 cleancut.py talk.mp4 --model medium --pause-threshold 0.4
  python3 cleancut.py interview.mp4 --output interview_final.mp4
  python3 cleancut.py webinar.mp4 --extra-fillers like so basically --language en
        """,
    )
    parser.add_argument("video_path", help="Path to the input video file")
    parser.add_argument(
        "--model", default=WHISPER_MODEL,
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size. Larger = more accurate, slower (default: small)",
    )
    parser.add_argument(
        "--pause-threshold", type=float, default=PAUSE_THRESHOLD, metavar="SECONDS",
        help="Remove pauses longer than this many seconds (default: 0.5)",
    )
    parser.add_argument(
        "--extra-fillers", nargs="+", default=[], metavar="WORD",
        help="Additional words to treat as fillers, e.g. --extra-fillers like so basically",
    )
    parser.add_argument(
        "--language", default="en", metavar="CODE",
        help="Language code for transcription, e.g. en, es, fr (default: en)",
    )
    parser.add_argument(
        "--crf", type=int, default=18, metavar="N",
        help="Video quality (0=lossless, 51=worst). Lower = better quality (default: 18)",
    )
    parser.add_argument(
        "--preset", default="fast",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"],
        help="Encoding speed preset — slower = smaller file (default: fast)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: <input>_cleaned.mp4)",
    )
    args = parser.parse_args()

    video_path = os.path.abspath(args.video_path)
    if not os.path.exists(video_path):
        sys.exit(f"[ERROR] File not found: {video_path}")

    output_path = args.output or (
        os.path.splitext(video_path)[0] + "_cleaned" + os.path.splitext(video_path)[1]
    )

    filler_words = DEFAULT_FILLERS | {w.lower() for w in args.extra_fillers}

    print(f"\n{'='*50}")
    print(f"  cleancut")
    print(f"{'='*50}")
    print(f"  Input : {video_path}")
    print(f"  Output: {output_path}")
    print(f"  Model : {args.model}  |  Pause: >{args.pause_threshold}s")
    if args.extra_fillers:
        print(f"  Extra fillers: {', '.join(args.extra_fillers)}")
    print(f"{'='*50}\n")

    video_duration = get_video_duration(video_path)
    print(f"[INFO] Duration: {fmt_duration(video_duration)} ({video_duration:.1f}s)\n")

    tmp_dir = tempfile.mkdtemp(prefix="cleancut_")
    try:
        ensure_whisper()
        wav_path    = extract_audio(video_path, tmp_dir)
        words       = transcribe(wav_path, args.model, args.language)

        print("[3/6] Detecting cuts...")
        cuts        = build_cut_regions(words, video_duration, args.pause_threshold,
                                        filler_words, FILLER_PADDING)
        keep_ranges = merge_cuts_to_keep_ranges(cuts, video_duration, MIN_SEGMENT)

        if not keep_ranges:
            sys.exit("[ERROR] Nothing to keep after cuts — try a higher --pause-threshold.")

        total_kept = sum(e - s for s, e in keep_ranges)
        total_cut  = video_duration - total_kept
        print(f"      Keeping {len(keep_ranges)} segments  ({fmt_duration(total_kept)})")
        print(f"      Removing {fmt_duration(total_cut)} ({100 * total_cut / video_duration:.1f}% of video)\n")

        filter_script = os.path.join(tmp_dir, "filter_complex.txt")
        print("[4/6] Writing filter graph...")
        write_filter_script(keep_ranges, filter_script)

        run_ffmpeg(video_path, filter_script, output_path, args.crf, args.preset)

    finally:
        print("[6/6] Cleaning up...")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    size_mb = os.path.getsize(output_path) / 1_000_000
    print(f"\n{'='*50}")
    print(f"  Done!")
    print(f"  Output : {output_path} ({size_mb:.1f} MB)")
    print(f"  Removed: {fmt_duration(total_cut)} ({100 * total_cut / video_duration:.1f}% shorter)")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
