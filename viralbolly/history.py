"""Keep the shorts from repeating what they showed in the last few days.

The "already used" list only catches the exact same upload. Channels post many
cuts of one interview (e.g. a podcast guest every day for a week), so a new
item is also skipped when it is about a person featured in a recent short AND
it comes from the same channel/outlet or shares the topic words.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import Post
from .news import names

log = logging.getLogger(__name__)

IGNORE = {"shorts", "short", "bollywood", "with", "after", "from", "this", "that", "viral", "reel",
          "reels", "watch", "video", "spotted", "reveals", "about", "their", "they", "when", "what"}


def people(title: str) -> set[str]:
    return {re.sub(r"[^a-z]", "", n.lower()) for n in names(title)} - {""}


def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in IGNORE}


def recent(out_root: Path, today: datetime, days: int = 3) -> list[dict]:
    """Items that appeared in a short on any of the `days` days before today."""
    items = []
    for k in range(1, days + 1):
        f = out_root / (today - timedelta(days=k)).strftime("%Y-%m-%d") / "manifest.json"
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for m in rows:
            if any(name.startswith("short") for name in m.get("used_in", {})):
                items.append({"id": m["id"], "title": m.get("title", ""),
                              "channel": (m.get("source") or m.get("author") or "").lower(),
                              "video": m.get("video_url") or ""})
    return items


def repeat_of(post: Post, seen: list[dict]) -> dict | None:
    who = people(post.title)
    ws = words(post.title)
    chan = (post.source or post.author or "").lower()
    for s in seen:
        if post.id == s["id"] or (post.video_url and post.video_url == s["video"]):
            return s
        if not who & people(s["title"]):
            continue
        overlap = len(ws & words(s["title"])) / max(1, min(len(ws), len(words(s["title"]))))
        if (chan and chan == s["channel"]) or overlap >= 0.3:
            return s
    return None


def drop_repeats(posts: list[Post], out_root: Path, today: datetime, days: int = 3) -> list[Post]:
    seen = recent(out_root, today, days)
    kept = []
    for p in posts:
        s = repeat_of(p, seen)
        if s:
            log.info("Skipping %s (repeats %s from the last %d days): %s", p.id, s["id"], days, p.title[:60])
        else:
            kept.append(p)
    return kept
