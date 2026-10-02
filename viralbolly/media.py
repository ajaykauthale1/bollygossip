"""ffmpeg helpers and video downloading."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


_ffmpeg_override: str | None = None


def set_ffmpeg(path: str | None) -> None:
    """Use a specific ffmpeg: either the executable or the folder containing it
    (e.g. C:\\ffmpeg\\bin). Overrides FFMPEG_PATH and PATH."""
    global _ffmpeg_override
    _ffmpeg_override = path
    ffmpeg_exe.cache_clear()


def _resolve(path: str) -> str | None:
    p = Path(path).expanduser()
    if p.is_dir():
        for name in ("ffmpeg.exe", "ffmpeg"):
            if (p / name).is_file():
                return str(p / name)
        return None
    return str(p) if p.is_file() else None


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """Order: --ffmpeg / config.json, FFMPEG_PATH env var, PATH, C:\\ffmpeg\\bin, imageio-ffmpeg."""
    for configured in (_ffmpeg_override, os.environ.get("FFMPEG_PATH")):
        if configured:
            exe = _resolve(configured)
            if not exe:
                raise SystemExit(f"ffmpeg not found at {configured!r} (give the ffmpeg executable or its folder)")
            return exe
    exe = shutil.which("ffmpeg") or _resolve(r"C:\ffmpeg\bin")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit("ffmpeg not found: set \"ffmpeg\" in config.json, set FFMPEG_PATH, "
                         "pass --ffmpeg, or put ffmpeg on PATH")
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    for attempt in range(3):
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return
        # Out of memory happens when a big model (e.g. the voice server) is loaded: wait and retry.
        if "Cannot allocate memory" in proc.stderr and attempt < 2:
            log.warning("ffmpeg ran out of memory; retrying in 10s")
            time.sleep(10)
            continue
        break
    raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")


def probe(path: str | Path) -> dict:
    """Duration and whether an audio stream exists, parsed from `ffmpeg -i`
    (the bundled ffmpeg ships without ffprobe)."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    info = proc.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    dims = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", info)
    return {"duration": duration, "has_audio": "Audio:" in info, "has_video": "Video:" in info,
            "width": int(dims[1]) if dims else 0, "height": int(dims[2]) if dims else 0}


def download_video(url: str, dest_dir: Path, post_id: str, max_seconds: int,
                   block_uploaders: Iterable[str] = (), allow_verified: bool = False,
                   meta: dict | None = None) -> Path | None:
    """Download with yt-dlp. Returns None if the link has no downloadable video, or if
    it comes from a company/media account (see viralbolly/sources.py)."""
    import yt_dlp

    from .sources import blocked_uploader

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(dest_dir / f"{post_id}.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_exe(),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # Skip very long videos outright; we only ever use the first max_seconds.
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <=? {max_seconds * 20}"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return None
            reason = blocked_uploader(info, block_uploaders, allow_verified)
            if reason:
                log.info("Skipping video %s: %s", url, reason)
                return None
            if meta is not None:
                meta["uploader"] = info.get("channel") or info.get("uploader") or ""
            info = ydl.process_ie_result(info, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if not path.exists():  # merged output may keep another extension
                matches = sorted(dest_dir.glob(f"{post_id}.*"))
                path = matches[0] if matches else None
            if path and probe(path)["has_video"]:
                return path
    except Exception as e:  # noqa: BLE001 - any failure means "treat as a text post"
        log.warning("Video download failed for %s: %s", url, e)
    return None


def loudness_curve(path: str | Path) -> list[tuple[float, float]]:
    """(time, energy) per ~0.5s of audio, used to find the liveliest part of a clip."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         "aresample=16000,asetnsamples=n=8000,astats=metadata=1:reset=1,"
         "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    curve, t = [], None
    for line in proc.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m[1])
            continue
        m = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
        if m and t is not None:
            db = -120.0 if m[1] == "-inf" else float(m[1])
            curve.append((t, 10 ** (db / 10)))
    return curve


def best_window(path: str | Path, seconds: float) -> float:
    """Start time of the `seconds`-long window with the most audio energy (laughs,
    shouting, crowd noise, the punchline). 0 if the clip is short or silent."""
    info = probe(path)
    if info["duration"] <= seconds + 1 or not info["has_audio"]:
        return 0.0
    curve = loudness_curve(path)
    if len(curve) < 4:
        return 0.0
    step = curve[1][0] - curve[0][0] or 0.5
    n = max(1, int(seconds / step))
    best, best_i, acc = -1.0, 0, sum(e for _, e in curve[:n])
    for i in range(0, len(curve) - n + 1):
        if i:
            acc += curve[i + n - 1][1] - curve[i - 1][1]
        # Skip windows that run into the last few seconds (channel outros, end screens).
        if acc > best and (curve[i + n - 1][0] <= info["duration"] - 6 or best < 0):
            best, best_i = acc, i
    # Start a beat early so the moment has a lead-in.
    return max(0.0, min(curve[best_i][0] - 1.0, info["duration"] - seconds))
