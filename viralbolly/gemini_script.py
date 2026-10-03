"""Voiceover script written by the Gemini CLI (Hindi or English), with word budgets
so every line fits the time its story has on screen.

The CLI is run headless (`gemini -p`). It needs a working login or GEMINI_API_KEY in
the environment; if it fails for any reason the caller falls back to the template
lines in hindi.py / voice.py, so a run never stops on this.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from .models import Post

log = logging.getLogger(__name__)

WORDS_PER_SECOND = {"hi": 1.9, "en": 2.6}  # measured on the local voices, with pauses
MODEL = "gemini-2.5-flash"


def budget_words(seconds: float, lang: str) -> int:
    return max(5, int(seconds * WORDS_PER_SECOND.get(lang, 2.2)))


def _prompt(items: list[dict], lang: str, hook_words: int) -> str:
    language = ("natural spoken Hindi in Devanagari script (names and English loanwords also in Devanagari)"
                if lang == "hi" else "plain, punchy spoken English")
    return f"""You write the voiceover for a daily Bollywood gossip YouTube Short, read by one friendly, lively, conversational anchor who talks like a real person, not a news reader.
Write in {language}.

Rules:
- Use ONLY facts in the given title/text. No new claims, no speculation presented as fact, nothing defamatory. If a title is a question or rumour, say so ("reports say", "fans are asking"). Reddit posts are rumours and discussion: say that they come from Reddit.
- Each story is an object with a word budget. Stay UNDER it; the line must be speakable in the time shown.
- Split every line into 1 to 3 short sentences (array). A pause is added between sentences, so put the beat or the punchline in its own sentence. Start each story's first sentence with its number ("Number 4" / "नंबर चार") except story 1, which starts "And number one" / "और अब नंबर एक".
- Clips (kind "video") have their own sound: keep the line short and only set it up, then stop so the clip can play.
- Cards (kind "text"/"news") are still images: the voice carries them, so say what the post is about and why people care.
- If a title says little, say only what it says and nothing more.
- Also write an "outro" (max 8 words, invite to follow).
- Output ONLY JSON: {{"outro": [..], "stories": {{"<id>": [..sentences..], ...}}}}

Stories (play order):
{json.dumps(items, ensure_ascii=False, indent=1)}"""


def build_prompt(posts_in_play_order: list[tuple[int, Post, float]], lang: str, hook_seconds: float) -> str:
    """posts_in_play_order: [(number, post, seconds available for the voice)]."""
    items = []
    for num, post, secs in posts_in_play_order:
        items.append({"id": post.id, "number": num, "kind": post.kind, "platform": post.platform,
                      "title": clean_title(post.title), "text": post.selftext[:400],
                      "top_comment": post.comments[0].body[:200] if post.comments else "",
                      "word_budget": budget_words(secs, lang)})
    return _prompt(items, lang, budget_words(hook_seconds, lang))


GENERIC_TAGS = {"shorts", "short", "viral", "reel", "reels", "trending", "vlog", "fashion", "actress", "actor", "bollywood",
                "ytshorts", "youtubeshorts", "couplegoals", "spotted", "paps", "celebrity", "instagram", "explore", "fyp"}


def clean_title(title: str) -> str:
    """Title for the prompt: emoji dropped, and hashtags turned into the names they spell
    (#aishwaryaraibachchan -> Aishwarya Rai Bachchan) because they are often the only place a name appears."""
    from .voice import split_tag

    def tag(m):
        t = m[1]
        return "" if t.lower() in GENERIC_TAGS else split_tag(t)

    t = re.sub(r"[🀀-🫿☀-➿‍️]", "", title)
    t = re.sub(r"#(\w+)", tag, t)
    return " ".join(t.split())


def compact(prompt: str) -> str:
    """One-line version for Google's AI Mode: it answers a single paragraph reliably but
    returned nothing for the multi-line pretty-printed prompt."""
    head, _, tail = prompt.partition("Stories (play order):")
    items = json.loads(tail)
    for it in items:
        for k in ("platform", "top_comment", "text"):
            if not it.get(k):
                it.pop(k, None)
        it["title"] = clean_title(it["title"])
    one = " ".join(head.split())
    return f"{one} Stories in play order: {json.dumps(items, ensure_ascii=False, separators=(',', ':'))}"


def parse_reply(text: str, ids: list[str]) -> dict:
    """Pull the script JSON out of a reply. Code fences and chatter are fine, and so is
    the prompt's own `{"hook": [..]}` template echoed above the answer: the last block
    that parses as a script wins."""
    dec = json.JSONDecoder()
    data = None
    for m in reversed(list(re.finditer(r"\{\s*\"hook\"", text))):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("stories"), dict) and obj["stories"]:
            data = obj
            break
    if data is None:
        raise ValueError("no script JSON in the reply")
    as_list = lambda v: [s.strip() for s in v if s.strip()] if isinstance(v, list) else [str(v)]  # noqa: E731
    stories = {k: as_list(v) for k, v in data["stories"].items()}
    missing = [i for i in ids if not stories.get(i)]
    if missing:
        raise ValueError(f"missing stories in the reply: {missing}")
    return {"hook": as_list(data.get("hook", [])), "hook_story": data.get("hook_story"), "outro": as_list(data.get("outro", [])), "stories": stories}


