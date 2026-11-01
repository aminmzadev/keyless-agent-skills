# Agent skills: media generation + web research, no keys, no accounts

Seven self-contained skills for Pi, Claude Code, Codex, Cursor, or any agent that
can run shell commands. Every script is stdlib-first, every network dependency is
key-less, and every one of them degrades instead of dying. Start with
[`GUIDE.md`](GUIDE.md) if you want install + usage in one document.

## Install

```bash
npx skills add aminmzadev/keyless-agent-skills              # all seven, into any agent
npx skills add aminmzadev/keyless-agent-skills --list       # what is inside
npx skills add aminmzadev/keyless-agent-skills --skill web-search -a claude-code --copy
```

npm (name reserved, first publish pending): `keyless-agent-skills` — 84.8 kB packed,
34 files, **no lifecycle scripts**, no binaries, no voice models.

Prefer files? The [v1.2.0 release](https://github.com/aminmzadev/keyless-agent-skills/releases/tag/v1.2.0)
has the bundle zip plus one zip per skill and a `SHA256SUMS`. Or just clone and run
`./install.sh --check` to see what your machine already has.

## Start here

| Skill | Does | Entry point | Hard deps |
|---|---|---|---|
| [`skill-router`](skill-router/SKILL.md) | intent → the right skill and a pasteable command | `scripts/route.py` | python3 |

## Media

| Skill | Does | Entry point | Hard deps |
|---|---|---|---|
| [`anon-image`](anon-image/SKILL.md) | generate / edit images, contact sheets, comparisons | `scripts/imgen.py` | python3 (+ Pillow for sheets) |
| [`anon-audio`](anon-audio/SKILL.md) | text-to-speech, 322 voices, 74 languages, auto-fallback | `scripts/tts.py` | python3 (+ edge-tts online, **piper** offline, ffmpeg) |
| [`ffmpeg-pipeline`](ffmpeg-pipeline/SKILL.md) | stills → video, narration muxing, GIF, sheets, loudness | `scripts/vidkit.py` | ffmpeg |

## Research

| Skill | Does | Entry point | Hard deps |
|---|---|---|---|
| [`web-search`](web-search/SKILL.md) | 17 keyless providers, groups, ranking, query relaxation | `scripts/search.py` | python3 |
| [`fetch-page`](fetch-page/SKILL.md) | URL → markdown, 3-stage fallback, PDFs, cache | `scripts/fetch.py` | python3 (+ poppler/pypdf for local PDFs) |
| [`deep-research`](deep-research/SKILL.md) | search → read → **follow links** → cited report | `scripts/research.py` | python3 |

`web-search` and `fetch-page` are independent: install either alone and it works.
`deep-research` ships its own copies of both scripts so it also installs alone.
The canonical copies live in `deep-research/scripts/`; `./sync.sh` republishes
them into the two standalone skills, so there is exactly one place to edit.

Which one to reach for:

```
not sure at all?        → route.py "what you want to do"
need links?             → web-search.py
have a URL, need text?  → fetch-page.py
need a written answer with citations? → research.py
```

## Install

```bash
./install.sh              # checks + installs ffmpeg, edge-tts, pillow, poppler (all optional)
./install.sh --piper      # + offline TTS: piper-tts and one ~63 MB voice
./install.sh --check      # report only, change nothing
./test-all.sh             # 46 smoke tests, ~2-3 min cold (network)
./test-all.sh --fast      # skip slow network work: 39 passed, 2 skipped
```

Nothing needs to be copied into a config file: each skill is a directory with a
`SKILL.md` (YAML frontmatter + usage) and a `scripts/` folder. Drop the directory
where your agent looks for skills, or call the scripts by path. `MANIFEST.json`
lists all six with entry points and routing hints.

## Composition

`ffmpeg-pipeline` finds `anon-image` and `anon-audio` as siblings automatically,
so one command can go prompt → narrated MP4:

```bash
ffmpeg-pipeline/scripts/vidkit.py video "a glass lighthouse at dusk" -o story.mp4 \
  --shots 3 --narration "Somewhere on a cold coast…"
```

`deep-research` is the same idea for the research trio: search → fetch → cite.

## Principles behind the pack

1. **Degrade, don't die.** Every skill has an ordered fallback chain and returns
   partial results with a warning rather than an exception.
2. **No credentials, ever.** Nothing here asks for a key, an account, or a browser.
3. **Measured, not assumed.** Latencies, limits and failure signatures in
   `reference/` come from tests run on 2026-11-01, not from documentation.
4. **The agent stays in charge.** Scripts return paths and JSON; judgement,
   wording and final checks stay with the model.

## Licence

Skill code: MIT. Upstream services are third parties with their own terms —
read `reference/` before shipping anything commercial.
