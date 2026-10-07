"""Voiceover: a spoken hook, a short line introducing each story, and a sign-off,
synthesised offline with Piper (free, runs locally, no API key).

Every line is written from the item's own title with templates, no LLM, so the
wording is plain: "Number four. Salman Khan makes a stylish appearance at a
dubbing studio." Each narration file starts with a short whoosh so cuts between
stories have a sound cue even before the voice comes in.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import wave
from pathlib import Path

from .media import probe, run_ffmpeg
from .models import Post

log = logging.getLogger(__name__)

VOICES_DIR = Path(__file__).resolve().parent.parent / "voices"
DEFAULT_VOICE = "en_US-ryan-high"
VOICE_START = 0.25  # seconds of whoosh before the voice comes in

NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
           9: "nine", 10: "ten"}

# Hinglish/Instagram spellings a US voice mangles, respelled so they sound right.
SAY = {
    r"\bpaps\b": "paparazzi", r"\bpapped\b": "is spotted", r"\bbday\b": "birthday",
    r"\bpost gym\b": "after the gym", r"\bloreal ?paris\b": "L'Oreal Paris", r"\baganist\b": "against",
    r"\bw/": "with ", r"\bvs\.?\b": "versus", r"\bft\.?\b": "featuring", r"\bSRK\b": "S R K",
    r"\bBTS\b": "behind the scenes", r"\bOTT\b": "O T T", r"\bIG\b": "Instagram", r"\bmei\b": "mein",
    r"\bboht\b": "bahut",
}
ACRONYMS = {"SRK", "BTS", "OTT", "IPL", "TV", "OMG", "AI", "UK", "US", "USA", "IT", "YRF"}

# Name pieces for splitting run-together hashtags ("#aishwaryaraibachchan" ->
# "Aishwarya Rai Bachchan"). A hashtag is only split when every piece is known.
NAME_PARTS = set("""
aishwarya rai bachchan abhishek amitabh jaya shweta aaradhya navya shah rukh khan gauri suhana aryan abram
salman arbaaz sohail aamir kiran junaid ira saif ali kareena kapoor karisma karishma taimur jeh sara ibrahim
soha kunal kemmu inaaya ranbir alia bhatt raha neetu riddhima ranveer singh deepika padukone dua katrina kaif
vicky kaushal sunny isabelle priyanka chopra nick jonas malti parineeti raghav chadha anushka sharma virat kohli
vamika akaay shahid mira rajput janhvi khushi boney arjun sonam anand ahuja anil harshvardhan rhea
tiger shroff jackie krishna disha patani kiara advani sidharth malhotra kriti sanon nupur shraddha shakti
varun dhawan natasha dalal ananya panday chunky bhavana ahaan tara sutaria ishaan khatter sushant rajkummar
patralekhaa ayushmann khurrana tahira hrithik roshan saba azad rakesh sussanne kangana ranaut taapsee pannu
bhumi pednekar kartik aaryan kajol ajay devgn devgan nysa yug madhuri dixit nene rekha hema malini esha deol
dharmendra bobby karan johar farah ekta raveena tandon rasha shilpa shetty raj kundra shamita urvashi rautela
nora fatehi jacqueline fernandez sonakshi sinha zaheer iqbal huma qureshi rakul preet jackky bhagnani mouni roy
tamannaah bhatia vijay varma samantha ruth prabhu naga chaitanya sobhita dhulipala rashmika mandanna deverakonda
allu prabhas yash ram charan upasana ntr mahesh babu namrata dulquer salmaan fahadh faasil nayanthara
bharti gola haarsh limbachiyaa munawar faruqui elvish yadav orry uorfi javed rakhi sawant tanna
kapil ginni tejasswi prakash kundrra shehnaaz gill ankita lokhande jain neha kakkar
suniel athiya rahul dhupia angad bedi aditi rao hydari siddharth
govinda sunita tina vikrant massey triptii dimri avneet kaur jannat zubair riddhi dogra siwet tomar
bigg boss splitsvilla
""".split())
DANGLING = set("""a an the at on in of for to with by as and or but her his their its she he they is was are
from after before about over into this that who while when during""".split())


def split_tag(tag: str) -> str:
    """Split a run-together hashtag into known name pieces, else leave it."""
    low = tag.lower()
    best: list = [[]] + [None] * len(low)
    for i in range(1, len(low) + 1):
        for j in range(max(0, i - 14), i):
            if best[j] is not None and low[j:i] in NAME_PARTS:
                cand = best[j] + [low[j:i]]
                if best[i] is None or len(cand) < len(best[i]):
                    best[i] = cand
    parts = best[-1]
    return " ".join(w.capitalize() for w in parts) if parts else tag


def spoken(title: str, max_words: int = 16) -> str:
    """A title cleaned up to be read aloud: no hashtags, emoji, pipes or shouting,
    cut at a natural break if it runs long."""
    t = re.split(r"\s[|•]\s|\s\|\s?", title)[0]
    t = re.sub(r"(\s*#\w+)+\s*$", "", t)
    t = re.sub(r"#shorts\b", "", t, flags=re.I)
    t = re.sub(r"#(\w+)", lambda m: split_tag(m[1]), t)
    t = re.sub(r"\b[a-z]{9,}\b", lambda m: split_tag(m[0]), t)  # "mouniroy's" written without the #
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿‍️]", "", t)
    t = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", t)  # "Bhagnanis.Gossip"
    t = re.sub(r"\b[A-Z]{2,}\b", lambda m: m[0] if m[0] in ACRONYMS else m[0].capitalize(), t)
    for pat, rep in SAY.items():
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -·:,")
    words = t.split()
    if len(words) > max_words:
        cut = " ".join(words[:max_words])
        # Prefer ending at a comma or clause break inside the limit.
        m = re.search(r"^(.{25,})[,;:?!]\s", cut)
        t = m[1] if m else cut
    # Don't leave the sentence hanging on "at", "with", "her"... (also happens
    # when a trailing hashtag was the object: "Owns the ramp at #pfw").
    words = t.split()
    while len(words) > 4 and words[-1].lower().strip(",'\"") in DANGLING:
        words.pop()
    t = " ".join(words)
    return t.rstrip(".!?,;") + ("?" if t.endswith("?") else ".")


def first_sentence(text: str, max_words: int = 18) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)).strip()
    if not text:
        return ""
    s = re.split(r"(?<=[.!?])\s", text)[0]
    return spoken(s, max_words)


def hook_line(count: int, seconds: float) -> str:
    return f"Today's top {NUMBERS.get(count, count)} Bollywood stories, in {int(round(seconds, -1))} seconds."


def item_line(post: Post, num: int, first: bool, max_words: int = 16) -> str:
    """Introduce one story. Clips get a short line (their own audio should carry
    them); cards get the whole headline plus the start of the post, since the
    voice is what keeps a still image watchable."""
    n = NUMBERS.get(num, str(num))
    lead = "And number one." if num == 1 else (f"Starting at number {n}." if first else f"Number {n}.")
    if post.kind == "video":
        where = " Trending on Reddit." if post.platform == "reddit" else ""
        return f"{lead}{where} {spoken(post.title, max_words)}"
    if post.kind == "news":
        return f"{lead} In the news. {spoken(post.title, max_words + 6)}"
    body = first_sentence(post.selftext, max_words - 2) if post.selftext else ""
    return f"{lead} Over on Reddit. {spoken(post.title, max_words + 6)} {body}".strip()


WELCOME_LINE = "Welcome to your daily dose of Bollywood!"


HOOKS = [
    ["Today's top {n} Bollywood stories.", "Number one is the biggest, so watch till the end!"],
    ["What's happening in Bollywood today?", "Top {n} stories, right here!"],
    ["Wait! Don't miss these {n} stories.", "Number one is saved for last, so stay with me!"],
    ["Gossip time!", "Today's top {n} Bollywood stories, let's go."],
    ["What happened in Bollywood today?", "{n} big stories, in under a minute!"],
    ["If you love Bollywood, this one's for you!", "Top {n} stories, watch till the end."],
    ["{n} stories, one video!", "See what's going on in Bollywood today."],
]
HOOKS_RUMOUR = [
    ["Hot Bollywood rumours!", "{n} stories, you decide how true they are."],
    ["What's the buzz on Reddit and the news today?", "Top {n} rumours, right here!"],
    ["Rumour or truth?", "Today's top {n} Bollywood whispers, take a look."],
    ["Gossip alert!", "{n} fresh rumours, watch till the end."],
    ["What are the fans talking about?", "Today's {n} most talked about Bollywood stories."],
]
KICKERS_RUMOUR = ["🗣️ RUMOUR MILL", "👀 RUMOUR OR TRUTH?", "📰 TODAY'S BUZZ", "🔥 GOSSIP ALERT"]
KICKERS = ["🔥 TODAY'S BIGGEST DRAMA", "⚡ FRESH BOLLYWOOD NEWS", "🎬 BOLLYWOOD UPDATE", "👀 DON'T MISS THIS"]


def flow_hook(count: int, variant: int = 0, kind: str = "clips") -> list[str]:
    """Hook for --welcome: about the whole list, so it fits over whichever clip plays first.
    `variant` picks one of HOOKS (the pipeline varies it from day to day)."""
    pool = HOOKS_RUMOUR if kind == "rumours" else HOOKS
    return [t.format(n=NUMBERS.get(count, count)) for t in pool[variant % len(pool)]]


def pick_variant(size: int, key: str, history_file: Path) -> int:
    """A random index below `size` that differs from the one used last time for `key`
    (remembered in `history_file`), so the opening never repeats two days running."""
    import json
    import random

    try:
        hist = json.loads(history_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hist = {}
    choices = [i for i in range(size) if i != hist.get(key)] or list(range(size))
    pick = random.choice(choices)
    hist[key] = pick
    try:
        history_file.write_text(json.dumps(hist), encoding="utf-8")
    except OSError:
        pass
    return pick


OUTRO_LINE = "Follow for tomorrow's drama."

# ---- Chatterbox take checks (6 Oct 2026, same fix as scandal_long/tts_check.py) -----------------
# Chatterbox misreads some words (कमेंट्स, दुआएं, English names) and, with the server's fixed seed,
# repeats the same bad take. Each take is now transcribed with Whisper and re-voiced with other seeds
# when it doesn't match; words are respelled from the shared pronunciation list first.
PRONUNCIATIONS = Path(__file__).resolve().parent.parent / "scandal_long" / "pronunciations.json"
CHECK_SEEDS = [100, 7, 21]  # up to 3 takes
CHECK_MIN_SCORE = 0.85  # daily lines carry English words Whisper writes loosely; 0.9 retook almost every line
# Soft edges: keep 80 ms before and 250 ms after the words, with short fades (the old hard -45 dB
# trim clipped first sounds and the last word's decay).
SOFT_TRIM = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse,"
             "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.25,afade=t=in:d=0.12,areverse,"
             "afade=t=in:d=0.02,")
NUM_WORDS = set("""शून्य एक दो तीन चार पांच पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह
उन्नीस बीस पच्चीस छब्बीस तीस चालीस पचास साठ सत्तर अस्सी नब्बे सौ हज़ार हजार लाख करोड़""".split())
_whisper = None


def load_pronunciations() -> dict[str, str]:
    try:
        data = json.loads(PRONUNCIATIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def respell(text: str, table: dict[str, str]) -> str:
    for k in sorted(table, key=len, reverse=True):
        text = text.replace(k, table[k])
    return text


def _norm(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFC", s).replace("़", "").replace("ँ", "ं")
    s = re.sub(r"[^\w\s]", " ", s).replace("्", "")
    return " ".join(w for w in s.lower().split() if not w.isdigit() and w not in NUM_WORDS)


def take_score(text: str, heard: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, _norm(text), _norm(heard)).ratio()


def transcribe(path: Path) -> str | None:
    """Whisper (faster-whisper "small", CPU) transcript, or None if it can't run."""
    global _whisper
    if _whisper is False:
        return None
    try:
        if _whisper is None:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(str(path), language="hi")
        return " ".join(s.text.strip() for s in segs)
    except Exception as e:  # noqa: BLE001
        log.warning("Whisper check unavailable (%s); voicing without it", e)
        _whisper = False
        return None


