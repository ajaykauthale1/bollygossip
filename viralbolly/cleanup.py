"""Strip the outlet's own overlay bands from a clip before it goes into a short.

News channels (Pinkvilla especially) post vertical clips with a white title card
burned in at the top and sometimes a white credit strip at the bottom. Under our
own title band and credit badge that reads as two titles fighting each other, so
with `--clean-overlays` those bands are cropped off and the frame around the
cropped clip is filled with a blurred, zoomed copy of it (or a dark strip).

Detection: sample frames across the clip and look for rows at the top/bottom
edge that never change (static) and are mostly one flat near-white or near-black
colour - a burned-in card or letterbox, never real footage. Channels can also
get a fixed crop (`clean_overlay_channels` in config.json), used when detection
can't run.
"""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from pathlib import Path

from .media import ffmpeg_exe, probe

log = logging.getLogger(__name__)

# Fractions of the frame height, used only when a clip from this channel can't be analysed.
CHANNEL_DEFAULTS = {"pinkvilla": {"top": 0.19, "bottom": 0.0}}
# Set from the command line / config.json by the pipeline.
SETTINGS = {"on": False, "fill": "blur", "channels": CHANNEL_DEFAULTS}

SAMPLES = 16
MAX_BAND = 0.35      # never crop more than this much from one side
MIN_BAND = 0.03      # smaller "bands" are noise (or the punch-in zoom already hides them)
STATIC = 5.0         # mean per-pixel change (0-255) below which a row counts as unchanging
FLAT = 0.55          # share of a row that must be the band colour
MARGIN = 0.006       # extra crop past the band edge, for the soft anti-aliased line


def _frames(clip: Path, w: int = 96):
    import numpy as np

    info = probe(clip)
    if not info["width"] or not info["height"] or not info["duration"]:
        return None
    if info["width"] > info["height"]:
        return None  # landscape clips are letterboxed by us anyway; outlet cards sit on vertical uploads
    h = max(64, int(round(info["height"] / info["width"] * w / 2)) * 2)
    dur = info["duration"]
    # Skip the first and last moments: intros and the closing disclaimer/end-screen.
    a, span = dur * 0.04, dur * 0.86
    frames = []
    for i in range(SAMPLES):  # one fast seek per frame: decoding the whole AV1 clip is far slower
        raw = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{a + span * i / (SAMPLES - 1):.2f}",
             "-i", str(clip), "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
            capture_output=True).stdout
        if len(raw) == w * h:
            frames.append(np.frombuffer(raw, dtype=np.uint8).reshape(h, w))
    if len(frames) < 4:
        return None
    return np.stack(frames).astype(np.float32)


def _band(rows_ok, h: int) -> int:
    """Length of the run of acceptable rows starting at the edge (index 0), allowing
    short gaps (a line of heavy title text) inside the band."""
    gap_allow = max(2, int(h * 0.025))
    end, gap = 0, 0
    for i, ok in enumerate(rows_ok[: int(h * MAX_BAND)]):
        if ok:
            end, gap = i + 1, 0
        else:
            gap += 1
            if gap > gap_allow or end == 0:
                break
    return end


def _landscape(clip: str) -> bool:
    info = probe(clip)
    return info["width"] > info["height"] > 0


@lru_cache(maxsize=256)
def detect(clip: str) -> tuple[float, float] | None:
    """(top, bottom) overlay bands as fractions of the frame height; None if the clip
    couldn't be analysed."""
    import numpy as np

    try:
        f = _frames(Path(clip))
    except Exception as e:  # noqa: BLE001
        log.info("Overlay check failed for %s: %s", clip, e)
        return None
    if f is None:
        return (0.0, 0.0) if _landscape(clip) else None
    _, h, _ = f.shape
    med = np.median(f, axis=0)
    motion = np.abs(f - med).mean(axis=(0, 2))           # per row
    mode = np.median(med, axis=1, keepdims=True)
    flat = (np.abs(med - mode) < 16).mean(axis=1)        # share of the row in its dominant colour
    level = mode[:, 0]
    white = (level > 200) & (motion < STATIC) & (flat > FLAT)       # title card (text on white)
    black = (level < 28) & (motion < 0.8) & (flat > 0.97)           # letterbox: dead still, unlike dark footage
    ok = white | black

    def side(rows):
        n = _band(rows, h)
        return min(MAX_BAND, n / h + MARGIN) if n / h >= MIN_BAND else 0.0

    return side(ok), side(ok[::-1])


def crop_for(clip: Path, source: str | None) -> tuple[float, float]:
    """The (top, bottom) fractions to crop off this clip; (0, 0) when cleanup is off."""
    if not SETTINGS["on"]:
        return 0.0, 0.0
    found = detect(str(clip))
    if found is None:
        chan = (source or "").lower()
        for key, d in (SETTINGS.get("channels") or {}).items():
            if key.lower() in chan:
                found = (float(d.get("top", 0)), float(d.get("bottom", 0)))
                log.info("Overlay cleanup %s: channel default for %s", Path(clip).name, key)
                break
    top, bottom = found or (0.0, 0.0)
    if top or bottom:
        log.info("Overlay cleanup %s (%s): crop top %.1f%%, bottom %.1f%%",
                 Path(clip).name, source or "?", top * 100, bottom * 100)
    return top, bottom


def crop_filter(top: float, bottom: float) -> str:
    """ffmpeg filter (with trailing comma) that removes the bands; '' for none."""
    if not top and not bottom:
        return ""
    keep = 1 - top - bottom
    return f"crop=iw:trunc(ih*{keep:.4f}/2)*2:0:trunc(ih*{top:.4f}/2)*2,"
