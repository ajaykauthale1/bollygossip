# Scandal long video

A separate pipeline next to the daily shorts. You write a Hindi script about a scandal or
viral story; it voices it with the same Chatterbox voice as the shorts, fetches YouTube clips
for each part, adds podcast music, sound effects, transitions and screenshots, and stitches a
1920x1080 landscape video (up to 20 min).

Nothing in the daily pipeline (`..\pipeline.py`, `..\config.json`, `..\run_daily.bat`, the
9 PM task) is used or changed; this folder only imports `..\viralbolly` read-only.

## Run

1. Write a script (copy `scripts\PODCAST_TEMPLATE.txt`) and save it in `scripts\`.
2. Optional: put screenshots in `screens\` (see Screenshots).
3. Run from this folder:

       run_scandal.bat scripts\my_story.txt

4. The video is `output\my_story_<date-time>\my_story.mp4`, with `timeline.txt` (which clip
   plays when) and `credits.txt` (every source with its link). Use `--out output\name` to pick
   the folder.

Time on this PC: voicing ~2-3 min per part (CPU), clips ~2-4 min per part, so a 7-8 min video
takes about 1-1.5 h. Voiced sentences, downloaded clips and clip hints are cached, so after
editing a script only the changed sentences are re-voiced and a re-render takes ~20-30 min.

The Chatterbox server (`C:\Users\Ajay\Downloads\chatterbox-hindi`, port 9000) is started
automatically and stopped after voicing.

### Options

| flag | what it does |
| --- | --- |
| `--out output\name` | output folder |
| `--factcheck-only` | Claude Code fact-checks the script on the web, writes `factcheck.md`, no video |
| `--factcheck` | same report, then makes the video |
| `--no-claude` | don't ask Claude Code for clip hints |
| `--voice-only` | only voice the script (fills the cache) |
| `--music <file or link>` | use this background track instead of the cues/config |
| `--no-music` | no background music |
| `--vertical` | vertical 1080x1920 video for Shorts/Reels (landscape clips sit as a band over a blurred copy, title lower, screenshots narrower). Default stays 1920x1080 |

## Script format (podcast style)

Full template with comments: `scripts\PODCAST_TEMPLATE.txt`. Example scripts:
`scripts\govinda_controversy.txt`, `scripts\kartik_triptii_fallout.txt`.

    title: Short English title (shown top-left for 6 s)
    topic: Main names + story in English (used when a part has no [clip:] line)
    music: auto

    [SFX: record scratch, music stops]
    [MUSIC: mysterious, intense]
    [clip: Kartik Aaryan Triptii Dimri]
    हिंदी लाइन एक।
    हिंदी लाइन दो।

    [MUSIC: gossip, investigative, fast]
    [clip: english search words] [clip: second search]
    [screen: reddit1.png | Reddit r/BollyBlindsNGossip]
    [play: 8]
    अगले हिस्से की लाइनें।

Rules:
- Header lines may start with `#` or not (`# title:` = `title:`).
- **Parts:** consecutive text lines form one part; a part ends at a `[MUSIC]`/`[SFX]`/`[clip]`/
  `[screen]` line or a blank line (or after 6 lines). Each part gets its own clips and, after
  the voice, a few seconds of a clip with its own sound.
- `[MUSIC: ...]` changes the background music with a crossfade and a white-flash transition.
  Moods: retro / 90s dance, mysterious / intense / suspense, nostalgic / light / emotional,
  dark / dramatic, gossip / investigative / fast, calm / ending, outro / fade out.
- `[SFX: ...]` plays a sound and the voice waits for it: record scratch, whoosh, camera shutter,
  thud / impact, tape click / mic drop. "music stops" / "रुक जाता है" cuts the music (with a dip
  to black). Headlines in "quotes" inside a reporters cue become overlapping TV-news voices.
  A "dance music starts" SFX at the very start plays the retro track loud as an intro.
- `[clip: english words]` YouTube search for that part (several allowed). No hint: Claude Code
  writes searches; if that's unavailable, the topic is searched.
- `[clip: yt:VIDEOID@40-90]` uses only seconds 40-90 of that video (skip anchors, ads, memes); `@40` = from 40 s on.
- `[clip: yt:VIDEOID]` (or a YouTube link) uses exactly that video, e.g. the official song or
  trailer. Hand-picked, so the blocklist and the resolution limit don't apply; official
  label/studio videos will likely get a Content ID claim, so keep them to short snippets.
