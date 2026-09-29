"""Fetch and rank the day's most viral posts from Reddit's public JSON listings
(the same data reddit.com shows a logged-out visitor; no API keys needed).

Reddit doesn't expose view counts publicly, so "viral" is scored from upvotes
and comment activity, normalised by post age so a fast-rising post can beat an
older one with a similar score.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .browser import Browser
from .models import Comment, Post
from .sources import blocked_domain

log = logging.getLogger(__name__)

REDDIT = "https://www.reddit.com"
# Hosts yt-dlp can usually pull a video from. If a download fails anyway, the
# post falls back to a screenshot.
VIDEO_DOMAINS = {
    "v.redd.it", "youtube.com", "youtu.be", "m.youtube.com", "streamable.com",
    "instagram.com", "twitter.com", "x.com", "tiktok.com", "dailymotion.com",
    "facebook.com", "fb.watch",
}
SKIP_COMMENT_AUTHORS = {"AutoModerator", "[deleted]"}


def virality_score(score: int, num_comments: int, upvote_ratio: float, age_hours: float) -> float:
    """Engagement per hour with a gentle age decay. Comments count double: gossip
    threads that people argue in are the ones worth featuring."""
    engagement = max(score, 0) + 2 * num_comments
    return engagement * (0.5 + upvote_ratio / 2) / (age_hours + 2) ** 0.8


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _image_url(d: dict) -> str | None:
    url = d.get("url_overridden_by_dest") or d.get("url") or ""
    if url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return url
    for item in (d.get("media_metadata") or {}).values():  # galleries: first image
        src = (item.get("s") or {}).get("u")
        if src:
            return html.unescape(src)
    images = (d.get("preview") or {}).get("images")
    if images:
        return html.unescape(images[0]["source"]["url"])
    return None


def is_video_listing(d: dict) -> bool:
    xposts = d.get("crosspost_parent_list")
    if xposts[0].get("is_video", False) if xposts else d.get("is_video", False):
        return True
    url = d.get("url_overridden_by_dest") or d.get("url") or ""
    return not d.get("is_self", True) and _domain(url) in VIDEO_DOMAINS


def classify(d: dict, post: Post) -> None:
    xposts = d.get("crosspost_parent_list")
    # A crosspost carries the original post's media flags.
    is_video = xposts[0].get("is_video", False) if xposts else d.get("is_video", False)
    url = d.get("url_overridden_by_dest") or d.get("url") or ""
    if is_video:
        post.kind, post.video_url = "video", post.permalink
    elif not d.get("is_self", True) and _domain(url) in VIDEO_DOMAINS:
        post.kind, post.video_url = "video", url
    else:
        post.kind = "text"
        post.image_url = _image_url(d)


def post_from_listing(d: dict, subreddit: str, virality: float = 0.0) -> Post:
    return Post(
        id=d["id"],
        title=html.unescape(d.get("title", "")),
        author=d.get("author") or "[deleted]",
        subreddit=subreddit,
        permalink=REDDIT + d["permalink"],
        url=d.get("url", ""),
        score=d.get("score", 0),
        num_comments=d.get("num_comments", 0),
        upvote_ratio=d.get("upvote_ratio", 1.0),
        created_utc=d.get("created_utc", 0),
        selftext=html.unescape(d.get("selftext") or ""),
        flair=d.get("link_flair_text"),
        over_18=d.get("over_18", False),
        virality=round(virality, 2),
    )


def parse_comments(thread_json, k: int) -> list[Comment]:
    """thread_json is the [post_listing, comment_listing] pair from /comments/<id>.json."""
    out: list[Comment] = []
    for child in thread_json[1]["data"]["children"]:
        if child.get("kind") != "t1":  # "more" placeholders
            continue
        c = child["data"]
        if c.get("stickied") or c.get("author") in SKIP_COMMENT_AUTHORS or c.get("body") in ("[deleted]", "[removed]"):
            continue
        out.append(Comment(author=c["author"], body=html.unescape(c.get("body", "")), score=c.get("score", 0)))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:k]


def fetch_trending(
    browser: Browser,
    subreddit: str,
    top_n: int = 10,
    comments_per_post: int = 3,
    hours: float = 24,
    min_score: int = 0,
    include_nsfw: bool = False,
    exclude_ids: set[str] | None = None,
    video_boost: float = 1.0,
    block_domains: set[str] | None = None,
) -> list[Post]:
    """`subreddit` may be several, comma-separated. Video posts get their virality
    multiplied by `video_boost`; link posts to company/media sites are skipped."""
    now = time.time()
    cutoff = now - hours * 3600
    exclude_ids = exclude_ids or set()

    seen: dict[str, dict] = {}
    # "top of the day" misses posts that are still climbing; "hot" and "rising" catch them.
    subs = [x.strip() for x in subreddit.split(",") if x.strip()]
    for sub in subs:
        for path in ("top.json?t=day&limit=100", "hot.json?limit=100", "rising.json?limit=50"):
            listing = browser.get_json(f"{REDDIT}/r/{sub}/{path}&raw_json=1")
            for child in listing["data"]["children"]:
                seen.setdefault(child["data"]["id"], child["data"])
    log.info("Read %d candidate posts from r/%s", len(seen), ", r/".join(subs))

    candidates = []
    for d in seen.values():
        if (d.get("created_utc", 0) < cutoff or d.get("stickied") or d["id"] in exclude_ids
                or d.get("score", 0) < min_score or d.get("removed_by_category")):
            continue
        if d.get("over_18") and not include_nsfw:
            continue
        link = d.get("url_overridden_by_dest") or ""
        if not d.get("is_self", True) and blocked_domain(link, block_domains or set()):
            log.debug("Skipping %s: links to a company/media site (%s)", d["id"], _domain(link))
            continue
        age_h = (now - d["created_utc"]) / 3600
        v = virality_score(d.get("score", 0), d.get("num_comments", 0), d.get("upvote_ratio", 1.0), age_h)
        candidates.append((v * (video_boost if is_video_listing(d) else 1.0), d))

    candidates.sort(key=lambda t: t[0], reverse=True)
    posts: list[Post] = []
    for vscore, d in candidates[:top_n]:
        post = post_from_listing(d, d.get("subreddit") or subs[0], vscore)
        classify(d, post)
        if post.kind == "text" and comments_per_post > 0 and post.num_comments:
            thread = browser.get_json(
                f"{REDDIT}/comments/{post.id}.json?sort=top&depth=1&limit={comments_per_post * 3}&raw_json=1")
            post.comments = parse_comments(thread, comments_per_post)
        posts.append(post)
        log.info("#%d %s [%s] score=%d comments=%d virality=%.1f", len(posts), post.id, post.kind,
                 post.score, post.num_comments, vscore)
    return posts


def load_posts_json(path: str | Path) -> list[Post]:
    """Load posts from a JSON file (a saved manifest.json works) instead of Reddit."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = set(Post.__dataclass_fields__)
    return [Post.from_dict({k: v for k, v in d.items() if k in fields}) for d in data]


