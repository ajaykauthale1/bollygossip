"""Word-group captions timed to the voiceover.

The voice file is scanned for the stretches where someone is actually speaking, and the
words of the script are spread over those stretches in proportion to their length. That
gives each 2 to 3 word group a start and end time close enough to read as in sync, without
needing a speech recogniser.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .media import ffmpeg_exe, probe


def speech_intervals(wav: Path, noise_db: int = -38, min_gap: float = 0.12) -> list[tuple[float, float]]:
    """[(start, end)] of the non-silent stretches of `wav`."""
    dur = probe(wav)["duration"]
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(wav), "-af",
                           f"silencedetect=n={noise_db}dB:d={min_gap}", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    speech, t = [], 0.0
    for i, s in enumerate(starts):
        if s - t > 0.05:
            speech.append((t, s))
        t = ends[i] if i < len(ends) else dur
    if dur - t > 0.05:
        speech.append((t, dur))
    return speech or [(0.0, dur)]


def phrases(wav: Path, text: str, per: int = 3, offset: float = 0.0) -> list[tuple[float, float, str]]:
    """Caption groups [(start, end, text)] for `text` spoken in `wav`, shifted by `offset` seconds."""
    words = text.split()
    if not words:
        return []
    ivs = speech_intervals(wav)
    total = sum(e - s for s, e in ivs)
    weights = [len(w) + 1 for w in words]
    wsum = sum(weights)

    def at(frac: float) -> float:  # position along the speech-only axis -> real time
        x = frac * total
        for s, e in ivs:
            if x <= e - s:
                return s + x
            x -= e - s
        return ivs[-1][1]

    spans, acc = [], 0.0
    for wt in weights:
        spans.append((acc / wsum, (acc + wt) / wsum))
        acc += wt
    groups, cur = [], []
    for w, sp in zip(words, spans):
        cur.append((w, sp))
        if len(cur) >= per or re.search(r"[।.!?]$", w):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = [(at(g[0][1][0]) + offset, at(g[-1][1][1]) + offset, " ".join(w for w, _ in g)) for g in groups]
    # Hold each group until the next one starts when the gap is short, so captions don't flicker.
    fixed = []
    for i, (a, b, t) in enumerate(out):
        nxt = out[i + 1][0] if i + 1 < len(out) else b + 0.5
        end = nxt if nxt - b < 0.35 else b + 0.12
        fixed.append((a, max(end, a + 0.45), t))
    return fixed