class Narrator:
    """Speaks lines with Piper (default, bundled) or Chatterbox (Resemble AI's
    open-source voice model; clones the voice in `voice_sample`, best on a GPU).
    If Chatterbox isn't installed the run falls back to Piper instead of failing."""

    def __init__(self, cache_dir: Path, voice: str = DEFAULT_VOICE, speed: float = 0.92,
                 engine: str = "piper", voice_sample: str | None = None, cues: bool = True,
                 server: str = "http://127.0.0.1:8765", style: dict | None = None,
                 tempo: float = 1.0, pitch: float = 0.0, ensure_server=None):
        self.ensure_server = ensure_server  # called to (re)start the voice server if it has gone away
        self.tempo, self.pitch = tempo, pitch  # post-processing: speed factor, pitch shift in semitones
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cues = cues
        self.engine = engine
        self.sample = voice_sample
        self.server = server.rstrip("/")
        self.style = style or {}
        self._server_ok = False
        if engine == "chatterbox-server":
            pass  # checked on the first line to voice (no fallback: Piper can't read a Hindi script)
        elif engine == "chatterbox":
            try:
                self._load_chatterbox()
            except Exception as e:  # noqa: BLE001
                log.warning("Chatterbox unavailable (%s); using Piper voice %s instead", e, voice)
                self.engine = "piper"
        if self.engine == "piper":
            self._load_piper(voice, speed)
        self.tag = (f"{self.engine}|{voice if self.engine == 'piper' else voice_sample}|{sorted(self.style.items())}"
                    f"|t{self.tempo}p{self.pitch}|v2")
        self.whoosh = self.dir / "whoosh.wav"
        if not self.whoosh.exists():
            # Filtered noise swept up then down: a soft "whoosh" transition cue.
            run_ffmpeg(["-f", "lavfi", "-i", "anoisesrc=d=0.45:color=pink:amplitude=0.6:sample_rate=44100",
                        "-af", "highpass=f=400,lowpass=f=5000,afade=t=in:d=0.18:curve=exp,"
                               "afade=t=out:st=0.18:d=0.27,volume=0.5,aformat=channel_layouts=stereo",
                        str(self.whoosh)])

    def _load_piper(self, voice: str, speed: float) -> None:
        from piper import PiperVoice, SynthesisConfig  # imported lazily: only needed with --voiceover

        model = Path(voice) if voice.endswith(".onnx") else VOICES_DIR / f"{voice}.onnx"
        if not model.is_file():
            raise FileNotFoundError(
                f"Voice model not found: {model}. Download it with: "
                f"python -m piper.download_voices {voice} --data-dir voices")
        self.piper = PiperVoice.load(str(model))
        self.config = SynthesisConfig(length_scale=speed)

    def _check_server(self) -> None:
        """Ajay's local Chatterbox HTTP server (hindi_server.py): wait for it to finish loading."""
        import time

        import requests

        for _ in range(120):
            try:
                st = requests.get(f"{self.server}/health", timeout=5).json().get("status")
            except requests.RequestException as e:
                raise RuntimeError(f"Chatterbox server not reachable at {self.server}: {e}") from e
            if st == "ok":
                return
            if st == "failed":
                raise RuntimeError("Chatterbox server failed to load its model")
            time.sleep(5)
        raise RuntimeError("Chatterbox server still loading after 10 minutes")

    def _load_chatterbox(self) -> None:
        import torch
        from chatterbox.tts import ChatterboxTTS

        if self.sample and not Path(self.sample).is_file():
            raise FileNotFoundError(f"voice sample not found: {self.sample}")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.cb = ChatterboxTTS.from_pretrained(device=device)
        log.info("Voiceover: Chatterbox on %s%s", device, f", voice from {self.sample}" if self.sample else "")

    def _synth(self, text: str, raw: Path) -> None:
        if self.engine == "chatterbox-server":
            import requests

            if not self._server_ok:
                try:
                    self._check_server()
                except RuntimeError:
                    if not self.ensure_server:
                        raise
                    log.warning("Chatterbox server is not answering; starting it")
                    self.ensure_server()
                    self._check_server()
                self._server_ok = True

            body = {"text": text, **self.style}
            if self.sample:
                body["ref"] = str(Path(self.sample).resolve())
            for attempt in range(4):
                try:
                    r = requests.post(f"{self.server}/tts", json=body, timeout=900)
                except requests.ConnectionError:
                    # The server died or was closed mid-run (e.g. someone closed its window): bring it back.
                    if not self.ensure_server or attempt == 3:
                        raise
                    log.warning("Chatterbox server went away; restarting it (try %d)", attempt + 1)
                    self.ensure_server()
                    self._check_server()
                    continue
                if r.headers.get("content-type") == "audio/wav":
                    raw.write_bytes(r.content)
                    return
                log.warning("Chatterbox server said %s (try %d)", r.text[:200], attempt + 1)
            raise RuntimeError(f"Chatterbox server could not voice: {text!r}")
        if self.engine == "chatterbox":
            import torchaudio

            wav = self.cb.generate(text, audio_prompt_path=self.sample) if self.sample else self.cb.generate(text)
            torchaudio.save(str(raw), wav, self.cb.sr)
        else:
            with wave.open(str(raw), "wb") as w:
                self.piper.synthesize_wav(text, w, syn_config=self.config)

    def _synth_checked(self, text: str, raw: Path) -> None:
        """Voice `text`, check the take with Whisper, re-voice with other seeds while it scores
        below CHECK_MIN_SCORE, and keep the best take in `raw`."""
        style, best = dict(self.style), (-1.0, None, "")
        try:
            for seed in CHECK_SEEDS:
                take = raw.with_name(f"{raw.stem}_s{seed}.wav")
                self.style = dict(style, seed=seed)
                self._synth(text, take)
                heard = transcribe(take)
                sc = 1.0 if heard is None else take_score(text, heard)
                if sc > best[0]:
                    best = (sc, take, heard or "")
                if sc >= CHECK_MIN_SCORE:
                    break
                log.info("voice retake (score %.2f, heard %r): %s", sc, heard, text[:60])
        finally:
            self.style = style
        sc, take, heard = best
        if sc < CHECK_MIN_SCORE:
            log.warning("voice check: best take scored %.2f for %r (heard %r)", sc, text, heard)
        take.replace(raw)
        for f in raw.parent.glob(f"{raw.stem}_s*.wav"):
            f.unlink()

    def say(self, text: "str | list[str]", whoosh: bool = True, pause: float = 0.35) -> tuple[Path, float]:
        """Narration file (whoosh, then the voice at VOICE_START) and its length in
        seconds. A list, or a string with several sentences, is spoken sentence by
        sentence with `pause` seconds of silence between them."""
        parts = text if isinstance(text, list) else [t for t in re.split(r"(?<=[!?।.])\s+", text.strip()) if t]
        checked = self.engine == "chatterbox-server"
        if checked:
            # One request per few sentences, like `hindi_tts.py say "a। b।"`: the model then paces and
            # pauses between sentences itself, which sounds far more natural than gluing separate takes.
            table = load_pronunciations()
            parts = chunk_sentences([respell(p, table) for p in parts])
        whoosh = whoosh and self.cues
        key = hashlib.sha1(f"{self.tag}|{parts}|{whoosh}|{pause}{'|v3' if checked else ''}".encode()).hexdigest()[:12]
        out = self.dir / f"vo_{key}.wav"
        if not out.exists():
            raws = []
            for i, part in enumerate(parts):
                raw = self.dir / f"raw_{key}_{i}.wav"
                if checked:
                    self._synth_checked(part, raw)
                else:
                    self._synth(part, raw)
                raws.append(raw)
            delay = int(VOICE_START * 1000) if whoosh else 0
            ins = [a for r in raws for a in ("-i", str(r))] + (["-i", str(self.whoosh)] if whoosh else [])
            # Trim the silence the model leaves at both ends so the pause between takes is about `pause`
            # (Chatterbox: soft edges that keep the words' onset and decay).
            trim = SOFT_TRIM if checked else (
                "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                "silenceremove=start_periods=1:start_threshold=-45dB,areverse,")
            gap = max(pause - 0.25, 0.08) if checked else pause  # soft edges already keep ~0.25 s of tail
            shape = ""
            if self.tempo != 1.0 or self.pitch:
                shape = f"rubberband=tempo={self.tempo}:pitch={2 ** (self.pitch / 12):.5f}:formant=preserved,"
            chain = "".join(f"[{i}:a]{trim}{shape}aresample=44100,aformat=channel_layouts=stereo,"
                            f"apad=pad_dur={(gap if i < len(raws) - 1 else 0):.2f}[p{i}];" for i in range(len(raws)))
            chain += "".join(f"[p{i}]" for i in range(len(raws))) + f"concat=n={len(raws)}:v=0:a=1,"
            chain += f"loudnorm=I=-14:TP=-1.5:LRA=7,adelay={delay}|{delay}[v]"
            # `[vb]` is the voice alone (no whoosh); captions are timed from it.
            graph = chain.replace("[v]", "[v0];[v0]asplit[v][vb]") + (
                f";[v][{len(raws)}:a]amix=inputs=2:duration=longest:normalize=0[a]" if whoosh else ";[v]anull[a]")
            run_ffmpeg([*ins, "-filter_complex", graph, "-map", "[a]", "-ar", "44100", str(out),
                        "-map", "[vb]", "-ar", "44100", str(voice_only(out))])
            for r in raws:
                r.unlink()
            log.debug("voiceover %s: %s", out.name, parts)
        return out, probe(out)["duration"]


def voice_only(path: Path) -> Path:
    """The voice-only copy of a narration file made by Narrator.say (no whoosh)."""
    return path.with_name(path.stem + "_voice.wav")


def chunk_sentences(sentences: list[str], limit: int = 280) -> list[str]:
    """Join sentences into requests of up to `limit` characters (the Chatterbox server cuts at 300)."""
    chunks: list[str] = []
    for sent in sentences:
        if chunks and len(chunks[-1]) + 1 + len(sent) <= limit:
            chunks[-1] += " " + sent
        else:
            chunks.append(sent)
    return chunks


def join_audio(parts: list[Path], out: Path, gap: float = 0.3) -> tuple[Path, float]:
    """Play narration files one after another with `gap` seconds between them."""
    if not out.exists():
        ins = [a for p in parts for a in ("-i", str(p))]
        chain = "".join(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,apad=pad_dur={gap if i < len(parts) - 1 else 0}[j{i}];"
                        for i in range(len(parts)))
        chain += "".join(f"[j{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[a]"
        run_ffmpeg([*ins, "-filter_complex", chain, "-map", "[a]", "-ar", "44100", str(out)])
    return out, probe(out)["duration"]


class ServerProcess:
    """Starts the Chatterbox Hindi server for the length of a batch run, if it isn't already up,
    with `run_server.ps1 --port N` from its folder, and stops it again afterwards. Callers talk to it
    over HTTP exactly as any other client does: POST {url}/tts with {"text", ...} -> WAV bytes."""

    def __init__(self, url: str, folder: str, log_file: Path, wait_seconds: int = 900):
        self.url, self.folder, self.log_file, self.wait = url.rstrip("/"), Path(folder), log_file, wait_seconds
        self.proc = None

    def _health(self) -> str | None:
        import requests

        try:
            return requests.get(f"{self.url}/health", timeout=5).json().get("status")
        except Exception:  # noqa: BLE001
            return None

    def start(self) -> "ServerProcess":
        import subprocess
        import time
        from urllib.parse import urlparse

        if self._health():
            log.info("Chatterbox server already running at %s", self.url)
            return self
        script = self.folder / "run_server.ps1"
        if not script.is_file():
            raise FileNotFoundError(f"Chatterbox server script not found: {script}")
        port = urlparse(self.url).port or 8765
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_file, "wb")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "--port", str(port)],
            cwd=str(self.folder), stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
        log.info("Starting Chatterbox server on port %s (log: %s) ...", port, self.log_file)
        end = time.time() + self.wait
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Chatterbox server exited with code {self.proc.returncode}; see {self.log_file}")
            st = self._health()
            if st == "ok":
                log.info("Chatterbox server ready")
                return self
            if st == "failed":
                self.stop()
                raise RuntimeError(f"Chatterbox server failed to load its model; see {self.log_file}")
            time.sleep(5)
        self.stop()
        raise RuntimeError(f"Chatterbox server not ready after {self.wait}s; see {self.log_file}")

    def stop(self) -> None:
        import subprocess

        if self.proc and self.proc.poll() is None:
            # taskkill /T also ends the python child that powershell started
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            log.info("Chatterbox server stopped")
        self.proc = None
