---
name: anon-audio
description: Free text-to-speech and audio assembly with no API key and no account. Microsoft Edge neural voices, automatic fallback to Google Translate TTS, and a fully offline Piper engine for air-gapped or network-dead machines. Use for narration, voiceovers, podcast intros, audiobook chapters, accessibility audio, multilingual samples (including Persian), or whenever an agent needs speech from text.
version: 1.2.0
license: skill-wrapper MIT; upstream endpoints are consumer services (see reference/rate-limits.md)
---

# anon-audio

Three engines, one interface, automatic fallback:

| Engine | Voices | Languages | Needs | Network | Quality |
|---|---|---|---|---|---|
| **edge** (default) | 322 neural | 74, incl. `fa-IR`, `pl-PL` | `pip install edge-tts` | yes | best |
| **gtts** (fallback) | 1 per language | ~40, **no Persian** | nothing (pure stdlib) | yes | flat but fine |
| **piper** (offline) | 1 per downloaded voice | per voice file | `pip install piper-tts` + one ~63 MB voice | **no** | good, local neural |

`--engine auto` (default) tries Edge, and if it fails for *any* reason — not
installed, 403 endpoint drift, 429 throttle, bad voice name — it silently
retries with Google, and finally with Piper if a voice is installed on disk.
Exit code stays 0 as long as some engine produced audio.

## Quick start

```bash
tts.py say "Welcome back to the show." -o intro.mp3
tts.py say --text-file script.txt -o chapter1.mp3 --voice en-GB-RyanNeural --rate -8%
# Persian sample (only the edge engine speaks it) — "Hello, this is a test."
tts.py say "سلام، این یک آزمایش است." -o fa.mp3 --voice fa-IR-DilaraNeural
tts.py say "No dependencies needed." -o plain.mp3 --engine gtts --lang en
tts.py voices --lang fa
tts.py concat part1.mp3 part2.mp3 -o full.mp3
```

## Offline mode (Piper)

Two commands and you never need the network again for speech:

```bash
tts.py fetch-voice en_US-amy-low      # once: ~63 MB into ~/.cache/agent-skills/piper
tts.py say "Runs with the network unplugged." -o out.mp3 --engine piper
tts.py say "same thing" -o out.mp3 --engine offline    # alias, piper only, no fallback
tts.py voices --offline
```

Measured on this box (CPU only, no GPU): **4.2 s of speech in 2.7 s wall**, one
voice, ~26 KB MP3. First call includes ONNX model load (~1 s); a long script
amortises it.

Notes that matter:

- The voice lives in `~/.cache/agent-skills/piper/` (override with `PIPER_DATA_DIR`),
  **not** in the skill directory — models are big and this keeps the zip at 8 KB.
- `--engine offline` is the one to use in tests and in air-gapped jobs: it will
  never reach for the network, so a failure is a real failure.
- Output is WAV if `ffmpeg` is absent, MP3 when it is present. The path printed is
  the file that actually exists.
- Piper voices are per-language files: `en_US-amy-low`, `en_US-ryan-high`,
  `de_DE-thorsten-medium` and ~900 others. `python3 -m piper.download_voices` with
  no arguments prints the catalogue.

## Choosing a voice

- Default to `en-US-AriaNeural` (female, US) or `en-GB-RyanNeural` (male, UK)
  for narration; `-10%` rate reads noticeably better for long-form.
- Persian: `fa-IR-DilaraNeural` (f) / `fa-IR-FaridNeural` (m) — **Edge only**;
  Google returns HTTP 400 for `fa` and the fallback will fail loudly there.
- `voices --lang xx` lists everything matching a language tag.

## Hard limits you must respect

- **Google: 200 characters per request, exactly.** The script splits at 190 on
  sentence boundaries and concatenates with ffmpeg. Do not bypass this.
- **Google: never send a `Referer` header** — it answers HTTP 404. The script
  doesn't, on purpose. If you re-implement it yourself, remember this.
- **Edge: no character cap** (20,000 chars tested in one call), but very large
  chunked jobs eventually get 429. Keep text chunks ≤ 2,000 chars.
- Long text + `--engine gtts` needs `ffmpeg` for concatenation; without it the
  script byte-concatenates (works, slightly less clean).

## Failure handling

| Symptom | Cause | Action |
|---|---|---|
| `edge: 403 Invalid response status` | Microsoft moved the endpoint; `edge-tts` too old | `pip install -U "edge-tts>=7.3.1"`, then retry |
| `edge: 429` | too many chunks at once | already falls back; otherwise split the text |
| `gtts 400` | language has no voice (fa, vi…) | switch to `--engine edge` |
| `gtts 404` | a Referer sneaked in | remove the header |
| `piper not installed` | package missing | `pip install piper-tts`, or `./install.sh --piper` |
| `no Piper voice in ~/.cache/agent-skills/piper` | package there, voice not | `tts.py fetch-voice en_US-amy-low` |
| piper output is `.wav`, not `.mp3` | no ffmpeg | install ffmpeg, or accept WAV |
| no sound but a file was written | engine returned an error page | check size: < 2 KB means failure; re-run with `--json` |

## Pairing with video

`ffmpeg-pipeline/scripts/vidkit.py video … --narration "text"` calls this skill
automatically (it looks for `../anon-audio/scripts/tts.py`). Normalise loudness
before muxing: `vidkit.py normalize voice.mp3 -o clean.mp3` (−16 LUFS).

## Notes for the agent

- Read the text you're about to synthesise **before** synthesising it: strip
  markdown, code fences, URLs and citation markers. TTS reads `**bold**` as
  "asterisk asterisk bold".
- Numbers, dates and initialisms are pronounced inconsistently; if the deliverable
  is user-facing, spell them out ("twenty twenty-six", "A P I").
- These are consumer endpoints, not a contract. Cache generated audio to disk and
  reuse it; it is polite and it makes you faster.