- `[screen: file | credit]` shows a screenshot from `screens\` at the start of the part.
- `[play: N]` seconds of the clip's own sound after the part (`[play: 0]` = none; default 5).
  Pin a sound bite with its own span no longer than N + 1 s (e.g. `[clip: yt:ID@2233.5-2245]` with
  `[play: 11.5]`) as the clip that plays with sound (the part's clip number (part-1) mod clips); it is
  then kept out of the shots under the voice, so its footage isn't shown twice.
- Lines like `VOICEOVER (HOST):`, `Format:`, `Tone:`, `Language:` are ignored; `#` lines are notes.
- Write years/numbers as Hindi words (दो हज़ार पच्चीस) if the voice reads digits badly.
- Use exact, verified dates; only quote people when a news report has the quote.

An older plain format also works: `[clip: ...]` lines and voiceover lines, parts separated by
blank lines (`scripts\TEMPLATE.txt`).

## Screenshots (Reddit posts, tweets, articles)

Reddit blocks this PC (403), so screenshots are taken by hand on the phone.
1. Save the image in `screens\` (PNG/JPG). Crop away the phone status bar, browser bar and
   ads, and blur usernames (ffmpeg `crop` + `boxblur` works; see the chat history or ask Claude).
2. Add `[screen: name.png | Reddit r/SubName]` above the part's text.
It is shown big (1300 px wide), scrolling top to bottom for `screen_seconds` (12 s), over a
blurred background with "Source: ..." bottom-right.

## Clips

- YouTube search via yt-dlp; landscape (16:9) clips first; vertical or letterboxed clips are
  shown full height over a blurred, zoomed copy of themselves (black bars are cropped first).
- Skipped: studios, music labels, streamers, TV entertainment channels, paparazzi agencies
  (`..\viralbolly\sources.py`) plus `block_uploaders` in `config.json` (film/song re-upload
  channels such as Music Remastered, Dream Cinema, AA Films, SuperSTARkaJALWA, Eros Zooming...).
  Short trailer footage is OK. News media (Pinkvilla, Bollywood Hungama, NDTV, zoom...) are
  allowed and credited. With `end_credits: true` (default) all sources (clips and screenshots)
  are listed on one still "Video credits" page at the end (7 s, centred columns, font shrinks to
  fit, never scrolls); with `false` every shot shows "Video: <channel>" bottom-right.
- Searched clips below 720p (`min_clip_long_side`, 1280 px) are skipped.
- Each part is cut into ~6 s shots alternating between up to 3 clips, skipping the first ~12 s
  of each clip (channel intros) and never repeating the same seconds.
- After each part, ~5 s of the clip's loudest moment plays with its own sound.

## Sound

- Voice: Chatterbox Hindi, `exact_1_polished.wav`, exaggeration 0.6, cfg 0.35, temperature 1.0,
  one request per sentence, then a studio chain (de-hiss, warmth, presence, de-ess, compression).
- Music: plays clearly under the voice and dips when a clip's own sound comes up.
  Cue tracks: `music\cues\<mood>.mp3` (retro, mysterious, nostalgic, dark, gossip, ending), all
  Pixabay tracks without Content ID; replace a file to change that mood.
  Without cues: `config.json` "music" (default `music\podcast\podcast_paulyudin.mp3`, which IS
  Content ID registered on Pixabay), or a script's `music: <file or link>` line.
- Sound effects: `sfx\*.mp3` (Pixabay). Sources listed in `music\cues\SOURCES.txt`, `sfx\SOURCES.txt`.
- Audio is mixed in Python (numpy): ffmpeg multi-input mixes freeze at random on this PC.

## Thumbnails

`thumbnail.py` makes 1280x720 YouTube thumbnails: cut-out people with a white outline and
coloured glow over a blurred event backdrop, light rays, a glowing crack, a 3D gradient Hindi
headline, a red ribbon line, a round sticker badge, an emoji, optionally a tilted screenshot inset.

1. Grab face frames from cached clips:
   `ffmpeg -ss <sec> -i cache\clips\<id>.mp4 -frames:v 1 thumbs
ame.png`
2. Cut the person out (separate venv `.venv_thumb` with rembg, so the main venv is untouched):
   `.venv_thumb\Scripts\python.exe thumbs\cutout.py thumbs
