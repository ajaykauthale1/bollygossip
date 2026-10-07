"""Careful sentence voicing for scandal videos: pronunciation fixes, Whisper-checked takes, soft edges.

Why (Ajay, 6 Oct 2026): English names were read the Hindi way ("बॉम" for Bom Diggy), and the closing
call to action ("अपनी दुआएं हमें कमेंट्स में ...") sounded broken in every video. Causes found:
- Chatterbox stumbles on some words (कमेंट्स, दुआएं, स्वीटी...) and, with a fixed seed, makes the same
  bad take every time, so a broken take was cached and reused;
- each take's start and end were cut with a hard silence trim (-45 dB, nothing kept), which clipped
  word onsets and the decay of the last word, and takes were joined with digital silence, so the last
  sentence of a part stopped dead right before the outro cue.

What this does per sentence:
1. applies `pronunciations.json` (whole-phrase replacements, longest first) to the spoken text only;
2. voices it with seed 100, transcribes it with Whisper (faster-whisper "small", CPU) and scores the
   transcript against the text; below `voice_min_score` it retries with other seeds and keeps the best;
3. trims silence gently (keeps 80 ms before and 250 ms after the words) with short fades, so words are
   never clipped and every sentence ends with a natural decay.
Segments are joined with real pauses plus `voice_tail` seconds of tail after the last sentence.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("scandal")
SEEDS = [100, 7, 21, 42, 1234]
_whisper = None


# ---- pronunciations ---------------------------------------------------------------------------
def load_pronunciations() -> dict[str, str]:
    f = HERE / "pronunciations.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def apply_pronunciations(text: str, table: dict[str, str]) -> str:
    for k in sorted(table, key=len, reverse=True):
        text = text.replace(k, table[k])
    return text


def unlisted_titles(lines: list[str], table: dict[str, str]) -> list[str]:
    """Quoted names ('...') in the script that the pronunciation table doesn't cover: often English
    song/film titles the voice will read the Hindi way. Logged so they get an entry."""
    found = []
    for line in lines:
        for q in re.findall(r"['‘’\"“”]([^'‘’\"“”]{2,40})['‘’\"“”]", line):
            q = q.strip()
            if q and q not in table and q not in table.values() and q not in found:
                found.append(q)
    return found


# ---- scoring ----------------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("़", "")  # nukta
    s = s.replace("ँ", "ं")  # chandrabindu -> anusvara
    s = re.sub(r"[^\w\s]", " ", s)
    s = s.replace("्", "")  # halant (Whisper writes clusters loosely)
    return " ".join(s.lower().split())


# Whisper writes numbers as digits ("2026") while scripts spell them out ("दो हज़ार छब्बीस"), so
# both sides drop numbers before comparing.
NUM_WORDS = set("""शून्य एक दो तीन चार पांच पाँच छह छः सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह
अठारह उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस
तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस पचास साठ सत्तर अस्सी नब्बे तिरानवे
सौ हज़ार हजार लाख करोड़""".split())


def _strip_numbers(s: str) -> str:
    words = [w for w in s.split() if not re.fullmatch(r"[\d.,]+", w) and _norm(w) not in {_norm(n) for n in NUM_WORDS}]
    return " ".join(words)


def score(text: str, heard: str) -> float:
    a, b = _norm(_strip_numbers(_norm(text))), _norm(_strip_numbers(_norm(heard)))
    return difflib.SequenceMatcher(None, a, b).ratio()


def transcribe(path: Path) -> str | None:
    global _whisper
    if _whisper is False:
        return None
    try:
        if _whisper is None:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(str(path), language="hi")
        return " ".join(s.text.strip() for s in segs)
    except Exception as e:  # noqa: BLE001
        log.warning("Whisper check unavailable (%s); voicing without it", e)
        _whisper = False
        return None


# ---- voicing ----------------------------------------------------------------------------------
EDGES = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse,"
         "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.25,afade=t=in:d=0.12,areverse,"
         "afade=t=in:d=0.02,loudnorm=I=-16:TP=-3:LRA=7")


def voice_sentence(narrator, text: str, cfg: dict, ffmpeg, cache: Path) -> Path:
    """One sentence, Whisper-checked, edges kept soft. Cached by text and settings."""
    spoken = apply_pronunciations(text, cfg["_pron"])
    key = hashlib.sha1(f"{narrator.tag}|{spoken}|v3".encode()).hexdigest()[:12]
    out = cache / f"sent_{key}.wav"
    if out.exists():
        return out
    tries = max(1, int(cfg.get("voice_retries", 3)))
    need = float(cfg.get("voice_min_score", 0.8))
    style = dict(narrator.style)
    best = (-1.0, None, "")
    try:
        for n, seed in enumerate(SEEDS[:tries]):
            raw = cache / f"take_{key}_{seed}.wav"
            if not raw.exists():
                narrator.style = dict(style, seed=seed)
                narrator._synth(spoken, raw)
            heard = transcribe(raw)
            sc = 1.0 if heard is None else score(spoken, heard)
            if sc > best[0]:
                best = (sc, raw, heard or "")
            if heard is None or sc >= need:
                break
            log.info("  retake %d (score %.2f, heard %r): %s", n + 1, sc, heard, spoken[:60])
    finally:
        narrator.style = style
    sc, raw, heard = best
    if sc < need:
        log.warning("Voice check: best take scored %.2f for %r (heard %r); listen to it", sc, spoken, heard)
    ffmpeg(["-i", str(raw), "-af", EDGES + ",aresample=44100,aformat=channel_layouts=stereo", str(out)])
    for f in cache.glob(f"take_{key}_*.wav"):
        f.unlink()
    return out


def join_takes(takes: list[Path], out: Path, gap: float, tail: float, load_audio, save_audio) -> tuple[Path, float]:
    """Takes one after another; each already carries ~0.25 s of natural tail, so the pause adds
    only what's missing; `tail` seconds of silence after the last one."""
    import numpy as np

    sr = 44100
    parts = []
    for i, t in enumerate(takes):
        a = load_audio(t)
        parts.append(a)
        pad = tail if i == len(takes) - 1 else max(gap - 0.25, 0.08)
        parts.append(np.zeros((int(pad * sr), 2), dtype=np.float32))
    track = np.concatenate(parts) if parts else np.zeros((sr, 2), dtype=np.float32)
    save_audio(track, out)
    return out, len(track) / sr
