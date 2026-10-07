"""Trending clips from entertainment channels' public YouTube pages (no API key:
yt-dlp reads the same listing a logged-out visitor sees). Each clip is credited
to its channel on screen.

Ranking: views per hour since upload, among each channel's latest Shorts and
short videos.
"""
from __future__ import annotations

import logging
import time

from .models import Post
from .sources import blocked_uploader

log = logging.getLogger(__name__)

YOUTUBE_CHANNELS = ["@pinkvilla", "@BollywoodHungama", "@zoomtv", "@etimes", "@Koimoi"]


def _listing(url: str, n: int) -> list[dict]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": n}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
        return [dict(e, _channel=info.get("channel") or info.get("uploader")) for e in (info.get("entries") or [])]
    except Exception as e:  # noqa: BLE001
        log.warning("YouTube listing %s unavailable: %s", url, str(e).splitlines()[0][:120])
        return []


def _details(video_id: str) -> dict | None:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            return y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as e:  # noqa: BLE001
        log.warning("YouTube video %s unavailable: %s", video_id, str(e).splitlines()[0][:120])
        return None


def fetch_youtube(top_n: int = 10, hours: float = 24, channels: list[str] | None = None,
                  per_channel: int = 15, max_duration: float = 300,
                  exclude_ids: set[str] | None = None, fresh: bool = False) -> list[Post]:
    channels = channels or YOUTUBE_CHANNELS
    exclude_ids = exclude_ids or set()
    now = time.time()

    candidates = []
    for ch in channels:
        handle = ch if ch.startswith("@") else "@" + ch
        for tab in ("shorts", "videos"):
            for e in _listing(f"https://www.youtube.com/{handle}/{tab}", per_channel):
                dur = e.get("duration")
                if dur and dur > max_duration:
                    continue
                if "yt_" + e["id"] in exclude_ids:
                    continue
                candidates.append(e)
    if fresh:  # channel listings are newest first: take turns across channels so each one's latest clips get checked
        lists: dict = {}
        for e in candidates:
            lists.setdefault(e.get("_channel"), []).append(e)
        candidates = [e for row in __import__("itertools").zip_longest(*lists.values()) for e in row if e]
    else:  # Listings don't carry upload times, so check the most-viewed candidates one by one.
        candidates.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
    log.info("YouTube: %d recent candidate clips from %d channels", len(candidates), len(channels))

    scored, seen = [], set()
    for e in candidates[: top_n * 3]:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        info = _details(e["id"])
        if not info:
            continue
        ts = info.get("timestamp") or 0
        if not ts:
            d = info.get("upload_date")  # YYYYMMDD
            ts = time.mktime(time.strptime(d, "%Y%m%d")) + 12 * 3600 if d else 0
        age_h = max((now - ts) / 3600, 0.5) if ts else 1e9
        if age_h > max(hours, 24) * 2:  # uploads are timestamped by day, so allow a little slack
            continue
        reason = blocked_uploader(info, allow_verified=True)
        if reason:
            log.info("YouTube: skipping %s (%s)", e["id"], reason)
            continue
        views = info.get("view_count") or e.get("view_count") or 0
        scored.append((1 / (age_h + 2) if fresh else views / (age_h + 2) ** 0.8, info))
    scored.sort(key=lambda t: t[0], reverse=True)

    posts = []
    for score, info in scored[:top_n]:
        channel = info.get("channel") or info.get("uploader") or "YouTube"
        posts.append(Post(
            id="yt_" + info["id"], title=info.get("title", ""), author=channel, subreddit="",
            permalink=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
            url=info.get("webpage_url", ""), score=info.get("like_count") or 0,
            num_comments=info.get("comment_count") or 0, upvote_ratio=1.0,
            created_utc=info.get("timestamp") or 0, kind="video",
            video_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
            platform="youtube", source=channel, views=info.get("view_count") or 0, virality=round(score, 1),
        ))
        log.info("YouTube #%d [%s] views=%s %s", len(posts), channel, info.get("view_count"), posts[-1].title[:60])
    return posts