# ---------------------------------------------------------------------------
# Fallback when the JSON listings are refused: Reddit's public RSS feed.
# It carries no scores or comment counts, so Reddit's own "top of the day" order
# is the ranking. The feed allows about one request a minute without a token, so
# this makes a single request and honours the rate-limit headers.

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
RSS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ViralBolly/1.0 (daily compilation; RSS reader)"


def _rss_get(url: str, tries: int = 3) -> bytes:
    import urllib.error
    import urllib.request

    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RSS_UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                wait = float(e.headers.get("x-ratelimit-reset") or e.headers.get("retry-after") or 60)
                log.warning("Reddit RSS rate limit; waiting %.0fs as asked", wait)
                time.sleep(min(wait + 2, 120))
                continue
            if e.code in (401, 403):
                from .browser import RedditBlocked
                raise RedditBlocked(f"Reddit refused the RSS feed too ({e.code}).")
            raise
    raise RuntimeError("Reddit RSS kept being rate limited")


def _rss_links(content_html: str) -> list[str]:
    return [html.unescape(u) for u in re.findall(r'href="(https?://[^"]+)"', content_html)]


def fetch_rss(subreddit: str, top_n: int = 10, hours: float = 24, exclude_ids: set[str] | None = None,
              include_nsfw: bool = False) -> list[Post]:
    import xml.etree.ElementTree as ET
    from datetime import datetime

    exclude_ids = exclude_ids or set()
    posts: list[Post] = []
    for sub in [x.strip() for x in subreddit.split(",") if x.strip()]:
        xml = _rss_get(f"{REDDIT}/r/{sub}/top/.rss?t=day&limit=50")
        entries = ET.fromstring(xml).findall(f"{ATOM}entry")
        cutoff = time.time() - hours * 3600
        for i, e in enumerate(entries):
            pid = (e.findtext(f"{ATOM}id") or "").removeprefix("t3_")
            if not pid or pid in exclude_ids:
                continue
            published = e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated") or ""
            try:
                ts = datetime.fromisoformat(published).timestamp()
            except ValueError:
                ts = time.time()
            if ts < cutoff:
                continue
            content = html.unescape(e.findtext(f"{ATOM}content") or "")
            if "nsfw" in content.lower() and not include_nsfw:
                continue
            author = (e.findtext(f"{ATOM}author/{ATOM}name") or "/u/[deleted]").removeprefix("/u/")
            link_el = e.find(f"{ATOM}link")
            permalink = link_el.get("href") if link_el is not None else f"{REDDIT}/comments/{pid}/"
            md = re.search(r'<div class="md">(.*?)</div>', content, re.S)
            selftext = html.unescape(re.sub(r"<[^>]+>", " ", md[1])).strip() if md else ""
            selftext = re.sub(r"\s+", " ", selftext)
            links = [u for u in _rss_links(content) if "/user/" not in u and "/comments/" not in u]
            post = Post(
                id=pid, title=html.unescape(e.findtext(f"{ATOM}title") or ""), author=author, subreddit=sub,
                permalink=permalink, url=links[0] if links else permalink, score=0, num_comments=0,
                upvote_ratio=1.0, created_utc=ts, selftext=selftext,
                virality=round(100 * (1 - i / max(len(entries), 1)), 1),
            )
            video = next((u for u in links if _domain(u) == "v.redd.it" or _domain(u) in VIDEO_DOMAINS), None)
            image = next((u for u in links if _domain(u) == "i.redd.it"), None)
            if video and _domain(video) == "v.redd.it":
                # The public media playlist, the same file the Reddit player streams.
                post.kind, post.video_url = "video", video.rstrip("/") + "/DASHPlaylist.mpd"
            elif video:
                post.kind, post.video_url = "video", video
            else:
                thumb = e.find(f"{MEDIA}thumbnail")
                small = thumb.get("url") if thumb is not None else None
                post.image_url = image or (None if small and "width=140" in small else small)
            posts.append(post)
            log.info("Reddit RSS #%d [%s] %s", len(posts), post.kind, post.title[:70])
            if len(posts) >= top_n:
                break
    return posts
