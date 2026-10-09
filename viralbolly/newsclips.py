"""Video for news stories.

1. Embeds: most gossip articles embed the Instagram reel, X post or YouTube clip
   the story is about. We open the article and take those links, which is how
   Instagram and X clips reach the shorts without logins or paid APIs.
2. Reference clip: when an article has no video, find a recent YouTube clip of
   the same person (title must name them). It plays muted under the story's
   headline and key facts while the voice reads the news.

Every candidate must name a person from the headline in its title/description
(see `matches`), so a story never gets footage of somebody else.
"""
from __future__ import annotations

import logging
import re
import urllib.request

from .models import Post
from .news import UA, names
from .sources import ALLOWED_MEDIA, blocked_uploader

log = logging.getLogger(__name__)

EMBED_PATTERNS = [
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[A-Za-z0-9_-]{5,}",
    r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/[A-Za-z0-9_]{1,15}/status/\d+",
    r"https?://(?:www\.)?youtube(?:-nocookie)?\.com/(?:embed|shorts)/[A-Za-z0-9_-]{11}",
    r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]{11}",
    r"https?://youtu\.be/[A-Za-z0-9_-]{11}",
]


def _get(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA.replace(" ViralBolly/1.0", "")})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log.info("Article %s unavailable: %s", url, str(e)[:100])
        return None


def _normal(url: str) -> str:
    url = url.replace("youtube-nocookie.com", "youtube.com")
    m = re.search(r"youtube\.com/(?:embed|shorts)/([A-Za-z0-9_-]{11})|youtu\.be/([A-Za-z0-9_-]{11})", url)
    if m:
        return f"https://www.youtube.com/watch?v={m[1] or m[2]}"
    return re.sub(r"https?://(?:www\.|mobile\.)?x\.com/", "https://twitter.com/", url).rstrip("/")


def embeds(article_html: str, limit: int = 4) -> list[str]:
    """Video links embedded in the article, in page order (the story's own embed comes first;
    'related stories' further down are less likely to be about it)."""
    # Only the part before the comments/related blocks most sites put at the end.
    body = re.split(r"(?i)related (?:news|stories|articles)|you may also like|also read", article_html)[0]
    found: list[tuple[int, str]] = []
    for pat in EMBED_PATTERNS:
        found += [(m.start(), _normal(m[0])) for m in re.finditer(pat, body.replace("\\/", "/"))]
    out: list[str] = []
    for _, u in sorted(found):
        if u not in out:
            out.append(u)
    return out[:limit]


def summary(article_html: str, max_chars: int = 600) -> str:
    """og:description plus the first real paragraphs, for stories whose feed has no summary."""
    import html as _h

    parts = []
    m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']+)', article_html)
    if m:
        parts.append(_h.unescape(m[1]).strip())
    for p in re.findall(r"<p[^>]*>(.*?)</p>", article_html, flags=re.S | re.I):
        t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
        if len(t) > 60 and not t.lower().startswith(("also read", "read more", "click", "follow us", "subscribe")):
            parts.append(t)
        if sum(len(x) for x in parts) > max_chars:
            break
    return " ".join(parts)[:max_chars]


def _keys(title: str) -> list[str]:
    """Lower-case name tokens from the headline ('Sanjay Dutt' -> ['sanjay dutt', 'dutt'])."""
    keys = []
    for n in names(title):
        n = n.lower()
        keys.append(n)
        last = n.split()[-1]
        if len(last) >= 4:
            keys.append(last)
    return keys


def matches(post: Post, info: dict) -> bool:
    """True if the video's own title/description/uploader names someone from the headline.
    Headlines with no recognisable name can't be checked, so they pass."""
    keys = _keys(post.title)
    if not keys:
        return True
    text = " ".join(str(info.get(k) or "") for k in ("title", "description", "uploader", "channel")).lower()
    flat = re.sub(r"[^a-z]", "", text)
    return any(k in text or k.replace(" ", "") in flat for k in keys)


def _info(url: str) -> dict | None:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as y:
            return y.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        log.info("Embed %s not downloadable: %s", url, str(e).splitlines()[0][:100])
        return None


def attach_embeds(posts: list[Post], block_uploaders=(), max_seconds: float = 300) -> int:
    """Give each news post the first embedded video that is downloadable, about the same
    person, not from a blocked company account, and not too long. Returns how many got one."""
    n = 0
    for p in posts:
        if p.platform != "news" or p.video_url:
            continue
        page = _get(p.url or p.permalink)
        if not page:
            continue
        if len(p.selftext or "") < 80:  # some feeds (Indian Express) carry no summary: take it from the article
            p.selftext = summary(page) or p.selftext
        for url in embeds(page):
            info = _info(url)
            if not info or (info.get("duration") or 0) > max_seconds:
                continue
            reason = blocked_uploader(info, block_uploaders, allow_verified=True)
            if reason:
                log.info("News %s: embed %s skipped (%s)", p.id, url, reason)
                continue
            if not matches(p, info):
                log.info("News %s: embed %s is about someone else, skipped", p.id, url)
                continue
            p.video_url = url
            p.source = info.get("channel") or info.get("uploader") or ""
            n += 1
            log.info("News %s: using embedded clip %s (%s)", p.id, url, p.source)
            break
    return n


# Capitalised headline words that are not part of a name ("Ravi Shastri Recalls Anushka Sharma Watching ...").
NOT_NAME = {"inside", "he", "she", "they", "their", "his", "her", "its", "who", "why", "how", "what", "when", "after", "before",
            "during", "amid", "over", "with", "about", "from", "into", "says", "said", "hit", "hits", "big", "new",
            "first", "last", "day", "box", "office", "watch", "video", "exclusive", "breaking", "reacts", "slams",
            "recalls", "reveals", "defends", "shares", "opens", "calls", "gets", "makes", "takes", "joins", "quits",
            "leaves", "announce", "announces", "wife", "husband", "daughter", "son", "actor", "actress", "star", "fans"}


