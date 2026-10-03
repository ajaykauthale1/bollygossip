"""Claude Code (your Pro login, no API key) for the two jobs that need judgment:

1. clip search hints: English YouTube search phrases for paragraphs without [clip: ...]
2. fact-check: a report of wrong dates, wrong order of events and unsourced quotes

Both run `claude -p` headless. If Claude Code isn't installed, isn't logged in, or the
Pro usage limit is reached, they log a warning and the video is made without them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("scandal.claude")

HERE = Path(__file__).resolve().parent
HINT_CACHE = HERE / "cache" / "hints.json"

HINT_PROMPT = """You pick YouTube search queries for a Hindi Bollywood news video.
Topic: {topic}

For each numbered paragraph of the voiceover below, give 1 or 2 short English YouTube search
queries (3-7 words) that would find real news or event footage matching what the paragraph says:
the people named, the event, the place, the year. Prefer queries that find news reports,
interviews and event footage. Never use a film song or movie title alone.

Reply with only a JSON object, no other text: {{"1": ["query", "query"], "2": ["query"], ...}}

{paragraphs}"""

FACTCHECK_PROMPT = """Fact-check this Hindi script for a Bollywood news video before it is published.
Today's date is {today}. Use web search. Check every date, number, sequence of events and quote
about real people. Do not rewrite the script.

Write the report in English as Markdown with these sections:
## Wrong or doubtful facts
For each: the script line (quoted), what reliable reports say, the fix, and the source links.
## Quotes without a source
Quotes attributed to real people that you could not find in any report.
## Checked and correct
One line per fact you confirmed.

Script:
{script}"""


def claude_exe() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    return str(local) if local.is_file() else None


def run_claude(prompt: str, tools: str = "", model: str | None = None, timeout: int = 600) -> str | None:
    """One headless Claude Code call. Returns the reply text, or None (with a warning) on any failure."""
    exe = claude_exe()
    if not exe:
        log.warning("Claude Code not found; skipping")
        return None
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence", "--tools", tools]
    if tools:
        cmd += ["--allowedTools", *tools.split(",")]
    if model:
        cmd += ["--model", model]
    try:
        import os

        # Run as a fresh, standalone Claude Code: drop variables inherited from a parent Claude session.
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("CLAUDE") or k in ("ANTHROPIC_BASE_URL", "CLAUDECODE"))}
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(HERE), env=env)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        log.warning("Claude Code call failed: %s", e)
        return None
    if data.get("is_error") or not data.get("result"):
        msg = data.get("result") or proc.stderr.strip()[-300:]
        if "authenticate" in str(msg).lower() or "login" in str(msg).lower():
            msg = f"{msg} (open a terminal, run `claude`, then /login with your Pro account)"
        log.warning("Claude Code: %s", msg)
        return None
    return data["result"]


def _key(text: str, topic: str) -> str:
    return hashlib.sha1(f"{topic}|{text}".encode()).hexdigest()[:16]


def add_hints(segments, topic: str, model: str = "haiku") -> int:
    """Fill in `hints` for segments that have none. Answers are cached per paragraph, so
    re-running the same script costs nothing. Returns how many segments got hints."""
    try:
        cache = json.loads(HINT_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    need = [s for s in segments if not s.hints]
    todo = [s for s in need if _key(" ".join(s.lines), topic) not in cache]
    if todo:
        paras = "\n\n".join(f"{i}. {' '.join(s.lines)}" for i, s in enumerate(todo, 1))
        reply = run_claude(HINT_PROMPT.format(topic=topic or "(see the text)", paragraphs=paras), model=model)
        if reply:
            m = re.search(r"\{.*\}", reply, re.S)
            try:
                found = json.loads(m[0]) if m else {}
            except ValueError:
                found = {}
            for i, s in enumerate(todo, 1):
                qs = [q.strip() for q in found.get(str(i), []) if isinstance(q, str) and q.strip()][:2]
                if qs:
                    cache[_key(" ".join(s.lines), topic)] = qs
            HINT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            HINT_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    done = 0
    for s in need:
        qs = cache.get(_key(" ".join(s.lines), topic))
        if qs:
            s.hints = list(qs)
            done += 1
    return done


def factcheck(script_path: Path, out: Path, model: str | None = None) -> Path | None:
    """Web-searched fact-check report for the script, written to `out` (Markdown)."""
    from datetime import date

    text = script_path.read_text(encoding="utf-8-sig")
    reply = run_claude(FACTCHECK_PROMPT.format(today=date.today().isoformat(), script=text),
                       tools="WebSearch,WebFetch", model=model, timeout=1200)
    if not reply:
        return None
    out.write_text(reply, encoding="utf-8")
    return out
