"""Produce the PNGs that go into the video, using the shared Chromium browser:

- thread screenshots: the real post plus its top comments, captured from the
  public old.reddit.com page (cleaner for screenshots than the new site) and
  framed with the compilation branding;
- fallback cards drawn from the post data, used when a page can't be captured;
- the credit overlay burned onto video clips, and the intro card.
"""
from __future__ import annotations

import base64
import html
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .browser import Browser
from .models import Post

log = logging.getLogger(__name__)

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { width: %(w)dpx; font-family: "Segoe UI", "Nirmala UI", "Segoe UI Emoji", Roboto, "Helvetica Neue", Arial, "Noto Sans", "Noto Color Emoji", sans-serif; }
.brand { font-weight: 800; letter-spacing: 3px; text-transform: uppercase; color: #ff4f8b; font-size: 30px; }
.chip { display: inline-block; padding: 6px 18px; border-radius: 999px; background: #ff4f8b; color: #fff; font-weight: 700; font-size: 28px; }
"""

CARD_CSS = """
body { min-height: %(h)dpx; background: linear-gradient(160deg, #1b0f2e 0%%, #0d0d12 55%%, #2a0f1f 100%%); color: #eee; padding: 90px 56px 120px; }
.top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 36px; }
.card { background: #1a1a1b; border: 2px solid #343536; border-radius: 28px; padding: 44px; }
.sub { color: #9aa0a6; font-size: 28px; margin-bottom: 18px; }
.sub b { color: #d7dadc; }
.flair { display: inline-block; background: #3b2a4d; color: #e9c9ff; font-size: 24px; padding: 4px 16px; border-radius: 999px; margin-bottom: 18px; }
h1 { font-size: 54px; line-height: 1.2; color: #fff; margin-bottom: 26px; word-wrap: break-word; }
.body { font-size: 34px; line-height: 1.45; color: #d7dadc; }
.body p { margin-bottom: 18px; }
.media { margin-top: 20px; width: 100%%; max-height: 900px; object-fit: contain; border-radius: 18px; background: #000; }
.meta { margin-top: 26px; font-size: 30px; color: #9aa0a6; display: flex; gap: 36px; }
.meta b { color: #ff8b3d; }
.ch { margin: 44px 0 20px; font-size: 30px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; color: #9aa0a6; }
.comment { background: #141415; border-left: 6px solid #ff4f8b; border-radius: 18px; padding: 28px 32px; margin-bottom: 22px; }
.comment .who { font-size: 26px; color: #9aa0a6; margin-bottom: 10px; }
.comment .who b { color: #4fbcff; }
.comment .txt { font-size: 32px; line-height: 1.4; color: #e8e8e8; }
.spoiler { background: #3a3a3c; padding: 0 6px; border-radius: 6px; }
.top, .card, .ch, .comment { max-width: 1100px; margin-left: auto; margin-right: auto; }
"""

OVERLAY_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.band { position: absolute; left: 0; right: 0; top: 0; padding: 70px 56px 50px;
        background: linear-gradient(180deg, rgba(0,0,0,.85) 0%%, rgba(0,0,0,.55) 70%%, rgba(0,0,0,0) 100%%); }
.title { margin-top: 20px; color: #fff; font-size: 46px; font-weight: 700; line-height: 1.2;
         display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; text-shadow: 0 2px 8px #000; }
.handle { position: absolute; left: 56px; bottom: 140px; background: rgba(0,0,0,.75); color: #fff; max-width: calc(100%% - 112px);
          border: 3px solid #ff4f8b; border-radius: 28px; padding: 16px 34px; font-size: 40px; font-weight: 700; }
.handle .lbl { display: block; color: #ff8bb0; font-size: 24px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; }
.handle span { color: #b8bcc2; font-weight: 400; font-size: 30px; }
"""

INTRO_CSS = """
body { height: %(h)dpx; background: radial-gradient(circle at 30%% 20%%, #4a1238 0%%, #0d0d12 60%%); color: #fff;
       display: flex; flex-direction: column; justify-content: center; padding: 0 80px; }
h1 { font-size: 110px; line-height: 1.05; margin: 30px 0; }
.date { font-size: 48px; color: #ffb3cf; }
.src { margin-top: 60px; font-size: 34px; color: #9aa0a6; }
"""

MAX_BODY_CHARS = 900
MAX_COMMENT_CHARS = 380


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def md_to_html(text: str) -> str:
    """Just enough Reddit markdown for a readable card: paragraphs, bold/italic,
    links as plain text, and >!spoilers!< (common in blind items)."""
    text = html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"&gt;!(.+?)!&lt;", r'<span class="spoiler">\1</span>', text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"^&gt;\s?", "", text, flags=re.M)  # quote markers
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    paras = [p.strip().replace("\n", "<br>") for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paras)


def _page(css: str, body: str, w: int, h: int) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS % {'w': w}}"
            f"{css % {'h': h}}</style></head><body>{body}</body></html>")


def _fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def card_html(post: Post, rank: int, w: int, h: int) -> str:
    e = html.escape
    flair = f"<div class='flair'>{e(post.flair)}</div>" if post.flair else ""
    body = md_to_html(_truncate(post.selftext, MAX_BODY_CHARS)) if post.selftext.strip() else ""
    img = f"<img class='media' src='{e(post.image_url)}'>" if post.image_url else ""
    comments = "".join(
        f"<div class='comment'><div class='who'><b>u/{e(c.author)}</b> · ▲ {_fmt(c.score)}</div>"
        f"<div class='txt'>{md_to_html(_truncate(c.body, MAX_COMMENT_CHARS))}</div></div>"
        for c in post.comments
    )
    comments_block = f"<div class='ch'>Top comments</div>{comments}" if comments else ""
    return _page(CARD_CSS, f"""
      <div class='top'><div class='brand'>Bolly Gossip Daily</div><div class='chip'>#{rank}</div></div>
      <div class='card'>
        <div class='sub'><b>r/{e(post.subreddit)}</b> · posted by u/{e(post.author)}</div>
        {flair}<h1>{e(post.title)}</h1>
        <div class='body'>{body}</div>{img}
        <div class='meta'><span><b>▲ {_fmt(post.score)}</b> upvotes</span><span>💬 {_fmt(post.num_comments)} comments</span></div>
      </div>
      {comments_block}""", w, h)


def _source(post: Post) -> str:
    """Where the clip originally came from, e.g. 'instagram.com', for the credit line."""
    host = re.sub(r"^(www\.|m\.)", "", urlparse(post.video_url or "").netloc.lower())
    return "" if not host or host.endswith("reddit.com") or host == "v.redd.it" else host


NEWS_CSS = CARD_CSS + """
body { display: flex; flex-direction: column; justify-content: center; }
.news-title { font-size: 60px; }
.kind { display: inline-block; background: #ff4f8b; color: #fff; font-weight: 800; letter-spacing: 2px;
        font-size: 24px; padding: 6px 16px; border-radius: 8px; margin-bottom: 20px; }
.src { margin-top: 26px; font-size: 30px; color: #b8bcc2; }
.src b { color: #ff8bb0; }
"""


def news_card_html(post: Post, rank: int, w: int, h: int) -> str:
    e = html.escape
    img = f"<img class='media' style='margin:0 0 26px' src='{e(post.image_url)}'>" if post.image_url else ""
    when = datetime.fromtimestamp(post.created_utc, timezone.utc).strftime("%d %b %Y") if post.created_utc else ""
    summary = f"<div class='body'><p>{e(_truncate(post.selftext, 420))}</p></div>" if post.selftext else ""
    return _page(NEWS_CSS, f"""
      <div class='top'><div class='brand'>Bolly Gossip Daily</div><div class='chip'>#{rank}</div></div>
      <div class='card'>
        <div class='kind'>NEWS</div>
        {img}<h1>{e(post.title)}</h1>{summary}
        <div class='src'>Source: <b>{e(post.author)}</b>{' · ' + when if when else ''}</div>
      </div>""", w, h)


def overlay_html(post: Post, rank: int, w: int, h: int) -> str:
    """Title band on top, credit badge at the bottom. When the clip came from another
    site (Instagram, YouTube, ...) the original uploader gets the headline credit and
    the Reddit poster is credited for sharing it."""
    e = html.escape
    site = _source(post)
    if post.platform == "youtube":
        who = e(post.author)
        sub = "on YouTube" + (f" · {_fmt(post.views)} views" if post.views else "")
    elif post.source and site:
        who = e(post.source)
        sub = f"on {e(site)} · shared by {e(post.handle)} on r/{e(post.subreddit)}"
    else:
        who = e(post.handle)
        sub = f"r/{e(post.subreddit)}" + (f" · via {e(site)}" if site else "")
    return _page(OVERLAY_CSS, f"""
      <div class='band'><span class='chip'>#{rank}</span> <span class='brand' style='margin-left:16px'>Bolly Gossip Daily</span>
        <div class='title'>{e(post.title)}</div></div>
      <div class='handle'><span class='lbl'>Video credit</span>{who} <span>· {sub}</span></div>""", w, h)


def intro_html(subreddit: str, count: int, day: datetime, w: int, h: int, label: str = "Daily compilation") -> str:
    return _page(INTRO_CSS, f"""
      <div class='brand'>{html.escape(label)}</div>
      <h1>Bollywood<br>Gossip<br>Daily</h1>
      <div class='date'>{day.strftime('%d %B %Y')}</div>
      <div class='src'>{count} trending stories from {html.escape(subreddit)}</div>""", w, h)


WELCOME_CSS = """
body { height: %(h)dpx; background: radial-gradient(circle at 50%% 35%%, #7a1650 0%%, #1b0f2e 55%%, #0d0d12 100%%); color: #fff;
       display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 0 70px; }
.hi { font-size: 56px; font-weight: 800; letter-spacing: 6px; color: #ffd400; text-transform: uppercase; }
h1 { font-size: 150px; line-height: 1.0; font-weight: 900; margin: 30px 0 34px; text-shadow: 0 8px 0 #ff2d6f, 0 0 50px rgba(255,45,111,.55); }
.date { font-size: 52px; font-weight: 700; color: #ffb3cf; }
.tag { margin-top: 70px; font-size: 40px; color: #d7dadc; font-weight: 700; }
"""


def welcome_html(day: datetime, w: int, h: int, lang: str = "en") -> str:
    hi = lang == "hi"
    return _page(WELCOME_CSS, f"""
      <div class='hi'>{'आपका स्वागत है' if hi else 'Welcome to'}</div>
      <h1>Daily Dose<br>of Bollywood</h1>
      <div class='date'>{day.strftime('%d %B %Y')}</div>
      <div class='tag'>{'आज की सबसे बड़ी बॉलीवुड खबरें' if hi else "Today's biggest Bollywood stories"}</div>""", w, h)


CREDITS_CSS = """
body { min-height: %(h)dpx; background: radial-gradient(circle at 70%% 80%%, #4a1238 0%%, #0d0d12 60%%); color: #fff;
       padding: 90px 70px; display: flex; flex-direction: column; justify-content: center; }
h1 { font-size: 80px; margin: 20px 0 10px; }
.sub { font-size: 32px; color: #b8bcc2; margin-bottom: 40px; }
.list { display: flex; flex-wrap: wrap; gap: 16px 20px; }
.list div { background: rgba(255,255,255,.08); border: 2px solid #ff4f8b; border-radius: 999px; padding: 10px 26px; font-size: 32px; font-weight: 700; }
.cta { margin-top: 56px; font-size: 38px; color: #ffb3cf; font-weight: 700; }
"""


def credits_html(handles: list[str], subreddit: str, w: int, h: int) -> str:
    e = html.escape
    chips = "".join(f"<div>{e(x)}</div>" for x in handles)
    return _page(CREDITS_CSS, f"""
      <div class='brand'>Credits</div>
      <h1>Thanks to today's creators</h1>
      <div class='sub'>Every post, clip, story and comment belongs to its original creator ({e(subreddit)}).</div>
      <div class='list'>{chips}</div>
      <div class='cta'>Follow for your daily dose of Bollywood gossip</div>""", w, h)


FRAME_CSS = """
body { min-height: %(h)dpx; background: linear-gradient(160deg, #1b0f2e 0%%, #0d0d12 55%%, #2a0f1f 100%%); padding: 90px 40px 120px;
       display: flex; flex-direction: column; }
.mid { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; }
.top { display: flex; justify-content: space-between; align-items: center; margin: 0 16px 20px; }
.credit { margin: 0 16px 30px; font-size: 30px; color: #d7dadc; }
.credit b { color: #ff8bb0; }
.shot { width: 100%%; max-width: 1000px; border-radius: 24px; box-shadow: 0 20px 60px rgba(0,0,0,.6); display: block; }
.thanks { margin: 30px 16px 0; font-size: 26px; color: #9aa0a6; text-align: center; }
"""

# Strips old.reddit.com down to the post and its top-k comments. Purely cosmetic:
# it only removes page chrome from content that is already visible.
CLEAN_THREAD_JS = """
(k) => {
  const junk = ['#header', '.side', '.footer-parent', '.debuginfo', '#sr-header-area', '.infobar',
    '.commentarea > .menuarea', '.commentarea > form', '.commentarea > .panestack-title',
    '.flat-list.buttons', '.morechildren', '.listingsignupbar', '.promotedlink', '.nav-buttons',
    '.expando-button', '.reportform', '.usertext-edit', '.bottommenu', '.read-next', '.onboardingbanner',
    '.linkinfo', '.sharelink', '.gold-wrap', '.awardings-bar'];
  junk.forEach(sel => document.querySelectorAll(sel).forEach(e => e.remove()));
  let kept = 0;
  document.querySelectorAll('.commentarea > .sitetable > .thing').forEach(c => {
    const author = c.getAttribute('data-author');
    const ok = c.classList.contains('comment') && !c.classList.contains('stickied')
      && author && author !== 'AutoModerator' && kept < k;
    if (ok) { kept++; c.querySelectorAll('.child').forEach(e => e.remove()); } else { c.remove(); }
  });
  const st = document.createElement('style');
  st.textContent = `body { background: #fff !important; margin: 0 !important; }
    .content { margin: 0 !important; padding: 16px 18px !important; max-width: none !important; }
    .md { font-size: 15px !important; line-height: 1.45 !important; max-width: none !important; }
    a.title { font-size: 19px !important; } .tagline { font-size: 12px !important; }
    .md-spoiler-text { color: inherit !important; background: #eee !important; }
    .comment .child, .comment .buttons, .thing .buttons { display: none !important; }`;
  document.head.appendChild(st);
  return kept;
}
"""


def frame_html(shot_png: bytes, post: Post, rank: int, w: int, h: int) -> str:
    e = html.escape
    src = "data:image/png;base64," + base64.b64encode(shot_png).decode()
    commenters = ", ".join(f"u/{e(c.author)}" for c in post.comments)
    thanks = f"<div class='thanks'>Comments by {commenters}</div>" if commenters else ""
    return _page(FRAME_CSS, f"""
      <div class='top'><div class='brand'>Bolly Gossip Daily</div><div class='chip'>#{rank}</div></div>
      <div class='credit'>Posted by <b>{e(post.handle)}</b> on r/{e(post.subreddit)}</div>
      <div class='mid'><img class='shot' src='{src}'></div>{thanks}""", w, h)


def old_reddit_url(permalink: str) -> str:
    return re.sub(r"^https://(www\.)?reddit\.com", "https://old.reddit.com", permalink) + "?sort=top"


class Renderer:
    """Renders at one frame size. Create one per output format (vertical shorts,
    landscape long video) and pass the same `shots` dict so each thread page is
    only fetched from Reddit once."""
    SHOT_CSS_WIDTH = 500  # old.reddit laid out at phone-ish width, captured at 2x DPI

    def __init__(self, browser: Browser, w: int | None = None, h: int | None = None,
                 shots: dict | None = None):
        self.b = browser
        self.w, self.h = w or browser.w, h or browser.h
        self.page = browser.new_context(viewport={"width": self.w, "height": self.h}).new_page()
        self.shots = shots if shots is not None else {}

    def _render(self, markup: str, out: Path, full_page: bool, transparent: bool = False) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.set_content(markup, wait_until="networkidle", timeout=20000)
        except Exception:  # noqa: BLE001 - a slow remote image shouldn't sink the card
            log.warning("Timed out waiting for images while rendering %s", out.name)
        self.page.screenshot(path=str(out), full_page=full_page, omit_background=transparent)
        return out

    def _capture(self, post: Post, comments: int) -> bytes | None:
        if post.id in self.shots:
            return self.shots[post.id]
        if "_page" not in self.shots:
            ctx = self.b.new_context(viewport={"width": self.SHOT_CSS_WIDTH, "height": 1200}, device_scale_factor=2)
            self.shots["_page"] = ctx.new_page()
        page = self.shots["_page"]
        url = old_reddit_url(post.permalink)
        shot = None
        try:
            self.b.polite_wait()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp is not None and resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
            page.wait_for_selector("#siteTable .thing.link", timeout=15000)
            kept = page.evaluate(CLEAN_THREAD_JS, comments)
            page.wait_for_timeout(500)  # let images in the post settle
            shot = page.locator("div.content[role=main]").first.screenshot(timeout=20000)
            log.debug("Captured %s with %d comments", url, kept)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not screenshot %s (%s); using a rendered card", url, str(e).splitlines()[0])
        self.shots[post.id] = shot
        return shot

    def thread_screenshot(self, post: Post, rank: int, comments: int, out: Path) -> Path | None:
        """Screenshot the public thread page. Returns None if it can't be captured
        (removed post, age gate, block page), so the caller can use a card instead."""
        shot = self._capture(post, comments)
        return self._render(frame_html(shot, post, rank, self.w, self.h), out, full_page=True) if shot else None

    def card(self, post: Post, rank: int, out: Path) -> Path:
        return self._render(card_html(post, rank, self.w, self.h), out, full_page=True)

    def news_card(self, post: Post, rank: int, out: Path) -> Path:
        return self._render(news_card_html(post, rank, self.w, self.h), out, full_page=True)

    def overlay(self, post: Post, rank: int, out: Path) -> Path:
        return self._render(overlay_html(post, rank, self.w, self.h), out, full_page=False, transparent=True)

    def intro(self, subreddit: str, count: int, out: Path, day: datetime | None = None,
              label: str = "Daily compilation") -> Path:
        day = day or datetime.now(timezone.utc)
        return self._render(intro_html(subreddit, count, day, self.w, self.h, label), out, full_page=False)

    def band(self, post: Post, rank: int, tag: str, out: Path) -> Path:
        return self._render(band_html(post, rank, tag, self.w, self.h), out, full_page=False, transparent=True)

    def news_panel(self, post: Post, rank: int, tag: str, facts: list[str], out: Path) -> Path:
        return self._render(news_panel_html(post, rank, tag, facts, self.w, self.h), out, full_page=False,
                            transparent=True)

    def badge(self, post: Post, out: Path) -> Path:
        return self._render(badge_html(post, self.w, self.h), out, full_page=False, transparent=True)

    def hook(self, top: Post, count: int, out: Path, day: datetime, rank: int = 1, brand: bool = False,
             headline: str = "", lang: str = "en", kicker: str = "") -> Path:
        return self._render(hook_html(top, count, day, self.w, self.h, rank, brand, headline, lang, kicker), out, full_page=False, transparent=True)

    def welcome(self, out: Path, day: datetime | None = None, lang: str = "en") -> Path:
        return self._render(welcome_html(day or datetime.now(timezone.utc), self.w, self.h, lang), out, full_page=False)

    def caption(self, text: str, out: Path) -> Path:
        return self._render(caption_html(text, self.w, self.h), out, full_page=False, transparent=True)

    def outro(self, out: Path) -> Path:
        return self._render(outro_html(self.w, self.h), out, full_page=False)

    def credits(self, handles: list[str], subreddit: str, out: Path) -> Path:
        return self._render(credits_html(handles, subreddit, self.w, self.h), out, full_page=True)


# ---------------------------------------------------------------------------
# Engagement layer: title band, bottom-right credit badge, hook opener, outro.

BAND_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.band { position: absolute; left: 0; right: 0; top: 0; padding: 44px 48px 90px;
        background: linear-gradient(180deg, rgba(0,0,0,.92) 0%%, rgba(0,0,0,.6) 65%%, rgba(0,0,0,0) 100%%); }
.row { display: flex; align-items: center; gap: 18px; }
.rank { font-size: 64px; font-weight: 900; color: #fff; background: #ff2d6f; border-radius: 18px; padding: 0 22px;
        line-height: 84px; box-shadow: 0 6px 24px rgba(255,45,111,.6); }
.tag { font-size: 30px; font-weight: 900; letter-spacing: 1px; color: #111; background: #ffd400; border-radius: 12px;
       padding: 8px 18px; transform: rotate(-2deg); }
.title { margin-top: 22px; color: #fff; font-size: %(title)dpx; font-weight: 900; line-height: 1.12;
         display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
         text-shadow: 0 3px 0 #000, 0 0 18px rgba(0,0,0,.9); }
.title em { font-style: normal; color: #ffd400; }
"""

BADGE_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.badge { position: absolute; right: 36px; bottom: %(bottom)dpx; max-width: 62%%; text-align: right;
         background: rgba(0,0,0,.72); border: 2px solid rgba(255,255,255,.25); border-radius: 18px;
         padding: 12px 22px; color: #fff; font-size: 30px; font-weight: 800; }
.badge small { display: block; font-size: 21px; font-weight: 600; color: #c9ccd1; letter-spacing: .5px; }
"""

HOOK_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.shade { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,.25), rgba(0,0,0,.15) 40%%, rgba(0,0,0,.8)); }
.box { position: absolute; left: 60px; right: 60px; bottom: %(bottom)dpx; }
.kicker { display: inline-block; background: #ffd400; color: #111; font-weight: 900; font-size: 38px; padding: 8px 22px;
          border-radius: 12px; transform: rotate(-2deg); }
h1 { margin: 22px 0 18px; font-size: %(big)dpx; line-height: 1.0; font-weight: 900; color: #fff;
     text-shadow: 0 5px 0 #ff2d6f, 0 0 30px rgba(0,0,0,.8); }
.tease { font-size: 40px; font-weight: 800; color: #fff; text-shadow: 0 2px 10px #000; }
.tease b { color: #ffd400; }
"""

OUTRO_CSS = """
body { height: %(h)dpx; background: radial-gradient(circle at 50%% 40%%, #5a1043 0%%, #0d0d12 65%%); color: #fff;
       display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 0 60px; }
h1 { font-size: 84px; font-weight: 900; line-height: 1.05; margin: 24px 0; }
.bell { font-size: 120px; }
p { font-size: 40px; color: #ffb3cf; font-weight: 700; }
"""

_EMPH = re.compile(r"\b(ANGRY|OUTBURST|SHOCK\w*|LEAK\w*|SECRET|EXPOSED|IGNORES?|SLAMS?|FIGHT|DIVORCE|BREAKUP|"
                   r"WEDDING|PREGNAN\w*|VIRAL|TWINS|STOPS|REFUSES?|CRIES|EMOTIONAL)\b", re.I)


def _headline(title: str) -> str:
    """Tidy a title for the band: drop the trailing hashtag run and emoji clutter,
    highlight drama words. Inline hashtags stay, since they are often the only
    place a name appears ("#aliabhatt STUNS ...")."""
    t = re.sub(r"(\s*#\w+)+\s*$", "", title)
    t = re.sub(r"#shorts\b", "", t, flags=re.I)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" |-·")
    return _EMPH.sub(lambda m: f"<em>{m[0]}</em>", html.escape(t))


def band_html(post: Post, rank: int, tag: str, w: int, h: int) -> str:
    n = len(re.sub(r"(\s*#\w+)+\s*$", "", post.title))
    title = (58 if n < 70 else 50 if n < 110 else 44) if h > w else (50 if n < 110 else 42)
    css = BAND_CSS.replace("%(title)d", str(title))
    return _page(css, f"""
      <div class='band'><div class='row'><span class='rank'>#{rank}</span><span class='tag'>{html.escape(tag)}</span></div>
        <div class='title'>{_headline(post.title)}</div></div>""", w, h)


def credit_text(post: Post) -> tuple[str, str]:
    """(main line, small line) for the bottom-right credit."""
    site = _source(post)
    if post.platform == "youtube":
        return post.author, "on YouTube" + (f" · {_fmt(post.views)} views" if post.views else "")
    if post.platform == "news" and post.ref_clip:
        return post.author, "news report" + (f" · clip: {post.source}" if post.source else "")
    if post.platform == "news" and post.source and post.video_url:
        return post.source, (f"on {site} · " if site else "") + f"via {post.author}"
    if post.platform == "news":
        return post.author, "news report"
    if post.source and site:
        return post.source, f"on {site} · via {post.handle}"
    return post.handle, f"r/{post.subreddit}" + (f" · {site}" if site else "")


def badge_html(post: Post, w: int, h: int) -> str:
    main, small = credit_text(post)
    bottom = 150 if h > w else 60
    css = BADGE_CSS.replace("%(bottom)d", str(bottom))
    return _page(css, f"<div class='badge'><small>CREDIT</small>{html.escape(main)}<small>{html.escape(small)}</small></div>", w, h)


BRAND_HOOK_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.shade { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,.78) 0%%, rgba(0,0,0,.15) 28%%, rgba(0,0,0,.2) 48%%, rgba(0,0,0,.88) 100%%); }
.brand { position: absolute; top: 70px; left: 0; right: 0; text-align: center; }
.logo { display: inline-block; padding: 18px 44px; border-radius: 22px; background: linear-gradient(90deg, #ff2d6f, #ff8a00);
        font-size: 58px; font-weight: 900; letter-spacing: 3px; text-transform: uppercase; color: #fff;
        box-shadow: 0 10px 40px rgba(255,45,111,.55); transform: rotate(-1.5deg); }
.date { margin-top: 18px; font-size: 34px; font-weight: 800; letter-spacing: 4px; color: #ffd400; text-shadow: 0 2px 10px #000; }
.box { position: absolute; left: 56px; right: 56px; bottom: %(bottom)dpx; }
.kicker { display: inline-block; background: #ffd400; color: #111; font-weight: 900; font-size: 44px; padding: 10px 26px;
          border-radius: 14px; transform: rotate(-2deg); }
h1 { margin: 26px 0 20px; font-size: %(big)dpx; line-height: 1.12; font-weight: 900; color: #fff;
     text-shadow: 0 5px 0 #ff2d6f, 0 0 34px rgba(0,0,0,.85); }
.more { font-size: 42px; font-weight: 800; color: #fff; text-shadow: 0 2px 10px #000; }
.more b { color: #ffd400; }
"""


def hook_html(top: Post, count: int, day: datetime, w: int, h: int, rank: int = 1, brand: bool = False,
              headline: str = "", lang: str = "en", kicker: str = "") -> str:
    big = 104 if h > w else 92
    bottom = 260 if h > w else 90
    tease = _headline(top.title)
    tease = tease if len(tease) < 110 else tease[:107].rsplit(" ", 1)[0] + "…"
    if brand:
        hi = lang == "hi"
        text = html.escape(headline) if headline else "Today's biggest Bollywood drama"
        size = (84 if len(text) < 60 else 70) if h > w else 70
        css = BRAND_HOOK_CSS.replace("%(big)d", str(size)).replace("%(bottom)d", str(bottom + 40))
        return _page(css, f"""
          <div class='shade'></div>
          <div class='brand'><span class='logo'>Daily Dose of Bollywood</span>
            <div class='date'>{day.strftime('%d %b %Y').upper()}</div></div>
          <div class='box'><span class='kicker'>{html.escape(kicker) if kicker else ('🔥 आज का सबसे बड़ा धमाका' if hi else "🔥 TODAY'S BIGGEST DRAMA")}</span>
            <h1>{text}</h1>
            <div class='more'>{'टॉप %d खबरें, देखते रहिए 👇' % count if hi else 'TOP %d STORIES, STAY TILL THE END 👇' % count}</div></div>""", w, h)
    css = HOOK_CSS.replace("%(big)d", str(big)).replace("%(bottom)d", str(bottom))
    return _page(css, f"""
      <div class='shade'></div>
      <div class='box'><span class='kicker'>{day.strftime('%d %b').upper()} · TOP {count}</span>
        <h1>Today's biggest<br>Bollywood drama</h1>
        <div class='tease'><b>#{rank}:</b> {tease}</div></div>""", w, h)


CAPTION_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.cap { position: absolute; left: 40px; right: 40px; top: 60%%; text-align: center; font-size: 92px; font-weight: 900;
       line-height: 1.12; color: #fff; -webkit-text-stroke: 10px #000; paint-order: stroke fill;
       text-shadow: 0 6px 0 #ff2d6f, 0 0 26px rgba(0,0,0,.8); }
"""


def caption_html(text: str, w: int, h: int) -> str:
    size = 92 if len(text) < 22 else 76 if len(text) < 34 else 62
    css = CAPTION_CSS.replace("92px", f"{size}px")
    return _page(css, f"<div class='cap'>{html.escape(text)}</div>", w, h)


def outro_html(w: int, h: int) -> str:
    return _page(OUTRO_CSS, """
      <div class='bell'>🔔</div><h1>Follow for daily<br>Bollywood gossip</h1>
      <p>New drama every day. All credit to the original creators.</p>""", w, h)



NEWS_PANEL_CSS = """
html, body { background: transparent; height: %(h)dpx; }
.shade { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,.35), rgba(0,0,0,.2) 35%%, rgba(0,0,0,.75) 70%%, rgba(0,0,0,.9)); }
.facts { position: absolute; left: 48px; right: 48px; bottom: %(bottom)dpx; }
.live { display: inline-block; background: #ff2d2d; color: #fff; font-weight: 900; font-size: 30px; letter-spacing: 3px;
        padding: 8px 20px; border-radius: 10px; box-shadow: 0 6px 24px rgba(255,45,45,.55); }
.fact { margin-top: 22px; background: rgba(12,12,16,.82); border-left: 10px solid #ffd400; border-radius: 16px;
        padding: 20px 26px; color: #fff; font-size: 38px; font-weight: 700; line-height: 1.25; }
.out { margin-top: 18px; font-size: 26px; color: #ffd400; font-weight: 800; letter-spacing: 1px; }
"""


def news_panel_html(post: Post, rank: int, tag: str, facts: list[str], w: int, h: int) -> str:
    """For news played over a reference clip: the band (rank, tag, headline) on top and the
    story's key facts as cards in the lower half, so the story reads with the sound off."""
    n = len(re.sub(r"(\s*#\w+)+\s*$", "", post.title))
    title = (58 if n < 70 else 50 if n < 110 else 44) if h > w else (50 if n < 110 else 42)
    css = (BAND_CSS + NEWS_PANEL_CSS).replace("%(title)d", str(title)).replace("%(bottom)d", str(330 if h > w else 140))
    items = "".join(f"<div class='fact'>{html.escape(f)}</div>" for f in facts)
    return _page(css, f"""
      <div class='shade'></div>
      <div class='band'><div class='row'><span class='rank'>#{rank}</span><span class='tag'>{html.escape(tag)}</span></div>
        <div class='title'>{_headline(post.title)}</div></div>
      <div class='facts'><span class='live'>NEWS</span>{items}<div class='out'>SOURCE: {html.escape(post.author.upper())}</div></div>""", w, h)
