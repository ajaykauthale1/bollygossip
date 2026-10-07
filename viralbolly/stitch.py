"""Turn clips and cards into uniform, punchy segments, then concatenate.

Every segment is encoded identically (size, fps, codecs, audio layout) so the
final concat is a lossless stream copy. Each segment gets the same "energy"
treatment: a white flash cut in, the title band sliding down, the credit badge
fading in bottom-right, and a progress bar across the top that runs through the
whole video.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .cleanup import crop_filter
from .media import probe, run_ffmpeg

log = logging.getLogger(__name__)

FPS = 30
ENCODE = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
]
SILENCE = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
BAR_COLOR = "0xFF4F8B"
FLASH = "fade=t=in:st=0:d=0.22:color=white"
# Clip audio clean-up, set with --denoise: cut rumble and hiss, then an FFT noise
# reducer that learns the steady background (fans, traffic, crowd murmur).
DENOISE = {"on": False}
DENOISE_CHAIN = "highpass=f=90,lowpass=f=12000,afftdn=nr=18:nf=-35:tn=1,"


def reading_seconds(post, min_s: float = 8, max_s: float = 18) -> float:
    """Time on screen for a card: enough to read the headline and the text on it."""
    words = len(post.title.split()) + len(post.selftext.split()[:90])
    words += sum(len(c.body.split()[:50]) for c in post.comments)
    return max(min_s, min(max_s, 3 + words / 3.0))


def title_seconds(post) -> float:
    """Minimum time a clip stays up so its title band can be read (short clips
    hold their last frame until then)."""
    return min(9.0, 2.5 + len(post.title.split()) / 2.5)


class MusicBed:
    """Hands out consecutive slices of the music file so the track keeps playing
    across segments instead of restarting on each one."""

    def __init__(self, path: str | None):
        self.path = path
        self.length = probe(path)["duration"] if path else 0.0
        self.pos = 0.0

    def inputs(self, seconds: float) -> list[str]:
        if not self.path or self.length <= 0:
            return SILENCE
        start = self.pos % self.length
        self.pos += seconds
        return ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", self.path]


@dataclass
class Progress:
    """Where this segment sits in the finished video, for the progress bar."""
    start: float
    total: float


def _bar(label_in: str, w: int, dur: float, progress: Progress | None, label_out: str) -> str:
    if not progress or progress.total <= 0:
        return f"[{label_in}]null[{label_out}]"
    return (f"color=c={BAR_COLOR}:s={w}x12:r={FPS}:d={dur:.2f}[bar];"
            f"[{label_in}][bar]overlay=x='-w+w*({progress.start:.2f}+t)/{progress.total:.2f}':y=0[{label_out}]")


def _voice(bed: str, vo_idx: int | None, out: str) -> str:
    """Lay the narration over the `bed` audio, pushing the bed down while the
    voice talks and letting it back up in the gaps."""
    if vo_idx is None:
        return f"[{bed}]anull[{out}]"
    return (f"[{vo_idx}:a]aresample=44100,aformat=channel_layouts=stereo,apad,asplit[vo][vokey];"
            f"[{bed}][vokey]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=450[bedd];"
            # peak safety only (level=0: no auto gain back up to 0 dBFS, which made voice + bed clip)
            f"[bedd][vo]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=0[{out}]")


def _captions(cur: str, caps: list, first_idx: int) -> tuple[str, str]:
    """Show each caption image only between its start and end times."""
    graph = ""
    for i, (a, b, _png) in enumerate(caps):
        graph += (f"[{first_idx + i}:v]format=rgba[cp{i}];"
                  f"[{cur}][cp{i}]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})':shortest=1[wc{i}];")
        cur = f"wc{i}"
    return graph, cur


def _overlays(base: str, top_idx: int | None, credit_idx: int | None, h: int,
              start: float = 0.0, intro_idx: int | None = None, intro_s: float = 0.0) -> tuple[str, str]:
    """Slide the title band down from above and fade the credit badge in. With an
    intro card, both wait until it has faded out (`start`), so the clip plays on
    without a cut: intro text over the first seconds, then the story's own title."""
    graph, cur = "", base
    if intro_idx is not None:
        graph += (f"[{intro_idx}:v]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,"
                  f"fade=t=out:st={max(intro_s - 0.45, 0):.2f}:d=0.45:alpha=1[intro];"
                  f"[{cur}][intro]overlay=0:0:shortest=1[wintro];")
        cur = "wintro"
    if top_idx is not None:
        y = (f"if(lt(t,{start:.2f}),-{h},if(lt(t,{start + 0.35:.2f}),"
             f"-{h // 3}+{h // 3}*(t-{start:.2f})/0.35,0))") if start else f"if(lt(t,0.35),-{h // 3}+{h // 3}*t/0.35,0)"
        graph += (f"[{top_idx}:v]format=rgba[top];"
                  f"[{cur}][top]overlay=x=0:y='{y}':shortest=1[wtop];")
        cur = "wtop"
    if credit_idx is not None:
        graph += (f"[{credit_idx}:v]format=rgba,fade=t=in:st={start + 0.4:.2f}:d=0.4:alpha=1[cr];"
                  f"[{cur}][cr]overlay=0:0:shortest=1[wcr];")
        cur = "wcr"
    return graph, cur


