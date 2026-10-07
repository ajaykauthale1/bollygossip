# ViralBolly: daily r/BollyBlindsNGossip compilation

Once a day this pipeline picks the most viral posts from the last 24 hours of
r/BollyBlindsNGossip and turns them into one MP4. No Reddit account or API keys needed:
a real Chromium browser reads the same public pages a logged-out visitor sees.

1. **Intro card** with the date.
2. **Video posts**: the clip is downloaded with yt-dlp and plays as is, fitted onto a
   blurred background, with the post title at the top and the poster's `u/handle` credited on screen.
3. **Text / image posts**: a screenshot of the actual thread (post plus its top comments,
   with page chrome, sidebars and replies stripped), framed with the compilation branding,
   shown long enough to read, scrolling if it's taller than the frame, with your music underneath.
   If a thread page can't be captured, a branded card drawn from the post data is used instead.

Output: `output/<YYYY-MM-DD>/compilation.mp4`, plus `manifest.json` (what was used and how),
the screenshots and the raw clips.

## How "viral" is measured

Reddit doesn't show view counts publicly, so posts are ranked on upvotes and
comments, normalised by age so a post that's climbing fast can beat an older one:

```
virality = (upvotes + 2 × comments) × (0.5 + upvote_ratio / 2) / (age_hours + 2) ^ 0.8
```

Candidates come from the subreddit's public *top (day)*, *hot* and *rising* listings
(`reddit.com/r/<sub>/top.json` etc.). Stickied, removed and NSFW posts (unless
`--include-nsfw`), posts under `--min-score` and posts already used on an earlier day are
skipped. Tweak `virality_score()` in `viralbolly/fetch.py` to change the weighting.

## Setup

Needs Python 3.9+ and ffmpeg.

**Windows (PowerShell):**

```powershell
cd viralbolly-pipeline
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy config.example.json config.json   # then edit config.json
```

