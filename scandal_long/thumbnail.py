"""YouTube thumbnails (1280x720 JPG): cut-out people with white outline and glow over a blurred
event backdrop, a glowing crack between them, 3D gradient Hindi headline, ribbon, stickers.

    ..\\.venv\\Scripts\\python.exe thumbnail.py thumbs\\thumbs.json

Cut-outs (transparent PNG) are made once with thumbs\\cutout.py in .venv_thumb (rembg):
    .venv_thumb\\Scripts\\python.exe thumbs\\cutout.py frame.png thumbs\\cut_name.png [x0 y0 x1 y1]
(crop away channel logos first; REMBG_MODEL=u2net_human_seg if a person is cut badly).

Spec (a list in the JSON file):
    {"out": "output/thumbnails/x_A.jpg",
     "bg": "thumbs/bg_lalbaug.png", "theme": "red" | "gold" | "purple",
     "left":  {"cut": "thumbs/cut_sunita.png", "h": 700, "x": -40, "glow": "#ff2a2a", "flip": false},
     "right": {"cut": "thumbs/cut_govinda.png", "h": 720, "x": -20, "glow": "#2a7bff"},
     "line1": "तलाक वापस!", "line2": "फिर कोमल कौन?",
     "badge": ["37", "साल"], "emoji": "😱", "crack": true,
     "inset": {"img": "thumbs/inset_reddit.png", "label": "Reddit पोस्ट"},
     "text_at": "bottom" | "middle"}
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1280, 720

THEMES = {  # page dark, (unused), light-ray colour, headline gradient top, bottom
    "red":    ("#2a0005", "#7a0010", "rgba(255,40,40,.55)", "#ffe600", "#ff8a00"),
    "gold":   ("#120a00", "#4a2a00", "rgba(255,170,40,.5)", "#fff3a0", "#ffb300"),
    "purple": ("#0d0018", "#3a0a5a", "rgba(190,60,255,.5)", "#ffe600", "#ff6a00"),
}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Baloo+2:wght@800&family=Mukta:wght@800&display=swap');
* { margin:0; padding:0; box-sizing:border-box; }
body { width:1280px; height:720px; overflow:hidden; position:relative; background:var(--d);
  font-family:'Baloo 2','Mukta','Nirmala UI',sans-serif; }
.bg { position:absolute; inset:-40px; background-size:cover; background-position:center;
  filter: blur(14px) saturate(1.3) brightness(.55); transform: scale(1.08); }
.tone { position:absolute; inset:0;
  background: radial-gradient(ellipse 70% 80% at 50% 45%, transparent 0%, var(--d) 95%),
              linear-gradient(180deg, transparent 40%, var(--d) 100%); }
.rays { position:absolute; left:50%; top:42%; width:1800px; height:1800px; margin:-900px 0 0 -900px;
  background: repeating-conic-gradient(from 0deg, var(--ray) 0deg 4deg, transparent 4deg 16deg);
  opacity:.22; mask-image: radial-gradient(circle, #000 0%, transparent 55%);
  -webkit-mask-image: radial-gradient(circle, #000 0%, transparent 55%); }
.p { position:absolute; bottom:-6px; }
.p img { display:block; height:100%; }
.crack { position:absolute; left:592px; top:-10px; width:120px; height:740px; }
.inset { position:absolute; left:470px; top:46px; width:340px; transform:rotate(-5deg);
  border:6px solid #fff; box-shadow:0 0 0 4px #e50914, 0 18px 40px rgba(0,0,0,.7); background:#111; }
.inset img { display:block; width:100%; height:190px; object-fit:cover; object-position:top; }
.inset span { position:absolute; left:-14px; top:-22px; background:#e50914; color:#fff; font-size:30px;
  padding:0 14px 4px; border-radius:8px; transform:rotate(-4deg); }
.head { position:absolute; left:0; right:0; text-align:center; }
.bottom { bottom:26px; } .middle { top:50%; transform:translateY(-55%); }
.l1 { position:relative; display:inline-block; font-size:150px; line-height:1; letter-spacing:1px; }
.l1 .back { position:absolute; inset:0; color:#000; -webkit-text-stroke:22px #000;
  text-shadow: 0 10px 0 #000, 0 16px 30px rgba(0,0,0,.8); }
.l1 .mid { position:absolute; inset:0; color:transparent; -webkit-text-stroke:8px #fff; }
.l1 .front { position:relative; background:linear-gradient(180deg, var(--t1) 25%, var(--t2) 85%);
  -webkit-background-clip:text; background-clip:text; color:transparent; }
.l2 { display:inline-block; margin-top:4px; font-size:58px; line-height:1.05; color:#fff;
  background:linear-gradient(180deg,#ff2b2b,#c4000c); padding:2px 30px 10px; transform:skewX(-8deg) rotate(-1deg);
  box-shadow: 0 8px 0 #6d0006, 0 14px 30px rgba(0,0,0,.6); text-shadow:0 3px 0 rgba(0,0,0,.45); }
.l2 i { display:inline-block; font-style:normal; transform:skewX(8deg); }
.badge { position:absolute; top:22px; left:24px; width:150px; height:150px; border-radius:50%;
  background: radial-gradient(circle at 35% 30%, #ffef5a, #ffb000 70%); color:#1a0a00;
  border:6px solid #fff; box-shadow:0 0 0 5px #e50914, 0 12px 30px rgba(0,0,0,.6);
  display:flex; flex-direction:column; align-items:center; justify-content:center; transform:rotate(-10deg); }
.badge b { font-size:70px; line-height:.8; } .badge small { font-size:30px; line-height:1; }
.emoji { position:absolute; top:18px; right:28px; font-size:110px; font-family:'Segoe UI Emoji',sans-serif;
  filter: drop-shadow(0 8px 10px rgba(0,0,0,.7)); transform:rotate(10deg); }
"""

