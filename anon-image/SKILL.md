---
name: anon-image
description: Free, anonymous AI image generation and editing with no API key and no account. Use when the user asks for an image, a picture, a mockup, a visual asset, a style transfer, an edit ("make it night", "remove the car", "repaint as oil"), or a before/after comparison. Also generates logo/hero/texture candidates and builds labelled contact sheets.
version: 1.2.1
license: skill-wrapper MIT; upstream services are third-party and rate-limited (see reference)
---

# anon-image

Generate and edit images through key-less public endpoints, then compose the
results into sheets and comparisons. Pure Python stdlib + Pillow; no SDK, no key,
no account, no browser.

## When to use

| The user wants… | Do this |
|---|---|
| "make me an image of X" | `imgen.py gen "X" -o out.jpg` |
| a specific aspect ratio / wallpaper / phone shot | add `--ratio 16:9` or `--ratio 9:16` |
| "change this image to Y" / restyle / relight / repaint | `imgen.py edit in.jpg "Y" -o out.jpg` |
| N prompt variants at once | `imgen.py batch prompts.txt --outdir shots/ --parallel 4` |
| a labelled grid of candidates for review | `imgen.py sheet out.jpg a.jpg b.jpg --labels "A|B"` |
| before/after for a diff | `imgen.py compare out.jpg before.jpg after.jpg` |

## Quick start

```bash
imgen.py gen "a glass lighthouse on a basalt cliff at dusk, cinematic" -o lighthouse.jpg --ratio 16:9
imgen.py edit lighthouse.jpg "make it a bright sunny afternoon" -o lighthouse_day.jpg
imgen.py compare cmp.jpg lighthouse.jpg lighthouse_day.jpg
imgen.py sheet sheet.jpg a.jpg b.jpg c.jpg --labels "one|two|three" --cols 3 --title "candidates"
```

All commands print `saved <path> (<size>, <time>)` and exit 0. Add `--json` to
`gen`/`edit` for a machine-readable line: `{"ok":true,"out":…,"bytes":…}`.

## Prompting rules that actually matter

- Write **one scene, concrete nouns, an explicit medium and light**: "a brass
  compass on weathered wood, macro, side light" beats "beautiful compass".
- For edits, say what to change **and what to keep**: "repaint as oil, keep the
  composition and the lighthouse position". Unsaid things drift.
- The model is FLUX-class. It renders text badly; avoid asking for words inside
  the image, or expect to fix them afterwards.
- `--ratio` is honoured at generation time (1248 px on the long edge). Do not
  generate square and crop later unless you must — you lose composition.

## Editing: the rules

- Input must be **≤ 3.5 MB**. Resize first if not:
  `ffmpeg -i big.jpg -vf scale=1536:-2 small.jpg`
- The instruction is applied to the whole frame; it is not a mask/inpaint tool.
  For "remove this object" phrasing, name the object and what replaces its
  background: "remove the parked car, extend the cobblestone street".
- Edits preserve dimensions. Two edits of the same edit compound drift — prefer
  one well-written instruction over three small ones.

## Interfaces

`gen` · `batch` · `edit` · `sheet` · `compare` · `probe`

`probe` returns 0 if the generation endpoint is alive, 2 if it has gone dark —
run it before promising the user a picture.

## Failure modes and what to do

| Symptom | Meaning | Action |
|---|---|---|
| `HTTP 502/504`, or 60 s+ then timeout | upstream is down or overloaded | retry once after ~10 s; if it persists, say so and offer to assemble from existing images |
| `input is 4.2MB … accepts 3.5MB` | oversize upload | resize (command is printed for you) |
| `no imageUrl in response` | upstream returned a shape we don't know | print the raw JSON, do not silently retry in a loop |
| Generation takes 10–20 s | normal | do not lower `--timeout` below 120 |

**Never** hammer it: 10 parallel images is tested-safe and productive, more is
pointless and rude. Two images per user turn, on average, is where this sits.

## Notes for the agent

- Prefer generating **2–3 candidates** and showing a contact sheet over
  generating one and apologising.
- Always save onto a path the user can open; never leave the asset in a temp dir.
- The image has no watermark and no C2PA manifest — but the upstream terms
  restrict commercial use of the *free text-to-image* tier. Editing (image-to-image)
  is the route that stays commercially usable. Check `reference/endpoints.md`
  before telling anyone "you can sell this".