def video_segment(clip: Path, top_png: Path | None, credit_png: Path | None, out: Path, w: int, h: int,
                  seconds: float, start: float = 0.0, music: MusicBed | None = None,
                  music_volume: float = 0.12, progress: Progress | None = None, hold_to: float = 0.0,
                  narration: Path | None = None, intro_png: Path | None = None, intro_s: float = 0.0,
                  captions: list | None = None, zoom: float = 1.0, mute: bool = False,
                  trim: tuple[float, float] = (0.0, 0.0), fill: str = "blur") -> Path:
    """Fit `seconds` of the clip (from `start`, its best moment) onto a blurred
    full-frame copy of itself, with title, credit and a soft music bed underneath.
    `trim` (top, bottom fractions) cuts off the outlet's own burned-in title/credit
    bands first; the space they leave is the blurred copy, or a dark strip with fill="dark"."""
    info = probe(clip)
    dur = max(0.5, min((info["duration"] or seconds) - start, seconds))
    pad = max(0.0, hold_to - dur)  # freeze the last frame so the title can be read
    dur += pad
    inputs = ["-ss", f"{start:.2f}", "-i", str(clip)]
    idx = 1
    top_idx = credit_idx = intro_idx = None
    for name, png in (("top", top_png), ("credit", credit_png), ("intro", intro_png)):
        if png:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(png)]
            if name == "top":
                top_idx = idx
            elif name == "credit":
                credit_idx = idx
            else:
                intro_idx = idx
            idx += 1
    caps = list(captions or [])
    caps_first = idx
    for _a, _b, cpng in caps:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(cpng)]
        idx += 1
    music_in = music.inputs(dur) if music else SILENCE
    inputs += music_in
    music_idx = idx
    vo_idx = None
    if narration:
        inputs += ["-i", str(narration)]
        vo_idx = idx + 1
    fade_out = max(dur - 0.3, 0)
    ov, cur = _overlays("base", top_idx, credit_idx, h, start=intro_s if intro_png else 0.0,
                        intro_idx=intro_idx, intro_s=intro_s)
    cap_graph, cur = _captions(cur, caps, caps_first)
    ov += cap_graph
    # A slight punch-in on the clip: motion on screen, and the source's corner watermarks get cropped.
    punch = (f",crop=trunc(iw/{zoom}/2)*2:trunc(ih/{zoom}/2)*2,scale=trunc(iw*{zoom}/2)*2:trunc(ih*{zoom}/2)*2"
             if zoom > 1.0 else "")
    pre = crop_filter(*trim)
    if pre and fill == "dark":
        bg = f",drawbox=x=0:y=0:w=iw:h=ih:color=0x0d0d12@1:t=fill"
    elif pre:
        bg = ",boxblur=25:3,eq=brightness=-0.06:saturation=0.85"  # a touch darker so the clip stays the focus
    else:
        bg = ",boxblur=25:3"
    graph = (
        f"[0:v]{pre}tpad=stop_mode=clone:stop_duration={pad:.2f},split[a][b];"
        f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}{bg}[bg];"
        f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease{punch}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base];"
        + ov + _bar(cur, w, dur, progress, "wbar") + ";"
        f"[wbar]{FLASH},fade=t=out:st={fade_out:.2f}:d=0.3,format=yuv420p[v];"
    )
    if info["has_audio"] and not mute:  # mute: a reference clip under a news story; music + voice only
        vol = music_volume if music else 0
        # Duck the music under the clip's own sound: whenever someone talks or the
        # crowd reacts, the bed drops ~20 dB and comes back up in the quiet gaps.
        clean = DENOISE_CHAIN if DENOISE["on"] else ""
        graph += (f"[0:a]aresample=44100,{clean}aformat=channel_layouts=stereo,apad,asplit[ca][key];"
                  f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume={vol}[mb];"
                  f"[mb][key]sidechaincompress=threshold=0.015:ratio=20:attack=15:release=600:makeup=1[duck];"
                  f"[ca][duck]amix=inputs=2:duration=first:normalize=0[bed];")
    else:
        graph += f"[{music_idx}:a]volume=0.3[bed];"
    graph += _voice("bed", vo_idx, "mix") + f";[mix]afade=t=in:d=0.15,afade=t=out:st={fade_out:.2f}:d=0.3[a]"
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-t", f"{dur:.2f}", *ENCODE, str(out)])
    return out


