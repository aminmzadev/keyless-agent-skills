---
name: fetch-page
description: Turn any URL into clean markdown for an agent, with a three-stage fallback (direct → two independent kinds of relay → Wayback Machine), PDF support and a 24-hour cache. Use whenever you have a URL and need its readable content — documentation, articles, changelogs, terms pages, papers, or a page a plain request cannot get through.
version: 1.2.0
license: MIT
---

# fetch-page

Give it URLs, get markdown. Three transports per URL, in order, so a blocked or
JS-heavy page is not a dead end.

## Use this when

- you have a specific URL and need what it *says*
- a previous `curl` returned a cookie wall, a JS shell, or a 403
- you need a PDF as text
- you want several pages read in parallel and written to disk

Use `web-search` when you don't have a URL yet.

## Quick start

```bash
fetch.py https://example.com
fetch.py https://vheer.com/terms --max-chars 8000
fetch.py url1 url2 url3 --out pages/ --json
fetch.py https://example.com --links            # also extract the links
fetch.py https://example.com --text             # print the text, no files
```

## The fallback chain

| Stage | Transport | Handles |
|---|---|---|
| 1. direct | urllib, browser UA, gzip, redirects | normal pages; a hand-written `html.parser` extractor drops nav/footer/aside/forms and cookie/subscribe boilerplate, keeps headings and list structure |
| 2. reader | `r.jina.ai/<url>` markdown, then the CORS relays (`allorigins` → `cors.lol` → `codetabs`) | JS-rendered pages, anti-bot walls, PDFs when no local extractor exists |
| 3. archive | Wayback Machine snapshot | pages that are gone, moved or blocked entirely |

PDF text: `pdftotext` → `pypdf` → reader proxy. If none of the three can read it,
you get a clear error naming the missing dependency, not a silent empty file.

## Output

- default: a one-line human summary per URL, `[ok] 11860 chars · direct · 0.3s · Title`
- `--json`: `[{url, ok, title, chars, source, elapsed, final_url, errors[], file?}]`
- `--out dir/`: writes one `.md` per page with a YAML header (url, title, source,
  chars, fetched_at) plus a `## Links` section when `--links` is set

`source` tells you which stage won — `direct`, `reader:jina`, `reader:allorigins`,
`reader:cors.lol`, or `wayback`. That matters: a Wayback result is a *snapshot*,
so check `snapshot_ts` before quoting it as current.

**Why two kinds of relay:** on 2026-11-01 `r.jina.ai` began answering
`401 You have been blocked` to this network, which broke every URL that a plain
request could not reach. The CORS relays were added that day; the markdown reader
is still tried first because its output is cleaner, and it disables itself for the
run as soon as it refuses us.

## Failure modes

| Symptom | Meaning | Action |
|---|---|---|
| `thin content (180 chars) — probably JS-rendered` | stage 1 hit a shell page | it already fell through to the reader; if the final `source` is still `direct`, the page is genuinely empty |
| `reader: HTTP 401/403` | the markdown reader refuses this IP | expected since 2026-11-01; the CORS relays take over automatically |
| `no proxy relayed: … 429/522/timeout` | every relay is rate-limiting at once | wait ~1 min, or `--no-archive --no-reader` if the page is static |
| `reader:allorigins HTTP 500` | that relay choked on this URL | the next relay is already being tried |
| `no Wayback snapshot` | page never archived | the URL is probably wrong; re-check it |
| `PDF but no text extractor` | no pdftotext/pypdf | `apt install poppler-utils` or `pip install pypdf`; the reader proxy usually covers you anyway |
| empty text, every stage failed | paywall or hard auth wall | report it, do not invent content |

## Shared cache

Every skill that caches writes into one root, so you can see and clean it as a
unit instead of hunting through three directories:

| Namespace | Written by | TTL |
|---|---|---|
| `<root>/search/` | `web-search`, `deep-research` | 1 h |
| `<root>/fetch/` | `fetch-page`, `deep-research` | 24 h |
| `<root>/piper/` | `anon-audio` voice files | never pruned |
| `<root>/kv/<ns>/` | anything else (`cachectl put/get`) | caller's choice |

`root = $AGENT_SKILLS_CACHE`, else `~/.cache/agent-skills`.

```bash
python3 scripts/_cachekit.py stats            # what is using the disk
python3 scripts/_cachekit.py prune --days 1   # drop entries older than a day
python3 scripts/_cachekit.py clear fetch      # wipe one namespace
python3 scripts/_cachekit.py clear all        # everything except voice models
AGENT_SKILLS_CACHE=/tmp/c python3 scripts/search.py "q"   # throwaway cache
```

Model files are protected from `clear all` and `prune` on purpose — they are the
one thing here that costs real time to get back.

## Caching

`~/.cache/agent-skills/fetch/`, 24 h TTL, keyed by URL. `--no-cache` bypasses and
rewrites; `--ttl 0` disables. A research loop that revisits the same host is free
after the first pass.

## Notes for the agent

- **Quote from the returned text, never from the snippet you saw in search.**
  Snippets are truncated and occasionally wrong.
- Check `chars` before trusting a source: under ~500 characters usually means you
  got a stub, not an article.
- `--max-chars` defaults to 20000, which is roughly a long article. Raise it for
  documentation pages, lower it when you only need the top of a page.
- Respect the hosts you hit: 4 parallel fetches is the default and is plenty.
