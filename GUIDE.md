# Agent Skills Pack — install and use

Seven skills, no API keys, no accounts, no sign-ups. One zip, one guide, ~4,000 lines
of Python that assume nothing about your machine beyond `python3`.

- **Version:** 1.2.0 · built 2026-11-01
- **Bundle:** `agent-skills-pack-v1.2.0.zip` (self-contained, nothing to download at install time)
- **Works with:** any agent that can run shell commands — pi, Claude Code, Codex, Cursor,
  aider, a cron job, or you at a terminal.

---

## 1. What's in the box

| # | Skill | One line | Script |
|---|---|---|---|
| 1 | `skill-router` | "which skill do I use?" → the answer and a pasteable command | `route.py` |
| 2 | `anon-image` | prompt → image, image + instruction → edit, contact sheets | `imgen.py` |
| 3 | `anon-audio` | text → speech: Edge (online), Google (fallback), **Piper (offline)** | `tts.py` |
| 4 | `ffmpeg-pipeline` | stills → narrated MP4, GIF, captions, loudness, frame sheets | `vidkit.py` |
| 5 | `web-search` | one query → ranked results from 20 keyless sources (17 classic + 3 vendor) | `search.py` |
| 6 | `fetch-page` | one URL → clean markdown, whatever the page throws at you | `fetch.py` |
| 7 | `deep-research` | question → search → read → **follow links** → cited report | `research.py` |

Plus `shared/cachectl.py` (one cache for everything), `install.sh`, `test-all.sh`,
`sync.sh`, `MANIFEST.json` (machine-readable catalogue) and this guide.

---

## 2. Install

### 2.1 Unzip it

```bash
unzip agent-skills-pack-v1.2.0.zip -d ~/agent-skills
cd ~/agent-skills
```

That's it for the minimum path: every skill runs with `python3` alone. There is no
`pip install -e`, no daemon, no config file, no environment variable to set.

### 2.2 Optional dependencies — what each one buys

```bash
./install.sh --check     # what's missing, change nothing
./install.sh             # install ffmpeg, poppler, pillow, edge-tts
./install.sh --piper     # + fully offline speech (piper-tts + a ~63 MB voice)
```

| Dependency | Size | Without it |
|---|---|---|
| `ffmpeg` / `ffprobe` | ~80 MB | no video, no mp3 conversion, no loudness — everything else still works |
| `poppler-utils` (`pdftotext`) | ~5 MB | PDFs need `pypdf`, or go through the reader relay |
| `pillow` | ~10 MB | no contact sheets, no before/after composites |
| `edge-tts` | ~1 MB | speech falls back to Google (worse) or Piper (offline) |
| `piper-tts` + voice | ~70 MB | no offline speech; online engines still work |

None of them is required. Every script detects what it has and says what it lost.

### 2.3 Verify

```bash
./test-all.sh            # 46 smoke tests, ~2-3 min cold (network)
./test-all.sh --fast     # skip slow network work: 39 passed, 2 skipped
```

Everything green means images, audio, video, search, fetch, routing, cache and
research all work on your machine — not just that the code imports.

### 2.4 Teach your agent about it

Each skill is a directory with a `SKILL.md` (YAML frontmatter + usage + failure
table) and a `scripts/` folder. Two options:

**A. Point the agent at the pack.** Tell it: *"skills live in `~/agent-skills`; read
`MANIFEST.json` and route with `skill-router/scripts/route.py`."* This is all the
context most agents need — `MANIFEST.json` carries `use_when`, `not_for` and
`keywords` per skill precisely so routing does not depend on prose.

**B. Copy one skill where the agent looks.** Every skill is independent:

```bash
cp -r ~/agent-skills/web-search ~/.pi/skills/        # whatever your agent's dir is
```

`web-search`, `fetch-page` and `deep-research` each ship their own copies of the
search/fetch code, so any of them works alone. Nothing imports anything outside its
own directory.

### 2.5 Install one skill only

Each archive is self-contained — `web-search-v1.2.0.zip` has no dependency on the
rest of the pack:

```bash
unzip web-search-v1.2.0.zip -d ~/skills && python3 ~/skills/web-search/scripts/search.py "ffmpeg zoompan"
```

---

## 3. Use it — the five-minute tour

### 3.1 Not sure which skill? Ask the router