CRACK = ('<svg class="crack" viewBox="0 0 120 740" style="filter:drop-shadow(0 0 8px #fff) '
         'drop-shadow(0 0 18px #ff2020) drop-shadow(0 0 34px #ff0000)"><polyline fill="none" stroke="#fff" '
         'stroke-width="8" stroke-linejoin="miter" points="70,0 40,120 78,200 34,320 82,410 40,520 '
         '84,620 52,740"/><polyline fill="none" stroke="#fff" stroke-width="3" points="78,200 112,236"/>'
         '<polyline fill="none" stroke="#fff" stroke-width="3" points="40,520 8,560"/></svg>')


def _uri(p: str) -> str:
    path = Path(p)
    return (path if path.is_absolute() else HERE / path).resolve().as_uri()


def _person(side: str, s: dict) -> str:
    glow = s.get("glow", "#ff2a2a")
    outline = " ".join(f"drop-shadow({dx}px {dy}px 0 #fff)" for dx, dy in
                       ((5, 0), (-5, 0), (0, 5), (0, -5), (4, 4), (-4, 4), (4, -4), (-4, -4)))
    flip = "transform:scaleX(-1);" if s.get("flip") else ""
    return (f'<div class="p" style="{side}:{s.get("x", 0)}px;height:{s.get("h", 700)}px;'
            f'filter:{outline} drop-shadow(0 0 22px {glow}) drop-shadow(0 0 40px {glow});">'
            f'<img src="{_uri(s["cut"])}" style="{flip}"></div>')


def page(t: dict) -> str:
    e = html.escape
    d, d2, ray, t1, t2 = THEMES[t.get("theme", "red")]
    out = [f'<html><head><meta charset="utf-8"><style>:root{{--d:{d};--ray:{ray};--t1:{t1};--t2:{t2};}}'
           f'{CSS}</style></head><body>',
           f'<div class="bg" style="background-image:url({_uri(t["bg"])})"></div>',
           '<div class="rays"></div><div class="tone"></div>']
    if t.get("crack", True):
        out.append(CRACK)
    out += [_person("left", t["left"]), _person("right", t["right"])]
    if t.get("inset"):
        out.append(f'<div class="inset"><img src="{_uri(t["inset"]["img"])}">'
                   f'<span>{e(t["inset"].get("label", ""))}</span></div>')
    if t.get("badge"):
        b = t["badge"]
        out.append(f'<div class="badge"><b>{e(b[0])}</b><small>{e(b[1]) if len(b) > 1 else ""}</small></div>')
    if t.get("emoji"):
        out.append(f'<div class="emoji">{t["emoji"]}</div>')
    l1 = e(t["line1"])
    out.append(f'<div class="head {t.get("text_at", "bottom")}"><div class="l1"><span class="back">{l1}</span>'
               f'<span class="mid">{l1}</span><span class="front">{l1}</span></div><br>'
               + (f'<div class="l2"><i>{e(t["line2"])}</i></div>' if t.get("line2") else "") + "</div>")
    out.append("</body></html>")
    return "".join(out)


# --------------------------------------------------------------------------- concept layouts
# "torn": an old sepia wedding photo torn down the middle (two cut-outs, paper edges, grain),
#         a third face in a red circle with "?" in the tear, warm red and gold, gold badge.
# "backtoback": two cut-outs turned away from each other, a big glowing cracked heart between
#         them, blue / purple neon rim glow, night backdrop.
# Both use the same headline + ribbon text style as the default layout.

GRAIN = ('<svg class="grain" width="1280" height="720"><filter id="n"><feTurbulence type="fractalNoise" '
         'baseFrequency=".9" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/>'
         '</filter><rect width="100%" height="100%" filter="url(#n)"/></svg>')

