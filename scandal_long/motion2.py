"""Motion-graphics pass v2: a punchier version of motion.py (which stays as it was).

Works on landscape (1920x1080) and vertical (1080x1920, --vertical renders) videos; the canvas
size comes from the video. Draws with Pillow into short transparent qtrle clips and overlays
them in one ffmpeg run (video only; audio is copied). Per part:
- whip transition: red/gold diagonal bands and light streaks sweep the frame + a quick flash;
- zoom kick: the picture punches in ~7% and eases back (also on every kinetic text hit);
- kinetic chapter lower-third: number badge spins in, headline words pop in one by one,
  sub-line types on, gold underline wipe, glint, slide out;
- chips: split pill (dark label | red value) that springs in with a typewriter value;
- kinetic text: big words that slam in one by one with an RGB-split glitch, then fly out
  on a dark strip ("quote" style adds gold quote marks);
- progress bar with chapter ticks and a glowing head (bottom; vertical: under the clip band);
- verdict card over the last part: question slams in, two answer pills fly in from the sides
  with a pulsing VS badge, bouncing "comment below";
- animated end card replacing the still credits page (names from credits.txt; "still_credits": true
  in the spec keeps the still page).

Usage (Pillow is in the thumbnail venv):
    .venv_thumb\\Scripts\\python.exe motion2.py output\\x\\x.mp4 motion\\x.json [--out file.mp4]

Spec (English only; Pillow here has no Devanagari shaping). Times are seconds after a part's
start in timeline.txt:
    {"chapters": {"1": ["HEADLINE", "sub-line"], ...},
     "chips": [{"seg": 2, "at": 1.0, "dur": 4.5, "label": "AIRED", "text": "2 OCT 2026"}],
     "kinetic": [{"seg": 2, "at": 9.0, "dur": 3.2, "text": "I CAN'T FEEL THAT PEACE", "style": "quote"}],
     "verdict": {"seg": 5, "at": 0.5, "q": "YOUR VERDICT?", "a": "OVERREACTION", "b": "FAIR ANGER",
                 "sub": "Comment below"}}
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from motion import (BOLD, FFMPEG, GOLD, IMPACT, RED, WHITE, clamp, font, in_cubic, out_back,
                    out_cubic, probe, segment_starts, text_w)

DARK = (12, 12, 18)
W, H = 1920, 1080  # replaced by the video's size in main()


def vertical() -> bool:
    return H > W


def band_box() -> tuple[int, int]:
    """Top and bottom of the landscape-clip band in a vertical video (scandal.py: 40% of H)."""
    fh = int(H * 0.4) // 2 * 2
    top = (H - fh) // 2
    return top, top + fh


# ---- pieces -------------------------------------------------------------------------------
def whip(dur: float = 0.55):
    """Full-frame transition overlay: diagonal bands + streaks sweep left to right."""
    rnd = random.Random(7)
    streaks = [(rnd.uniform(0, H), rnd.uniform(4, 16), rnd.uniform(0.6, 1.4), rnd.randint(90, 200))
               for _ in range(14)]
    span = W + H  # bands are slanted, so they travel further than the width

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        p = t / dur
        slant = H * 0.45
        for off, col, width in ((0.0, RED + (235,), 0.34), (0.09, GOLD + (230,), 0.05), (0.14, DARK + (200,), 0.12)):
            x = -W * 0.6 + span * 1.2 * out_cubic((p - off) / 0.8)
            wpx = W * width
            d.polygon([(x, 0), (x + wpx, 0), (x + wpx - slant, H), (x - slant, H)], fill=col)
        for y, th, speed, a in streaks:
            x = -W + (W * 2.4) * clamp(p * speed)
            d.rectangle([x, y, x + W * 0.35, y + th], fill=(255, 255, 255, int(a * (1 - p))))
        flash = max(0.0, 1 - abs(p - 0.42) / 0.12)
        if flash > 0:
            im.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(110 * flash))))
        return im

    return frame, (W, H), (0, 0)


def lower_third(num: str, head: str, sub: str, dur: float):
    big = vertical()
    fh, fs, fn = font(IMPACT, 70 if not big else 62), font(BOLD, 30 if not big else 28), font(IMPACT, 54)
    words = head.split()
    gap = text_w(fh, " ")
    head_w = sum(text_w(fh, w) for w in words) + gap * (len(words) - 1)
    badge = 104
    pw = min(max(head_w, text_w(fs, sub)) + badge + 110, W - 60)
    ph = 196
    cw, ch = pw + 80, ph + 70
    py = 30
    panel = Image.new("RGBA", (pw, ph))
    g = ImageDraw.Draw(panel)
    for x in range(pw):
        g.line([(x, 0), (x, ph)], fill=DARK + (int(235 - 80 * x / pw),))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], 16, fill=255)
    panel.putalpha(Image.composite(panel.getchannel("A"), mask, mask))
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([46, py + 12, 46 + pw, py + 12 + ph], 18, fill=(0, 0, 0, 160))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    # number badge (gold disc with the number), pre-drawn and spun in
    bd = Image.new("RGBA", (badge, badge), (0, 0, 0, 0))
    bdd = ImageDraw.Draw(bd)
    bdd.ellipse([0, 0, badge - 1, badge - 1], fill=GOLD)
    bdd.ellipse([7, 7, badge - 8, badge - 8], outline=DARK, width=4)
    bdd.text(((badge - text_w(fn, num)) / 2, 18), num, font=fn, fill=DARK)

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        grow = out_cubic(t / 0.45)
        out = in_cubic((t - (dur - 0.45)) / 0.45)
        x0 = int(40 - (pw + 140) * out)
        vis_w = max(1, int(pw * grow))
        im.alpha_composite(shadow.crop((0, 0, 46 + vis_w + 30, ch)), (int(-(pw + 140) * out), 0))
        if x0 + vis_w > 0:
            im.paste(panel.crop((0, 0, vis_w, ph)), (x0, py), panel.crop((0, 0, vis_w, ph)))
        d = ImageDraw.Draw(im)
        d.rectangle([x0, py, x0 + 10, py + ph * grow], fill=RED)
        # badge: scale from 0 with overshoot while spinning a half turn
        b = out_back((t - 0.1) / 0.45, 2.2)
        if b > 0.02:
            size = max(2, int(badge * b))
            rot = bd.rotate(-180 * (1 - clamp((t - 0.1) / 0.45)), resample=Image.Resampling.BICUBIC)
            rot = rot.resize((size, size), Image.Resampling.LANCZOS)
            cx, cy = x0 + 30 + badge // 2, py + ph // 2 - 12
            im.alpha_composite(rot, (int(cx - size / 2), int(cy - size / 2)))
        # headline words pop one by one
        tx = x0 + 30 + badge + 28
        for i, w in enumerate(words):
            k = out_back((t - 0.3 - 0.09 * i) / 0.32, 2.4)
            if k > 0.02:
                ww = text_w(fh, w)
                lay = Image.new("RGBA", (ww + 20, 110), (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay)
                ld.text((13, 13), w, font=fh, fill=(0, 0, 0, 170))
                ld.text((10, 10), w, font=fh, fill=WHITE if i else GOLD)
                s = max(0.05, k)
                lw, lh = max(1, int(lay.width * s)), max(1, int(lay.height * s))
                lay = lay.resize((lw, lh), Image.Resampling.LANCZOS)
                im.alpha_composite(lay, (int(tx + (ww + 20 - lw) / 2 - 10), int(py + 26 + (110 - lh) / 2)))
            tx += text_w(fh, w) + gap
        # sub-line types on
        n = int(len(sub) * clamp((t - 0.75) / 0.7))
        if n:
            d.text((x0 + 30 + badge + 30, py + 128), sub[:n] + ("|" if n < len(sub) and int(t * 8) % 2 else ""),
                   font=fs, fill=(235, 235, 235))
        u = out_cubic((t - 0.55) / 0.6)
        if u > 0:
            d.rectangle([x0 + 10, py + ph - 6, x0 + 10 + (pw - 10) * u, py + ph], fill=GOLD)
        gl = (t - 1.4) / 0.7
        if 0 < gl < 1:
            lay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            gx = x0 + gl * (pw + 200) - 100
            ImageDraw.Draw(lay).polygon([(gx, py), (gx + 70, py), (gx + 25, py + ph), (gx - 45, py + ph)],
                                        fill=(255, 255, 255, 60))
            m = Image.new("L", (cw, ch), 0)
            m.paste(panel.getchannel("A"), (x0, py))
            lay.putalpha(ImageChops.multiply(lay.getchannel("A"), m))
            im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(3)))
        return im

    y = band_box()[1] + 20 if vertical() else H - ch - 60
    return frame, (cw, ch), (0, y)


def chip(label: str, text: str, dur: float):
    fl, ft = font(BOLD, 30), font(IMPACT, 44)
    lw = text_w(fl, label) + 44 if label else 0
    tw = text_w(ft, text) + 60
    pw, ph = lw + tw + 26, 72
    cw, ch = pw + 60, ph + 50

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        x = (cw + 40) * (1 - out_back(t / 0.5, 1.4)) + (cw + 40) * in_cubic((t - (dur - 0.35)) / 0.35)
        x0, y0 = int(20 + x), 20
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0 + 4, y0 + 8, x0 + pw + 4, y0 + ph + 8], 36, fill=(0, 0, 0, 150))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], 36, fill=RED)
        if label:
            d.rounded_rectangle([x0, y0, x0 + lw + 26, y0 + ph], 36, fill=DARK + (240,))
            pulse = 0.5 + 0.5 * math.sin(t * 8)
            r = 7 + 3 * pulse
            d.ellipse([x0 + 26 - r, y0 + ph / 2 - r, x0 + 26 + r, y0 + ph / 2 + r], fill=GOLD)
            d.text((x0 + 42, y0 + 17), label, font=fl, fill=GOLD)
        n = int(len(text) * clamp((t - 0.3) / 0.5))
        d.text((x0 + lw + 50, y0 + 9), text[:n], font=ft, fill=WHITE)
        return im

    y = band_box()[0] - ch - 30 if vertical() else 30
    return frame, (cw, ch), (W - cw - 20, y)


def kinetic(text: str, dur: float, style: str = ""):
    """Big words slamming in one at a time (wrapped to fit), glitch on impact, fly out at the end."""
    size = 104 if not vertical() else 92
    f = font(IMPACT, size)
    maxw = W - 220
    lines, cur = [], []
    for w in text.split():
        if cur and text_w(f, " ".join(cur + [w])) > maxw:
            lines.append(cur)
            cur = []
        cur.append(w)
    lines.append(cur)
    lh = int(size * 1.12)
    cw, ch = W, lh * len(lines) + 120
    gap = text_w(f, " ")
    placed = []  # (word, x, y, index)
    i = 0
    for li, ws in enumerate(lines):
        lw = sum(text_w(f, w) for w in ws) + gap * (len(ws) - 1)
        x = (W - lw) / 2
        for w in ws:
            placed.append((w, x, 60 + li * lh, i))
            x += text_w(f, w) + gap
            i += 1
    step = min(0.16, 0.9 / max(1, len(placed)))
    quote = style == "quote"

    def word_img(w: str, col) -> Image.Image:
        lay = Image.new("RGBA", (text_w(f, w) + 40, lh + 40), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.text((20, 14), w, font=f, fill=col, stroke_width=6, stroke_fill=DARK)
        return lay

    cache = {(w, k): word_img(w, GOLD if (quote and k == len(placed) - 1) else WHITE) for w, _, _, k in placed}

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        out = in_cubic((t - (dur - 0.35)) / 0.35)
        a = int(150 * out_cubic(t / 0.25) * (1 - out))  # dark strip keeps the words readable
        if a:
            strip = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            ImageDraw.Draw(strip).rectangle([0, 30, cw, ch - 30], fill=(0, 0, 0, a))
            im.alpha_composite(strip)
            if quote:
                q = font(IMPACT, 180)
                ImageDraw.Draw(im).text((50, 0), "“", font=q, fill=GOLD + (int(a * 1.6),))
        for w, x, y, k in placed:
            p = (t - k * step) / 0.22
            if p <= 0:
                continue
            s = 1 + 0.9 * (1 - out_cubic(p))  # from 1.9x down to 1x
            alpha = clamp(p * 3) * (1 - out)
            img = cache[(w, k)]
            if s != 1:
                img = img.resize((int(img.width * s), int(img.height * s)), Image.Resampling.BILINEAR)
            if p < 0.6:  # RGB split glitch on impact
                r, g_, b, a_ = img.split()
                sh = int(14 * (1 - p / 0.6))
                img = Image.merge("RGBA", (ImageChops.offset(r, sh, 0), g_, ImageChops.offset(b, -sh, 0), a_))
            if alpha < 1:
                img.putalpha(img.getchannel("A").point(lambda v, al=alpha: int(v * al)))
            dy = -120 * out
            ox = x - 20 - (img.width - cache[(w, k)].width) / 2
            oy = y - 14 - (img.height - cache[(w, k)].height) / 2 + dy
            im.alpha_composite(img, (int(ox), int(oy)))
        return im

    if vertical():
        y = max(140, band_box()[0] - ch - 10)
    else:
        y = 110
    return frame, (cw, ch), (0, y)


def verdict(q: str, a: str, b: str, sub: str, dur: float):
    fq, fa, fs, fv = font(IMPACT, 96), font(IMPACT, 54), font(BOLD, 36), font(IMPACT, 50)
    pill_w = max(text_w(fa, a), text_w(fa, b)) + 90
    cw = min(W - 40, max(text_w(fq, q) + 120, pill_w * 2 + 200))
    if vertical():
        pill_w = min(pill_w, (cw - 160) // 2)
    ch = 430

    def frame(t: float) -> Image.Image:
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        out = in_cubic((t - (dur - 0.4)) / 0.4)
        card_a = out_cubic(t / 0.3) * (1 - out)
        d = ImageDraw.Draw(im)
        if card_a > 0:
            glow = 0.5 + 0.5 * math.sin(t * 5)
            gl = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            ImageDraw.Draw(gl).rounded_rectangle([20, 20, cw - 20, ch - 20], 28,
                                                 outline=RED + (int((150 + 100 * glow) * card_a),), width=14)
            im.alpha_composite(gl.filter(ImageFilter.GaussianBlur(10 + 6 * glow)))
            d.rounded_rectangle([20, 20, cw - 20, ch - 20], 28, fill=DARK + (int(225 * card_a),),
                                outline=RED + (int(255 * card_a),), width=5)
        # question slams down
        k = out_back((t - 0.15) / 0.4, 2.0)
        if k > 0.02 and out < 1:
            lay = Image.new("RGBA", (cw, 130), (0, 0, 0, 0))
            ImageDraw.Draw(lay).text(((cw - text_w(fq, q)) / 2, 6), q, font=fq, fill=WHITE,
                                     stroke_width=4, stroke_fill=DARK)
            s = 1 + 1.2 * (1 - clamp(k))
            lay2 = lay.resize((int(cw * s), int(130 * s)), Image.Resampling.BILINEAR)
            lay2.putalpha(lay2.getchannel("A").point(lambda v: int(v * (1 - out))))
            im.alpha_composite(lay2.crop(((lay2.width - cw) // 2, 0, (lay2.width - cw) // 2 + cw, min(lay2.height, ch))),
                               (0, 40))
        # answer pills from the sides, VS badge between
        cy = 220
        for side, label, col, delay in ((-1, a, (40, 120, 255), 0.45), (1, b, RED, 0.6)):
            p = out_back((t - delay) / 0.4, 1.5)
            if p <= 0 or out >= 1:
                continue
            target = cw / 2 - 70 - pill_w if side < 0 else cw / 2 + 70
            x = target + side * (cw / 2 + pill_w) * (1 - p) + side * cw * out
            d.rounded_rectangle([x, cy, x + pill_w, cy + 86], 43, fill=col + (240,), outline=WHITE, width=3)
            lbl = label
            while text_w(fa, lbl) > pill_w - 30 and len(lbl) > 3:
                lbl = lbl[:-1]
            d.text((x + (pill_w - text_w(fa, lbl)) / 2, cy + 12), lbl, font=fa, fill=WHITE)
        v = out_back((t - 0.8) / 0.35, 2.5) * (1 - out)
        if v > 0.02:
            r = 50 * v * (1 + 0.08 * math.sin(t * 9))
            d.ellipse([cw / 2 - r, cy + 43 - r, cw / 2 + r, cy + 43 + r], fill=GOLD, outline=DARK, width=4)
            if v > 0.7:
                d.text((cw / 2 - text_w(fv, "VS") / 2, cy + 13), "VS", font=fv, fill=DARK)
        sp = clamp((t - 1.1) / 0.3) * (1 - out)
        if sp > 0:
            bounce = 7 * abs(math.sin(t * 4))
            s = sub + "  ▼"
            d.text(((cw - text_w(fs, s)) / 2, 340 + bounce), s, font=fs, fill=GOLD + (int(255 * sp),))
        return im

    y = band_box()[1] + 10 if vertical() else (H - ch) // 2 + 60
    return frame, (cw, ch), ((W - cw) // 2, y)


def credits_card(names: list[str], dur: float, backdrop: Image.Image):
    """Full-frame animated end card (replaces the still "Video credits" page): blurred, slowly
    zooming backdrop from the last shot, drifting gold particles, title letters dropping in,
    source cards sliding up one by one, a light sweep, a pulsing subscribe pill, fade to black."""
    vert = vertical()
    bg = backdrop.convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(28))
    bg = Image.blend(bg, Image.new("RGB", (W, H), DARK), 0.62).convert("RGBA")
    big = bg.resize((int(W * 1.08), int(H * 1.08)), Image.Resampling.BILINEAR)
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.25, -H * 0.25, W * 1.25, H * 1.25], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(min(W, H) // 5))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    shade.putalpha(vig.point(lambda v: int((255 - v) * 0.85)))
    rnd = random.Random(11)
    parts = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(2, 6), rnd.uniform(20, 70), rnd.uniform(0, 6.28))
             for _ in range(46)]
    ft = font(IMPACT, 96 if vert else 112)
    fsub = font(BOLD, 28 if vert else 30)
    fname = font(BOLD, 46 if vert else 48)
    fthx = font(IMPACT, 50 if vert else 52)
    fsubs = font(IMPACT, 40)
    title = "VIDEO CREDITS"
    tw = text_w(ft, title)
    ty = int(H * (0.17 if vert else 0.1))
    # source cards in a centred grid
    cols = 1 if (vert and len(names) <= 8) else (2 if vert or len(names) <= 4 else 3)
    rows = math.ceil(len(names) / cols)
    card_w = int(W * 0.8) if cols == 1 else min(int(W * 0.84 / cols) - 30, 640)
    card_h, gap = 112, 30
    grid_w = cols * card_w + (cols - 1) * gap
    gx0 = (W - grid_w) // 2
    gy0 = ty + (270 if vert else 250)
    cards = []
    for i, n in enumerate(names):
        r, c = i // cols, i % cols
        if r == rows - 1 and len(names) % cols:  # centre a short last row
            left = len(names) - r * cols
            x = (W - (left * card_w + (left - 1) * gap)) // 2 + c * (card_w + gap)
        else:
            x = gx0 + c * (card_w + gap)
        cards.append((n, x, gy0 + r * (card_h + gap)))
    grid_bottom = gy0 + rows * (card_h + gap)
    card_img = {}
    for n, _, _ in cards:
        im = Image.new("RGBA", (card_w + 20, card_h + 24), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([6, 12, card_w + 6, card_h + 12], 18, fill=(0, 0, 0, 160))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, card_w, card_h], 18, fill=(18, 18, 26, 235), outline=(255, 255, 255, 40), width=2)
        d.rounded_rectangle([0, 0, 10, card_h], 5, fill=RED)
        cx, cy = 66, card_h // 2
        d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=RED)
        d.polygon([(cx - 10, cy - 15), (cx - 10, cy + 15), (cx + 16, cy)], fill=WHITE)
        lbl = n
        while text_w(fname, lbl) > card_w - 130 and len(lbl) > 4:
            lbl = lbl[:-2] + "…"
        d.text((114, (card_h - 58) // 2), lbl, font=fname, fill=WHITE)
        card_img[n] = im

    def frame(t: float) -> Image.Image:
        z = clamp(t / max(dur, 0.1))
        ox, oy = int((big.width - W) * (0.5 - 0.5 * z)), int((big.height - H) * 0.5)
        im = big.crop((ox, oy, ox + W, oy + H))
        im.alpha_composite(shade)
        d = ImageDraw.Draw(im)
        for x, y, r, sp, ph in parts:  # gold dust drifting up and twinkling
            yy = (y - sp * t * 6) % H
            a = int(90 + 120 * (0.5 + 0.5 * math.sin(t * 3 + ph)))
            d.ellipse([x - r * 2, yy - r * 2, x + r * 2, yy + r * 2], fill=GOLD + (a // 5,))
            d.ellipse([x - r, yy - r, x + r, yy + r], fill=GOLD + (a,))
        # title letters drop in one by one
        x = (W - tw) / 2
        for k, ch in enumerate(title):
            p = out_back((t - 0.15 - 0.04 * k) / 0.45, 2.0)
            if p > 0 and ch.strip():
                yy = ty - 160 * (1 - p)
                d.text((x + 4, yy + 5), ch, font=ft, fill=(0, 0, 0, 170))
                d.text((x, yy), ch, font=ft, fill=WHITE if k < 6 else GOLD)
            x += text_w(ft, ch)
        u = out_cubic((t - 0.75) / 0.6)
        if u > 0:
            half = tw * 0.55 * u
            d.rectangle([W / 2 - half, ty + 150, W / 2 + half, ty + 157], fill=GOLD)
            d.rectangle([W / 2 - half * 0.6, ty + 163, W / 2 + half * 0.6, ty + 166], fill=RED)
        sp_ = clamp((t - 1.0) / 0.5)
        if sp_ > 0:
            sub = "All clips belong to their original creators"
            d.text(((W - text_w(fsub, sub)) / 2, ty + 180 - 10 * (1 - sp_)), sub, font=fsub,
                   fill=(230, 230, 230, int(230 * sp_)))
        for i, (n, cx, cy) in enumerate(cards):  # source cards slide up with a small overshoot
            p = (t - 1.2 - 0.14 * i) / 0.5
            if p <= 0:
                continue
            k = out_back(p, 1.6)
            img = card_img[n]
            if p < 0.5:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v, a=clamp(p * 2): int(v * a)))
            im.alpha_composite(img, (int(cx), int(cy + 60 * (1 - k))))
        # light sweep across the whole card
        g = (t - 2.4) / 0.9
        if 0 < g < 1:
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sx = -W * 0.3 + g * W * 1.6
            ImageDraw.Draw(lay).polygon([(sx, 0), (sx + W * 0.12, 0), (sx - W * 0.1, H), (sx - W * 0.22, H)],
                                        fill=(255, 255, 255, 34))
            im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(18)))
        # thanks + pulsing subscribe pill
        q = out_back((t - 2.2) / 0.5, 1.8)
        if q > 0.02:
            d = ImageDraw.Draw(im)
            by = max(grid_bottom + 30, int(H * (0.74 if vert else 0.76)))
            thx = "THANKS FOR WATCHING"
            d.text(((W - text_w(fthx, thx)) / 2, by), thx, font=fthx, fill=WHITE + (int(255 * clamp(q)),))
            pulse = 1 + 0.06 * math.sin(t * 6) * clamp(q)
            s = "SUBSCRIBE"
            pw_, ph_ = (text_w(fsubs, s) + 120) * pulse * q, 76 * pulse * q
            px, py = W / 2 - pw_ / 2, by + 80 + (76 - ph_) / 2
            d.rounded_rectangle([px, py, px + pw_, py + ph_], ph_ / 2, fill=RED)
            if q > 0.8:
                d.ellipse([px + 26, py + ph_ / 2 - 11, px + 48, py + ph_ / 2 + 11], fill=WHITE)
                d.text((px + 64 * pulse, py + (ph_ - 50) / 2), s, font=fsubs, fill=WHITE)
        f = clamp((t - (dur - 0.6)) / 0.6)
        if f > 0:
            im.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, int(255 * f))))
        return im

    return frame, (W, H), (0, 0)


# ---- rendering ----------------------------------------------------------------------------
def render_clip(frame_fn, size, dur: float, fps: float, out: Path) -> None:
    w, h = size
    w += w % 2
    h += h % 2
    p = subprocess.Popen([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
                          "-c:v", "qtrle", str(out)], stdin=subprocess.PIPE)
    assert p.stdin
    for i in range(int(round(dur * fps))):
        im = frame_fn(i / fps)
        if im.size != (w, h):
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            im = canvas
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError(f"encoding {out} failed")


def zoom_expr(kicks: list[float], amount: float = 0.07, length: float = 0.4) -> str:
    """Scale factor expression: 1 + a decaying punch-in at each kick time."""
    terms = [f"if(between(t,{k:.3f},{k + length:.3f}),{amount}*pow(1-(t-{k:.3f})/{length},2),0)" for k in kicks]
    return "1+" + "+".join(terms) if terms else "1"


def main() -> None:
    global W, H
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    starts, tail = segment_starts(a.video.parent / "timeline.txt")
    total, fps = probe(a.video)
    W, H = map(int, subprocess.run(
        [FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(a.video)],
        capture_output=True, text=True).stdout.strip().split(","))
    end = tail or total
    work = a.video.parent / "motion_work"
    work.mkdir(exist_ok=True)
    events: list[tuple[float, Path, tuple[int, int]]] = []
    kicks: list[float] = []

    def add(name: str, t0: float, dur: float, made) -> None:
        fn, size, pos = made
        f = work / f"{name}.mov"
        render_clip(fn, size, dur, fps, f)
        events.append((t0, f, pos))
        print(f"{t0:7.1f}s  {name}", flush=True)

    chapters = sorted(spec.get("chapters", {}).items(), key=lambda kv: int(kv[0]))
    n = len(chapters)
    for idx, (seg, (head, sub)) in enumerate(chapters, 1):
        t0 = starts.get(int(seg))
        if t0 is None:
            continue
        if int(seg) > 1:
            add(f"whip{idx:02d}", max(0.0, t0 - 0.25), 0.55, whip(0.55))
            kicks.append(t0)
        lt0 = t0 + (6.3 if int(seg) == 1 else 0.4)  # part 1 opens under the video title (6 s)
        add(f"lt{idx:02d}", lt0, 5.6, lower_third(f"{idx:02d}", head, sub, 5.6))
    for k, c in enumerate(spec.get("chips", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 4.5)
            add(f"chip{k:02d}", t0 + c.get("at", 1.0), dur, chip(c.get("label", ""), c["text"], dur))
    for k, c in enumerate(spec.get("kinetic", [])):
        t0 = starts.get(c["seg"])
        if t0 is not None:
            dur = c.get("dur", 3.0)
            t0 += c.get("at", 1.0)
            add(f"kin{k:02d}", t0, dur, kinetic(c["text"], dur, c.get("style", "")))
            kicks.append(t0 + 0.05)
    if spec.get("verdict"):
        c = spec["verdict"]
        t0 = starts.get(c["seg"])
        if t0 is not None:
            t0 += c.get("at", 0.5)
            dur = max(3.0, end - t0 - 0.2)
            add("verdict", t0, dur, verdict(c["q"], c["a"], c["b"], c.get("sub", "Comment below"), dur))
            kicks.append(t0 + 0.15)

    # animated end card over the still "Video credits" page
    if tail and total - tail > 1.0 and not spec.get("still_credits"):
        names = []
        cfile = a.video.parent / "credits.txt"
        for line in (cfile.read_text(encoding="utf-8").splitlines() if cfile.exists() else []):
            n = line.split(": http")[0].strip()
            if n and n not in names:
                names.append(n)
        if names:
            shot = work / "credits_backdrop.png"
            subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, tail - 0.6):.3f}",
                            "-i", str(a.video), "-frames:v", "1", str(shot)], check=True)
            add("whip_end", max(0.0, tail - 0.25), 0.55, whip(0.55))
            add("credits", tail, total - tail, credits_card(names, total - tail, Image.open(shot)))

    # progress track with chapter ticks + glowing head
    bar_y = band_box()[1] - 10 if vertical() else H - 16
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
    ImageDraw.Draw(head).ellipse([16, 16, 32, 32], fill=WHITE)
    head.save(work / "head.png")

    cmd = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-stats",
           "-i", str(a.video), "-loop", "1", "-i", str(work / "track.png"),
           "-loop", "1", "-i", str(work / "head.png")]
    for t0, f, _ in events:
        cmd += ["-itsoffset", f"{t0:.3f}", "-i", str(f)]
    z = zoom_expr(sorted(kicks))
    g = [f"[0:v]scale=w='2*trunc(iw*({z})/2)':h=-2:eval=frame,crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,setsar=1[z]",
         f"color=c=0xE50914:s={W}x8:r={fps}:d={end:.3f}[fill]",
         f"[z][fill]overlay=x='-W+W*t/{end:.3f}':y={bar_y + 8}:eof_action=pass[p0]",
         f"[p0][1:v]overlay=0:{bar_y}:shortest=0:enable='lt(t,{end:.3f})'[p1]",
         f"[p1][2:v]overlay=x='W*t/{end:.3f}-24':y={bar_y - 12}:enable='lt(t,{end:.3f})'[p2]"]
    last = "p2"
    for i, (t0, f, (x, y)) in enumerate(events):
        g.append(f"[{last}][{i + 3}:v]overlay={x}:{y}:eof_action=pass[v{i}]")
        last = f"v{i}"
    g[-1] = g[-1].rsplit("[", 1)[0] + "[vout]"
    script = work / "graph2.txt"
    script.write_text(";\n".join(g), encoding="utf-8", newline="\n")
    out = a.out or a.video.with_name(a.video.stem + "_motion.mp4")
    cmd += ["-/filter_complex", str(script), "-map", "[vout]", "-map", "0:a?", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("Motion video:", out)


if __name__ == "__main__":
    main()