def people(title: str) -> list[str]:
    """Likely person names in a headline, in order: two- or three-word runs of capitalised
    words with headline verbs and filler cut out ('Anushka Sharma', 'Virat Kohli')."""
    out: list[str] = []
    for run in re.findall(r"(?:[A-Z][a-zA-Z'’.-]+\s+)+[A-Z][a-zA-Z'’.-]+", title):
        cur: list[str] = []
        for w in run.split() + [""]:
            possessive = re.search(r"['’]s$", w)
            w = re.sub(r"['’]s$", "", w)
            bare = w.strip(".'’").lower()
            bad = not w or bare in NOT_NAME or re.search(r"(?:ing|ed)$", bare)
            if not bad:
                cur.append(w.strip(".'’"))
                if not possessive:
                    continue  # "Katrina Kaif’s fans": the name ends at the possessive
                w = ""
            if len(cur) >= 2:
                name = " ".join(cur[:3])
                if name not in out:
                    out.append(name)
            cur = []
    return out


def attach_reference(posts: list[Post], block_uploaders=(), max_seconds: float = 300, search_n: int = 8) -> int:
    """News posts still without video get a recent YouTube clip of the person in the headline,
    marked `ref_clip` so it plays muted under the story text."""
    n = 0
    for p in posts:
        if p.platform != "news" or p.video_url:
            continue
        # Try the people in the headline in order (the story's subject usually comes first),
        # until one of them has a recent clip from an entertainment outlet.
        for person in (people(p.title) or sorted(names(p.title), key=len, reverse=True))[:3]:
            if _reference_for(p, person, block_uploaders, max_seconds, search_n):
                n += 1
                break
    return n


# Broadcasters' uploads are news packages with their own anchors, logos and tickers: fine as
# embeds, but a reference clip should be plain footage of the person.
NEWS_PACKAGERS = ("ndtv", "india today", "aaj tak", "abp", "news18", "times of india", "etimes", "hindustan times",
                  "indian express", "e24", "zee news")
PAP_WORDS = ("spotted", "snapped", "clicked", "papped", "paps", "paparazzi", "airport", "arrives", "arrive", "poses", "event",
             "launch", "screening", "party", "visits", "outside")
SENSITIVE = ("bigg boss", "courtesy", "full episode", "jiohotstar", "colors tv", "death", "d*ath", "passes away",
             "passed away", "funeral", "rip ", "accident", "doppelganger", "lookalike", "look-alike",
             "deepfake", "morphed", "ai video")


def _reference_for(p: Post, person: str, block_uploaders, max_seconds: float, search_n: int) -> bool:
    """Pick a clip of `person` from an entertainment outlet, preferring short paparazzi/event
    footage (no anchor, logo or ticker) over interviews and news packages."""
    import yt_dlp

    best, best_score = None, -1
    for query in (f"{person} spotted", f"{person} latest"):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as y:
                entries = (y.extract_info(f"ytsearch{search_n}:{query}", download=False) or {}).get("entries") or []
        except Exception as e:  # noqa: BLE001
            log.info("News %s: reference search failed: %s", p.id, str(e)[:100])
            continue
        for e in entries:
            dur = e.get("duration") or 0
            if not e or not e.get("id") or dur > max_seconds or (dur or 99) < 8:
                continue
            chan = (e.get("channel") or e.get("uploader") or "").lower()
            if (not any(re.search(rf"\b{re.escape(m)}\b", chan) for m in ALLOWED_MEDIA)  # 'eros zooming' is not zoom
                    or any(m in chan for m in NEWS_PACKAGERS)):
                continue  # random reuploads carry wrong people; broadcasters carry tickers
            title = (e.get("title") or "").lower()
            if person.lower() not in title and person.lower().replace(" ", "") not in title.replace(" ", ""):
                continue  # the full name must be in the clip's title, not just a surname
            if any(w in title for w in SENSITIVE):
                continue
            if blocked_uploader({"uploader": e.get("channel") or e.get("uploader") or ""}, block_uploaders,
                                allow_verified=True):
                continue
            score = (2 if any(w in title for w in PAP_WORDS) else 0) + (1 if dur <= 120 else 0)
            if re.search(re.escape(person.lower()) + r"['’]s\b", title):
                score -= 2  # "Sanjay Dutt's kids spotted": about someone else
            if score > best_score:
                best, best_score = e, score
        if best_score >= 3:
            break  # short paparazzi clip found; no need for the second search
    if not best or best_score < 2:
        return False  # only neutral paparazzi/event footage: the narration carries the story
    p.video_url = f"https://www.youtube.com/watch?v={best['id']}"
    p.source = best.get("channel") or best.get("uploader") or "YouTube"
    p.ref_clip = True
    log.info("News %s: reference clip %s (%s) %s", p.id, best["id"], p.source, (best.get("title") or "")[:60])
    return True


def facts(post: Post, n: int = 3, max_chars: int = 110) -> list[str]:
    """Up to `n` short sentences from the article summary for the on-screen fact list."""
    text = re.sub(r"\s+", " ", post.selftext or "").strip()
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        s = s.strip()
        if len(s) < 25 or s.lower().startswith(("read more", "also read", "click")):
            continue
        if len(s) > max_chars:
            s = s[:max_chars].rsplit(" ", 1)[0] + "…"
        out.append(s)
        if len(out) >= n:
            break
    return out
