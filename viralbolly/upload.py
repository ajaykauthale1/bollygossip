"""YouTube upload helpers: a sources file (JSON) and a ready-to-paste description
with chapters and a credit + link for every item in the video."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .media import probe
from .models import Post
from .render import credit_text


def _ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _clean(title: str) -> str:
    t = re.sub(r"(\s*#\w+)+\s*$", "", title)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿️]", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" |-·")


def _seconds(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def short_link(url: str) -> str:
    """Shorter canonical links so more fit in the description."""
    m = re.search(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([\w-]{11})", url)
    if m:
        return f"https://youtu.be/{m[1]}"
    m = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
    if m:
        return f"https://redd.it/{m[1]}"
    return url


def link(post: Post) -> str:
    return post.permalink or post.url or post.video_url or ""


def write(name: str, video: Path, items: list[tuple[Path, int, Post]], day: datetime, is_short: bool,
          lead: float = 0.0) -> Path:
    """items: (segment file, on-screen number, post) in play order. Writes
    <name>_youtube.json and <name>_description.txt next to the video."""
    t = 0.0
    starts = {}
    for seg, num, post in items:
        starts[post.id] = t
        t += probe(seg)["duration"]
    offset = lead  # the hook/intro plays before the first item; chapters start at 0:00
    chapters = [{"time": "0:00", "title": "Today's biggest Bollywood drama"}]
    sources = []
    for seg, num, post in items:
        main, small = credit_text(post)
        at = starts[post.id] + offset
        chapters.append({"time": _ts(at), "title": f"#{num} {_clean(post.title)[:80]}"})
        sources.append({"number": num, "title": _clean(post.title), "credit": main, "where": small,
                        "platform": post.platform, "link": link(post), "starts_at": _ts(at)})
    ranked = sorted(sources, key=lambda s: s["number"])
    top = ranked[0]["title"] if ranked else "Bollywood gossip"
    title = f"{top[:60]} & more | Bollywood Gossip {day.strftime('%d %b')}"[:100]
    head = [f"Today's biggest Bollywood gossip, {day.strftime('%d %B %Y')}. All credit to the original creators.", ""]
    foot = ["", "Clips and stories belong to their original creators and are shown with credit for news and commentary.",
            "Want something credited differently or removed? Leave a comment.", "",
            "#bollywood #bollywoodgossip #bollywoodnews" + (" #shorts" if is_short else "")]
    by_time = sorted(sources, key=lambda s: _seconds(s["starts_at"]))
    full = [f"#{s['number']} {s['title']} | Credit: {s['credit']} ({s['where']}) {s['link']}" for s in ranked]

    def body(title_len: int, links: bool) -> list[str]:
        # One line per item: chapter time (long video), number, title, credit, link.
        rows = by_time if not is_short else ranked
        out = ["0:00 Intro"] if not is_short else []
        for s in rows:
            ln = (f"{s['starts_at']} " if not is_short else "") + f"#{s['number']} {s['title'][:title_len]}"
            ln += f" | {s['credit']}" + (f" {short_link(s['link'])}" if links else "")
            out.append(ln)
        return out

    # YouTube allows 5,000 characters; shrink until it fits, and put the full
    # credit list in a pinned-comment file when links have to be dropped.
    pinned = None
    for tl, links in ((80, True), (50, True), (32, True), (50, False), (32, False)):
        desc = "\n".join(head + ["Chapters & credits:" if not is_short else "Credits:"] + body(tl, links) + foot)
        if len(desc) <= 4900:
            break
    if not links:
        pinned = "Full credits & links:\n" + "\n".join(full)
        video.with_name(f"{name}_pinned_comment.txt").write_text(pinned + "\n", encoding="utf-8")
    data = {"video": video.name, "suggested_title": title, "description": desc,
            "chapters": chapters if not is_short else [], "sources": ranked, "pinned_comment": pinned}
    out = video.with_name(f"{name}_youtube.json")
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    video.with_name(f"{name}_description.txt").write_text(f"{title}\n\n{desc}\n", encoding="utf-8")
    return out