TORN_CSS = """
body.torn { background: radial-gradient(ellipse at 50% 40%, #5a0c0c 0%, #2a0405 60%, #140102 100%); }
.dust { position:absolute; inset:0; opacity:.55;
  background-image: radial-gradient(#ffcf5a 1.2px, transparent 1.6px), radial-gradient(#ffb300 1px, transparent 1.4px);
  background-size: 46px 46px, 73px 73px; background-position: 0 0, 20px 31px;
  mask-image: radial-gradient(ellipse at 50% 30%, transparent 30%, #000 80%);
  -webkit-mask-image: radial-gradient(ellipse at 50% 30%, transparent 30%, #000 80%); }
.photo { position:absolute; top:30px; width:600px; height:600px; background:#f1e2c0;
  box-shadow: 0 25px 50px rgba(0,0,0,.65); }
.photo .pic { position:absolute; inset:16px; overflow:hidden;
  background: radial-gradient(ellipse at 50% 40%, #d9b98a 0%, #a77d4c 60%, #5e4022 100%); }
.photo .pic::after { content:""; position:absolute; inset:0;
  background: radial-gradient(ellipse at 50% 45%, transparent 45%, rgba(40,20,5,.65) 100%); }
.photo .pic img { position:absolute; bottom:-8px; filter: sepia(.85) saturate(1.25) contrast(1.08) brightness(1.02); }
.halfL { left:6px; transform: rotate(-3.5deg); }
.halfR { right:6px; transform: rotate(3deg); }
.grain { position:absolute; inset:0; opacity:.22; mix-blend-mode:overlay; pointer-events:none; }
.who { position:absolute; left:50%; top:210px; width:250px; height:250px; margin-left:-125px; border-radius:50%;
  overflow:hidden; border:10px solid #e50914; box-shadow: 0 0 0 6px #fff, 0 0 40px 10px rgba(255,30,30,.85); }
.who img { width:100%; height:100%; object-fit:cover; }
.qmark { position:absolute; left:50%; top:150px; margin-left:62px; font-size:200px; line-height:1; font-weight:800;
  color:#ff1a1a; -webkit-text-stroke:10px #fff; paint-order:stroke fill; transform:rotate(12deg);
  text-shadow: 0 10px 0 #600, 0 0 30px rgba(0,0,0,.8); font-family:'Baloo 2',sans-serif; }
.badge.gold { background: radial-gradient(circle at 35% 30%, #fff1a8, #e2a300 70%); box-shadow:0 0 0 5px #8a5a00, 0 12px 30px rgba(0,0,0,.6); }
"""

B2B_CSS = """
body.b2b { background: radial-gradient(ellipse at 50% 35%, #2a0f5c 0%, #0c0626 55%, #04020f 100%); }
.bokeh { position:absolute; inset:0; opacity:.55;
  background-image: radial-gradient(circle, rgba(120,90,255,.55) 0 18px, transparent 19px),
                    radial-gradient(circle, rgba(40,200,255,.4) 0 12px, transparent 13px),
                    radial-gradient(circle, rgba(255,80,200,.35) 0 26px, transparent 27px);
  background-size: 240px 200px, 180px 260px, 320px 300px; background-position: 30px 40px, 120px 10px, 200px 120px;
  filter: blur(3px); }
.floor { position:absolute; left:0; right:0; bottom:0; height:240px;
  background: linear-gradient(180deg, transparent, rgba(4,2,15,.95)); }
.heart { position:absolute; left:50%; top:40px; width:420px; height:400px; margin-left:-210px; }
"""

HEART = ('<svg class="heart" viewBox="0 0 420 400" style="filter:drop-shadow(0 0 10px #ff3b8d) '
         'drop-shadow(0 0 28px #ff0a6c) drop-shadow(0 0 60px #b4007a)">'
         '<defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff5fa8"/>'
         '<stop offset="1" stop-color="#c3002f"/></linearGradient></defs>'
         # left half of a heart, pushed left and tilted; right half pushed right
         '<g transform="translate(-14,6) rotate(-6 210 200)"><path fill="url(#hg)" stroke="#ffd0e6" stroke-width="6" '
         'd="M210 360 L60 210 C0 150 30 50 110 45 C160 42 195 75 210 105 L190 160 L225 205 L185 255 L220 300 Z"/></g>'
         '<g transform="translate(14,6) rotate(6 210 200)"><path fill="url(#hg)" stroke="#ffd0e6" stroke-width="6" '
         'd="M210 360 L360 210 C420 150 390 50 310 45 C260 42 225 75 210 105 L190 160 L225 205 L185 255 L220 300 Z"/></g>'
         '<polyline fill="none" stroke="#fff" stroke-width="5" points="210,100 190,160 225,205 185,255 220,300 205,360"/>'
         '</svg>')