```bash
python3 skill-router/scripts/route.py "make me a logo for a coffee shop"
# anon-image  (1.88)
#   python3 anon-image/scripts/imgen.py gen "make me a logo for a coffee shop" -o out.jpg

python3 skill-router/scripts/route.py --list          # the whole routing table
python3 skill-router/scripts/route.py "which skill handles PDFs"
python3 skill-router/scripts/route.py "read this page" --json     # for programs
```

It reads `MANIFEST.json` when present and falls back to parsing every `SKILL.md`
frontmatter — so it also routes for *other* people's packs. Below a confidence
threshold it refuses to answer rather than guessing (exit code 1).

### 3.2 Image

```bash
python3 anon-image/scripts/imgen.py gen "a red bicycle against a white wall" -o bike.jpg --ratio 16:9
python3 anon-image/scripts/imgen.py edit bike.jpg "make it night, wet street" -o bike_night.jpg
python3 anon-image/scripts/imgen.py sheet shots.jpg a.jpg b.jpg --labels "before|after"
python3 anon-image/scripts/imgen.py probe          # is the free endpoint alive?
```

9–19 s per image on the free endpoint. Two backends behind one interface; if the
first refuses, the second answers.

### 3.3 Speech — three engines, one flag

```bash
# online, best quality, 322 voices, 74 languages
python3 anon-audio/scripts/tts.py say "Welcome back to the show." -o intro.mp3
python3 anon-audio/scripts/tts.py say --text-file script.txt -o chapter1.mp3 --voice en-GB-RyanNeural --rate -8%

# offline: no network at all, after one download
python3 anon-audio/scripts/tts.py fetch-voice en_US-amy-low     # once, ~63 MB
python3 anon-audio/scripts/tts.py say "Plane mode." -o p.mp3 --engine offline

# pure stdlib, no installs at all
python3 anon-audio/scripts/tts.py say "No dependencies." -o g.mp3 --engine gtts
```

`--engine auto` (default) is Edge → Google → Piper-if-installed. It returns a
playable file or a clear error; never a silent zero-length MP3.

### 3.4 Video

```bash
# prompt -> stills -> narrated 720p MP4, in one command
python3 ffmpeg-pipeline/scripts/vidkit.py video "a glass lighthouse at dusk" -o story.mp4 \
  --shots 3 --seconds 4 --narration "Somewhere on a cold coast, a light keeps turning."

python3 ffmpeg-pipeline/scripts/vidkit.py slideshow a.jpg b.jpg -o s.mp4 --seconds 3 --transition fade
python3 ffmpeg-pipeline/scripts/vidkit.py mux s.mp4 narration.mp3 -o final.mp4
python3 ffmpeg-pipeline/scripts/vidkit.py gif final.mp4 -o preview.gif --width 480
python3 ffmpeg-pipeline/scripts/vidkit.py info final.mp4
```

Local ffmpeg only — it never truncates narration: audio is padded or looped to fit.

### 3.5 Search, fetch, research

```bash
# results only, fast
python3 web-search/scripts/search.py "edge-tts 403 websocket" --group tech --limit 5
python3 web-search/scripts/search.py "python asyncio timeout" --json

# one URL -> markdown
python3 fetch-page/scripts/fetch.py https://example.com --text
python3 fetch-page/scripts/fetch.py url1 url2 --out pages/ --json

# question -> cited report, optionally following the links inside the sources
python3 deep-research/scripts/research.py "why does ffmpeg zoompan jitter" -o jitter.md --depth 2
python3 deep-research/scripts/research.py "ffmpeg ken burns" -o kb.md --follow 2 --follow-budget 6
```

`--follow 2` reads each page's outbound links, keeps the ones that look like they
answer the question (off-site preferred, one hop only), fetches them, and marks
them `hop 2` in the report so you can see which evidence came from a search engine
and which from a link.

### 3.6 Cache

```bash
python3 shared/cachectl.py stats            # search/, fetch/, piper/, kv/ — size + age
python3 shared/cachectl.py prune --days 1   # drop stale entries (models are protected)
python3 shared/cachectl.py clear fetch      # wipe one namespace
AGENT_SKILLS_CACHE=/tmp/c python3 web-search/scripts/search.py "q"   # throwaway cache
```

Cache root: `$AGENT_SKILLS_CACHE`, else `~/.cache/agent-skills`. Search entries live
1 h, fetched pages 24 h, Piper voice models are never auto-removed.

