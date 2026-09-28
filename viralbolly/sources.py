"""Which video sources to keep out of the compilation.

The goal is clips posted by individuals or by entertainment media (Pinkvilla,
Bollywood Hungama, ..., credited on screen), not footage owned by studios, music
labels, streaming services, TV channels or paparazzi agencies, which are the
accounts that most often file copyright claims. This lowers the risk; it can't
remove it (an individual's upload can still contain someone else's footage), so
review clips before publishing.

Extend both lists from config.json with "block_domains" / "block_uploaders".
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

# Link posts pointing at these sites are skipped outright.
BLOCK_DOMAINS = {
    # studios and streaming
    "netflix.com", "primevideo.com", "hotstar.com", "jiocinema.com", "sonyliv.com", "zee5.com",
    "yashrajfilms.com", "dharma-production.com",
}

# yt-dlp's uploader/channel name is checked against these (case-insensitive substring).
BLOCK_UPLOADERS = {
    # music labels and studios
    "t-series", "tseries", "zee music", "sony music", "saregama", "tips official", "tips music",
    "yrf", "yash raj", "dharma", "excel movies", "excel entertainment", "red chillies",
    "eros now", "shemaroo", "venus", "speed records", "aditya music",
    # streaming and TV
    "netflix", "prime video", "disney", "hotstar", "jiocinema", "sonyliv", "zee5", "zee tv",
    "colors tv", "star plus", "sony tv", "sab tv", "mx player",
    # paparazzi agencies (their clips are the most commonly claimed)
    "viral bhayani", "viralbhayani", "instant bollywood", "instantbollywood", "manav manglani",
    "varinder chawla", "voompla", "yogen shah", "filmy mantra",
}

# Entertainment/news media: allowed (and credited by name), even though their
# channels are usually verified.
ALLOWED_MEDIA = {
    "pinkvilla", "bollywood hungama", "bollywoodhungama", "filmfare", "filmygyan", "bollywood bubble",
    "bollywoodlife", "koimoi", "missmalini", "etimes", "times of india", "india today", "ndtv",
    "hindustan times", "news18", "zoom", "aaj tak", "abp", "filmibeat", "mid-day", "midday",
    "indian express", "film companion", "galatta",
}


def domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def blocked_domain(url: str, extra: Iterable[str] = ()) -> bool:
    host = domain(url)
    return any(host == d or host.endswith("." + d) for d in BLOCK_DOMAINS | set(extra))


def blocked_uploader(info: dict, extra: Iterable[str] = (), allow_verified: bool = False) -> str | None:
    """Reason to skip a clip based on yt-dlp metadata, or None if it looks like an individual."""
    names = " | ".join(str(info.get(k) or "") for k in ("uploader", "channel", "uploader_id", "creator")).lower()
    for word in BLOCK_UPLOADERS | {e.lower() for e in extra}:
        if word and word in names:
            return f"company account ({word})"
    if any(m in names for m in ALLOWED_MEDIA):
        return None
    if not allow_verified and info.get("channel_is_verified"):
        return "verified channel"
    return None
