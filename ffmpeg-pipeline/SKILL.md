---
name: ffmpeg-pipeline
description: Turn stills into video and do media surgery from the shell — Ken Burns motion, crossfades, narration muxing, contact sheets, frame extraction, GIFs, loudness normalisation and speed changes. Use when the user wants a video, a slideshow, a preview clip, a contact sheet of a video, or needs audio and video combined, trimmed or normalised.
version: 1.2.0
license: MIT
---

# ffmpeg-pipeline

The glue skill. It turns generated (or supplied) stills into a moving MP4 with
narration, and does the mundane media jobs an agent hits constantly.

Requires `ffmpeg` + `ffprobe`. Needs `anon-image` for prompt mode and
`anon-audio` for `--narration`; both are auto-discovered as siblings, and both
are optional if you pass `--images` / `--audio` instead.

## Commands

| Command | What it does |
|---|---|
| `video` | stills (generated or from `--images`) → MP4 with zoompan motion + crossfades, optional narration and music bed |
| `slideshow` | existing images → MP4 |
| `mux` | attach an audio track to a video, looping/padding to fit |
| `frames` | extract stills every N seconds |
| `sheet` | contact sheet (N evenly spaced frames with timestamps) |
| `gif` | cropped, palette-optimised GIF from any section |
| `normalize` | loudness-normalise audio to −16 LUFS |
| `speed` | change audio speed without pitch shift (chained atempo) |
| `info` | one-line ffprobe summary |
| `probe` | dependency check |

## Quick start

```bash
# the whole pipeline in one call: 3 generated shots, 4 s each, narrated
vidkit.py video "a lighthouse on a basalt cliff at dusk, cinematic" -o story.mp4 \
  --shots 3 --seconds 4 --ratio 16:9 \
  --narration "Somewhere on a cold coast, a lighthouse keeps its watch." \
  --voice en-GB-RyanNeural

# reuse pictures you already have
vidkit.py video --images shots/ -o out.mp4 --seconds 3 --music bed.mp3

# media surgery
vidkit.py mux clip.mp4 voiceover.mp3 -o final.mp4
vidkit.py sheet final.mp4 -o preview.jpg --cols 4 --count 8
vidkit.py gif final.mp4 -o teaser.gif --start 1 --dur 2.5 --width 480
vidkit.py normalize voiceover.mp3 -o clean.mp3
vidkit.py info final.mp4
```

## Timing maths (so you can predict the output)

With `N` shots of `S` seconds and a 0.8 s crossfade:

```
duration = N*S - (N-1)*0.8
```

3 × 4 s → 10.4 s. If narration is **longer** than the picture, the video loops
to cover it (`-stream_loop`). If it is **shorter**, the audio is padded with
silence so you never lose the ending. Both cases are handled — do not add
`-shortest` yourself.

## Decision rules

- **Ratio**: `16:9` for YouTube/web, `9:16` for Shorts/Reels/TikTok, `1:1` for
  feeds and thumbnails. Everything is scaled-to-cover then cropped, so source
  images of any shape survive without letterboxing.
- **Seconds per shot**: 3–4 s is right for a still; longer than 6 s of a static
  image feels dead. If you need more runtime, add shots, not seconds.
- **Music bed**: pass `--music` and it is mixed at 12 % under the voice. Do not
  mix two loud sources; normalise first.
- **GIF**: keep width ≤ 480 and duration ≤ 3 s or the file exceeds chat limits.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `missing dependency: ffmpeg` | not installed | `apt install ffmpeg` (or `brew install ffmpeg`) |
| `ffmpeg failed: … Invalid argument` on zoompan | odd source dimensions | the filter already scales to `W*2`; if you edited it, keep even numbers |
| output is silent | narration file missing/empty | check the path; `--narration` accepts either text or an audio path |
| output much shorter than expected | audio shorter than video and something added `-shortest` | remove it; `vidkit.py` pads instead |
| `contact sheet needs Pillow` | no Pillow | `pip install pillow`, or use `frames` + your own sheet |

## Notes for the agent

- Render at 720p unless the user asks for more. It is 4× faster and nobody can
  tell on a laptop screen.
- Always run `info` on the final artefact and quote the real duration, resolution
  and size back to the user — it catches silent failures.
- Deliver the MP4 path; do not embed it. The file is the artefact.