---

## 4. Recipes

**Stills → narrated video from a research report**

```bash
python3 deep-research/scripts/research.py "the history of the ken burns effect" -o kb.md --depth 2
# (agent writes a script from kb.md)
python3 anon-audio/scripts/tts.py say --text-file narration.txt -o voice.mp3 --rate -8%
python3 ffmpeg-pipeline/scripts/vidkit.py video "sepia photographs of early cinema" -o doc.mp4 \
  --shots 4 --seconds 5 --narration "$(cat narration.txt)"
```

**Offline everything (air-gapped box)**

```bash
./install.sh --piper
python3 anon-audio/scripts/tts.py say "no network here" -o out.mp3 --engine offline
python3 ffmpeg-pipeline/scripts/vidkit.py slideshow *.jpg -o deck.mp4 --seconds 3
# search/fetch/research are the only skills that need the network, by nature
```

**Agent loop: answer a question with sources**

```bash
CMD=$(python3 skill-router/scripts/route.py "how do I fix zoompan jitter" --json | \
      python3 -c "import json,sys; print(json.load(sys.stdin)['match']['command'])")
eval "$CMD"
```

---

## 5. When things go wrong

| Symptom | What it means | Fix |
|---|---|---|
| `no confident match` from the router | intent too vague or not covered | say the object: "logo", "voiceover", "PDF"; or `--list` |
| search returns `count: 0` with errors | every relay is rate-limiting at once | wait ~1 min; results cache for 1 h |
| `reader: HTTP 401/403` | the markdown reader refuses your IP | automatic — CORS relays take over |
| `no proxy relayed: … 429/522` | volunteer relays having a bad minute | retry; use `--group tech` or academic providers (no relay needed) |
| force an image | the first free endpoint said no | automatic fallback; `imgen.py probe` shows which is live |
| `piper not installed` | offline engine missing | `./install.sh --piper` |
| `no Piper voice in ~/.cache/agent-skills/piper` | package present, model absent | `tts.py fetch-voice en_US-amy-low` |
| `PDF but no text extractor` | no pdftotext/pypdf | `apt install poppler-utils` or `pip install pypdf` |
| a test fails once, passes on re-run | the network, not the code | `./test-all.sh` again; check the log line it prints |

---

## 6. Honest limits

- **These are consumer endpoints, not contracts.** Google, Microsoft and the free
  image hosts can throttle or block without notice; that is why every skill has more
  than one leg and says which leg it used.
- **The web relays are volunteer services.** `r.jina.ai` blocked this network
  outright on 2026-11-01; the pack now has five transports and cooldowns, but on a
  bad day search is slower and thinner. Tech/academic providers need no relay.
- **No generative text-to-video.** Nothing key-less exists. `vidkit video` builds a
  *documentary edit* from AI stills — honest, and it works.
- **Anonymous ≠ untraceable.** No account is used, but your IP is yours.
- **Piper quality is good, not ElevenLabs.** One voice per 63 MB file; `low` models
  are fast and small, `high` models are noticeably better and ~4× bigger.
- **Measured on one machine, one day.** Re-run `probe` / `--check` on yours.

---

## 7. Reference

```
agent-skills-pack-v1.2.0/
├── GUIDE.md                  ← this file
├── README.md                 the short version
├── MANIFEST.json             machine-readable catalogue (route from this)
├── install.sh                optional deps; --piper, --check
├── test-all.sh               46 smoke tests; --fast
├── sync.sh                   republish canonical scripts into the standalone skills
├── shared/cachectl.py        one cache for everything
├── skill-router/             SKILL.md + scripts/route.py
├── anon-image/               SKILL.md + scripts/imgen.py + reference/endpoints.md
├── anon-audio/               SKILL.md + scripts/tts.py  + reference/rate-limits.md
├── ffmpeg-pipeline/          SKILL.md + scripts/vidkit.py + reference/cookbook.md
├── web-search/               SKILL.md + scripts/search.py + reference/providers.md
├── fetch-page/               SKILL.md + scripts/fetch.py  + reference/fetch-chain.md
└── deep-research/            SKILL.md + scripts/{research,search,fetch}.py + reference/
```

Per-skill archives in `dist/` are the same content, one directory each, for agents
that want a single install target.

**Licence:** skill code MIT. Upstream services are third parties with their own
terms — read the `reference/` file of the skill you are about to use commercially.