def _cut_img(s: dict, extra: str = "") -> str:
    flip = "transform:scaleX(-1);" if s.get("flip") else ""
    return f'<img src="{_uri(s["cut"])}" style="height:{s.get("h", 560)}px;left:{s.get("x", 0)}px;{flip}{extra}">'


def page_torn(t: dict) -> str:
    e = html.escape
    d, _, ray, t1, t2 = THEMES["red"]
    out = [f'<html><head><meta charset="utf-8"><style>:root{{--d:{d};--ray:{ray};--t1:{t1};--t2:{t2};}}'
           f'{CSS}{TORN_CSS}</style></head><body class="torn"><div class="dust"></div>']
    # each half: paper card with a torn inner edge (jagged clip-path), the person inside
    tearL = "polygon(0 0,100% 0,96% 8%,100% 15%,95% 24%,99% 33%,94% 42%,99% 51%,95% 60%,100% 69%,94% 78%,99% 87%,95% 94%,98% 100%,0 100%)"
    tearR = "polygon(4% 0,100% 0,100% 100%,2% 100%,5% 93%,0 85%,5% 77%,1% 68%,6% 59%,1% 50%,5% 41%,0 32%,5% 23%,1% 14%,5% 6%)"
    out.append(f'<div class="photo halfL" style="clip-path:{tearL}"><div class="pic">{_cut_img(t["left"])}</div></div>')
    out.append(f'<div class="photo halfR" style="clip-path:{tearR}"><div class="pic">{_cut_img(t["right"])}</div></div>')
    out.append(GRAIN)
    if t.get("circle"):
        out.append(f'<div class="who"><img src="{_uri(t["circle"])}"></div><div class="qmark">?</div>')
    if t.get("badge"):
        b = t["badge"]
        out.append(f'<div class="badge gold"><b>{e(b[0])}</b><small>{e(b[1]) if len(b) > 1 else ""}</small></div>')
    out.append(_headline(t))
    out.append("</body></html>")
    return "".join(out)


def page_b2b(t: dict) -> str:
    d, _, ray, t1, t2 = THEMES["purple"]
    out = [f'<html><head><meta charset="utf-8"><style>:root{{--d:{d};--ray:{ray};--t1:{t1};--t2:{t2};}}'
           f'{CSS}{B2B_CSS}</style></head><body class="b2b"><div class="bokeh"></div>']
    if t.get("bg"):
        out.append(f'<div class="bg" style="background-image:url({_uri(t["bg"])});opacity:.35;'
                   f'filter:blur(16px) hue-rotate(220deg) saturate(1.4) brightness(.5)"></div>')
    out.append(HEART)
    for side in ("left", "right"):
        s = t[side]
        c = s.get("glow", "#2ad1ff")
        rim = (f"drop-shadow(0 0 2px {c}) drop-shadow(0 0 6px {c}) drop-shadow(0 0 16px {c}) "
               f"drop-shadow(0 0 34px {c})")
        out.append(f'<div class="p" style="{side}:{s.get("x", 0)}px;height:{s.get("h", 700)}px;filter:{rim};">'
                   f'<img src="{_uri(s["cut"])}" style="{"transform:scaleX(-1);" if s.get("flip") else ""}"></div>')
    out.append('<div class="floor"></div>')
    out.append(_headline(t))
    out.append("</body></html>")
    return "".join(out)


def _headline(t: dict) -> str:
    e = html.escape
    l1 = e(t["line1"])
    return (f'<div class="head {t.get("text_at", "bottom")}"><div class="l1"><span class="back">{l1}</span>'
            f'<span class="mid">{l1}</span><span class="front">{l1}</span></div><br>'
            + (f'<div class="l2"><i>{e(t["line2"])}</i></div>' if t.get("line2") else "") + "</div>")


LAYOUTS = {"torn": page_torn, "backtoback": page_b2b}


def render(specs: list[dict], workdir: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    outs = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": W, "height": H})
        for i, t in enumerate(specs):
            f = workdir / f"_thumb_{i}.html"
            f.write_text(LAYOUTS.get(t.get("layout"), page)(t), encoding="utf-8")
            pg.goto(f.as_uri())
            pg.wait_for_timeout(1500)  # web fonts
            out = Path(t["out"]) if Path(t["out"]).is_absolute() else HERE / t["out"]
            out.parent.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(out), type="jpeg", quality=93)
            f.unlink()
            outs.append(out)
            print("thumbnail:", out)
        br.close()
    return outs


if __name__ == "__main__":
    spec_file = Path(sys.argv[1]).resolve()
    specs = json.loads(spec_file.read_text(encoding="utf-8"))
    render(specs if isinstance(specs, list) else [specs], spec_file.parent)