ame.png thumbs\cut_name.png x0 y0 x1 y1`
   (crop away channel logos with x0 y0 x1 y1; if the cut is bad try `set REMBG_MODEL=u2net_human_seg`).
3. Describe the thumbnails in `thumbs	humbs.json` (fields in thumbnail.py's docstring), then
   `..\.venv\Scripts\python.exe thumbnail.py thumbs	humbs.json` -> `output	humbnails\*.jpg`.
Make an A and a B per video; people should face the centre (`"flip": true`); keep the headline
2-3 words, the ribbon one short line, accurate and free of accusations.

## Motion graphics (last step)

`motion.py` adds animated graphics to a finished video: a chapter lower-third at the start of
each part, date/stat chips top-right, a progress bar with chapter ticks, and a pulsing
"comment below" card over the last part. Labels go in `motion\<name>.json` (English only;
see the docstring); times come from `timeline.txt`. Pillow is in `.venv_thumb`:

    .venv_thumb\Scripts\python.exe motion.py output\x\x.mp4 motion\x.json --out output\x\x_motion.mp4

Takes ~1 min per 1.5 min of video. Keep labels factual (no accusations).

`motion2.py` is the punchier version (same command, works on vertical videos too): whip
transitions with a zoom kick at each part, kinetic chapter lower-thirds with a spinning number
badge, split-pill date chips, kinetic text (`"kinetic"` entries, `"style": "quote"` for quotes),
a progress bar with a glowing head and a VS verdict card (`"verdict"`). Spec format in its
docstring; example: `motion\sanjay_dutt_peace_long.json`.

## Claude Code (Pro login, no API key)

`claude_helper.py` runs `claude -p` headless with the Pro subscription (no API key, no extra
bill; uses the Pro usage allowance):
- clip search hints for parts without `[clip:]` (Haiku, cached in `cache\hints.json`);
- `--factcheck` / `--factcheck-only` web fact-check report.
Setup once: open PowerShell, run `claude`, type `/login`, sign in, then check `claude -p "hi"`.
If it is logged out or over the limit, the run continues without these steps (warning in log).

## Settings (`config.json`)

| key | meaning |
| --- | --- |
| `tts_style`, `voice_sample`, `tts_url`, `tts_folder` | Chatterbox voice |
| `voice_mode` | `sentence` (default) or `segment` |
| `voice_clarity` | studio polish on the voice |
| `music`, `music_volume`, `clip_volume` | default music and levels |
| `intro_music_volume`, `intro_music_seconds` | the retro intro before the first voice |
| `clip_moment_seconds` | clip's own sound after each part (5) |
| `shot_seconds`, `clips_per_segment` | shot length, clips per part |
| `screen_seconds` | how long a screenshot shows (12) |
| `segment_gap`, `line_pause` | pauses |
| `block_uploaders` | extra channel names / title words to skip |
| `claude_hints` | ask Claude Code for clip searches |
| `keep_work` | keep per-shot files in `work\` |
| `end_credits` | `true`: one "Video credits" card at the end instead of a label on every shot |
| `min_clip_long_side` | skip searched clips smaller than this (1280 = 720p) |

## Files

| path | what |
| --- | --- |
| `scandal.py` | the pipeline |
| `cues.py` | `[MUSIC]` / `[SFX]` keyword map |
| `claude_helper.py` | Claude Code hints and fact-check |
| `thumbnail.py`, `thumbs\` | thumbnail maker and its specs/face frames |
| `run_scandal.bat` | entry point |
| `scripts\` | scripts (`PODCAST_TEMPLATE.txt`, `*.original.txt` = before fact-check edits) |
| `screens\`, `music\`, `sfx\` | your screenshots, music, sound effects |
| `cache\` | voice, clips, hints (safe to delete; makes the next run slower) |
| `output\` | finished videos |
| `CLAUDE.md` | instructions for Claude when working in this folder |

## Voice checks and pronunciations (since 6 Oct 2026)

Ajay heard English names read the Hindi way (Bom Diggy) and the closing line ("अपनी दुआएं ... कमेंट्स में")
sound broken in every video. Causes and fixes (`tts_check.py`):
- Chatterbox misreads some words (दुआएं, कमेंट्स, English titles) and, with the server's fixed seed,
  made the same bad take every time. Now each sentence is transcribed with Whisper (faster-whisper
  "small", CPU) and re-voiced with other seeds when it scores below `voice_min_score` (0.9), up to
  `voice_retries` (3); the best take is kept and logged if still low.
- `pronunciations.json` rewrites the spoken text only (e.g. बॉम डिग्गी डिग्गी -> बम डिगी डिगी,
  कमेंट्स -> कमेंट, दुआएं -> दुआ). The run log warns about quoted names ('...') with no entry: voice a
  few spellings, check them, and add the one that sounds English.
- Takes were trimmed with a hard silence cut, clipping first sounds and the last word's decay, and
  joined with digital silence. Now 80 ms before and 250 ms after the words are kept with short fades,
  plus `voice_tail` (0.35 s) after each part's last sentence, so the line before the outro ends cleanly.
- The mix limiters used ffmpeg's auto level (pushes back to 0 dBFS), so voice + effects + music
  clipped (scratch at the start, loud last lines). Limiters now run with `level=0`, the mix has 2 dB
  of headroom; the finishing step brings it to -14 LUFS.