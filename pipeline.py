#!/usr/bin/env python3
"""Daily r/BollyBlindsNGossip compilations.

  1. Read the last 24h of public posts and rank them by virality (upvotes + comments).
  2. Video posts: download the clip and credit the poster's handle on screen.
  3. Text/image posts: screenshot the thread with its top comments, credited.
  4. Stitch them into:
       - shorts: a few vertical videos of up to 2 minutes each (hottest posts first)
       - long:   one landscape video of up to 20 minutes with every trending post
     Each video opens with an intro card and ends with a credits card listing
     every creator featured.

Output goes to output/<YYYY-MM-DD>/ (short_1.mp4, short_2.mp4, long.mp4) with a
manifest.json of what was used where.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from viralbolly import captions, cleanup, fetch, gemini_script, hindi, history, media, news, newsclips, rank, render, stitch, upload, voice, youtube
from viralbolly.browser import Browser, RedditBlocked
from viralbolly.models import Post

log = logging.getLogger("viralbolly")

HOOK_CUT = 0.9       # seconds per moment in the cold-open montage
HOOK_MOMENTS = 5
INTRO_SECONDS = 3.0  # static opener when there are no clips for a montage
OUTRO_SECONDS = 2.5
VO_HOOK_SECONDS = 3.2   # with --voiceover: spoken hook over a quick montage
VO_CARD_MAX = 14.0      # with --voiceover: longest a still card stays up in a short

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict:
    """config.json next to this script sets defaults for any option (command-line flags win)."""
    if not CONFIG_FILE.exists():
        return {}
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {k.replace("-", "_"): v for k, v in cfg.items() if not k.startswith("_")}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ffmpeg", help=r"ffmpeg executable or its folder, e.g. C:\ffmpeg\bin (else FFMPEG_PATH, then PATH)")
    p.add_argument("--sources", default="reddit,youtube,news", help="comma-separated: reddit, youtube, news")
    p.add_argument("--subreddit", default="BollyBlindsNGossip", help="one or more, comma-separated")
    p.add_argument("--youtube-channels", nargs="*", default=youtube.YOUTUBE_CHANNELS,
                   help="YouTube channel handles to pull trending clips from")
    p.add_argument("--youtube-top", type=int, default=25, help="YouTube clips to collect")
    p.add_argument("--news-top", type=int, default=10, help="news stories to collect")
    p.add_argument("--video-boost", type=float, default=2.5,
                   help="multiply video posts' virality by this so more clips make the cut (1 = no preference)")
    p.add_argument("--block-domains", nargs="*", default=[],
                   help="extra sites to skip, added to the built-in list in viralbolly/sources.py")
    p.add_argument("--block-uploaders", nargs="*", default=[],
                   help="extra uploader/channel names to skip (substring match)")
    p.add_argument("--allow-verified", action="store_true",
                   help="allow clips from verified channels (skipped by default: usually companies)")
    p.add_argument("--top", type=int, default=30, help="trending posts to collect (the long video uses as many as fit)")
    p.add_argument("--formats", default="shorts,long", help="comma-separated: shorts, long")
    p.add_argument("--shorts", type=int, default=2, help="number of short videos")
    p.add_argument("--short-seconds", type=float, default=120, help="max length of each short")
    p.add_argument("--short-size", default="1080x1920", help="WIDTHxHEIGHT of the shorts (vertical)")
    p.add_argument("--short-clip-seconds", type=float, default=25, help="trim video clips in shorts to this")
    p.add_argument("--long-seconds", type=float, default=1200, help="max length of the long video")
    p.add_argument("--long-size", default="1920x1080", help="WIDTHxHEIGHT of the long video (landscape)")
    p.add_argument("--long-clip-seconds", type=float, default=75, help="trim video clips in the long video to this")
    p.add_argument("--comments", type=int, default=3, help="top comments shown with each text post")
    p.add_argument("--style", choices=["screenshot", "card"], default="screenshot",
                   help="screenshot the real thread (default) or draw a branded card from the post data")
    p.add_argument("--hours", type=float, default=24, help="only consider posts newer than this")
    p.add_argument("--min-score", type=int, default=20, help="ignore posts below this upvote score")
    p.add_argument("--music", help="audio file played under intro, credits and text posts")
    p.add_argument("--music-volume", type=float, default=0.3)
    p.add_argument("--include-nsfw", action="store_true")
    p.add_argument("--allow-repeats", action="store_true", help="include posts already used on earlier days")
    p.add_argument("--repeat-days", type=int, default=3,
                   help="skip items on the same person and channel/topic as a short from the last N days (0 = off)")
    p.add_argument("--news-clips", action="store_true",
                   help="open each news article and use the Instagram/X/YouTube video embedded in it")
    p.add_argument("--ref-clips", action="store_true",
                   help="news with no video: play a recent clip of the same person, muted, under the headline and key facts")
    p.add_argument("--news-need-clip", action="store_true",
                   help="shorts: leave out news stories with no video (embedded or --ref-clips reference clip) "
                        "instead of showing a news card")
    p.add_argument("--rank", choices=["views", "fresh"], default="views",
                   help="views: most viewed/upvoted per hour; fresh: newest first plus cross-source buzz, ignoring views")
    p.add_argument("--max-per-channel", type=int, default=2, help="most clips from one channel/outlet in one short (0 = no limit)")
    p.add_argument("--voiceover", action="store_true",
                   help="spoken hook, a line introducing each story, whoosh cues and a sign-off (offline Piper voice)")
    p.add_argument("--voice-style", choices=["full", "intro"], default="full",
                   help="full: a spoken line per story and a sign-off; intro: only the opening hook is spoken, then video "
                        "clips play their own sound (text cards, which have no sound, keep their line)")
    p.add_argument("--voice", default=voice.DEFAULT_VOICE, help="Piper voice name in voices/ (or an .onnx path)")
    p.add_argument("--tts", choices=["piper", "chatterbox", "chatterbox-server"], default="piper",
                   help="voice engine; chatterbox-server uses a running hindi_server.py over HTTP")
    p.add_argument("--script", choices=["template", "aimode", "gemini", "file", "auto"], default="template",
                   help="who writes the voiceover lines: Google AI Mode in a browser, the Gemini CLI, a saved reply, or fixed templates; any failure falls back to templates")
    p.add_argument("--lang", choices=["en", "hi"], default="en", help="language of the voiceover script")
    p.add_argument("--tts-url", default="http://127.0.0.1:8765", help="chatterbox-server address")
    p.add_argument("--tts-style", type=json.loads, default={},
                   help='chatterbox-server settings as JSON, e.g. {"exaggeration": 0.9, "cfg_weight": 0.3}')
    p.add_argument("--voice-sample", help="chatterbox: a 10-20 s WAV of the voice to clone")
    p.add_argument("--denoise", action="store_true", help="reduce background hiss and crowd noise in clip audio")
    p.add_argument("--tts-autostart", action=argparse.BooleanOptionalAction, default=False,
                   help="start the Chatterbox server (run_server.ps1 --port N from --tts-folder) when the run starts, "
                        "if it isn't already running, and stop it at the end")
    p.add_argument("--tts-folder", default="C:/Users/Ajay/Downloads/chatterbox-hindi",
                   help="folder holding run_server.ps1 for --tts-autostart")
    p.add_argument("--voice-speed", type=float, default=1.0, help="speed factor for the voiceover (1.1 = 10%% faster)")
    p.add_argument("--voice-pitch", type=float, default=0.0, help="shift the voiceover pitch in semitones (-2 = lower)")
    p.add_argument("--captions", action="store_true", help="bold word-group subtitles timed to the voiceover (shorts)")
    p.add_argument("--zoom", type=float, default=1.0, help="punch-in on video clips in shorts (1.12 = 12%%), crops source watermarks")
    p.add_argument("--clean-overlays", action=argparse.BooleanOptionalAction, default=False,
                   help="crop the outlet's own burned-in title/credit bands (e.g. Pinkvilla's white title card) off clips "
                        "and fill the space with a blurred copy of the clip")
    p.add_argument("--clean-overlays-fill", choices=["blur", "dark"], default="blur",
                   help="what fills the space left by --clean-overlays: blurred zoomed clip or a dark strip")
    p.add_argument("--clean-overlay-channels", type=json.loads, default=None,
                   help='fixed crops per channel when a clip cannot be analysed, JSON {"pinkvilla": {"top": 0.19, "bottom": 0}}')
    p.add_argument("--spoken-welcome", action="store_true", help="say the welcome line aloud before the hook (off: hook first)")
    p.add_argument("--short-max-items", type=int, default=0, help="most stories in one short (0 = as many as fit)")
    p.add_argument("--split-shorts", action="store_true",
                   help="short 1 = video clips only, short 2 = Reddit/news rumours only (each with its own opening)")
    p.add_argument("--prompts-only", action="store_true",
                   help="fetch the day's stories, save the plan (plan_posts.json, plan_ids.json) and the script prompts "
                        "(script_prompt_<short>_oneline.txt), then stop without rendering")
    p.add_argument("--use-saved-plan", action="store_true",
                   help="if a --prompts-only run already saved today's plan, use exactly those stories")
    p.add_argument("--last-clip-full", action="store_true",
                   help="in shorts, play the #1 clip to its end (up to --last-clip-max) instead of cutting it")
    p.add_argument("--last-clip-max", type=float, default=30.0, help="longest the #1 clip may run with --last-clip-full")
    p.add_argument("--fixed-plan", help='JSON {"short_1": [item ids, best first], ...}: use exactly these items in each short '
                                        "(to re-render a video you already approved)")
    p.add_argument("--welcome", action="store_true",
                   help='open with the "Daily Dose of Bollywood" brand and a spoken welcome, on top of the hook (needs --voiceover)')
    p.add_argument("--no-whoosh", action="store_true", help="no whoosh sound between stories")
    p.add_argument("--no-intro", action="store_true", help="skip the hook montage at the start")
    p.add_argument("--headed", action="store_true", help="show the browser window while it works")
    p.add_argument("--delay", type=float, default=3.0, help="minimum seconds between requests to Reddit")
    p.add_argument("--posts-json", help="use posts from this JSON file instead of calling Reddit")
    p.add_argument("--out", default="output")
    p.add_argument("-v", "--verbose", action="store_true")
    cfg = load_config()
    unknown = set(cfg) - {a.dest for a in p._actions}
    if unknown:
        p.error(f"unknown keys in config.json: {', '.join(sorted(unknown))}")
    p.set_defaults(**cfg)
    return p.parse_args(argv)


def _size(text: str) -> tuple[int, int]:
    w, h = (int(x) for x in text.lower().split("x"))
    return w, h


@dataclass
class Output:
    name: str      # file stem, e.g. "short_1"
    w: int
    h: int
    clip_max: float
    countdown: bool  # play weakest first and end on #1 (shorts), else best first
    posts: list      # [Post], best first
    label: str = "clips"  # "clips" or "rumours" when the shorts are split by kind


def plan_outputs(args, ranked: list[Post], clip_len: dict[str, float]) -> list[Output]:
    """Split the ranked items across the videos, filling each to its time budget.
    Shorts take turns picking from the top so each one has strong moments."""
    formats = {f.strip() for f in args.formats.split(",") if f.strip()}
    opener = 0 if args.no_intro else (VO_HOOK_SECONDS if args.voiceover else HOOK_MOMENTS * HOOK_CUT)

    def length(post: Post, clip_max: float, short: bool = False) -> float:
        if post.id in clip_len and post.ref_clip:  # muted reference clip: as long as the story takes to read
            return min(clip_len[post.id], VO_CARD_MAX)
        if post.id in clip_len:
            return max(min(clip_len[post.id], clip_max), stitch.title_seconds(post))
        if short and args.voiceover:
            return min(stitch.reading_seconds(post), VO_CARD_MAX)
        return stitch.reading_seconds(post)

    outputs = []
    if "shorts" in formats and args.shorts > 0:
        w, h = _size(args.short_size)
        shorts = [Output(f"short_{i}", w, h, args.short_clip_seconds, True, []) for i in range(1, args.shorts + 1)]
        used_s = [opener + OUTRO_SECONDS] * len(shorts)
        turn = 0
        for post in ranked:
            if args.news_need_clip and post.platform == "news" and post.id not in clip_len:
                continue  # a news story is narrated over a relevant clip, never shown as a news card
            for k in range(len(shorts)):
                i = (turn + k) % len(shorts)
                d = length(post, args.short_clip_seconds, short=True)
                chan = (post.source or post.author or "").lower()
                same = sum(1 for q in shorts[i].posts if (q.source or q.author or "").lower() == chan)
                if args.max_per_channel and same >= args.max_per_channel:
                    continue
                if used_s[i] + d <= args.short_seconds and not (args.short_max_items and len(shorts[i].posts) >= args.short_max_items):
                    shorts[i].posts.append(post)
                    used_s[i] += d
                    turn = i + 1
                    break
        if args.split_shorts and len(shorts) >= 2:
            cap = args.short_max_items or 5
            shorts[0].posts = [p for p in ranked if p.id in clip_len][:cap]
            shorts[1].posts = [p for p in ranked if p.id not in clip_len][:cap]
            shorts[1].label = "rumours"
            for extra in shorts[2:]:
                extra.posts = []
        if args.fixed_plan:
            by_id = {p.id: p for p in ranked}
            fixed = json.loads(Path(args.fixed_plan).read_text(encoding="utf-8"))
            for o in shorts:
                o.posts = [by_id[i] for i in fixed.get(o.name, []) if i in by_id]
        for o, t in zip(shorts, used_s):
            if o.posts and args.voiceover:
                lead_with_clip(o.posts, clip_len)
            if o.posts:
                outputs.append(o)
                log.info("%s: %d items, ~%.0fs", o.name, len(o.posts), t)
    if "long" in formats:
        w, h = _size(args.long_size)
        o, total = Output("long", w, h, args.long_clip_seconds, False, []), opener + OUTRO_SECONDS
        for post in ranked:
            d = length(post, args.long_clip_seconds)
            if total + d <= args.long_seconds:
                o.posts.append(post)
                total += d
        if o.posts:
            outputs.append(o)
            log.info("long: %d items, ~%.0fs", len(o.posts), total)
    return outputs


def lead_with_clip(posts: list[Post], clip_len: dict[str, float]) -> None:
    """Shorts count down, so the last item plays first. Reorder (in place) so a
    clip plays first and clips and still cards alternate: the first seconds after
    the hook decide whether people stay, and two stills in a row lose them.
    The #1 item stays last."""
    if len(posts) < 3:
        return
    top, rest = posts[0], posts[:0:-1]  # rest in play order
    clips = [p for p in rest if p.id in clip_len]
    cards = [p for p in rest if p.id not in clip_len]
    played: list[Post] = []
    while clips or cards:
        if clips and (not played or played[-1].id not in clip_len or not cards):
            played.append(clips.pop(0))
        else:
            played.append(cards.pop(0))
    posts[:] = [top] + played[::-1]