AI_MODE_URL = "https://www.google.com/search?udm=50"


def ask_ai_mode(context, prompt: str, ids: list[str], timeout: int = 120) -> dict | None:
    """Ask Google's AI Mode (no sign-in needed) in a Playwright browser context and
    return the parsed script, or None if Google blocks it, changes its page, or the
    answer isn't usable."""
    import time

    page = context.new_page()
    try:
        page.goto(AI_MODE_URL, wait_until="domcontentloaded", timeout=45000)
        if "/sorry/" in page.url or "consent." in page.url:
            raise RuntimeError(f"Google showed a block/consent page ({page.url[:60]})")
        box = page.locator("textarea").first
        box.wait_for(timeout=20000)
        box.fill(compact(prompt))
        box.press("Enter")
        end, last_err = time.time() + timeout, None
        while time.time() < end:
            time.sleep(4)
            if "/sorry/" in page.url:
                raise RuntimeError("Google asked for a captcha")
            body = page.evaluate("document.body.innerText")
            # The reply is done once the page says so; before that the JSON may be cut off.
            if "response is ready" not in body.lower() and "use code with caution" not in body.lower():
                continue
            try:
                return parse_reply(body, ids)
            except ValueError as e:
                last_err = e
        raise RuntimeError(f"no usable answer within {timeout}s ({last_err})")
    except Exception as e:  # noqa: BLE001
        log.warning("Google AI Mode script failed (%s)", e)
        return None
    finally:
        page.close()


def _ask_api(prompt: str, key: str, timeout: int) -> str:
    """Gemini API (generativelanguage.googleapis.com) with a free AI Studio key."""
    import requests

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"}},
        timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini API {r.status_code}: {r.text[:200]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def write_script(posts_in_play_order: list[tuple[int, Post, float]], lang: str, hook_seconds: float,
                 timeout: int = 240) -> dict | None:
    """Ask Gemini. Uses the API when GEMINI_API_KEY is set (the Gemini CLI's free Google login is no
    longer accepted), else the Gemini CLI. Returns the script, or None if it failed."""
    import os

    prompt = build_prompt(posts_in_play_order, lang, hook_seconds)
    ids = [p.id for _, p, _ in posts_in_play_order]
    try:
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if key:
            return parse_reply(_ask_api(prompt, key, timeout), ids)
        exe = shutil.which("gemini")
        if not exe:
            log.warning("no GEMINI_API_KEY and no gemini CLI; using template script")
            return None
        proc = subprocess.run([exe, "-m", MODEL, "-p", prompt, "-o", "json"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip().splitlines()[0][:300])
        outer = json.loads(proc.stdout[proc.stdout.index("{"):])
        return parse_reply(outer.get("response", ""), ids)
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini script failed (%s); using template script", e)
        return None
