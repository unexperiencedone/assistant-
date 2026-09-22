"""Cutting, captioning and reframing video, locally, on the CPU.

This laptop has no discrete GPU, so everything here encodes with libx264 on the
processor. That shapes the design more than anything else:

- **Render once.** A cut list is data until the last moment; chaining three renders to
  produce one file costs three encodes and three generations of quality loss.
- **Copy rather than encode where it is legal.** A cut on keyframe boundaries can be a
  stream copy, which is instant. `trim(..., precise=False)` is that path.
- **Expect minutes, not seconds.** A ten-minute 1080p render is a few minutes of fan
  noise here. Anything that pretends otherwise is lying to the user.

Aspect ratios are the other half. A 16:9 screen recording posted to a vertical feed gets
letterboxed into a phone-shaped frame rather than cropped blindly, because cropping the
middle of a screen recording throws away the part people need to read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import inbox

# name -> (width, height). What each platform actually wants.
FORMATS = {
    "vertical": (1080, 1920),    # Reels, Stories, Shorts
    "square": (1080, 1080),      # feed
    "landscape": (1920, 1080),   # YouTube, LinkedIn video
}

FONT = "C:/Windows/Fonts/segoeui.ttf"
RENDER_TIMEOUT = 3600


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: Path) -> dict[str, Any]:
    """Duration, size and frame rate, or an empty dict if it is not really a video."""
    if shutil.which("ffprobe") is None:
        return {}
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or 0)
    return {"width": stream.get("width", 0), "height": stream.get("height", 0),
            "duration": round(duration, 2), "fps": stream.get("r_frame_rate", "")}


def _filter_path(path: str) -> str:
    """A Windows path as ffmpeg's filter parser wants it.

    Inside a filter graph, `:` separates options, so the colon in `C:/...` has to be
    escaped or ffmpeg reads the font path as three broken arguments and says only
    "Invalid argument". Backslashes become forward slashes for the same reason.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def _run(args: list[str]) -> tuple[bool, str]:
    if not available():
        return False, "I don't have ffmpeg and ffprobe here, so I can't edit video."
    try:
        done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                              capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "The render ran past an hour, so I stopped it."
    except OSError as error:
        return False, f"I couldn't run ffmpeg: {error}"
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        return False, f"ffmpeg refused: {detail[-1] if detail else 'no reason given'}"
    return True, ""


def trim(source: Path, base: Path, start: float = 0.0, end: float | None = None,
         precise: bool = True) -> tuple[bool, str, Path | None]:
    """Cut one span out. `precise=False` copies the streams: instant, but snaps to keyframes."""
    out = inbox.new_path(base, f"{Path(source).stem}-cut", Path(source).suffix or ".mp4")
    args = ["-ss", f"{start:.3f}"]
    if end is not None and end > start:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(source)]
    args += ["-c", "copy"] if not precise else ["-c:v", "libx264", "-preset", "medium",
                                                "-crf", "20", "-c:a", "aac"]
    ok, detail = _run([*args, str(out)])
    return (True, f"Cut to {inbox.describe(out)}.", out) if ok else (False, detail, None)


def caption(source: Path, base: Path, text: str, size: int = 48,
            position: str = "bottom") -> tuple[bool, str, Path | None]:
    """Burn one line of text into the picture. Burnt in, so it survives every re-upload."""
    out = inbox.new_path(base, f"{Path(source).stem}-captioned", ".mp4")
    y = {"top": "h*0.08", "middle": "(h-text_h)/2"}.get(position, "h*0.82")
    safe = text.replace("\\", "").replace(":", "\\:").replace("'", "")
    draw = (f"drawtext=fontfile='{_filter_path(FONT)}':text='{safe}':fontcolor=white:fontsize={size}"
            f":box=1:boxcolor=black@0.5:boxborderw=18:x=(w-text_w)/2:y={y}")
    ok, detail = _run(["-i", str(source), "-vf", draw, "-c:v", "libx264", "-preset", "medium",
                       "-crf", "20", "-c:a", "copy", str(out)])
    return (True, f"Captioned: {inbox.describe(out)}.", out) if ok else (False, detail, None)


def reframe(source: Path, base: Path, shape: str = "vertical",
            blur_background: bool = True) -> tuple[bool, str, Path | None]:
    """Fit the picture into a platform's frame without cropping away the content.

    The video is scaled to fit and centred; the gap is filled with a blurred, enlarged
    copy of the same frame, which reads as deliberate rather than as two black bars.
    """
    if shape not in FORMATS:
        return False, f"I don't know the {shape} shape. I know vertical, square and landscape.", None
    width, height = FORMATS[shape]
    out = inbox.new_path(base, f"{Path(source).stem}-{shape}", ".mp4")
    if blur_background:
        chain = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height},gblur=sigma=24[bg];"
                 f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        chain = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    ok, detail = _run(["-i", str(source), "-filter_complex", chain, "-c:v", "libx264",
                       "-preset", "medium", "-crf", "21", "-c:a", "copy", str(out)])
    return (True, f"Reframed to {shape}: {inbox.describe(out)}.", out) if ok else (False, detail, None)


def thumbnail(source: Path, base: Path, at: float = 1.0) -> tuple[bool, str, Path | None]:
    out = inbox.new_path(base, f"{Path(source).stem}-frame", ".jpg")
    ok, detail = _run(["-ss", f"{at:.3f}", "-i", str(source), "-frames:v", "1", str(out)])
    return (True, f"Grabbed a frame: {inbox.describe(out)}.", out) if ok else (False, detail, None)


def spoken_probe(path: Path) -> str:
    """What a file is, said out loud, without reading a path aloud."""
    info = probe(path)
    if not info or not info.get("duration"):
        return f"{Path(path).name} isn't something I can read as video."
    minutes, seconds = divmod(int(info["duration"]), 60)
    length = f"{minutes} minutes {seconds} seconds" if minutes else f"{seconds} seconds"
    return f"{Path(path).name}: {length}, {info['width']} by {info['height']}."
