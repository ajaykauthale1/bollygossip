"""One interest score across Reddit, YouTube and news, so the most gripping items
lead (the hook, the top of each short) and weak ones fall to the end or out.

score = popularity within its own source (0..1, by that source's virality)
        x source weight (videos hold attention better than cards)
        + drama bonus for headline words that make people stop scrolling
"""
from __future__ import annotations

import re

from .models import Post

SOURCE_WEIGHT = {"youtube": 1.0, "reddit": 0.95, "news": 0.7}
VIDEO_BONUS = 0.15

# Words that signal a moment worth watching. Weight = how strongly.
DRAMA = {
    "angry": .35, "outburst": .35, "loses cool": .35, "lashes": .3, "slams": .3, "fight": .35, "spat": .3,
    "breakup": .35, "break up": .35, "divorce": .35, "split": .25, "cheating": .35, "affair": .3,
    "dating": .25, "wedding": .2, "married": .2, "pregnan": .3, "baby": .15, "engaged": .25,
    "leaked": .35, "leak": .3, "secret": .25, "exposed": .35, "blind item": .3, "rumour": .2, "rumor": .2,
    "ignores": .3, "refuses": .25, "snubs": .3, "walks out": .3, "storms": .3, "cries": .3, "tears": .25,
    "emotional": .2, "trolled": .25, "backlash": .25, "controversy": .3, "reacts": .15, "viral": .2,
    "oops": .25, "wardrobe": .25, "slip": .2, "paps": .15, "paparazzi": .15, "spotted": .1,
    "shocking": .3, "shock": .25, "banned": .25, "arrested": .35, "police": .25, "legal": .2,
    "hospital": .25, "twins": .15, "kiss": .2, "hug": .1, "dance": .1,
}
MAX_DRAMA = 0.5


def drama(title: str) -> float:
    t = title.lower()
    return min(MAX_DRAMA, sum(w for k, w in DRAMA.items() if re.search(r"\b" + re.escape(k), t)))


# Clip types that fill feeds every day but rarely hold attention (talk-show promo cuts,
# outfit/gym sightings). They can still make it, just behind real stories.
FILLER = ("podcast", "rapid fire", "photoshoot", "workout", "fitness", "fashion", "ootd", "outfit", "gym",
          "looks stunning", "looks dapper", "#vlog", "vlog", "promote", "promotes", "promotion", "unboxing")
FILLER_PENALTY = 0.25


def buzz(found: dict[str, list[Post]]) -> dict[str, int]:
    """For each item: how many items from OTHER sources mention the same person."""
    from .history import people

    who = {p.id: people(p.title) for posts in found.values() for p in posts}
    out = {}
    for source, posts in found.items():
        others = [who[q.id] for s2, qs in found.items() if s2 != source for q in qs]
        for p in posts:
            out[p.id] = sum(1 for o in others if who[p.id] & o)
    return out


def rank(found: dict[str, list[Post]], mode: str = "views", hours: float = 24) -> list[Post]:
    """mode "views": popularity within each source (views/upvotes per hour).
    mode "fresh": newest first, plus how many sources are covering the same person;
    views and likes don't count."""
    import time

    now = time.time()
    talk = buzz(found) if mode == "fresh" else {}
    scored = []
    for source, posts in found.items():
        ordered = sorted(posts, key=lambda p: p.virality, reverse=True)
        n = len(ordered)
        for i, p in enumerate(ordered):
            has_clip = p.kind == "video" or bool(p.video_url)
            bonus = (VIDEO_BONUS / 2 if p.ref_clip else VIDEO_BONUS) if has_clip else 0
            if mode == "fresh":
                age_h = (now - p.created_utc) / 3600 if p.created_utc else hours
                fresh = max(0.0, 1 - age_h / max(hours * 1.5, 1))
                low = p.title.lower()
                s = (fresh + 0.15 * min(talk.get(p.id, 0), 3) + drama(p.title) + bonus
                     - (FILLER_PENALTY if any(f in low for f in FILLER) else 0))
            else:
                pop = 1 - i / max(n, 1)
                s = pop * SOURCE_WEIGHT.get(source, 0.8) + drama(p.title) + bonus
            p.interest = round(s, 3)
            scored.append((s, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return dedupe([p for _, p in scored])


def tag(post: Post) -> str:
    """Short hook label shown next to the rank."""
    t = post.title.lower()
    if any(k in t for k in ("leak", "exposed", "secret", "blind item")):
        return "LEAKED"
    if any(k in t for k in ("angry", "outburst", "slams", "fight", "lashes", "loses cool", "backlash", "controversy")):
        return "DRAMA ALERT"
    if any(k in t for k in ("breakup", "divorce", "split", "dating", "wedding", "married", "affair", "engaged")):
        return "LOVE LIFE"
    if post.kind == "news":
        return "BREAKING"
    if post.views >= 100_000:
        return f"{post.views // 1000}K VIEWS"
    if post.platform == "reddit":
        return "TRENDING ON REDDIT"
    return "VIRAL NOW"


# Same person + same occasion = the same story (e.g. five "Ranbir's birthday" clips).
EVENTS = ("birthday", "wedding", "anniversary", "engagement", "ramp", "airport", "fashion week", "pfw",
          "trailer", "teaser", "box office", "premiere", "screening", "party", "funeral", "reception")


def dedupe(posts: list[Post], max_per_person: int = 2) -> list[Post]:
    """Keep the best-ranked item per person+occasion, and at most `max_per_person`
    items about any one person, so the compilation doesn't loop on one story."""
    from .news import names

    full: set[str] = set()
    first: dict[str, str] = {}
    for p in posts:
        for n in names(p.title):
            key = re.sub(r"[^a-z]", "", n.lower())
            if len(key) >= 6:
                full.add(key)
                f = re.sub(r"[^a-z]", "", n.split()[0].lower())
                if len(f) >= 4:
                    first.setdefault(f, key)

    def people(title: str) -> set[str]:
        low = title.lower()
        flat = re.sub(r"[^a-z]", "", low)
        found = {k for k in full if k in flat}
        found |= {k for f, k in first.items() if re.search(r"\b" + f, low)}
        return found

    def words(title: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in ("shorts", "bollywood", "with", "after", "from")}

    per_person: dict[str, int] = {}
    seen_events: set[tuple[str, str]] = set()
    seen_words: list[set[str]] = []
    kept = []
    for p in posts:
        who = people(p.title)
        ws = words(p.title)
        if any(len(ws & o) / max(1, min(len(ws), len(o))) > 0.5 for o in seen_words):
            continue  # same headline from another outlet/channel
        low = p.title.lower()
        events = {e for e in EVENTS if e in low}
        if any(per_person.get(k, 0) >= max_per_person for k in who):
            continue
        if any((k, e) in seen_events for k in who for e in events):
            continue
        for k in who:
            per_person[k] = per_person.get(k, 0) + 1
            seen_events.update((k, e) for e in events)
        seen_words.append(ws)
        kept.append(p)
    return kept