def source_label(found: dict[str, list[Post]], subreddit: str) -> str:
    parts = []
    if found.get("reddit"):
        parts.append("r/" + subreddit.replace(",", ", r/").replace("  ", " "))
    if found.get("youtube"):
        parts.append("YouTube")
    if found.get("news"):
        parts.append("news sites")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


def load_dotenv(path: Path = Path(__file__).resolve().parent / ".env") -> None:
    """Put KEY=VALUE lines from .env into the environment (e.g. GEMINI_API_KEY for the Gemini CLI).
    Existing environment variables win. Values are never printed."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    load_dotenv()
    media.set_ffmpeg(args.ffmpeg)
    ensure_server = None
    if args.voiceover and args.tts == "chatterbox-server" and args.tts_autostart and not args.prompts_only:
        import atexit

        server = voice.ServerProcess(args.tts_url, args.tts_folder, Path(args.out) / "chatterbox_server.log").start()
        atexit.register(server.stop)
        ensure_server = server.start  # starts it again if it has gone away; no-op while it answers
    stitch.DENOISE["on"] = args.denoise
    cleanup.SETTINGS.update(on=args.clean_overlays, fill=args.clean_overlays_fill)
    if args.clean_overlay_channels is not None:
        cleanup.SETTINGS["channels"] = args.clean_overlay_channels
    log.info("Using ffmpeg: %s", media.ffmpeg_exe())
    if args.music and not Path(args.music).is_file():
        sys.exit(f"Music file not found: {args.music}")

    today = datetime.now(timezone.utc)
    out_root = Path(args.out)
    day_dir = out_root / today.strftime("%Y-%m-%d")
    (day_dir / "clips").mkdir(parents=True, exist_ok=True)

    # Posts used on earlier days, so a post trending for two days isn't repeated.
    history_file = out_root / "used_posts.json"
    used = set(json.loads(history_file.read_text(encoding="utf-8"))) if history_file.exists() else set()

    w0, h0 = _size(args.short_size)
    finished: list[tuple[Output, Path]] = []
    with Browser(w0, h0, headless=not args.headed, delay=args.delay) as browser:
        # 1. Fetch each source, then rank everything on one interest score.
        sources = {s.strip() for s in args.sources.split(",") if s.strip()}
        skip = set() if args.allow_repeats else used
        found: dict[str, list[Post]] = {}
        saved_plan = False
        plan_posts_file, plan_ids_file = day_dir / "plan_posts.json", day_dir / "plan_ids.json"
        if args.use_saved_plan and not args.posts_json and plan_posts_file.exists() and plan_ids_file.exists():
            log.info("Using the plan saved earlier today (%s)", plan_posts_file.name)
            args.posts_json, args.fixed_plan, saved_plan = str(plan_posts_file), str(plan_ids_file), True
        if args.posts_json:  # saved items; sources missing from the file are still fetched live
            for post in fetch.load_posts_json(args.posts_json):
                found.setdefault(post.platform, []).append(post)
        if "reddit" in sources and "reddit" not in found:
            try:
                found["reddit"] = fetch.fetch_trending(
                    browser, args.subreddit, top_n=args.top, comments_per_post=args.comments,
                    hours=args.hours, min_score=args.min_score, include_nsfw=args.include_nsfw,
                    exclude_ids=skip, video_boost=args.video_boost, block_domains=set(args.block_domains),
                )
            except RedditBlocked as e:
                log.warning("Reddit listings refused (%s); falling back to the public RSS feed", e)
                try:
                    found["reddit"] = fetch.fetch_rss(args.subreddit, args.top, args.hours, skip, args.include_nsfw)
                    args.style = "card"  # thread pages are refused too; draw cards from the feed instead
                except Exception as e2:  # noqa: BLE001
                    log.warning("Skipping Reddit today: %s", e2)
        if "youtube" in sources and "youtube" not in found:
            found["youtube"] = youtube.fetch_youtube(args.youtube_top, args.hours, args.youtube_channels,
                                                     exclude_ids=skip, fresh=args.rank == "fresh")
        if "news" in sources and "news" not in found:
            found["news"] = news.fetch_news(args.news_top, args.hours, exclude_ids=skip)
        if found.get("news") and args.news_clips:
            log.info("News: %d of %d stories have an embedded clip",
                     newsclips.attach_embeds(found["news"], set(args.block_uploaders)), len(found["news"]))
        if found.get("news") and args.ref_clips:
            log.info("News: %d stories got a reference clip",
                     newsclips.attach_reference(found["news"], set(args.block_uploaders)))
        ranked = rank.rank(found, args.rank, args.hours)
        if not args.allow_repeats and not saved_plan and args.repeat_days > 0:
            ranked = history.drop_repeats(ranked, out_root, today, args.repeat_days)
        if not ranked:
            log.warning("No posts qualified today; nothing to compile.")
            return 1
        log.info("Selected %d items: %s", len(ranked), ", ".join(f"{len(v)} {k}" for k, v in found.items()))
        for i, p in enumerate(ranked[:10], 1):
            log.info("  interest #%d %.2f [%s] %s", i, p.interest, p.platform, p.title[:70])

        # 2. Download each video once (at the longest length any format needs).
        clips: dict[str, Path] = {}
        longest = max(args.short_clip_seconds, args.long_clip_seconds)
        for post in ranked:
            if post.kind != "video" and not post.video_url:
                continue
            meta: dict = {}
            clip = Path(post.local_video) if post.local_video else media.download_video(
                post.video_url, day_dir / "clips", post.id, int(longest),
                set(args.block_uploaders), args.allow_verified or post.platform == "youtube", meta)
            post.source = post.source or meta.get("uploader") or None
            if clip:
                clips[post.id] = clip
            else:
                log.info("%s: no downloadable video, using a %s instead", post.id,
                         "news card" if post.platform == "news" else "screenshot")
                post.kind = "news" if post.platform == "news" else "text"
                post.ref_clip = False
        clip_len = {pid: media.probe(c)["duration"] for pid, c in clips.items()}
        windows: dict[tuple[str, float], float] = {}

        def moment(post: Post, seconds: float) -> float:
            key = (post.id, seconds)
            if key not in windows:
                windows[key] = media.best_window(clips[post.id], seconds)
            return windows[key]

        # 3. Build each output
        script = hindi if args.lang == "hi" else voice  # module that writes the spoken lines
        narrator_all = voice.Narrator(day_dir / "voice", args.voice, engine=args.tts,
                                  voice_sample=args.voice_sample, cues=not args.no_whoosh,
                                  server=args.tts_url, style=args.tts_style,
                                  tempo=args.voice_speed, pitch=args.voice_pitch,
                                  ensure_server=ensure_server) if args.voiceover else None
        shots: dict = {}  # thread captures shared across formats
        renderers: dict[tuple[int, int], render.Renderer] = {}
        outputs = plan_outputs(args, ranked, clip_len)
        if args.prompts_only:
            json.dump([p.to_dict() | {"local_video": None} for p in ranked], plan_posts_file.open("w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            json.dump({o.name: [p.id for p in o.posts] for o in outputs if o.countdown},
                      plan_ids_file.open("w", encoding="utf-8"), indent=1)
        for out in outputs:
            if args.prompts_only and not out.countdown:
                continue  # only the shorts need scripts
            narrator = narrator_all if out.countdown else None  # the voiceover is for the shorts; the long video keeps clip sound
            r = renderers.get((out.w, out.h)) or renderers.setdefault(
                (out.w, out.h), render.Renderer(browser, out.w, out.h, shots))
            seg_dir = day_dir / "segments" / out.name
            seg_dir.mkdir(parents=True, exist_ok=True)
            card_dir = day_dir / "cards" / out.name
            music = stitch.MusicBed(args.music)

            # Items in play order with their on-screen number and length.
            n = len(out.posts)
            numbered = list(enumerate(out.posts, 1))
            if out.countdown:
                numbered.reverse()
            def cmax(post):  # longest this clip may play; the #1 story gets to finish
                return args.last_clip_max if (args.last_clip_full and out.countdown and post is out.posts[0]) else out.clip_max

            lengths = {p.id: min(clip_len[p.id], VO_CARD_MAX) if p.id in clips and p.ref_clip
                       else max(min(clip_len[p.id], cmax(p)), stitch.title_seconds(p)) if p.id in clips
                       else stitch.reading_seconds(p) for p in out.posts}
            hook_moments = [(clips[p.id], moment(p, min(clip_len[p.id], out.clip_max)) + 0.5)
                            for p in out.posts if p.id in clips][:1 if narrator else HOOK_MOMENTS]  # one clean clip, not rapid flashes
            opener = 0.0 if args.no_intro else (len(hook_moments) * HOOK_CUT if hook_moments else INTRO_SECONDS)
            hook_post = next((p for p in out.posts if p.id in clips), out.posts[0])
            if hook_moments:  # the first clip in out.posts is the one in hook_moments
                hook_moments = hook_moments[:1] if narrator else hook_moments
            outro_s = OUTRO_SECONDS
            welcome_s = 0.0
            # --welcome flow: no separate opener; the first story's clip starts at once, with the brand,
            # welcome and hook laid over its first seconds, then its own title and the video play on.
            flow = bool(args.welcome and narrator and not args.no_intro)
            if flow:
                opener, hook_moments = 0.0, []
            intro_png, intro_s, kicker = None, 0.0, ""
            caps: dict[str, list] = {}
            vo_secs: dict[str, float] = {}
            vo: dict[str, Path] = {}  # narration per item id, plus "hook" and "outro"
            if narrator:
                VO_CLIP_MAX, HOOK_MAX = 13.0, 7.0  # longest voice line over a clip / for the hook, seconds

                said: dict[str, list[str]] = {}

                def fit(lines, limit, whoosh=True, key=None):
                    """Speak the sentences; while the take runs over `limit`, drop the last sentence."""
                    lines = [t for t in re.split(r"(?<=[!?।.])\s+", lines.strip()) if t] if isinstance(lines, str) else list(lines)
                    while True:
                        path, secs = narrator.say(lines, whoosh=whoosh)
                        if secs <= limit or len(lines) == 1:
                            said[key] = lines
                            return path, secs
                        lines.pop()

                plan = None
                if args.script in ("gemini", "file", "aimode", "auto") or args.prompts_only:
                    slots = [(num, post, VO_CLIP_MAX - 1.5 if post.id in clips and not post.ref_clip else
                              (VO_CARD_MAX if out.countdown else lengths[post.id]) - 1.0) for num, post in numbered]
                if args.prompts_only:
                    prompt = gemini_script.build_prompt(slots, args.lang, HOOK_MAX - 1.0)
                    (day_dir / f"script_prompt_{out.name}.txt").write_text(prompt, encoding="utf-8")
                    (day_dir / f"script_prompt_{out.name}_oneline.txt").write_text(
                        gemini_script.compact(prompt), encoding="utf-8")
                    log.info("%s: wrote script_prompt_%s_oneline.txt", out.name, out.name)
                    continue
                elif args.script == "auto":
                    # A script saved for this short (by the evening Claude task) wins; else Gemini by API key
                    # or CLI; else the fixed template lines. Never stops the run.
                    reply = day_dir / f"script_{out.name}.json"
                    if reply.exists():
                        try:
                            plan = gemini_script.parse_reply(reply.read_text(encoding="utf-8"), [p.id for p in out.posts])
                        except (ValueError, KeyError) as e:
                            log.warning("%s: saved script unusable (%s)", out.name, e)
                    if plan is None:
                        plan = gemini_script.write_script(slots, args.lang, HOOK_MAX - 1.0)
                    log.info("%s: script by %s", out.name, "saved/Gemini script" if plan else "templates")
                elif args.script == "gemini":
                    plan = gemini_script.write_script(slots, args.lang, HOOK_MAX - 1.0)
                    log.info("%s: script by %s", out.name, "Gemini" if plan else "templates")
                elif args.script == "aimode":
                    # Google's AI Mode in a visible browser (it refuses headless), then templates if it fails.
                    br, ctx = browser.headed_context()
                    try:
                        plan = gemini_script.ask_ai_mode(
                            ctx, gemini_script.build_prompt(slots, args.lang, HOOK_MAX - 1.0), [p.id for p in out.posts])
                    finally:
                        br.close()
                    if plan:
                        (day_dir / f"script_{out.name}.json").write_text(
                            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
                    log.info("%s: script by %s", out.name, "Google AI Mode" if plan else "templates")
                elif args.script == "file":
                    # Script pasted from Gemini (e.g. in a browser): day_dir/script_<name>.json.
                    # No file yet: write the prompt to give Gemini and skip this video for now.
                    reply = day_dir / f"script_{out.name}.json"
                    if reply.exists():
                        plan = gemini_script.parse_reply(reply.read_text(encoding="utf-8"), [p.id for p in out.posts])
                    else:
                        prompt_file = day_dir / f"script_prompt_{out.name}.txt"
                        prompt_file.write_text(gemini_script.build_prompt(slots, args.lang, HOOK_MAX - 1.0),
                                               encoding="utf-8")
                        log.info("%s: wrote %s; save Gemini's reply as %s and run again", out.name,
                                 prompt_file.name, reply.name)
                        continue
                intro_only = args.voice_style == "intro"
                for k, (num, post) in enumerate(numbered):
                    if intro_only and post.id in clips and not post.ref_clip:
                        continue  # the clip's own sound carries it (a muted reference clip still needs the voice)
                    line = plan["stories"][post.id] if plan else script.item_line(post, num, first=k == 0)
                    limit = VO_CLIP_MAX if post.id in clips and not post.ref_clip else (VO_CARD_MAX if out.countdown else lengths[post.id])
                    vo[post.id], vo_s = fit(line, limit, key=post.id)
                    vo_secs[post.id] = vo_s
                    if post.id in clips:
                        lengths[post.id] = max(lengths[post.id], vo_s + 1.5)
                    else:
                        cap = VO_CARD_MAX if out.countdown else lengths[post.id]
                        lengths[post.id] = max(min(lengths[post.id], cap), vo_s + 0.8)
                est = VO_HOOK_SECONDS + sum(lengths.values()) + outro_s
                # The hook plays over one clip, and its words and headline must be about that clip.
                hs = plan.get("hook_story") if plan else None
                if hs in clips:
                    hook_post = next(p for p in out.posts if p.id == hs)
                    hook_moments = [(clips[hs], moment(hook_post, min(clip_len[hs], out.clip_max)) + 0.5)]
                elif plan:
                    log.warning("%s: hook_story %r is not a clip; hook plays over %s", out.name, hs, hook_post.id)
                hook_lines = list(plan["hook"]) if plan else [script.hook_line(n, est)]
                if flow:  # the hook has to fit the clip that is already playing, so it covers the whole list
                    hist = out_root / "hook_history.json"  # vary the opening: never the same two days running
                    rum = out.label == "rumours"
                    hooks_pool = script.HOOKS_RUMOUR if rum else script.HOOKS
                    kick_pool = script.KICKERS_RUMOUR if rum else script.KICKERS
                    hook_lines = script.flow_hook(n, voice.pick_variant(len(hooks_pool), f"hook_{args.lang}_{out.label}", hist),
                                                  out.label)
                    kicker = kick_pool[voice.pick_variant(len(kick_pool), f"kicker_{args.lang}_{out.label}", hist)]
                hook_text = " ".join(hook_lines)
                # --welcome: the welcome line and the hook share one opening over the hook clip
                spoken_open = ([script.WELCOME_LINE] if args.welcome and args.spoken_welcome else []) + hook_lines
                vo["hook"], hook_s = fit(spoken_open, HOOK_MAX + (3.0 if args.welcome and args.spoken_welcome else 0), whoosh=False,
                                         key="hook")
                if not intro_only:
                    vo["outro"], outro_vo = fit(plan["outro"] if plan else script.OUTRO_LINE, 6.0, key="outro")
                    outro_s = max(OUTRO_SECONDS, outro_vo + 0.4)
                if args.captions and out.countdown:
                    def make_caps(key, parts):
                        items = []
                        for wav, text, off in parts:
                            vf = voice.voice_only(wav)
                            items += captions.phrases(vf if vf.exists() else wav, text, offset=off)
                        return [(a, b, r.caption(t, card_dir / f"cap_{key}_{i}.png")) for i, (a, b, t) in enumerate(items)]

                    hook_len = media.probe(vo["hook"])["duration"] if flow else 0.0
                    for k, (_num, post) in enumerate(numbered):
                        if post.id not in vo:
                            continue
                        text = " ".join(said.get(post.id, []))
                        # first story in flow mode: its voice starts after the hook and a 0.3 s gap
                        caps[post.id] = make_caps(post.id, [(vo[post.id], text, hook_len + 0.3 if flow and k == 0 else 0.0)])
                if flow:
                    first = numbered[0][1]
                    intro_s = hook_s + 0.3
                    if first.id in vo:
                        vo[first.id], joined_s = voice.join_audio([vo["hook"], vo[first.id]], day_dir / "voice" /
                                                                  f"join_{out.name}_{first.id}.wav", gap=0.3)
                    else:  # intro-only: the hook is the only voice over the first clip
                        vo[first.id], joined_s = vo["hook"], hook_s
                    lengths[first.id] = max(lengths[first.id], joined_s + 1.5)
                    said[first.id] = hook_lines + said.get(first.id, [])
                    intro_png = r.hook(first, n, card_dir / "00_intro_overlay.png", today, brand=True,
                                       headline=" ".join(hook_lines), lang=args.lang, kicker=kicker)
                elif not args.no_intro:
                    opener = min(max(hook_s + 0.2, 2.5), 10.0) if hook_moments else max(INTRO_SECONDS, hook_s + 0.3)
            total = welcome_s + opener + sum(lengths.values()) + outro_s
            t = 0.0
            segments: list[Path] = []
            played: list[tuple[Path, int, Post]] = []  # for chapters and the sources file
            if not args.no_intro and not flow:
                if hook_moments:
                    png = r.hook(hook_post, n, card_dir / "00_hook.png", today,
                                 rank=next(k for k, p in numbered if p.id == hook_post.id),
                                 brand=args.welcome, headline=hook_text if narrator else "", lang=args.lang)
                    cut = opener / len(hook_moments) if narrator else HOOK_CUT
                    src_of = {clips[p.id]: p.source or p.author for p in out.posts if p.id in clips}
                    segments.append(stitch.hook_segment(hook_moments, png, seg_dir / "00_hook.mp4",
                                                        out.w, out.h, music, cut=cut, narration=vo.get("hook"),
                                                        trims=[cleanup.crop_for(c, src_of.get(c))
                                                               for c, _ in hook_moments]))
                else:
                    png = r.intro(source_label(found, args.subreddit), n, card_dir / "00_intro.png", today)
                    segments.append(stitch.image_segment(png, seg_dir / "00_intro.mp4", out.w, out.h,
                                                         opener, music, args.music_volume, narration=vo.get("hook")))
                t += opener
            for num, post in numbered:
                seg = seg_dir / f"{len(segments):02d}_{post.id}.mp4"
                prog = stitch.Progress(t, total)
                try:
                    badge = r.badge(post, card_dir / f"{post.id}_badge.png")
                    if post.id in clips:
                        band = (r.news_panel(post, num, rank.tag(post), newsclips.facts(post),
                                             card_dir / f"{post.id}_panel.png") if post.ref_clip
                                else r.band(post, num, rank.tag(post), card_dir / f"{post.id}_band.png"))
                        stitch.video_segment(clips[post.id], band, badge, seg, out.w, out.h,
                                             lengths[post.id] if post.ref_clip else max(cmax(post), lengths[post.id]),
                                             start=moment(post, min(clip_len[post.id], cmax(post))),
                                             music=music, progress=prog, hold_to=lengths[post.id],
                                             narration=vo.get(post.id), captions=caps.get(post.id),
                                             zoom=args.zoom if out.countdown else 1.0, mute=post.ref_clip,
                                             trim=cleanup.crop_for(clips[post.id], post.source or post.author),
                                             fill=cleanup.SETTINGS["fill"],
                                             intro_png=intro_png if flow and post is numbered[0][1] else None,
                                             intro_s=intro_s if flow and post is numbered[0][1] else 0.0)
                        used_as = "reference clip" if post.ref_clip else "video"
                    elif post.kind == "news":
                        png, used_as = r.news_card(post, num, card_dir / f"{post.id}.png"), "news"
                        stitch.image_segment(png, seg, out.w, out.h, lengths[post.id], music,
                                             args.music_volume, credit_png=badge, progress=prog,
                                             narration=vo.get(post.id), zoom_from=1.1 if narrator else 1.04,
                                             captions=caps.get(post.id),
                                             intro_png=intro_png if flow and post is numbered[0][1] else None,
                                             intro_s=intro_s if flow and post is numbered[0][1] else 0.0)
                    else:
                        png, used_as = None, "screenshot"
                        if args.style == "screenshot":
                            png = r.thread_screenshot(post, num, args.comments, card_dir / f"{post.id}.png")
                        if png is None:
                            png, used_as = r.card(post, num, card_dir / f"{post.id}.png"), "card"
                        stitch.image_segment(png, seg, out.w, out.h, lengths[post.id], music,
                                             args.music_volume, credit_png=badge, progress=prog,
                                             narration=vo.get(post.id), zoom_from=1.1 if narrator else 1.04,
                                             captions=caps.get(post.id),
                                             intro_png=intro_png if flow and post is numbered[0][1] else None,
                                             intro_s=intro_s if flow and post is numbered[0][1] else 0.0)
                except Exception:  # noqa: BLE001 - one bad item shouldn't kill the whole video
                    log.exception("%s #%d %s: failed to build segment, skipping", out.name, num, post.id)
                    continue
                t += lengths[post.id]
                segments.append(seg)
                played.append((seg, num, post))
                post_used = getattr(post, "_used_in", {})
                post_used[out.name] = used_as
                post._used_in = post_used
                log.info("%s #%d %s done (%s)", out.name, num, post.id, used_as)
            if not played:
                log.warning("%s: no segments built, skipping", out.name)
                continue
            png = r.outro(card_dir / "99_outro.png")
            segments.append(stitch.image_segment(png, seg_dir / "99_outro.mp4", out.w, out.h, outro_s,
                                                 music, args.music_volume, progress=stitch.Progress(t, total),
                                                 narration=vo.get("outro")))
            final = stitch.concat(segments, day_dir / f"{out.name}.mp4")
            if narrator:  # exact start/end of every segment and the words spoken in it
                rows, at = [], 0.0
                names = ([] if flow else [("hook", hook_post)]) +                         [(p.id, p) for _, _, p in played] + [("outro", None)]
                for seg_path, (kid, p) in zip(segments, names):
                    d = media.probe(seg_path)["duration"]
                    rows.append(f"{at:6.1f}s - {at + d:6.1f}s  {kid:16} {(p.title[:60] if p else '')}")
                    rows.append(f"{'':27}voice: {' | '.join(said.get(kid, []))}")
                    at += d
                (day_dir / f"{out.name}_timeline.txt").write_text("\n".join(rows), encoding="utf-8")
            lead = sum(media.probe(sg)["duration"] for sg in segments[:len(segments) - len(played) - 1])
            upload.write(out.name, final, played, today, is_short=out.countdown, lead=lead)
            finished.append((out, final))

    # 4. Record what was used
    manifest = [{"used_in": getattr(p, "_used_in", {}), **p.to_dict()}
                for p in ranked if getattr(p, "_used_in", None)]
    (day_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.posts_json or saved_plan:
        history_file.write_text(json.dumps(sorted(used | {m["id"] for m in manifest})), encoding="utf-8")
    if args.prompts_only:
        log.info("Plan and script prompts saved; nothing rendered (--prompts-only).")
        return 0
    if not finished:
        log.error("No videos were produced.")
        return 1
    for out, final in finished:
        log.info("Ready: %s (%.0fs, %d items, %dx%d)", final, media.probe(final)["duration"],
                 len(out.posts), out.w, out.h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
