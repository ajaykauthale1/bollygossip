"""[MUSIC: ...] and [SFX: ...] cue lines of podcast-style scripts, turned into sound.

No LLM: each cue is matched by keywords (Hindi or English) to a mood track in
music/cues/<mood>.mp3 or a sound effect in sfx/<name>.mp3. Replace those files
to change the sound; add keywords below to teach it new cues.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUE_MUSIC = HERE / "music" / "cues"
SFX_DIR = HERE / "sfx"

# First match wins, so the more specific moods come first.
MUSIC_MOODS = [
    ("outro", ["आउट्रो", "outro", "fade out"]),
    ("retro", ["डांस", "dance", "90 के दशक", "90s", "रेट्रो", "retro"]),
    ("ending", ["एंडिंग", "ending", "शांत", "calm"]),
    ("nostalgic", ["नॉस्टैल्जिक", "nostalgic", "हल्का", "यादें", "emotional", "इमोशनल"]),
    ("gossip", ["गॉसिप", "gossip", "इन्वेस्टिगेटिव", "investigat", "तेज", "upbeat", "fast"]),
    ("dark", ["डार्क", "dark", "ड्रामेटिक", "dramatic", "थ्रिलर", "thriller"]),
    ("mysterious", ["मिस्टीरियस", "रहस्य", "mysterious", "mystery", "इंटेंस", "intense", "सस्पेंस", "suspense"]),
]

# name, keywords, volume, seconds the voice waits after it starts
SFX_KINDS = [
    ("record_scratch", ["scratch", "स्क्रैच"], 1.0, 1.1),
    ("camera_shutter", ["shutter", "शटर", "camera", "कैमरे", "कैमरा"], 0.6, 1.0),
    ("thud", ["thud", "धमाक", "impact", "boom"], 1.0, 0.7),
    ("whoosh", ["whoosh", "व्हूश"], 0.7, 0.6),
    ("tape_click", ["tape", "टेप", "mic drop", "माइक", "click", "क्लिक"], 0.8, 0.6),
    ("news_chatter", ["रिपोर्टर", "reporter", "anchor", "एंकर"], 2.0, 0.0),  # waits its own length
]
STOP = ["रुक जाता", "रुक जा", "एकदम से रुक", "music stops", "stops", "बंद हो"]
INTRO_MUSIC = ["म्यूज़िक बजना", "music starts", "music plays", "गाना बज"]


def _has(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def music_mood(text: str) -> str | None:
    for mood, words in MUSIC_MOODS:
        if _has(text, words):
            return mood
    return "mysterious"  # a [MUSIC: ...] cue we can't read still changes the music


def stops_music(text: str) -> bool:
    return _has(text, STOP)


def sfx_list(text: str) -> list[tuple[str, float, float]]:
    """Sound effects named in an [SFX: ...] cue, in the order they appear in the text."""
    found = []
    low = text.lower()
    for name, words, vol, wait in SFX_KINDS:
        pos = [low.find(w.lower()) for w in words if w.lower() in low]
        if pos:
            found.append((min(pos), name, vol, wait))
    return [(n, v, w) for _, n, v, w in sorted(found)]


def chatter_lines(text: str) -> list[str]:
    """The quoted headlines inside a news-reporters cue."""
    return [q.strip(" .…") for q in re.findall(r"[\"“”]([^\"“”]{4,})[\"“”]", text)]


def wants_intro_music(text: str) -> bool:
    """An [SFX: ...] cue that describes music starting (e.g. a 90s dance hit before a scratch)."""
    return _has(text, INTRO_MUSIC) and music_mood(text) not in (None, "mysterious")


def music_file(mood: str) -> Path | None:
    for ext in (".mp3", ".wav", ".m4a"):
        f = CUE_MUSIC / f"{mood}{ext}"
        if f.is_file():
            return f
    return None


def sfx_file(name: str) -> Path | None:
    for ext in (".mp3", ".wav"):
        f = SFX_DIR / f"{name}{ext}"
        if f.is_file():
            return f
    return None


@dataclass
class MusicEvent:
    t: float
    mood: str | None  # None = silence
    abrupt: bool = False  # cut instead of fading (record scratch, thud)


@dataclass
class SfxEvent:
    t: float
    path: Path
    volume: float
