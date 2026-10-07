"""Trending Bollywood headlines from entertainment news sites' public RSS feeds
(no keys needed). Each story becomes a card credited to the outlet.

"Trending" = how many headlines across all outlets (plus Google News) mention the
same people/titles in the lookback window, with a small bonus for freshness.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime

from .models import Post

log = logging.getLogger(__name__)

# name -> feed URL. Feeds that fail (down, expired certificate, blocked) are skipped.
NEWS_FEEDS = {
    "Pinkvilla": "https://www.pinkvilla.com/rss.xml",
    "Hindustan Times": "https://www.hindustantimes.com/feeds/rss/entertainment/bollywood/rssfeed.xml",
    "Indian Express": "https://indianexpress.com/section/entertainment/bollywood/feed/",
    "Times of India": "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",
    "Bollywood Hungama": "https://www.bollywoodhungama.com/feed/",
    "India Today": "https://www.indiatoday.in/rss/1206578",
    "NDTV": "https://feeds.feedburner.com/ndtvmovies-latest",
    "Koimoi": "https://www.koimoi.com/feed/",
    "Bollywood Bubble": "https://www.bollywoodbubble.com/feed/",
}
# Only used to measure how widely a story is being covered, not shown directly.
SIGNAL_FEEDS = [
    "https://news.google.com/rss/search?q=bollywood+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ViralBolly/1.0"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"

# Capitalised words that are not names of people or films.
STOP = {
    "The", "A", "An", "And", "Of", "In", "On", "At", "To", "For", "With", "From", "By", "As", "Is", "Are",
    "Box", "Office", "Day", "Collection", "Update", "Watch", "Video", "Photos", "Pics", "News", "Latest",
    "Bollywood", "Hindi", "Film", "Movie", "Trailer", "Song", "Review", "Exclusive", "Report", "Reports",
    "India", "Indian", "Mumbai", "Here", "How", "Why", "What", "When", "Who", "After", "Before", "Not",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Advance", "Booking",
    "Birthday", "Anniversary", "Fans", "Netizens", "Internet", "Star", "Starrer", "Actor", "Actress",
}


def _get(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001 - one bad feed shouldn't stop the others
        log.warning("News feed %s unavailable: %s", url, str(e)[:120])
        return None


def _text(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _image(item: ET.Element, raw_desc: str) -> str | None:
    for tag in (f"{MEDIA_NS}content", f"{MEDIA_NS}thumbnail", "enclosure"):
        el = item.find(tag)
        if el is not None and el.get("url") and (el.get("type", "image").startswith("image") or tag != "enclosure"):
            return el.get("url")
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", html.unescape(raw_desc or ""))
    return m[1] if m else None


def _items(xml: bytes):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return root.iter("item")


def _when(item) -> float:
    try:
        return parsedate_to_datetime(item.findtext("pubDate")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def names(title: str) -> set[str]:
    """People/film names in a headline: runs of 2+ capitalised words, minus stopwords."""
    out = set()
    for run in re.findall(r"(?:[A-Z][a-zA-Z'’.-]+\s+){1,3}[A-Z][a-zA-Z'’.-]+", title):
        words = [w for w in run.split() if w.strip(".'’") not in STOP]
        if len(words) >= 2:
            out.add(" ".join(words[:3]).strip(".'’"))
    return out


def fetch_news(top_n: int = 10, hours: float = 24, feeds: dict[str, str] | None = None,
               exclude_ids: set[str] | None = None) -> list[Post]:
    feeds = feeds or NEWS_FEEDS
    exclude_ids = exclude_ids or set()
    now = time.time()
    cutoff = now - hours * 3600

    stories = []
    for outlet, url in feeds.items():
        xml = _get(url)
        if not xml:
            continue
        n = 0
        for item in _items(xml):
            ts = _when(item)
            if ts and ts < cutoff:
                continue
            title = _text(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            raw = item.findtext("description") or ""
            stories.append(dict(outlet=outlet, title=title, link=link, ts=ts or now,
                                summary=_text(raw), image=_image(item, raw)))
            n += 1
        log.info("News: %d stories from %s", n, outlet)

    # How often each name shows up across every headline we can see today.
    mentions = Counter()
    headlines = [s["title"] for s in stories]
    for url in SIGNAL_FEEDS:
        xml = _get(url)
        if xml:
            headlines += [_text(i.findtext("title")) for i in _items(xml)]
    for h in headlines:
        mentions.update(names(h))

    scored = []
    for s in stories:
        buzz = sum(mentions[n] - 1 for n in names(s["title"]))  # other headlines on the same people
        fresh = max(0.0, 1 - (now - s["ts"]) / (hours * 3600))
        scored.append((buzz + 2 * fresh, s))
    scored.sort(key=lambda t: t[0], reverse=True)

    posts, taken, taken_names = [], [], set()
    for score, s in scored:
        pid = "news_" + hashlib.sha1(s["link"].encode()).hexdigest()[:10]
        key = set(re.findall(r"\w+", s["title"].lower()))
        n = names(s["title"])
        if (pid in exclude_ids or n & taken_names
                or any(len(key & k) / max(1, len(key | k)) > 0.5 for k in taken)):
            continue  # already used, or the same story/people from another outlet
        if taken and not n and score < 1:
            continue  # nothing anyone else is talking about
        taken.append(key)
        taken_names |= n
        posts.append(Post(
            id=pid, title=s["title"], author=s["outlet"], subreddit="", permalink=s["link"], url=s["link"],
            score=0, num_comments=0, upvote_ratio=1.0, created_utc=s["ts"],
            selftext=s["summary"][:600], kind="news", image_url=s["image"], platform="news",
            virality=round(score, 2),
        ))
        log.info("News #%d [%s] buzz=%.1f %s", len(posts), s["outlet"], score, s["title"][:70])
        if len(posts) >= top_n:
            break
    return posts
