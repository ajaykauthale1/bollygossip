"""Motion-graphics pass on a finished scandal video (optional last step).

Draws animated graphics with Pillow, frame by frame, into short transparent clips (qtrle .mov)
and overlays them on the finished video in one ffmpeg run (video only; audio is copied):
- chapter lower-third at the start of each part: panel slides in (ease-out), red accent bar
  grows, number tag, headline wipes in, sub-line rises, gold underline wipe, light glint, slide out;
- date/stat chips top-right that drop in with a small overshoot;
- a progress bar along the bottom with chapter ticks (stops before the end credits);
- a pulsing call-to-action over the last part.

Usage (Pillow lives in the thumbnail venv, so the main venv stays untouched):
    .venv_thumb\\Scripts\\python.exe motion.py output\\x\\x.mp4 motion\\x.json [--out file.mp4]

The JSON gives per-part labels; part start times come from timeline.txt next to the video:
    {"chapters": {"1": ["RS 800 CRORE+", "India's most expensive film"], ...},
     "chips": [{"seg": 2, "at": 1.0, "dur": 5, "text": "2011 | ROCKSTAR PROMOTIONS"}],
     "cta": {"seg": 11, "text": "YOUR VERDICT?", "sub": "Comment below"}}
English text only: Pillow here has no Devanagari shaping (no raqm).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FFMPEG = "C:/ffmpeg/bin/ffmpeg.exe"
FFPROBE = "C:/ffmpeg/bin/ffprobe.exe"
IMPACT = "C:/Windows/Fonts/impact.ttf"
BOLD = "C:/Windows/Fonts/segoeuib.ttf"
RED, GOLD, WHITE = (229, 9, 20), (255, 200, 61), (255, 255, 255)
W, H = 1920, 1080


def probe(video: Path) -> tuple[float, float]:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True)
    j = json.loads(out.stdout)
    num, den = j["streams"][0]["r_frame_rate"].split("/")
    return float(j["format"]["duration"]), float(num) / float(den)


def segment_starts(timeline: Path) -> tuple[dict[int, float], float | None]:
    """First time of each part, and when the outro/end credits begin."""
    starts, tail = {}, None
    for line in timeline.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([\d.]+)s\s+seg (\d+)", line)
        if m:
            starts.setdefault(int(m.group(2)), float(m.group(1)))
        elif tail is None:
            m = re.match(r"\s*([\d.]+)s\s+(outro|end credits)", line)
            if m:
                tail = float(m.group(1))
    return starts, tail


# ---- easing -------------------------------------------------------------------------------
def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def out_cubic(x: float) -> float:
    return 1 - (1 - clamp(x)) ** 3


def in_cubic(x: float) -> float:
    return clamp(x) ** 3


def out_back(x: float, s: float = 1.7) -> float:
    x = clamp(x) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_w(f: ImageFont.FreeTypeFont, s: str) -> int:
    return int(f.getlength(s))


# ---- graphics (each returns a frame function t -> RGBA image of its canvas) ----------------
def lower_third(num: str, head: str, sub: str, dur: float):
    fh, fs, fn = font(IMPACT, 64), font(BOLD, 30), font(IMPACT, 30)
    pw = max(text_w(fh, head), text_w(fs, sub)) + 170
    cw, ch = pw + 80, 250
    ph, py = 188, 30
    # static panel: dark gradient with rounded corners and soft shadow
    panel = Image.new("RGBA", (pw, ph))
    g = ImageDraw.Draw(panel)
    for x in range(pw):
        a = int(225 - 70 * x / pw)
        g.line([(x, 0), (x, ph)], fill=(12, 12, 18, a))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], 14, fill=255)
    panel.putalpha(Image.composite(panel.getchannel("A"), mask, mask))
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([46, py + 10, 46 + pw, py + 10 + ph], 16, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        slide = -(pw + 120) * (1 - out_cubic(t / 0.5)) - (pw + 120) * in_cubic((t - (dur - 0.5)) / 0.5)
        x0 = int(40 + slide)
        im.alpha_composite(shadow, (int(slide), 0))
        if x0 + pw > 0:
            im.paste(panel, (x0, py), panel)
        d = ImageDraw.Draw(im)
        # red accent bar grows from the middle
        bar = out_cubic((t - 0.12) / 0.4) * ph
        d.rectangle([x0, py + ph / 2 - bar / 2, x0 + 11, py + ph / 2 + bar / 2], fill=RED)
        # number tag
        tag = out_back((t - 0.2) / 0.35)
        if tag > 0:
            tw = text_w(fn, num) + 24
            d.rounded_rectangle([x0 + 40, py + 18, x0 + 40 + tw * tag, py + 54], 6, fill=GOLD)
            if tag > 0.8:
                d.text((x0 + 52, py + 19), num, font=fn, fill=(15, 15, 15))
        # headline, revealed by a left-to-right wipe
        reveal = out_cubic((t - 0.3) / 0.5)
        if reveal > 0:
            layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.text((x0 + 43, py + 67), head, font=fh, fill=(0, 0, 0, 160))
            ld.text((x0 + 40, py + 64), head, font=fh, fill=WHITE)
            m = Image.new("L", (cw, ch), 0)
            ImageDraw.Draw(m).rectangle([0, 0, x0 + 40 + (pw - 40) * reveal, ch], fill=255)
            im.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), m, m))
        # sub-line rises and fades in
        s = out_cubic((t - 0.55) / 0.4)
        if s > 0:
            layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((x0 + 42, py + 140 + 14 * (1 - s)), sub, font=fs,
                                       fill=(235, 235, 235, int(235 * s)))
            im.alpha_composite(layer)
        # gold underline wipe
        u = out_cubic((t - 0.45) / 0.6)
        if u > 0:
            d.rectangle([x0 + 12, py + ph - 5, x0 + 12 + (pw - 12) * u, py + ph], fill=GOLD)
        # light glint sweeping across the panel once
        gl = (t - 1.1) / 0.7
        if 0 < gl < 1:
            layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            gx = x0 + gl * (pw + 200) - 100
            ImageDraw.Draw(layer).polygon([(gx, py), (gx + 60, py), (gx + 20, py + ph), (gx - 40, py + ph)],
                                          fill=(255, 255, 255, 55))
            m = Image.new("L", (cw, ch), 0)
            m.paste(panel.getchannel("A"), (x0, py))
            layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", (cw, ch), 0), m))
            im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(3)))
        return im

    return frame, (cw, ch), (0, 780)


def chip(text: str, dur: float):
    f = font(IMPACT, 42)
    tw = text_w(f, text)
    pw, ph = tw + 90, 70
    cw, ch = pw + 40, 150

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        y = -130 * (1 - out_back(t / 0.5)) - 150 * in_cubic((t - (dur - 0.4)) / 0.4)
        x0, y0 = 20, int(30 + y)
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0 + 4, y0 + 8, x0 + pw + 4, y0 + ph + 8], 35, fill=(0, 0, 0, 140))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], 35, fill=RED)
        pulse = 0.5 + 0.5 * math.sin(t * 7)
        r = 9 + 3 * pulse
        d.ellipse([x0 + 34 - r, y0 + ph / 2 - r, x0 + 34 + r, y0 + ph / 2 + r], fill=GOLD)
        d.text((x0 + 60, y0 + 10), text, font=f, fill=WHITE)
        return im

    return frame, (cw, ch), (W - cw - 40, 20)


def cta(text: str, sub: str, dur: float):
    fh, fs = font(IMPACT, 72), font(BOLD, 34)
    pw = max(text_w(fh, text), text_w(fs, sub)) + 160
    ph = 170
    cw, ch = pw + 120, ph + 120

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sc = out_back(t / 0.6) * (1 - in_cubic((t - (dur - 0.4)) / 0.4))
        if sc <= 0.02:
            return im
        card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        glow = 0.5 + 0.5 * math.sin(t * 5)
        gl = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle([60, 60, 60 + pw, 60 + ph], 24, outline=RED + (int(150 + 100 * glow),), width=14)
        card.alpha_composite(gl.filter(ImageFilter.GaussianBlur(10 + 6 * glow)))
        d.rounded_rectangle([60, 60, 60 + pw, 60 + ph], 24, fill=(12, 12, 18, 225), outline=RED, width=5)
        d.text(((cw - text_w(fh, text)) / 2, 76), text, font=fh, fill=WHITE)
        bounce = 6 * abs(math.sin(t * 4))
        s = sub + "  \u25bc"
        d.text(((cw - text_w(fs, s)) / 2, 168 + bounce), s, font=fs, fill=GOLD)
        if sc != 1:
            nw, nh = max(1, int(cw * sc)), max(1, int(ch * sc))
            card = card.resize((nw, nh), Image.Resampling.LANCZOS)
            im.alpha_composite(card, ((cw - nw) // 2, (ch - nh) // 2))
        else:
            im = card
        return im

    return frame, (cw, ch), ((W - cw) // 2, 700)


def progress(starts: dict[int, float], end: float):
    """Track with chapter ticks; the red fill is a separate color source moved by overlay."""
    im = Image.new("RGBA", (W, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 8, W, 16], fill=(255, 255, 255, 50))
    for s, t in starts.items():
        if s > 1:
            x = int(W * t / end)
            d.rectangle([x - 2, 4, x + 2, 16], fill=GOLD)
    return im


# ---- rendering ----------------------------------------------------------------------------
def render_clip(frame_fn, size: tuple[int, int], dur: float, fps: float, out: Path) -> None:
    w, h = size
    w += w % 2
    h += h % 2
    p = subprocess.Popen([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
                          "-c:v", "qtrle", str(out)], stdin=subprocess.PIPE)
    assert p.stdin
    n = int(round(dur * fps))
    for i in range(n):
        im = frame_fn(i / fps)
        if im.size != (w, h):
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            im = canvas
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError(f"encoding {out} failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    starts, tail = segment_starts(a.video.parent / "timeline.txt")
    total, fps = probe(a.video)
    end = tail or total
    work = a.video.parent / "motion_work"
    work.mkdir(exist_ok=True)

    events: list[tuple[float, Path, tuple[int, int]]] = []  # start, clip, position

    def add(name: str, t0: float, dur: float, made) -> None:
        fn, size, pos = made
        f = work / f"{name}.mov"
        render_clip(fn, size, dur, fps, f)
        events.append((t0, f, pos))
        print(f"{t0:7.1f}s  {name}")

    chapters = sorted(spec.get("chapters", {}).items(), key=lambda kv: int(kv[0]))
    n = len(chapters)
    for idx, (seg, (head, sub)) in enumerate(chapters, 1):
        t0 = starts.get(int(seg))
        if t0 is None:
            continue
        t0 += 6.6 if int(seg) == 1 else 0.6  # part 1 opens under the video title (6 s)
        add(f"lt{idx:02d}", t0, 5.8, lower_third(f"{idx:02d}/{n:02d}", head, sub, 5.8))
    for k, c in enumerate(spec.get("chips", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 5.0)
            add(f"chip{k:02d}", t0 + c.get("at", 1.0), dur, chip(c["text"], dur))
    if spec.get("cta"):
        c = spec["cta"]
        t0 = starts.get(c["seg"])
        if t0 is not None:
            t0 += c.get("at", 1.0)
            dur = max(3.0, end - t0 - 0.2)
            add("cta", t0, dur, cta(c["text"], c.get("sub", ""), dur))

    track = work / "progress.png"
    progress(starts, end).save(track)

    # One ffmpeg run: video + the track png + each transparent clip (offset with -itsoffset).
    cmd = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-stats",
           "-i", str(a.video), "-i", str(track)]
    for t0, f, _ in events:
        cmd += ["-itsoffset", f"{t0:.3f}", "-i", str(f)]
    g = [f"color=c=0xE50914:s={W}x8:r={fps}:d={end:.3f}[fill]",
         f"[0:v][fill]overlay=x='-W+W*t/{end:.3f}':y={H - 8}:eof_action=pass[p0]",
         f"[p0][1:v]overlay=0:{H - 16}:enable='lt(t,{end:.3f})'[p1]"]
    last = "p1"
    for i, (t0, f, (x, y)) in enumerate(events):
        g.append(f"[{last}][{i + 2}:v]overlay={x}:{y}:eof_action=pass[v{i}]")
        last = f"v{i}"
    g[-1] = g[-1].rsplit("[", 1)[0] + "[vout]"
    script = work / "graph.txt"
    script.write_text(";\n".join(g), encoding="utf-8", newline="\n")
    out = a.out or a.video.with_name(a.video.stem + "_motion.mp4")
    cmd += ["-/filter_complex", str(script), "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("Motion video:", out)


if __name__ == "__main__":
    main()
