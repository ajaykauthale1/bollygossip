"""Motion-graphics pass v3: motion2.py's graphics plus camera work and sound hits.

On top of everything motion2.py draws (whips, kinetic lower-thirds, chips, kinetic text,
progress bar, verdict card, animated end card) this adds:
- camera: a slow push-in / pull-out on every shot (alternating, from the cuts in timeline.txt),
  so no shot sits still; zoom kicks from motion2 ride on top;
- rack focus: the picture starts blurred and pulls into focus at each part start and at each
  "focus" moment;
- focus moments ("focus" in the spec): a slow punch-in that holds, a dark vignette that closes in,
  a rack focus and a low hit, for the key beats (e.g. the stretcher shot);
- light-leak transition: warm orange/gold light washes across the frame with a bloom. Per part,
  "transitions": {"3": "whip", "2": "leak"} picks whip (default) or leak;
- sound: whoosh on whips/leaks (only where the script has no SFX of its own at that part),
  a soft low hit on kinetic slams and focus moments, a light click on chips. Mixed in numpy
  (one input per ffmpeg call), written as the new audio track.

Usage (Pillow + numpy are in the thumbnail venv):
    .venv_thumb\\Scripts\\python.exe motion3.py output\\x\\x.mp4 motion\\x.json [--out file.mp4]

Spec = motion2 spec plus:
    "transitions": {"2": "leak", "4": "whip"},
    "focus": [{"seg": 3, "at": 4.0, "dur": 3.0, "amount": 0.12}],
    "sfx_parts": [2, 3, 5]      # parts whose start already has a script SFX (no extra whoosh)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import motion2 as m2
from motion import FFMPEG, GOLD, clamp, in_cubic, out_cubic, probe, segment_starts

HERE = Path(__file__).resolve().parent
SR = 48000


# ---- new pieces ---------------------------------------------------------------------------
def leak(dur: float = 0.9):
    """Warm light leak: blurred orange/gold/rose blobs drift across, peak bloom in the middle."""
    W, H = m2.W, m2.H
    s = max(W, H)
    blobs = []
    for col, r in (((255, 120, 30), 0.55), ((255, 200, 90), 0.38), ((255, 70, 110), 0.3)):
        b = Image.new("RGBA", (int(s * r * 2), int(s * r * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(b).ellipse([b.width * 0.2, b.height * 0.2, b.width * 0.8, b.height * 0.8], fill=col + (255,))
        blobs.append(b.filter(ImageFilter.GaussianBlur(b.width * 0.12)))
    paths = [(-0.6, 0.15, 1.3, 0.55), (1.2, 0.85, -0.2, 0.25), (-0.3, 0.9, 0.9, 0.1)]

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        p = clamp(t / dur)
        env = max(0.0, 1 - abs(p - 0.45) / 0.55)  # rises, peaks a bit before the middle, fades
        for b, (x0, y0, x1, y1) in zip(blobs, paths):
            k = out_cubic(p)
            cx, cy = W * (x0 + (x1 - x0) * k), H * (y0 + (y1 - y0) * k)
            lay = b.copy()
            lay.putalpha(lay.getchannel("A").point(lambda v, e=env: int(v * 0.75 * e)))
            im.alpha_composite(lay, (int(cx - b.width / 2), int(cy - b.height / 2)))
        bloom = max(0.0, 1 - abs(p - 0.45) / 0.18)
        if bloom > 0:
            im.alpha_composite(Image.new("RGBA", (W, H), (255, 235, 200, int(150 * bloom))))
        return im

    return frame, (W, H), (0, 0)


def vignette(dur: float):
    """Dark edges closing in (focus moment), with a thin gold frame glint."""
    W, H = m2.W, m2.H
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse([-W * 0.08, H * 0.12, W * 1.08, H * 0.88] if m2.vertical()
                              else [W * 0.05, -H * 0.1, W * 0.95, H * 1.1], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(min(W, H) // 6))
    base = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    base.putalpha(m.point(lambda v: int((255 - v) * 0.8)))

    def frame(t: float) -> Image.Image:
        a = out_cubic(t / 0.35) * (1 - in_cubic((t - (dur - 0.45)) / 0.45))
        im = base.copy()
        im.putalpha(im.getchannel("A").point(lambda v, a=a: int(v * a)))
        return im

    return frame, (W, H), (0, 0)


# ---- helpers ------------------------------------------------------------------------------
def shots(timeline: Path) -> list[tuple[float, float]]:
    out = []
    for line in timeline.read_text(encoding="utf-8").splitlines():
        mm = re.match(r"\s*([\d.]+)s\s+seg \d+\s+\S+ @ [\d.]+s for ([\d.]+)s", line)
        if mm:
            out.append((float(mm.group(1)), float(mm.group(2))))
    return out


def camera_expr(shot_list, kicks, holds) -> str:
    """Scale factor: per-shot push-in/pull-out (4%), zoom kicks, held focus punches."""
    terms = []
    for i, (s, d) in enumerate(shot_list):
        if i % 2 == 0:
            terms.append(f"if(gte(t,{s:.3f})*lt(t,{s + d:.3f}),0.04*(t-{s:.3f})/{d:.3f},0)")
        else:
            terms.append(f"if(gte(t,{s:.3f})*lt(t,{s + d:.3f}),0.04*(1-(t-{s:.3f})/{d:.3f}),0)")
    for k in kicks:
        terms.append(f"if(between(t,{k:.3f},{k + 0.4:.3f}),0.07*pow(1-(t-{k:.3f})/0.4,2),0)")
    for s, d, amt in holds:  # ease in 0.5 s, hold, ease out 0.5 s
        terms.append(f"if(between(t,{s:.3f},{s + d:.3f}),{amt}*min(min((t-{s:.3f})/0.5,1),min(({s + d:.3f}-t)/0.5,1)),0)")
    return "1+" + "+".join(terms) if terms else "1"


def ff(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def load(path: Path) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "a.f32"
        ff(["-i", str(path), "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", str(raw)])
        return np.fromfile(raw, dtype="<f4").reshape(-1, 2).copy()


def place(track: np.ndarray, snd: np.ndarray, t: float, gain: float) -> None:
    i = max(int(round(t * SR)), 0)
    n = min(len(snd), len(track) - i)
    if n > 0:
        track[i:i + n] += snd[:n] * gain


# ---- main ---------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    tl = a.video.parent / "timeline.txt"
    starts, tail = segment_starts(tl)
    total, fps = probe(a.video)
    m2.W, m2.H = map(int, subprocess.run(
        [FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(a.video)],
        capture_output=True, text=True).stdout.strip().split(","))
    W, H = m2.W, m2.H
    end = tail or total
    work = a.video.parent / "motion_work3"
    work.mkdir(exist_ok=True)
    events: list[tuple[float, Path, tuple[int, int]]] = []
    kicks: list[float] = []
    holds: list[tuple[float, float, float]] = []
    racks: list[tuple[float, float]] = []  # (start, duration) of blurred-to-sharp pulls
    hits: list[tuple[float, str, float]] = []  # (time, sfx name, gain)
    sfx_parts = set(spec.get("sfx_parts", []))
    trans = {int(k): v for k, v in spec.get("transitions", {}).items()}

    def add(name: str, t0: float, dur: float, made) -> None:
        fn, size, pos = made
        f = work / f"{name}.mov"
        m2.render_clip(fn, size, dur, fps, f)
        events.append((t0, f, pos))
        print(f"{t0:7.1f}s  {name}", flush=True)

    chapters = sorted(spec.get("chapters", {}).items(), key=lambda kv: int(kv[0]))
    for idx, (seg, (head, sub)) in enumerate(chapters, 1):
        seg = int(seg)
        t0 = starts.get(seg)
        if t0 is None:
            continue
        if seg > 1:
            if trans.get(seg) == "leak":
                add(f"leak{idx:02d}", max(0.0, t0 - 0.4), 0.9, leak(0.9))
            else:
                add(f"whip{idx:02d}", max(0.0, t0 - 0.25), 0.55, m2.whip(0.55))
                kicks.append(t0)
            racks.append((t0, 0.7))
            if seg not in sfx_parts:
                hits.append((max(0.0, t0 - 0.3), "whoosh", 0.45))
        lt0 = t0 + (6.3 if seg == 1 else 0.4)
        add(f"lt{idx:02d}", lt0, 5.6, m2.lower_third(f"{idx:02d}", head, sub, 5.6))
    for k, c in enumerate(spec.get("chips", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 4.5)
            t0 += c.get("at", 1.0)
            add(f"chip{k:02d}", t0, dur, m2.chip(c.get("label", ""), c["text"], dur))
            hits.append((t0 + 0.05, "tape_click", 0.22))
    for k, c in enumerate(spec.get("kinetic", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 3.0)
            t0 += c.get("at", 1.0)
            add(f"kin{k:02d}", t0, dur, m2.kinetic(c["text"], dur, c.get("style", "")))
            kicks.append(t0 + 0.05)
            hits.append((t0, "thud", 0.28))
    for k, c in enumerate(spec.get("focus", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 3.0)
            t0 += c.get("at", 1.0)
            add(f"vig{k:02d}", t0, dur, vignette(dur))
            holds.append((t0, dur, c.get("amount", 0.12)))
            racks.append((t0, 0.6))
            hits.append((t0, "thud", 0.35))
    if spec.get("verdict"):
        c = spec["verdict"]
        t0 = starts.get(c["seg"])
        if t0 is not None:
            t0 += c.get("at", 0.5)
            dur = max(3.0, end - t0 - 0.2)
            add("verdict", t0, dur, m2.verdict(c["q"], c["a"], c["b"], c.get("sub", "Comment below"), dur))
            kicks.append(t0 + 0.15)
    if tail and total - tail > 1.0 and not spec.get("still_credits"):
        names = []
        cfile = a.video.parent / "credits.txt"
        for line in (cfile.read_text(encoding="utf-8").splitlines() if cfile.exists() else []):
            n = line.split(": http")[0].strip()
            if n and n not in names:
                names.append(n)
        if names:
            shot = work / "credits_backdrop.png"
            ff(["-ss", f"{max(0.0, tail - 0.6):.3f}", "-i", str(a.video), "-frames:v", "1", str(shot)])
            add("leak_end", max(0.0, tail - 0.4), 0.9, leak(0.9))
            add("credits", tail, total - tail, m2.credits_card(names, total - tail, Image.open(shot)))
            hits.append((max(0.0, tail - 0.35), "whoosh", 0.4))

    # progress track + head (as motion2)
    bar_y = m2.band_box()[1] - 10 if m2.vertical() else H - 16
    track = Image.new("RGBA", (W, 16), (0, 0, 0, 0))
    td = ImageDraw.Draw(track)
    td.rectangle([0, 8, W, 16], fill=(255, 255, 255, 55))
    for s, t in starts.items():
        if s > 1:
            x = int(W * t / end)
            td.rectangle([x - 2, 3, x + 2, 16], fill=GOLD)
    track.save(work / "track.png")
    head = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    ImageDraw.Draw(head).ellipse([8, 8, 40, 40], fill=GOLD + (200,))
    head = head.filter(ImageFilter.GaussianBlur(5))
    ImageDraw.Draw(head).ellipse([16, 16, 32, 32], fill=(255, 255, 255))
    head.save(work / "head.png")

    # sound hits mixed in numpy
    aud = load(a.video)
    cache: dict[str, np.ndarray] = {}
    for t, name, gain in hits:
        if name not in cache:
            cache[name] = load(HERE / "sfx" / f"{name}.mp3")
        place(aud, cache[name], t, gain)
    peak = float(np.abs(aud).max() or 1.0)
    if peak > 0.98:
        aud *= 0.98 / peak
    wav = work / "audio_hits.wav"
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "a.f32"
        aud.astype("<f4").tofile(raw)
        ff(["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw), str(wav)])

    cmd = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-stats",
           "-i", str(a.video), "-i", str(wav), "-loop", "1", "-i", str(work / "track.png"),
           "-loop", "1", "-i", str(work / "head.png")]
    for t0, f, _ in events:
        cmd += ["-itsoffset", f"{t0:.3f}", "-i", str(f)]
    z = camera_expr(shots(tl), sorted(kicks), holds)
    g = [f"[0:v]scale=w='2*trunc(iw*({z})/2)':h=-2:eval=frame,crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,setsar=1,format=yuv420p[z]"]
    src = "z"
    if racks:
        outs = "".join(f"[r{i}]" for i in range(len(racks)))
        g.append(f"[z]split={len(racks) + 1}[zm]{outs}")
        src = "zm"
        for i, (s, d) in enumerate(racks):
            g.append(f"[r{i}]trim=start={s:.3f}:end={s + d:.3f},setpts=PTS-STARTPTS,gblur=sigma=22,"
                     f"format=yuva420p,fade=t=out:st=0:d={d:.3f}:alpha=1,setpts=PTS+{s:.3f}/TB[rb{i}]")
            g.append(f"[{src}][rb{i}]overlay=0:0:eof_action=pass[rk{i}]")
            src = f"rk{i}"
    g += [f"color=c=0xE50914:s={W}x8:r={fps}:d={end:.3f}[fill]",
          f"[{src}][fill]overlay=x='-W+W*t/{end:.3f}':y={bar_y + 8}:eof_action=pass[p0]",
          f"[p0][2:v]overlay=0:{bar_y}:shortest=0:enable='lt(t,{end:.3f})'[p1]",
          f"[p1][3:v]overlay=x='W*t/{end:.3f}-24':y={bar_y - 12}:enable='lt(t,{end:.3f})'[p2]"]
    last = "p2"
    for i, (t0, f, (x, y)) in enumerate(events):
        g.append(f"[{last}][{i + 4}:v]overlay={x}:{y}:eof_action=pass[v{i}]")
        last = f"v{i}"
    g[-1] = g[-1].rsplit("[", 1)[0] + "[vout]"
    script = work / "graph3.txt"
    script.write_text(";\n".join(g), encoding="utf-8", newline="\n")
    out = a.out or a.video.with_name(a.video.stem + "_motion.mp4")
    cmd += ["-/filter_complex", str(script), "-map", "[vout]", "-map", "1:a", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("Motion video:", out)


if __name__ == "__main__":
    main()