def image_segment(png: Path, out: Path, w: int, h: int, seconds: float, music: MusicBed,
                  volume: float = 0.3, credit_png: Path | None = None,
                  progress: Progress | None = None, narration: Path | None = None, zoom_from: float = 1.04,
                  captions: list | None = None, intro_png: Path | None = None, intro_s: float = 0.0) -> Path:
    """Show a card for `seconds` with a slow push-in. Cards taller than the frame
    scroll from top to bottom, holding for a moment at each end."""
    hold = min(1.2, seconds / 5)
    scroll = max(seconds - 2 * hold, 0.1)
    y = f"'min(max(ih-{h},0), max(0,(ih-{h})*(t-{hold:.2f})/{scroll:.2f}))'"
    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(png)]
    credit_idx = None
    if credit_png:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(credit_png)]
        credit_idx = 1
    intro_idx = None
    if intro_png:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(intro_png)]
        intro_idx = 2 if credit_png else 1
    caps = list(captions or [])
    caps_first = (2 if credit_png else 1) + (1 if intro_png else 0)
    for _a, _b, cpng in caps:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(cpng)]
    inputs += music.inputs(seconds)
    music_idx = caps_first + len(caps)
    vo_idx = None
    if narration:
        inputs += ["-i", str(narration)]
        vo_idx = music_idx + 1
    # Push-in: start at 104% and ease to 100% so a static card still feels alive.
    zoom = (f"scale='iw*({zoom_from}-{zoom_from - 1:.3f}*min(t/{seconds:.2f},1))':-2:eval=frame,crop={w}:{h}")
    ov, cur = _overlays("base", None, credit_idx, h, start=intro_s if intro_png else 0.0,
                        intro_idx=intro_idx, intro_s=intro_s)
    cap_graph, cur = _captions(cur, caps, caps_first)
    ov += cap_graph
    graph = (
        f"[0:v]scale={w}:-2,pad={w}:'max(ih,{h})':0:0:color=0x0d0d12,crop={w}:{h}:0:{y},{zoom},setsar=1[base];"
        + ov + _bar(cur, w, seconds, progress, "wbar") + ";"
        f"[wbar]{FLASH},fade=t=out:st={seconds - 0.3:.2f}:d=0.3,format=yuv420p[v];"
        f"[{music_idx}:a]volume={volume}[bed];" + _voice("bed", vo_idx, "mix") + ";"
        f"[mix]afade=t=in:d=0.3,afade=t=out:st={max(seconds - 0.5, 0):.2f}:d=0.5[a]"
    )
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-t", f"{seconds:.2f}", *ENCODE, str(out)])
    return out


def hook_segment(moments: list[tuple[Path, float]], hook_png: Path, out: Path, w: int, h: int,
                 music: MusicBed, volume: float = 0.6, cut: float = 0.9,
                 narration: Path | None = None, trims: list | None = None) -> Path:
    """Cold open: rapid cuts of the day's best moments (each `cut` seconds, flash
    between them) under a big headline, with the music up loud. `trims` holds each
    moment's (top, bottom) overlay-band crop, as in video_segment."""
    inputs, parts = [], []
    for i, (clip, start) in enumerate(moments):
        inputs += ["-ss", f"{start:.2f}", "-t", f"{cut:.2f}", "-i", str(clip)]
        pre = crop_filter(*trims[i]) if trims else ""
        parts.append(f"[{i}:v]{pre}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                      f"fps={FPS},trim=duration={cut:.2f},setpts=PTS-STARTPTS" + (f",{FLASH}" if i else "")
                      + f"[m{i}]")  # no flash on the very first frame: the headline shows at once
    n = len(moments)
    total = n * cut
    inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(hook_png)]
    inputs += music.inputs(total)
    if narration:
        inputs += ["-i", str(narration)]
    graph = (";".join(parts) + ";" + "".join(f"[m{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[mont];"
             f"[{n}:v]format=rgba[hk];[mont][hk]overlay=0:0:shortest=1,"
             f"fade=t=out:st={total - 0.2:.2f}:d=0.2:color=white,format=yuv420p[v];"
             f"[{n + 1}:a]volume={volume}[bed];" + _voice("bed", n + 2 if narration else None, "a"))
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-t", f"{total:.2f}", *ENCODE, str(out)])
    return out


def concat(segments: list[Path], out: Path) -> Path:
    listing = out.with_suffix(".txt")
    # Forward slashes work on Windows too and avoid backslash escaping in the concat format.
    lines = ("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n" for p in segments)
    listing.write_text("".join(lines), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)])
    listing.unlink()
    return out
