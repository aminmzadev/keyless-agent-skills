<div align="center">

# Keyless Agent Skills

**Seven self-contained skills that give an AI agent hands: images, speech, video, web search, page fetching, cited research, and a router that picks between them.**

No API keys. No accounts. No sign-ups. No telemetry. Just `python3`, `curl`-shaped tools, and every fallback you wish the vendor SDK had.

[![CI](https://github.com/aminmzadev/keyless-agent-skills/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aminmzadev/keyless-agent-skills/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/aminmzadev/keyless-agent-skills?sort=semver&display_name=release)](https://github.com/aminmzadev/keyless-agent-skills/releases)
[![License: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/aminmzadev/keyless-agent-skills)](https://github.com/aminmzadev/keyless-agent-skills/commits/main)

[![No API key](https://img.shields.io/badge/API%20key-not%20required-2ea44f)](#the-promise)
[![No account](https://img.shields.io/badge/account-not%20required-2ea44f)](#the-promise)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey)](#compatibility)
[![Skills](https://img.shields.io/badge/skills-7-blueviolet)](#the-seven-skills)
[![Tests](https://img.shields.io/badge/tests-167%20hermetic%20%2B%20fuzz-informational)](#quality)

[Quick start](#quick-start) · [The seven skills](#the-seven-skills) · [See it work](#see-it-work) · [Install](#install) · [Composition](#composition) · [Quality](#quality) · [Docs](#documentation)

</div>

---

## The promise

| | |
|---|---|
| **No key, ever** | Nothing here reads `OPENAI_API_KEY` or asks you to sign up. The consumer path is `python3 script.py`. |
| **Degrade, don't die** | Every network path is an ordered chain. When a stage is blocked, rate-limited or dead, the next one answers and the result says which stage produced it. |
| **Measure, then document** | Latencies, limits and failure signatures in `reference/` come from runs against the real services, not from vendor docs. When a measurement is wrong, the note is corrected where it was written. |
| **The agent stays in charge** | Scripts return text, paths and JSON. Judgement, wording and verification stay with the model. |

## Quick start

```bash
# 1 — install all seven into whatever agent you use (no global install, nothing to configure)
npx skills add aminmzadev/keyless-agent-skills

# 2 — or take the bundle zip from the latest release and run a script directly
url=$(curl -sL https://api.github.com/repos/aminmzadev/keyless-agent-skills/releases/latest \
      | grep -oE 'https://[^"]*pack-v[0-9.]+\.zip' | head -1)
curl -LO "$url" && unzip -q "$(basename "$url")" -d skills && cd skills

# 3 — go
python3 fetch-page/scripts/fetch.py https://example.com
python3 web-search/scripts/search.py "keyless text to speech" --limit 5
python3 deep-research/scripts/research.py "does the vendor tier stay opt-in" -o report.md
```

Nothing is copied into a config file. Each skill is a directory with a `SKILL.md` (frontmatter + usage) and a `scripts/` folder — drop it where your agent looks for skills, or call the scripts by path.

## The seven skills

| Skill | What it does | One command | Hard deps |
|---|---|---|---|
| [`skill-router`](skill-router/SKILL.md) | turns a vague intent into the right skill and a pasteable command | `route.py "make a narrated video from a photo"` | `python3` |
| [`anon-image`](anon-image/SKILL.md) | text→image, edits, contact sheets, before/after comparisons | `imgen.py gen "a red bicycle" -o bike.jpg` | `python3` (Pillow for sheets) |
| [`anon-audio`](anon-audio/SKILL.md) | speech: 322 neural voices across 74 languages, gTTS fallback, **fully offline** Piper | `tts.py say "hello" -o hi.mp3` | `python3` (`edge-tts`, `piper`, `ffmpeg` optional) |
| [`ffmpeg-pipeline`](ffmpeg-pipeline/SKILL.md) | stills → video, narration muxing, GIF, frame grabs, contact sheets, loudness | `vidkit.py video "a lighthouse at dusk" -o out.mp4` | `ffmpeg` |
| [`web-search`](web-search/SKILL.md) | 21 keyless providers (18 classic + 3 opt-in vendor), merged, deduped, ranked | `search.py "piper tts" --group tech` | `python3` |
| [`fetch-page`](fetch-page/SKILL.md) | URL → clean markdown through a 17-stage chain, incl. **reddit and X posts with their pictures**, PDFs, wall detection | `fetch.py https://example.com --media pics/` | `python3` (poppler/pypdf optional — a stdlib PDF parser ships) |
| [`deep-research`](deep-research/SKILL.md) | search → read → follow links → cited markdown report with a JSON trail | `research.py "free tts without a key" -o tts.md` | `python3` |

`web-search` and `fetch-page` stand alone. `deep-research` carries its own copies of both scripts so it also stands alone; the canonical sources live in `deep-research/scripts/` and `./sync.sh` republishes them, so there is exactly one place to edit.

## See it work

Real output, trimmed, from this repo at `v1.7.0`:

```console
$ python3 fetch-page/scripts/fetch.py https://example.com
[ok]     1005 chars · reader:jina · 0.2s · Example Domain
```

```console
$ python3 web-search/scripts/search.py "piper tts offline" --limit 2
 1. Make Your Machine Talk: Piper TTS Offline - rmauro.dev
    https://rmauro.dev/how-to-run-piper-tts-on-your-raspberry-pi-offline-voice-zero-internet-needed/
    Jul 7, 2025 · Want to make your computer talk — using real, natural-sounding voices without needing the cloud? …
    via bing,yahoo,ddg
 2. Offline-Fast-CPU-PIPER-TTS - GitHub
    https://github.com/mdmonsurali/Offline-Fast-CPU-PIPER-TTS
    via bing,yahoo,ddg
```

```console
$ python3 skill-router/scripts/route.py "make a narrated video from a photo"
ffmpeg-pipeline  (2.62)
  python3 ffmpeg-pipeline/scripts/vidkit.py --help   # then: video / slideshow / mux / gif / …
```

```console
$ python3 ffmpeg-pipeline/scripts/vidkit.py info clip.mp4
clip.mp4: 5K, 3.00s, 15 kbps
  video: h264 1280x720 @ 25/1

$ python3 anon-audio/scripts/tts.py say "seven skills, no keys" -o out.mp3
saved out.mp3 (20K, engine=gtts, 0.1s, 2.6s audio)
```

Third-party results (`via bing,yahoo,ddg`) are ordinary search hits; the point is that three independent providers had to agree before they ranked, and that no key was involved in any of the four commands above.

## Install

### 1 · Skills CLI (any agent)

```bash
npx skills add aminmzadev/keyless-agent-skills            # all seven
npx skills add aminmzadev/keyless-agent-skills --list     # what is inside
npx skills add aminmzadev/keyless-agent-skills --skill web-search -a claude-code --copy
```

### 2 · Release archives

Every [release](https://github.com/aminmzadev/keyless-agent-skills/releases) carries the bundle, one zip per skill, and `SHA256SUMS.txt`. Assets are built by CI from the tag, never uploaded by hand:

```bash
base=https://github.com/aminmzadev/keyless-agent-skills/releases/download/v1.7.0   # ← current tag
curl -LO $base/keyless-agent-skills-pack-v1.7.0.zip
curl -LO $base/SHA256SUMS.txt && sha256sum -c SHA256SUMS.txt
```

Asset names carry the version on purpose: a release asset that keeps the same
name across releases gets served from a CDN cache for a while after it is
replaced, which looks exactly like a corrupted download.

### 3 · npm

The package is `keyless-agent-skills` (unscoped, no lifecycle scripts, no binaries, 142.8 kB packed / 35 files). Publishing is automated from tags with OIDC trusted publishing + provenance; until the first publish lands, use the two routes above.

```bash
npm install keyless-agent-skills     # once published
```

### 4 · Clone

```bash
git clone https://github.com/aminmzadev/keyless-agent-skills
cd keyless-agent-skills && ./install.sh --check     # what your machine already has
```

## Compatibility

| | |
|---|---|
| **Agents** | pi (`pi.skills`), Claude Code (the npm package ships a `.claude-plugin/marketplace.json`), Codex, Cursor, or anything that can run a shell command. Plain directories + `SKILL.md` — no SDK, no lock-in. |
| **Runtime** | Python 3.10+. CI runs 3.10, 3.12 and 3.13 on every push. |
| **OS** | Linux and macOS. Windows needs WSL — the scripts are POSIX and `ffmpeg`-shaped. |
| **Optional deps** | `ffmpeg` (video), `poppler`/`pypdf` (better PDFs), `Pillow` (contact sheets), `edge-tts` (best voices), `piper-tts` + one 63 MB voice (offline speech). Each is optional and each one's absence is a documented, tested path — never a crash. |

```bash
./install.sh            # check + install the optional deps
./install.sh --piper    # + offline TTS engine and voice
./install.sh --check    # report only, change nothing
```

## Composition

Skills find their siblings automatically, so one command can go prompt → narrated MP4:

```bash
ffmpeg-pipeline/scripts/vidkit.py video "a glass lighthouse at dusk" -o story.mp4 \
  --shots 3 --narration "Somewhere on a cold coast, a light kept turning."
```

The research trio is the same idea: `deep-research` searches, fetches through the 17-stage chain, follows the links that look relevant, and writes a report where every claim carries a source number that matches the sources table — with a JSON trail recording every candidate, provider, error and raw extract.

```
not sure at all?                      → route.py "what you want to do"
need links?                           → search.py "query"
have a URL, need text?                → fetch.py URL
need a written answer with citations? → research.py "question" -o answer.md
```

## Surviving the real web

The interesting engineering is not the happy path. `fetch.py` walks an ordered chain and stops at the first stage that returns something that is not a wall:

| Stage | What it is |
|---|---|
| `reddit:post` / `x:post` | keyless archive APIs for reddit and X, including photos and video (with `--media`) |
| `rewrite:*` | machine-readable twins — GitHub raw, StackExchange API, Wikipedia summary, nitter, scribe, YouTube oEmbed, arXiv PDF, reddit archive |
| `direct` | urllib with a browser UA, gzip, redirects, HTML→markdown, stdlib PDF text |
| `reader:*` | `r.jina.ai`, then three CORS relays, for JS shells and soft paywalls |
| `vendor:*` | Firecrawl's anonymous scrape, Exa's MCP fetch — opt-in, `--group vendor` / `FETCH_VENDOR` |
| `wayback` | the Wayback snapshot, for pages that are gone for good |

Every stage runs under a share of a shared wall-clock deadline, so a hostile page that ignores its socket timeouts cannot hang the caller: the stage is abandoned, the chain continues, and `stages_cut` says which ones were dropped. Bodies are read under a cap, gzip is inflated under a second cap, and only `http`/`https` are ever fetched — `file://` is refused, because a URL fetcher that reads local files is an exfiltration vector, not a feature.

`search.py` fans out across providers in parallel, merges and dedupes by URL, scores by relevance + cross-provider agreement + domain diversity, and relaxes the query once if everything came back empty. A provider that returns malformed rows is contained at the boundary instead of taking the whole search down.

## Quality

| Gate | What it covers |
|---|---|
| **CI matrix** | Python 3.10 / 3.12 / 3.13 on every push and PR, plus a full-suite job with `ffmpeg` installed so the video paths actually execute. |
| **167 hermetic tests** | No network: local HTTP servers impersonate origins, vendor APIs and CDNs, including ones that accept a connection and never answer. |
| **52 integration checks** | `./test-all.sh` runs every skill end to end against the real services — 52 passed, 0 failed, 1 skipped (the offline Piper voice, which needs a 63 MB model). `--fast` skips only the two slow generations. |
| **Fuzz harness** | `packaging/fuzz.py` throws 33 parsers × N rounds of hostile values (NUL, CRLF, invisible characters, emoji, YAML/Markdown/shell metacharacters, 4 KB URLs, `file://`, negatives). A clean refusal is a pass; any other exception is a failure. |
| **Packaging lint** | `SKILL.md` frontmatter, workflow YAML, Python 3.10 syntax, and a check that no voice model or `examples/` media can reach a package. |
| **Release rehearsal** | The published tarball is unpacked and inspected before a tag is announced — that is how the missing `.claude-plugin` manifest was caught. |

```bash
./test-all.sh              # full: ~3 min cold (network)
./test-all.sh --fast       # skip slow generations
python3 packaging/fuzz.py  # parser fuzzing, seeded
```

Two adversarial review passes went over the whole tree before 1.7.0 and fixed 18 real bugs — cache poisoning by a caller's `--max-chars`, a silent zero-duration video, unparseable YAML frontmatter from an ordinary page title, a `file://` read, unbounded gzip inflation, and a media download that overwrote the previous page's pictures. Every fix landed with a regression test.

## Documentation

| Document | Contents |
|---|---|
| [`GUIDE.md`](GUIDE.md) | install, usage and troubleshooting in one file — start here |
| [`MANIFEST.json`](MANIFEST.json) | all seven skills, entry points, commands, routing hints, fallback chain |
| `<skill>/SKILL.md` | per-skill frontmatter + usage, written for an agent to read |
| `<skill>/reference/` | measured provider notes, limits and failure signatures |
| [`SECURITY.md`](SECURITY.md) | threat model, disclose path, what the scripts will and will not do |
| [`DISCLOSURE`](DISCLOSURE) · [`NOTICE`](NOTICE) | third-party services, their terms, and what is borrowed |

## Security

No credentials are read, stored or transmitted. No telemetry. URLs are restricted to `http`/`https`, response bodies and decompression are capped, subprocesses are argument lists (never a shell), and archive extraction, `eval` and `pickle` are absent from the tree — `grep -rE "shell=True|eval\(|exec\(|pickle"` returns nothing.

Found a problem? Open an issue, or see [`SECURITY.md`](SECURITY.md) for the private route.

## Licence

Skill code: **MIT** — see [`LICENSE`](LICENSE). The upstream services these scripts talk to are third parties with their own terms; read their notes in `<skill>/reference/` and the [`DISCLOSURE`](DISCLOSURE) before shipping anything commercial.

<div align="center"><sub>Built to be boring in the best way: no keys, no accounts, no surprises — and a fallback for the day the free tier changes.</sub></div>
