"""Long landscape video (1920x1080, up to 20 min) from a hand-written script on a
scandal or viral news story: each segment of the script is voiced with the same
Chatterbox voice as the shorts, YouTube clips matching the segment are fetched
and cut to fit under the voice, and everything is stitched into one video.

Separate from the daily pipeline: it only imports ../viralbolly read-only
(ffmpeg helpers, voice, uploader blocklist) and writes under scandal_long/.

    run_scandal.bat scripts\\my_story.txt

Script format (see scripts/TEMPLATE.txt):
    # title: Shown at the top for the first few seconds (English only)
    # topic: words added to every clip search, e.g. "Ranveer Allahbadia controversy"

    [clip: search words for this segment]
    Voiceover line one.
    Voiceover line two.

    [clip: next search]  [clip: another search]
    Next segment's lines...

Segments are separated by blank lines (or ---). A segment without [clip: ...]
is searched with the topic plus its own words.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from viralbolly import media, voice  # noqa: E402  (read-only reuse of the daily pipeline)
from viralbolly.media import probe, run_ffmpeg  # noqa: E402
from viralbolly.sources import blocked_uploader  # noqa: E402

import claude_helper  # noqa: E402  (scandal_long/claude_helper.py: Claude Code with your Pro login)
import cues  # noqa: E402  (scandal_long/cues.py)
import tts_check  # noqa: E402  (scandal_long/tts_check.py: pronunciations, Whisper-checked voice takes)

log = logging.getLogger("scandal")

W, H, FPS = 1920, 1080, 30  # --vertical switches W, H to 1080x1920 (shorts)
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
FONT = "C:/Windows/Fonts/arialbd.ttf"
# News/podcast voice polish: cut rumble and hiss, thin the mud, lift presence so words
# are crisp, tame sibilance, then even the level with gentle compression.
VOICE_CLARITY = ("highpass=f=85,afftdn=nf=-28,equalizer=f=140:t=q:w=1:g=1.5,equalizer=f=320:t=q:w=1.2:g=-2.5,"
                 "equalizer=f=3200:t=q:w=1.4:g=3,equalizer=f=9000:t=h:w=0.7:g=1.5,deesser=i=0.35,"
                 "acompressor=threshold=-20dB:ratio=3:attack=6:release=120:makeup=2.5,alimiter=limit=0.7:level=0")
STOP = set("""a an the and or but of to in on at for with by from is was are were be been this that it its
his her their he she they them as about after before over into than then so very just also what who
""".split())


# --------------------------------------------------------------------------- script

@dataclass
class Segment:
    lines: list[str]
    hints: list[str] = field(default_factory=list)
    play: float | None = None  # seconds of a clip with its own sound after the voiceover ([play: N])
    cues: list[tuple[str, str]] = field(default_factory=list)  # ("music"|"sfx", text) just before it
    screens: list[tuple[str, str]] = field(default_factory=list)  # ([screen: file | credit]) shown first


@dataclass
class Script:
    title: str
    topic: str
    segments: list[Segment]
    mood: str = ""
    music: str = ""  # file path or link from a "# music:" line ("auto" = follow [MUSIC:] cues)
    end_cues: list[tuple[str, str]] = field(default_factory=list)  # cues after the last segment


def parse_script(path: Path) -> Script:
    title = topic = mood = music = ""
    segments: list[Segment] = []
    cur = Segment([])

    def flush():
        nonlocal cur
        if cur.lines:
            segments.append(cur)
        elif cur.hints and segments:  # hints with no lines belong to the next segment
            return
        cur = Segment([])

    import html

    text = html.unescape(path.read_text(encoding="utf-8-sig"))
    # Podcast-style scripts ([SFX: ...] / [MUSIC: ...] cue lines): every paragraph line is its own segment.
    per_line = bool(re.search(r"^\s*\[\s*(SFX|MUSIC)\s*:", text, re.I | re.M))
    pending: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        cue = re.fullmatch(r"\[\s*(SFX|MUSIC|SOUND)\s*:\s*(.+)\]", line, re.I | re.S)
        if cue:
            if cur.lines:
                flush()
            pending.append(("music" if cue[1].upper() == "MUSIC" else "sfx", cue[2].strip()))
            continue
        # Header and speaker lines of podcast scripts are not spoken.
        if re.match(r"(format|tone|language|voice ?over|host|narrator)\b[^:]{0,20}:", line, re.I) or re.match(r"[^\[\]]{0,30}स्क्रिप्ट\s*:", line):
            continue
        m = re.match(r"#?\s*(title|topic|mood|music)\s*:\s*(.+)", line, re.I)
        if m:
            key, val = m[1].lower(), m[2].strip()
            if key == "title":
                title = val
            elif key == "topic":
                topic = val
            elif key == "music":
                music = val
            else:
                mood = val.lower()
            continue
        if line.startswith("#"):
            continue
        if not line or re.fullmatch(r"-{3,}", line):
            if cur.lines:
                flush()
            continue
        sm = re.fullmatch(r"\[\s*screen\s*:\s*([^\]|]+?)\s*(?:\|\s*([^\]]+?)\s*)?\]", line, re.I)
        if sm:  # a screenshot (Reddit post, tweet, article) shown scrolling at the start of the part
            if cur.lines:
                flush()
            cur.screens.append((sm[1], sm[2] or "Reddit"))
            continue
        pm = re.fullmatch(r"\[\s*play\s*:\s*([\d.]+)\s*s?\s*\]", line, re.I)
        if pm:  # applies to the segment being written (or the next one, if none yet)
            cur.play = float(pm[1])
            continue
        hints = re.findall(r"\[\s*clip\s*:\s*([^\]]+)\]", line, re.I)
        if hints or re.match(r"clip\s*:", line, re.I):
            if cur.lines:  # a new hint after lines starts a new segment
                flush()
            cur.hints += [h.strip() for h in hints] or [line.split(":", 1)[1].strip()]
            rest = re.sub(r"\[\s*clip\s*:[^\]]+\]", "", line).strip()
            if rest and not re.match(r"clip\s*:", line, re.I):
                cur.lines.append(rest)
            continue
        if pending and not cur.lines:
            cur.cues += pending
            pending = []
        cur.lines.append(line)
        if per_line and len(cur.lines) >= 6:  # long runs of lines: split every 6 sentences-lines
            flush()
    flush()
    if not segments:
        raise SystemExit(f"No voiceover lines found in {path}")
    return Script(title, topic, segments, mood, music, pending)


def search_terms(seg: Segment, topic: str) -> list[str]:
    if seg.hints:
        # Short hints ("court hearing") get the topic for context; full hints are used as written.
        return [h if exact_video(h) else f"{topic} {h}".strip() if topic and len(h.split()) < 4 else h
                for h in seg.hints]
    text = " ".join(seg.lines)
    if re.search(r"[A-Za-z]", text) and not re.search(r"[\u0900-\u097F]", text):
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z']+", text) if w.lower() not in STOP]
        key = " ".join(words[:8])
    else:  # Hindi line: YouTube search understands it, keep it short
        key = " ".join(text.split()[:8])
    return [f"{topic} {key}".strip()]


# --------------------------------------------------------------------------- clips

@dataclass
class Clip:
    id: str
    path: Path
    credit: str
    width: int
    height: int
    duration: float
    cursor: float = 0.0  # next unused second (so the same footage isn't shown twice)
    crop: str = ""  # ffmpeg crop w:h:x:y that removes black bars baked into the video
    span: tuple[float, float] | None = None  # "[clip: yt:ID@40-90]": only use these seconds

    @property
    def landscape(self) -> bool:
        return self.width >= self.height * 1.2


class ClipFinder:
    """yt-dlp YouTube search; prefers landscape clips, falls back to vertical ones.
    Company accounts (studios, labels, streamers, TV channels, paparazzi agencies)
    are skipped by the same rules as the daily pipeline."""

    def __init__(self, cache: Path, cfg: dict):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.used: set[str] = set()
        self.meta_file = cache / "clips.json"
        try:
            self.meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.meta = {}

    def _save(self):
        self.meta_file.write_text(json.dumps(self.meta, indent=1, ensure_ascii=False), encoding="utf-8")

    def search(self, query: str, n: int) -> list[dict]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as y:
                info = y.extract_info(f"ytsearch{n}:{query}", download=False)
            return list(info.get("entries") or [])
        except Exception as e:  # noqa: BLE001
            log.warning("Search failed for %r: %s", query, str(e).splitlines()[0][:150])
            return []

    def details(self, vid: str) -> dict | None:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
                return y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        except Exception as e:  # noqa: BLE001
            log.info("  skip %s: %s", vid, str(e).splitlines()[0][:120])
            return None

    def download(self, info: dict) -> Path | None:
        import yt_dlp

        vid = info["id"]
        path = self.cache / f"{vid}.mp4"
        if path.exists() and probe(path)["has_video"]:
            return path
        opts = {"outtmpl": str(self.cache / f"{vid}.%(ext)s"),
                "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                "merge_output_format": "mp4", "ffmpeg_location": media.ffmpeg_exe(),
                "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
        dur = info.get("duration") or 0
        limit = self.cfg["max_download_seconds"]
        if dur > limit:  # long news videos: only fetch a stretch after the anchor's intro
            start = min(dur * 0.1, dur - limit)
            from yt_dlp.utils import download_range_func
            opts["download_ranges"] = download_range_func(None, [(start, start + limit)])
        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([url])
        except Exception as e:  # noqa: BLE001
            if "download_ranges" not in opts or dur > self.cfg["max_video_minutes"] * 60 / 2:
                log.warning("  download failed %s: %s", vid, str(e).splitlines()[0][:150])
            else:  # partial downloads sometimes fail in ffmpeg: fetch the whole video instead
                log.info("  partial download failed for %s; fetching the whole video", vid)
                opts.pop("download_ranges")
                for f in self.cache.glob(f"{vid}.*"):
                    f.unlink(missing_ok=True)
                try:
                    with yt_dlp.YoutubeDL(opts) as y:
                        y.download([url])
                except Exception as e2:  # noqa: BLE001
                    log.warning("  download failed %s: %s", vid, str(e2).splitlines()[0][:150])
        if not path.exists():
            found = sorted(self.cache.glob(f"{vid}.*"))
            found = [f for f in found if f.suffix in (".mp4", ".mkv", ".webm")]
            if not found:
                return None
            path = found[0]
        return path if probe(path)["has_video"] else None

    def find(self, queries: list[str], want: int) -> list[Clip]:
        """Up to `want` clips for these searches, landscape first. A YouTube link or "yt:<id>"
        hint takes exactly that video (chosen by hand, so the blocklist doesn't apply)."""
        cands, seen, exact = [], set(), []
        for q in queries:
            vid = exact_video(q)
            if vid:
                clip = self._clip(vid, {}, chosen=True)
                if clip:
                    clip.span = clip_span(q, clip.duration)
                    exact.append(clip)
                continue
            log.info("  search: %s", q)
            for rank, e in enumerate(self.search(q, self.cfg["search_results"])):
                vid = e.get("id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                dur = e.get("duration") or 0
                if dur and (dur < self.cfg["min_clip_seconds"] or dur > self.cfg["max_video_minutes"] * 60):
                    continue
                if e.get("live_status") in ("is_live", "is_upcoming"):
                    continue
                short = "/shorts/" in (e.get("url") or "")
                cands.append((rank + (5 if short else 0), vid, e))
        cands.sort(key=lambda t: t[0])

        fresh = [c for c in cands if c[1] not in self.used]
        pool = fresh + [c for c in cands if c[1] in self.used]  # reuse footage only if nothing new
        landscape, vertical = [], []
        checked = 0
        for _, vid, e in pool:
            if len(exact) >= want:
                break
            if len(landscape) >= want or checked >= self.cfg["max_checks"]:
                break
            clip = self._clip(vid, e)
            checked += 1
            if not clip:
                continue
            (landscape if clip.landscape else vertical).append(clip)
        picked = exact + (landscape + vertical)[:max(want - len(exact), 0)]
        for c in picked:
            self.used.add(c.id)
            log.info("  clip %s %dx%d %.0fs  credit: %s", c.id, c.width, c.height, c.duration, c.credit)
        return picked

    def _clip(self, vid: str, entry: dict, chosen: bool = False) -> Clip | None:
        if vid in self.meta and Path(self.meta[vid]["path"]).exists():
            m = self.meta[vid]
            # cached clips are re-checked too, so a newly blocked channel stops being used
            names = f"{m['credit']} {m.get('title', '')}".lower()
            if not chosen and any(b.lower() in names for b in self.cfg.get("block_uploaders", []) if b):
                log.info("  skip %s: blocked (%s)", vid, m["credit"])
                return None
            if not chosen and self._low_res(m["width"], m["height"]):
                log.info("  skip %s: low resolution %dx%d (%s)", vid, m["width"], m["height"], m["credit"])
                return None
            if "crop" not in m:
                m["crop"], m["width"], m["height"] = detect_bars(Path(m["path"]), m["width"], m["height"], m["duration"])
                self._save()
            return Clip(vid, Path(m["path"]), m["credit"], m["width"], m["height"], m["duration"], crop=m["crop"])
        info = self.details(vid)
        if not info:
            return None
        reason = None if chosen else blocked_uploader(info, self.cfg.get("block_uploaders", []))
        title = (info.get("title") or "").lower()
        if not chosen and not reason and any(b.lower() in title for b in self.cfg.get("block_uploaders", []) if b):
            reason = "blocked word in title"
        if not chosen and not reason and self._low_res(info.get("width") or 0, info.get("height") or 0):
            reason = f"low resolution {info.get('width')}x{info.get('height')}"
        if reason:
            log.info("  skip %s: %s (%s)", vid, reason, info.get("channel") or info.get("uploader"))
            return None
        path = self.download(info)
        if not path:
            return None
        p = probe(path)
        credit = info.get("channel") or info.get("uploader") or "YouTube"
        crop, w, h = detect_bars(path, p["width"], p["height"], p["duration"])
        self.meta[vid] = {"path": str(path), "credit": credit, "width": w, "height": h, "crop": crop,
                          "duration": p["duration"], "title": info.get("title", ""),
                          "url": info.get("webpage_url", "")}
        self._save()
        if not chosen and self._low_res(w, h):
            log.info("  skip %s: low resolution %dx%d after download (%s)", vid, w, h, credit)
            return None
        return Clip(vid, path, credit, w, h, p["duration"], crop=crop)

    def _low_res(self, w: int, h: int) -> bool:
        """Below `min_clip_long_side` px on the long side (1280 = 720p); 0 = unknown, allowed."""
        side = max(w or 0, h or 0)
        return 0 < side < self.cfg.get("min_clip_long_side", 0)


def exact_video(hint: str) -> str | None:
    """The video id of a "[clip: <YouTube link>]" or "[clip: yt:<id>]" hint, else None."""
    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|^yt:)([\w-]{11})", hint.strip())
    return m.group(1) if m else None


def clip_span(hint: str, duration: float) -> tuple[float, float] | None:
    """The "@start-end" (or "@start") seconds of a "[clip: yt:<id>@40-90]" hint, else None."""
    m = re.search(r"@(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\s*$", hint.strip())
    if not m:
        return None
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else duration
    b = min(b, duration)
    return (a, b) if b - a >= 3 else None


def detect_bars(path: Path, w: int, h: int, duration: float) -> tuple[str, int, int]:
    """Black bars baked into the video (e.g. a vertical phone clip uploaded as 4:3):
    the crop that removes them and the size after cropping. ("", w, h) if none."""
    import subprocess

    found = []
    for at in (duration * 0.25, duration * 0.5, duration * 0.75):
        proc = subprocess.run([media.ffmpeg_exe(), "-hide_banner", "-ss", f"{at:.1f}", "-t", "3", "-i", str(path),
                               "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
        if m:
            found.append(tuple(map(int, m[-1])))
    if not found:
        return "", w, h
    # The largest box seen, so a dark scene doesn't crop real picture away.
    cw, ch = max(f[0] for f in found), max(f[1] for f in found)
    x, y = min(f[2] for f in found), min(f[3] for f in found)
    if cw >= w * 0.92 and ch >= h * 0.92 or cw < w * 0.2 or ch < h * 0.2:
        return "", w, h
    return f"{cw}:{ch}:{x}:{y}", cw, ch


def loud_window(clip: Clip, seconds: float) -> float:
    """Start of the loudest `seconds` of the clip (someone talking, a crowd, a song), skipping
    the channel intro and end screen, so the clip's own sound is worth hearing."""
    lead = min(12.0, clip.duration * 0.12)
    tail = min(8.0, clip.duration * 0.1)
    if clip.span:
        lead, tail = clip.span[0], clip.duration - clip.span[1]
    curve = [(t, e) for t, e in media.loudness_curve(clip.path) if lead <= t <= clip.duration - tail - seconds]
    if len(curve) < 4:
        return take_window(clip, seconds)
    step = (curve[1][0] - curve[0][0]) or 0.5
    n = max(1, int(seconds / step))
    if n > len(curve):  # clip too short for the window (stock footage)
        return take_window(clip, seconds)
    best_i = max(range(len(curve) - n + 1), key=lambda i: sum(e for _, e in curve[i:i + n]))
    return max(0.0, curve[best_i][0] - 0.3)


def take_window(clip: Clip, seconds: float) -> float:
    """Start of the next unused `seconds` of the clip, skipping its first and last seconds
    (anchors' intros, end screens). Wraps around when the clip is used up."""
    lead = min(12.0, clip.duration * 0.12)  # news videos open with a channel bumper
    tail = min(6.0, clip.duration * 0.1)
    if clip.span:
        lead, tail = clip.span[0], clip.duration - clip.span[1]
    usable = max(clip.duration - lead - tail, 0.1)
    if clip.cursor + seconds > usable:
        clip.cursor = 0.0
    start = lead + clip.cursor
    clip.cursor += seconds
    return max(0.0, min(start, clip.duration - seconds - 0.05))


# --------------------------------------------------------------------------- render

def ff_path(p: Path | str) -> str:
    """A path written inside an ffmpeg filter argument (Windows drive colon escaped)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def render_shot(clip: Clip, start: float, dur: float, out: Path, work: Path, title: str = "",
                flash: str = "", credit: bool = True) -> None:
    """One shot of the video. `flash` = "white" or "black": open the shot with a quick flash or
    dip, the transition used where the script's music or mood changes."""
    credit_file = work / f"credit_{clip.id}.txt"
    credit_file.write_text(f"Video: {clip.credit}", encoding="utf-8")
    aspect = clip.width / max(clip.height, 1)
    src = f"[0:v]crop={clip.crop},setsar=1[src];" if clip.crop else ""
    v0 = "[src]" if clip.crop else "[0:v]"
    frame = W / H
    if frame * 0.85 <= aspect <= frame * 1.1:  # already the frame's shape: fill it
        vf = f"{src}{v0}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]"
    elif H > W and aspect > 1:  # landscape clip in a vertical video: band across the middle,
        fh = int(H * 0.4) // 2 * 2  # sides trimmed a little, over a blurred copy
        vf = (f"{src}{v0}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"gblur=sigma=30,eq=brightness=-0.12:saturation=1.1[bg];"
              f"[b]scale=-2:{fh},crop='min(iw,{W})':{fh},setsar=1[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    else:  # vertical / square: fit the full clip over a blurred, zoomed copy of itself
        vf = (f"{src}{v0}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"gblur=sigma=30,eq=brightness=-0.12:saturation=1.1[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    font = ff_path(FONT)
    vf += f";[base]fps={FPS}"
    if credit:
        vf += (f",drawtext=fontfile='{font}':textfile='{ff_path(credit_file)}':fontsize=30:"
               f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=14:x=w-tw-48:y=h-th-44")
    if title:
        tfile = work / "title.txt"
        tfile.write_text(title, encoding="utf-8")
        size, ty = (54, 60) if W > H else (44, 220)
        vf += (f",drawtext=fontfile='{font}':textfile='{ff_path(tfile)}':fontsize={size}:fontcolor=white:"
               f"box=1:boxcolor=0xC2185B@0.92:boxborderw=22:x=60:y={ty}:enable='lt(t,6)'")
    if flash:
        vf += f",fade=t=in:st=0:d={0.35 if flash == 'white' else 0.25}:color={flash}"
    vf += "[v]"
    has_audio = probe(clip.path)["has_audio"]
    ins = ["-ss", f"{start:.2f}", "-t", f"{dur:.3f}", "-i", str(clip.path)]
    if has_audio:
        af = f"[0:a]aresample=44100,aformat=channel_layouts=stereo,apad,atrim=0:{dur:.3f}[a]"
    else:
        ins += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        af = "[1:a]anull[a]"
    run_ffmpeg([*ins, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
                "-t", f"{dur:.3f}", *ENCODE, str(out)])


def credits_layout(names: list[str]) -> tuple[int, int, int]:
    """(font size, columns, rows per column) that fit every name on one page: the biggest font
    from 46 down to 20 px, with up to 4 columns (2 in a vertical video)."""
    max_cols = 4 if W > H else 2
    area_w, area_h = W * 0.88, H * (0.66 if W > H else 0.6)
    longest = max((len(n) for n in names), default=1)
    for size in range(46, 19, -2):
        rows = max(1, int(area_h // (size * 1.8)))
        cols = -(-len(names) // rows)
        if cols <= max_cols and longest * size * 0.6 <= area_w / cols:
            return size, cols, rows
    return 20, max_cols, -(-len(names) // max_cols)


def render_credits(names: list[str], out: Path, work: Path, dur: float = 7.0) -> float:
    """One still end card: "Video credits" and every source in centred columns (no scrolling).
    Returns its length in seconds."""
    names = [re.sub(r"[^\u0000-\uFFFF]", "", n).strip() or n for n in names]  # drop emoji (font has none)
    size, cols, rows = credits_layout(names)
    font = ff_path(FONT)
    title = work / "end_credits_title.txt"
    title.write_text("Video credits", encoding="utf-8")
    top = int(H * (0.24 if W > H else 0.28))
    parts = [f"drawtext=fontfile='{font}':textfile='{ff_path(title)}':fontsize={int(size * 1.6)}:"
             f"fontcolor=white:x=(w-text_w)/2:y={int(H * (0.1 if W > H else 0.16))}",
             f"drawbox=x={int(W * 0.3)}:y={top - int(size * 1.2)}:w={int(W * 0.4)}:h=3:color=white@0.5:t=fill"]
    col_w = W * 0.88 / cols
    for c in range(cols):
        chunk = names[c * rows:(c + 1) * rows]
        if not chunk:
            continue
        f = work / f"end_credits_{c}.txt"
        f.write_text("\n".join(chunk), encoding="utf-8", newline="\n")  # \r\n = two line breaks in drawtext
        centre = W * 0.06 + col_w * (c + 0.5)
        parts.append(f"drawtext=fontfile='{font}':textfile='{ff_path(f)}':fontsize={size}:fontcolor=white@0.92:"
                     f"line_spacing={int(size * 0.6)}:x={centre:.0f}-text_w/2:y={top}")
    vf = ",".join(parts) + ",fade=t=in:st=0:d=0.5"
    run_ffmpeg(["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"color=c=0x101014:s={W}x{H}:r={FPS}",
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", f"{dur:.3f}", *ENCODE, str(out)])
    return probe(out)["duration"] or dur


def find_screen(name: str) -> Path | None:
    for p in (Path(name), HERE / "screens" / name, HERE / name):
        if p.is_file():
            return p
    return None


def _img_size(img: Path) -> tuple[int, int]:
    p = probe(img)
    return p["width"], p["height"]


def render_screen(img: Path, dur: float, out: Path, work: Path, credit: str, flash: str = "",
                  show_credit: bool = True) -> None:
    """A screenshot on screen for `dur` seconds: centred over a blurred copy of itself and,
    when it is taller than the frame, scrolled slowly from top to bottom (post, then comments)."""
    w, h = _img_size(img)
    fgw = 1300 if h > w else 1600  # big enough to read on a phone; tall images scroll
    fgw = min(fgw, W - 60) // 2 * 2
    sh = int(h * fgw / max(w, 1)) // 2 * 2
    view = 1000 if W > H else 1500
    credit_file = work / f"credit_screen_{abs(hash(credit)) % 10**8}.txt"
    credit_file.write_text(f"Source: {credit}", encoding="utf-8")
    bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,"
          f"eq=brightness=-0.25:saturation=0.8,setsar=1[bg];")
    if sh > view:  # scroll: hold the top 0.8 s, glide down, hold the end 0.8 s
        move = f"min(max(t-0.8,0)/{max(dur - 1.6, 0.5):.3f},1)"
        fg = (f"[0:v]scale={fgw}:{sh},crop={fgw}:{view}:0:'({sh}-{view})*{move}',setsar=1[fg];")
        y = (H - view) // 2
    else:  # short image: slow push-in instead
        fg = f"[0:v]scale={fgw}:{sh},setsar=1[fg];"
        y = f"(H-h)/2"
    vf = bg + fg + f"[bg][fg]overlay=(W-w)/2:{y}:shortest=1,fps={FPS}"
    if show_credit:
        vf += (f",drawtext=fontfile='{ff_path(FONT)}':textfile='{ff_path(credit_file)}':fontsize=30:"
               f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=14:x=w-tw-48:y=h-th-44")
    if flash:
        vf += f",fade=t=in:st=0:d=0.3:color={flash}"
    vf += "[v]"
    run_ffmpeg(["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(img),
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                "-filter_complex", vf, "-map", "[v]", "-map", "1:a", "-t", f"{dur:.3f}", *ENCODE, str(out)])


def concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{f.resolve().as_posix()}'\n" for f in files), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


SR = 44100


def load_audio(path: Path, filters: str = "", loop: bool = False) -> "np.ndarray":
    """Decode one audio file (through optional ffmpeg filters) to float32 stereo samples.
    One input per ffmpeg call: multi-input mixes in ffmpeg stall at random on this machine."""
    import tempfile

    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "a.f32"
        chain = ",".join(x for x in (filters, f"aresample={SR},aformat=sample_fmts=flt:channel_layouts=stereo") if x)
        ffmpeg([*(["-stream_loop", "-1"] if loop else []), "-i", str(path), "-vn", "-af", chain,
                "-f", "f32le", "-ar", str(SR), "-ac", "2", str(raw)])
        return np.fromfile(raw, dtype="<f4").reshape(-1, 2).copy()


def save_audio(samples: "np.ndarray", out: Path) -> Path:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "a.f32"
        samples.astype("<f4").tofile(raw)
        ffmpeg(["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw), str(out)])
    return out


def place(track: "np.ndarray", sound: "np.ndarray", t: float, gain: float = 1.0) -> None:
    """Add `sound` into `track` starting at `t` seconds."""
    i = max(int(round(t * SR)), 0)
    n = min(len(sound), len(track) - i)
    if n > 0:
        track[i:i + n] += sound[:n] * gain


def voice_track(parts: list[tuple[Path, float, float]], out: Path) -> "np.ndarray":
    """Each segment's narration, started `delay` seconds into the segment (after its sound
    effects), segments back to back. Returns the samples and writes `out`."""
    import numpy as np

    track = np.zeros((int(sum(d for _, d, _ in parts) * SR) + SR, 2), dtype=np.float32)
    at = 0.0
    for wav, length, delay in parts:
        place(track, load_audio(wav), at + delay)
        at += length
    track = track[:int(at * SR)]
    save_audio(track, out)
    return track


def final_mix(video: Path, vo: Path, music: Path | None, out: Path, cfg: dict, total: float,
              prepared: bool = False, sfx: Path | None = None, mute=()) -> None:
    """Podcast-style mix: music plays as a bed under the voiceover; clip sound is held low while
    the voice speaks and comes up when it stops, and the music dips whenever the clip's own
    sound (speech, songs) comes up. Faded in and out."""
    ins = ["-i", str(video), "-i", str(vo)]
    clarity = (VOICE_CLARITY + ",") if cfg.get("voice_clarity") else ""
    graph = (f"[1:a]{clarity}asplit=2[vo][k1];"
             # clip sound at an even level, pushed down hard under the voice
             f"[0:a]loudnorm=I=-14:TP=-2:LRA=11,volume={cfg['clip_volume']}{mute_expr(mute)}[ca];"
             f"[ca][k1]sidechaincompress=threshold=0.015:ratio=20:attack=15:release=400[cad];")
    if music and prepared:  # cue-built music track: levels and fades already set
        ins += ["-i", str(music)]
        graph += ("[cad]asplit=2[cadm][k2];"
                  "[2:a]aresample=44100,aformat=channel_layouts=stereo[mu];"
                  f"[mu][k2]sidechaincompress=threshold=0.03:ratio=6:attack=60:release=900[mud];"
                  "[vo][cadm][mud]amix=inputs=3")
    elif music:
        ins += ["-stream_loop", "-1", "-i", str(music)]
        graph += ("[cad]asplit=2[cadm][k2];"
                  f"[2:a]atrim=0:{total + 1:.3f},aresample=44100,aformat=channel_layouts=stereo,"
                  f"loudnorm=I=-16:TP=-2:LRA=11,volume={cfg['music_volume']}[mu];"
                  # duck the music only when the clip's own sound is up (i.e. no voiceover)
                  f"[mu][k2]sidechaincompress=threshold=0.03:ratio=6:attack=60:release=900[mud];"
                  "[vo][cadm][mud]amix=inputs=3")
    else:
        graph += "[vo][cad]amix=inputs=2"
    if sfx:  # sound effects on top, not ducked
        n_in = sum(1 for a in ins if a == "-i")
        ins += ["-i", str(sfx)]
        graph += f"[pre];[{n_in}:a]aresample=44100,aformat=channel_layouts=stereo[fx];[pre][fx]amix=inputs=2"
    graph += (f":duration=first:normalize=0,"
              f"afade=t=in:d=0.5,afade=t=out:st={max(total - 2, 0):.2f}:d=2,"
              f"volume=0.8,alimiter=limit=0.84:level=0:attack=5:release=80[a];"
              f"[0:v]fade=t=in:d=0.6,fade=t=out:st={max(total - 1.2, 0):.2f}:d=1.2[v]")
    ffmpeg([*ins, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
                *ENCODE, "-movflags", "+faststart", str(out)])


def ffmpeg(args: list[str]) -> None:
    """run_ffmpeg, but with stdout discarded instead of captured: some multi-input audio mixes
    stall forever on Windows when ffmpeg's stdout is a pipe."""
    import subprocess

    cmd = [media.ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    for attempt in range(3):
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return
        if "Cannot allocate memory" in proc.stderr and attempt < 2:
            time.sleep(10)
            continue
        break
    raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")


# --------------------------------------------------------------------------- cues (podcast scripts)

def mute_expr(ranges) -> str:
    """Silence the clips' own sound in these time ranges (intros made of music and effects)."""
    if not ranges:
        return ""
    on = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in ranges)
    return f",volume=0:enable='{on}'"


def make_chatter(narrator, lines: list[str]) -> Path:
    """Overlapping TV-news voices: each headline voiced, pitched a little differently,
    started ~1 s apart, band-limited like a TV speaker."""
    import hashlib

    import numpy as np

    out = HERE / "cache" / "voice" / ("chatter_" + hashlib.sha1("|".join(lines).encode()).hexdigest()[:10] + ".wav")
    if out.exists():
        return out
    takes = [voice.voice_only(narrator.say(t, whoosh=False)[0]) for t in lines]
    rates = [1.0, 1.12, 0.92, 1.06]
    mix = np.zeros((int(3.6 * SR), 2), dtype=np.float32)
    for i, take in enumerate(takes):
        place(mix, load_audio(take, f"asetrate={int(SR * rates[i % 4])},aresample={SR}"), i * 1.1, 1.0 if i == 0 else 0.8)
    raw = out.with_suffix(".raw.wav")
    save_audio(mix, raw)
    ffmpeg(["-i", str(raw), "-af", "highpass=f=350,lowpass=f=3600,acompressor=threshold=-18dB:ratio=4,"
            "afade=t=out:st=2.8:d=0.8", str(out)])
    raw.unlink()
    return out


def plan_cues(seg: Segment, start: float, chatter: dict, cfg: dict):
    """Where a segment's cues land: (seconds before the voice starts, music events, sound
    events, clip-mute ranges, transition flash)."""
    t, music, fx, flash = 0.0, [], [], ""
    for kind, text in seg.cues:
        if kind == "music":
            music.append(cues.MusicEvent(start + t, cues.music_mood(text)))
            t += 0.9  # let the new mood land before the voice
            flash = flash or "white"
            continue
        if cues.wants_intro_music(text):
            music.append(cues.MusicEvent(start + t, cues.music_mood(text)))
            t += cfg.get("intro_music_seconds", 4.0)
        stopped = False
        for name, vol, wait in cues.sfx_list(text):
            if name == "news_chatter":
                f = chatter.get(text)
                if f:
                    fx.append(cues.SfxEvent(start + t, f, vol))
                    t += probe(f)["duration"] + 0.2
                continue
            f = cues.sfx_file(name)
            if not f:
                log.warning("No sound file for %s (put one in sfx/)", name)
                continue
            fx.append(cues.SfxEvent(start + t, f, vol))
            if cues.stops_music(text) and name in ("record_scratch", "thud") and not stopped:
                music.append(cues.MusicEvent(start + t + 0.12, None, abrupt=True))
                stopped = True
                flash = flash or "black"
            t += wait
        if cues.stops_music(text) and not stopped:
            music.append(cues.MusicEvent(start + t, None, abrupt=True))
    if flash == "white" and not fx:
        w = cues.sfx_file("whoosh")
        if w:
            fx.append(cues.SfxEvent(start, w, 0.55))
    mute = [(start, start + t)] if t > 1.5 else []
    return t, music, fx, mute, flash


def build_music(events: list, total: float, out: Path, cfg: dict) -> Path | None:
    """One music track from the cue events: each mood's track looped for its section,
    crossfaded into the next, cut short where a cue stops the music."""
    import numpy as np

    events = sorted(events, key=lambda e: e.t)
    track = np.zeros((int(total * SR) + SR, 2), dtype=np.float32)
    used = False
    for k, ev in enumerate(events):
        if ev.mood in (None, "outro"):
            continue
        f = cues.music_file(ev.mood)
        if not f:
            log.warning("No track for music mood %r (put one in music/cues/)", ev.mood)
            continue
        nxt = events[k + 1] if k + 1 < len(events) else None
        end = nxt.t if nxt else total
        if nxt and nxt.mood == "outro":  # play on, fading out over the rest of the video
            end, fade_out = total, max(total - nxt.t, 2.0)
        elif nxt and nxt.abrupt:
            fade_out = 0.08
        else:
            fade_out = 1.5 if nxt else 2.5
            end += 1.5 if nxt else 0  # overlap the next mood for a crossfade
        length = max(min(end, total) - ev.t, 0.5)
        fade_in = 0.4 if (k == 0 or events[k - 1].abrupt) else 1.5
        vol = cfg.get("intro_music_volume", 0.9) if (ev.mood == "retro" and ev.t < 1) else cfg["music_volume"]
        section = load_audio(f, f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-2:LRA=11,"
                                f"volume={vol},afade=t=in:d={min(fade_in, length / 2):.2f},"
                                f"afade=t=out:st={max(length - fade_out, 0):.2f}:d={min(fade_out, length):.2f}",
                             loop=True)
        place(track, section, ev.t)
        used = True
        log.info("Music %6.1fs-%6.1fs: %s", ev.t, ev.t + length, f.name)
    if not used:
        return None
    return save_audio(track[:int(total * SR)], out)


def build_sfx(events: list, total: float) -> "np.ndarray | None":
    """All sound effects on one track at their times."""
    import numpy as np

    if not events:
        return None
    track = np.zeros((int(total * SR) + SR, 2), dtype=np.float32)
    for e in events:
        place(track, load_audio(e.path), e.t, e.volume)
        log.info("Sound %6.1fs: %s", e.t, e.path.name)
    return track[:int(total * SR)]


# --------------------------------------------------------------------------- main

AUDIO = (".mp3", ".wav", ".m4a", ".ogg", ".flac")


def fetch_music(src: str) -> Path:
    """A music file from a local path, a direct audio link, or a page yt-dlp can read
    (YouTube, SoundCloud, ...). Downloads go to music/downloads/ and are reused."""
    import hashlib

    p = Path(src.strip().strip('"'))
    if not src.lower().startswith("http"):
        if not p.is_absolute():
            p = HERE / p if (HERE / p).exists() else p
        if not p.is_file():
            raise SystemExit(f"Music file not found: {src}")
        return p
    folder = HERE / "music" / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(src.encode()).hexdigest()[:10]
    done = [f for f in folder.glob(f"{key}.*") if f.suffix.lower() in AUDIO]
    if done:
        return done[0]
    if src.lower().split("?")[0].endswith(AUDIO):
        import requests

        r = requests.get(src, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out = folder / f"{key}{Path(src.split('?')[0]).suffix.lower()}"
        out.write_bytes(r.content)
        return out
    import yt_dlp

    opts = {"outtmpl": str(folder / f"{key}.%(ext)s"), "format": "bestaudio/best", "quiet": True,
            "noplaylist": True, "ffmpeg_location": media.ffmpeg_exe(),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([src])
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Could not download music from {src}: {str(e).splitlines()[0][:150]}. "
                         "Download it yourself and give the file path instead.")
    return next(f for f in folder.glob(f"{key}.*") if f.suffix.lower() in AUDIO)


def pick_music(mood: str, cfg: dict) -> Path | None:
    """A random track from scandal_long/music/<mood>/ (the script's `# mood:` line), else from
    anywhere under scandal_long/music/, never the same track twice in a row; falls back to
    config "music" when the library is empty."""
    import random

    lib = HERE / "music"
    pool = []
    if mood and (lib / mood).is_dir():
        pool = [f for f in (lib / mood).rglob("*") if f.suffix.lower() in AUDIO]
    if not pool and lib.is_dir():
        pool = [f for f in lib.rglob("*") if f.suffix.lower() in AUDIO]
    if not pool:
        return None
    last_file = HERE / "cache" / "last_music.txt"
    try:
        last = last_file.read_text(encoding="utf-8").strip()
    except OSError:
        last = ""
    pick = random.choice([f for f in pool if str(f) != last] or pool)
    last_file.parent.mkdir(parents=True, exist_ok=True)
    last_file.write_text(str(pick), encoding="utf-8")
    return pick


def load_config() -> dict:
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def main(argv=None) -> int:
    global W, H
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("script", help="script text file (see scripts/TEMPLATE.txt)")
    p.add_argument("--out", help="output folder (default: scandal_long/output/<script>_<time>)")
    p.add_argument("--no-music", action="store_true")
    p.add_argument("--music", help="background music for this video: file path or link (overrides the script and config)")
    p.add_argument("--voice-mode", choices=["segment", "sentence"],
                   help="voice each segment in one go, or each sentence separately (overrides config.json)")
    p.add_argument("--segment-gap", type=float, help="silence after each segment, seconds (overrides config.json)")
    p.add_argument("--voice-only", action="store_true", help="only voice the script (fills the voice cache), no video")
    p.add_argument("--factcheck", action="store_true",
                   help="Claude Code checks dates/quotes on the web first (report: factcheck.md), then makes the video")
    p.add_argument("--factcheck-only", action="store_true", help="only write the fact-check report, no video")
    p.add_argument("--no-claude", action="store_true", help="don't ask Claude Code for clip search hints")
    p.add_argument("--vertical", action="store_true", help="vertical 1080x1920 video (shorts) instead of 1920x1080")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if args.vertical:
        W, H = 1080, 1920
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    media.set_ffmpeg(cfg.get("ffmpeg"))
    if args.voice_mode:
        cfg["voice_mode"] = args.voice_mode
    if args.segment_gap is not None:
        cfg["segment_gap"] = args.segment_gap

    script_path = Path(args.script)
    if not script_path.is_file() and (HERE / "scripts" / args.script).is_file():
        script_path = HERE / "scripts" / args.script
    script = parse_script(script_path)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = Path(args.out) if args.out else HERE / "output" / f"{script_path.stem}_{stamp}"
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    log.info("Script %s: %d segments, title=%r topic=%r", script_path.name, len(script.segments),
             script.title, script.topic)

    if args.factcheck or args.factcheck_only:
        log.info("Fact-checking with Claude Code (web search, a few minutes)...")
        report = claude_helper.factcheck(script_path, out_dir / "factcheck.md")
        log.info("Fact-check report: %s", report or "not made (see warning above)")
        if args.factcheck_only:
            return 0
    if cfg.get("claude_hints", True) and not args.no_claude and any(not s.hints for s in script.segments):
        n = claude_helper.add_hints(script.segments, script.topic or script.title)
        log.info("Claude Code clip hints: %d of %d segments", n, sum(1 for s in script.segments if not s.hints) + n)

    if args.no_music:
        music = None
    elif not args.music and script.music.lower() == "auto":
        music = None  # follow the script's [MUSIC: ...] cues (built in build_music)
    elif args.music or script.music:  # this video's own track: --music, else the script's "# music:" line
        music = fetch_music(args.music or script.music)
    elif cfg.get("music"):
        music = fetch_music(cfg["music"])
    else:
        music = pick_music(script.mood, cfg)
    log.info("Music: %s", music or "none")

    # 1. Voice every segment (Chatterbox server, same voice and style as the shorts).
    server = None
    if cfg.get("tts_autostart"):
        server = voice.ServerProcess(cfg["tts_url"], cfg["tts_folder"], out_dir / "chatterbox_server.log").start()
    try:
        narrator = voice.Narrator(HERE / "cache" / "voice", engine="chatterbox-server",
                                  voice_sample=cfg["voice_sample"], cues=False, server=cfg["tts_url"],
                                  style=cfg["tts_style"])
        vo = []
        cfg["_pron"] = tts_check.load_pronunciations()
        for q in tts_check.unlisted_titles([ln for sg in script.segments for ln in sg.lines], cfg["_pron"]):
            log.warning("Quoted name %r has no entry in pronunciations.json: check how the voice says it", q)
        for i, seg in enumerate(script.segments, 1):
            t0 = time.time()
            if cfg.get("voice_mode", "segment") == "sentence":
                # One request per sentence (short inputs keep Chatterbox steady), each checked with
                # Whisper and retried if misread, soft edges, joined with pauses (tts_check.py).
                sents = [t for line in seg.lines for t in re.split(r"(?<=[!?।.])\s+", line.strip()) if t]
                takes = [tts_check.voice_sentence(narrator, t, cfg, ffmpeg, HERE / "cache" / "voice") for t in sents]
                joined = work / f"voice_seg{i:03d}.wav"
                wav, d = tts_check.join_takes(takes, joined, cfg["line_pause"],
                                              cfg.get("voice_tail", 0.35), load_audio, save_audio)
            else:  # the whole segment in as few requests as fit (up to ~280 characters each)
                wav, d = narrator.say(seg.lines, whoosh=False, pause=cfg["line_pause"])
                wav = voice.voice_only(wav) if voice.voice_only(wav).exists() else wav
            vo.append((wav, d))
            log.info("Voice %d/%d: %.1fs (%.0fs to make)", i, len(script.segments), d, time.time() - t0)
        chatter = {}  # news-reporter voices for [SFX: ...] cues that quote headlines
        for kind, text in [c for sg in script.segments for c in sg.cues] + script.end_cues:
            if kind == "sfx" and any(n == "news_chatter" for n, _, _ in cues.sfx_list(text)):
                lines = cues.chatter_lines(text)
                if lines:
                    chatter[text] = make_chatter(narrator, lines)
    finally:
        if server:
            server.stop()

    if args.voice_only:
        log.info("Voice done (%d segments, %.0f s)", len(vo), sum(d for _, d in vo))
        return 0

    # 2. Clips for each segment, cut into shots that fill the narration.
    finder = ClipFinder(HERE / "cache" / "clips", cfg)
    shots: list[Path] = []
    seg_lengths: list[float] = []
    timeline = []
    clock = 0.0
    fallback: list[Clip] = []
    on_clip_credit = not cfg.get("end_credits", False)  # "Video: <channel>" on each shot, or one end card
    screen_credits: set[str] = set()
    music_events, sfx_events, mutes, delays = [], [], [], []
    for i, (seg, (wav, d)) in enumerate(zip(script.segments, vo), 1):
        pre, m_ev, fx_ev, mute, flash = plan_cues(seg, clock, chatter, cfg)
        music_events += m_ev
        sfx_events += fx_ev
        mutes += mute
        delays.append(pre)
        length = pre + d + cfg["segment_gap"]
        seg_lengths.append(length)
        log.info("Segment %d/%d (%.1fs): %s", i, len(script.segments), length, seg.lines[0][:70])
        want = max(1, min(cfg["clips_per_segment"], int(length // cfg["shot_seconds"]) + 1))
        clips = finder.find(search_terms(seg, script.topic), want)
        if not clips:  # fall back to short searches: the topic's first words, then the title
            short = " ".join(re.sub(r"[^\w\s'-]", " ", script.topic).split()[:6])
            for q in [x for x in (short, script.title) if x]:
                clips = finder.find([q], 2)
                if clips:
                    break
        if not clips:
            clips = fallback[-2:]
        if not clips:
            raise SystemExit(f"No usable clip found for segment {i}; add a [clip: ...] hint to it")
        fallback = clips
        left, k, j = length, 0, 0
        for name, credit in seg.screens:  # screenshots open the part, then the clips take over
            img = find_screen(name)
            if not img:
                log.warning("Screenshot not found: %s (put it in scandal_long/screens/)", name)
                continue
            dur = min(cfg.get("screen_seconds", 9.0), max(left, 3.0))
            out = work / f"shot_{i:03d}_{j:02d}.mp4"
            render_screen(img, dur, out, work, credit, flash=flash if j == 0 and i > 1 else "",
                          show_credit=on_clip_credit)
            screen_credits.add(credit)
            real = probe(out)["duration"] or dur
            shots.append(out)
            timeline.append(f"{clock:7.1f}s  seg {i}  screenshot {img.name} for {real:.1f}s  ({credit})")
            clock += real
            left -= real
            j += 1
        # A pinned span that is just the sound bite ("[clip: yt:ID@a-b]" no longer than [play: N] + 1 s)
        # is kept for the clip-sound moment, so its footage isn't shown twice in a row.
        moment = seg.play if seg.play is not None else cfg.get("clip_moment_seconds", 0)
        talkers = [c for c in clips if probe(c.path)["has_audio"] and c.duration > moment + 2]
        bite = talkers[(i - 1) % len(talkers)] if moment > 0.5 and talkers else None
        visual = [c for c in clips if not (c is bite and c.span and c.span[1] - c.span[0] <= moment + 1)] or clips
        while left > 0.05:
            dur = min(cfg["shot_seconds"], left)
            if 0 < left - dur < 2.0:  # don't leave a blink-length last shot
                dur = left
            clip = visual[k % len(visual)]
            dur = min(dur, max(clip.duration - 0.1, 0.5))
            start = take_window(clip, dur)
            out = work / f"shot_{i:03d}_{j:02d}.mp4"
            render_shot(clip, start, dur, out, work, title=script.title if (i == 1 and j == 0) else "",
                        flash=flash if j == 0 and i > 1 else "", credit=on_clip_credit)
            real = probe(out)["duration"] or dur
            shots.append(out)
            timeline.append(f"{clock:7.1f}s  seg {i}  {clip.id} @ {start:.1f}s for {real:.1f}s  ({clip.credit})")
            clock += real
            left -= real
            k, j = k + 1, j + 1
        # The clip "speaks": a few seconds of its liveliest moment with its own sound, no voiceover
        # (the mix lowers the music there). Length from [play: N] in the script, else config.
        if bite:
            clip = bite
            start = loud_window(clip, moment)
            out = work / f"shot_{i:03d}_{j:02d}.mp4"
            render_shot(clip, start, moment, out, work, credit=on_clip_credit)
            real = probe(out)["duration"] or moment
            shots.append(out)
            timeline.append(f"{clock:7.1f}s  seg {i}  {clip.id} @ {start:.1f}s for {real:.1f}s  ({clip.credit})  CLIP SOUND")
            clock += real
            left -= real
        seg_lengths[-1] = length - left  # what was actually rendered (voice padded with silence to fit)

    if script.end_cues:  # outro: a few seconds of picture while the music fades and the last effect plays
        tail = cfg.get("outro_seconds", 4.0)
        clip = fallback[0]
        start = take_window(clip, tail)
        out = work / "shot_999_00.mp4"
        render_shot(clip, start, tail, out, work, credit=on_clip_credit)
        real = probe(out)["duration"] or tail
        shots.append(out)
        timeline.append(f"{clock:7.1f}s  outro  {clip.id} @ {start:.1f}s for {real:.1f}s  ({clip.credit})")
        mutes.append((clock, clock + real))
        for kind, text in script.end_cues:
            if kind == "music":
                music_events.append(cues.MusicEvent(clock, cues.music_mood(text)))
            else:
                for name, vol, _ in cues.sfx_list(text):
                    f = cues.sfx_file(name)
                    if f:
                        sfx_events.append(cues.SfxEvent(clock + real - 1.6, f, vol))
        clock += real
        seg_lengths[-1] += real

    if not on_clip_credit:
        names = sorted({m["credit"] for vid, m in finder.meta.items() if vid in finder.used} | screen_credits,
                       key=str.lower)
        out = work / "shot_999_99.mp4"
        real = render_credits(names, out, work)
        shots.append(out)
        timeline.append(f"{clock:7.1f}s  end credits ({len(names)} sources) for {real:.1f}s")
        clock += real

    # 3. Stitch, lay the voice over, duck clip sound and music.
    log.info("Stitching %d shots...", len(shots))
    video = work / "video.mp4"
    concat(shots, video)
    vo_track = work / "voice.wav"
    voice_track([(w, L, dl) for (w, _), L, dl in zip(vo, seg_lengths, delays)], vo_track)
    total = probe(video)["duration"]
    final = out_dir / f"{script_path.stem}.mp4"
    prepared = False
    if music is None and music_events and not args.no_music:
        music = build_music(music_events, total, work / "music.wav", cfg)
        prepared = music is not None
    # Voice polish first, then the sound effects go on the same track (keeps the final mix to
    # three inputs; the effects also briefly duck the clip sound, like the voice does).
    voice_fx = load_audio(vo_track, VOICE_CLARITY if cfg.get("voice_clarity") else "")
    sfx = build_sfx(sfx_events, total)
    if sfx is not None:
        n = min(len(voice_fx), len(sfx))
        voice_fx[:n] += sfx[:n]
    vo_track = save_audio(voice_fx, work / "voice_fx.wav")
    final_mix(video, vo_track, music, final, dict(cfg, voice_clarity=False), total, prepared=prepared,
              mute=mutes)
    (out_dir / "timeline.txt").write_text("\n".join(timeline) + "\n", encoding="utf-8")
    credits = sorted({f"{m['credit']}: {m['url']}" for vid, m in finder.meta.items() if vid in finder.used})
    (out_dir / "credits.txt").write_text("\n".join(credits) + "\n", encoding="utf-8")
    if total > cfg["max_seconds"]:
        log.warning("Video is %.0f s, longer than max_seconds (%s); shorten the script", total, cfg["max_seconds"])
    log.info("Done: %s (%.0f s). Credits list: %s", final, total, out_dir / "credits.txt")
    if not cfg.get("keep_work"):
        for f in work.glob("shot_*.mp4"):
            f.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