**macOS / Linux:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json
```

### Settings: config.json

`config.json` (next to `pipeline.py`) holds your defaults, so a daily run needs no flags.
Any command-line option can go in it, using the long name without `--`; flags you pass
on the command line win. Backslashes in Windows paths must be doubled in JSON:

```json
{
  "ffmpeg": "C:\\ffmpeg\\bin",
  "music": "music\\background.mp3",
  "top": 10
}
```

**ffmpeg** is looked up in this order: `--ffmpeg` / `"ffmpeg"` in config.json (the
executable or its folder), the `FFMPEG_PATH` environment variable, `PATH`, then
`C:\ffmpeg\bin`. The first line of each run's log says which one is used.

Music: put a royalty-free track at `music/background.mp3` (YouTube Audio Library and
Pixabay Music are good sources) or point `"music"` at any file. It plays continuously
across the text posts and loops if needed. Video clips keep their own audio.

## Run

```bash
python pipeline.py            # uses config.json
python pipeline.py --headed   # same, but watch the browser
```

A run makes about 3 + (number of text posts) × 2 requests to Reddit, spaced at least
`--delay` seconds apart, so 10 posts take a few minutes. Add `--headed` to watch the browser work.

Useful options (`python pipeline.py -h` for all):

| Option | Default | What it does |
|---|---|---|
| `--top` | 10 | posts in the compilation |
| `--comments` | 3 | top comments shown with each text post |
| `--style` | screenshot | `card` draws a branded card from the post data instead of a screenshot |
| `--size` | 1080x1920 | vertical for Shorts/Reels; use `1920x1080` for landscape YouTube |
| `--max-clip-seconds` | 60 | longer videos are trimmed |
| `--min-score` | 20 | ignore low-engagement posts |
| `--hours` | 24 | lookback window |
| `--delay` | 3 | minimum seconds between requests to Reddit |
| `--headed` | off | show the browser window |
| `--allow-repeats` | off | allow posts used on earlier days |
| `--posts-json FILE` | | skip reading listings and use saved posts (a previous `manifest.json` works) |

### Daily schedule

**Windows:** Task Scheduler > Create Basic Task > Daily > pick a time > Start a program >
browse to `run_daily.bat` in this folder. It activates `.venv` if present, reads
`config.json`, and appends to `output\run.log`. The computer has to be on (and logged in,
unless you tick "Run whether user is logged on or not") at that time.

**macOS / Linux:** `run_daily.sh` from cron:

```cron
0 16 * * * /path/to/viralbolly-pipeline/run_daily.sh >> /path/to/viralbolly-pipeline/output/cron.log 2>&1
```

### Offline test run

Needs bash (Git Bash on Windows) for the helper that makes dummy clips:

```bash
bash samples/make_test_media.sh
python pipeline.py --posts-json samples/sample_posts.json --style card --music samples/test_music.mp3 --out test-output
```

## Behaviour details

- Only public, logged-out content is used. If Reddit rate-limits a request (HTTP 429) the
  pipeline waits as long as Reddit asks; if Reddit refuses access (403, block page) the run
  stops with a message rather than trying to get around it. Just try again later.
- Thread screenshots come from `old.reddit.com`, which renders a clean, static page that is
  easy to trim to the post and top comments. The trimming is in `CLEAN_THREAD_JS` in
  `viralbolly/render.py` if you want to keep more or less of the page.
- If a video link can't be downloaded (deleted, private, an Instagram photo, etc.) the
  post falls back to a screenshot instead of being dropped.
- If one post fails, it's logged and skipped; the rest of the compilation still builds.
- `output/used_posts.json` remembers which posts have been featured.
- `CHROMIUM_EXECUTABLE=/path/to/chrome` makes Playwright use an existing Chrome/Chromium.

## Copyright and Reddit terms

- Videos posted on the sub are usually clips from TV, films, paparazzi accounts or
  Instagram. Re-uploading them can draw copyright claims or strikes on YouTube or
  Instagram even with credit; crediting the Reddit poster doesn't transfer rights.
  Consider trimming clips short and adding your own commentary.
- Reddit's User Agreement restricts automated collection and commercial reuse of content
  without permission, and posts deleted on Reddit are expected to disappear from copies too.
  Keep the request rate low (the defaults do) and re-check the terms before monetising.
- Text cards show usernames and comments; blind items can be defamatory, so review the
  compilation before publishing.

## Output formats (shorts + long)

Each run now produces, in `output/<date>/`:

- `short_1.mp4`, `short_2.mp4`: vertical 1080x1920, up to 2 minutes each, hottest posts first
  (short 1 gets the top posts, short 2 the next ones). Clips are trimmed to 40s.
- `long.mp4`: landscape 1920x1080, up to 20 minutes, every trending post that fits. Clips up to 2 min.

Every video starts with an intro card and ends with a credits card listing each poster and
commenter featured. Video clips carry a "Video credit" badge (with the original site, e.g.
instagram.com, when the clip came from elsewhere); screenshots carry "Posted by u/…" and the
commenters' handles.

Options: `--formats shorts,long`, `--shorts 2`, `--short-seconds 120`, `--long-seconds 1200`,
`--short-size`, `--long-size`, `--short-clip-seconds`, `--long-clip-seconds`, `--top 30`.
All can also go in `config.json`.

## More clips, from individuals rather than companies

- `video_boost` (default 2.5) multiplies the virality of video posts so more clips make the cut.
  Set it to 1 for no preference.
- `subreddit` can list several subs, comma-separated, to widen the pool of viral clips.
- Clips are skipped (the post becomes a screenshot) when yt-dlp reports the uploader is a
  studio, label, streaming service, TV channel or paparazzi agency, or another verified
  channel. Entertainment media (Pinkvilla, Bollywood Hungama, Filmfare, ... see
  `ALLOWED_MEDIA`) are allowed and credited by name: the badge reads "Pinkvilla · on
  instagram.com · shared by u/… on r/…", and the end card lists them first.
  Link posts to studio/streaming sites are dropped entirely.
  The built-in lists live in `viralbolly/sources.py`; add your own in `config.json` via
  `block_uploaders` / `block_domains`. `--allow-verified` lets verified channels through.

This reduces claims but can't rule them out: an individual's upload can still contain
film, TV or paparazzi footage. Watch the clips before publishing.

## Voice fix (6 Oct 2026)
Same fix as scandal_long (see scandal_long\README.md > Voice checks). For the Chatterbox voice, `viralbolly\voice.py` now:
- respells words from the shared list `scandal_long\pronunciations.json` before voicing (e.g. बॉम डिग्गी डिग्गी -> बम डिगी डिगी, कमेंट्स -> कमेंट);
- transcribes each take with Whisper and re-voices it with another seed (up to 3 takes) when it scores below 0.85;
- keeps soft sentence edges (80 ms before, 250 ms after the words, short fades) instead of a hard silence cut.
`viralbolly\stitch.py`: the voice + music mix gets a peak-only limiter (level=0, no auto gain).
Originals: `viralbolly\_backup_2026-10-06\`. Voicing takes longer (about 5 s per take for Whisper, plus any retakes).
