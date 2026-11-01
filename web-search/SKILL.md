---
name: web-search
description: Keyless multi-provider web search for agents. Seventeen providers behind one command, with automatic fallback, query relaxation, cross-provider agreement ranking and domain diversity. Use for any "look this up", "what is the latest", "find me a library/tool/paper" request when you only need results, not a full report.
version: 1.2.0
license: MIT
---

# web-search

One command, seventeen providers, no key and no account. This is the *finding* half
of the research pair; `fetch-page` reads, `deep-research` orchestrates both.

## Use this when

- you need **results, not reading**: links, titles, snippets, enough to decide
- you want to check whether something exists before spending a fetch on it
- you need a source in a specific domain: code, papers, packages, news

Use `fetch-page` instead when you already have a URL and need its content.
Use `deep-research` when the answer has to be written with citations.

## Quick start

```bash
search.py "free tts api"                        # web group: bing + ddg + wikipedia
search.py "python subprocess timeout" --group tech
search.py "speech synthesis" --group academic --limit 5
search.py "rust async" --all --json             # every provider, merged and ranked
search.py "numpy" --provider pypi               # package lookup
search.py --list-providers
```

Groups: `web` (bing, ddg, wikipedia) · `tech` (hn, stackexchange, github) ·
`academic` (arxiv, openalex, europepmc, crossref) · `all` (everything except `pypi`).

## The one thing to know

DuckDuckGo, Bing, Brave, Mojeek, Ecosia and every public searx instance block
datacenter IPs. So every general engine is fetched through a **transport chain**
and the first one that answers wins:

| # | transport | what it gives you | cost |
|---|---|---|---|
| 1 | **direct** | the SERP itself, if today's IP is not blocked | ~1 s |
| 2 | **reader** (`r.jina.ai/<serp>`) | the SERP rendered from Jina's IP, as markdown | 3–15 s |
| 3 | **CORS relays** (`allorigins`, `cors.lol`, `codetabs`) | the raw SERP HTML, fetched from their IP | 5–40 s |

Each transport feeds the same parser, which unwraps `uddg=` / `ck/a?u=a1…`
redirects and drops chrome. That is why this works with no key where a plain
`curl` gets `202` or `403`.

**Why the chain and not one proxy:** on 2026-11-01 `r.jina.ai` started answering
`401 You have been blocked` to this network. Every search and every fetch broke
at once — the whole skill had one point of failure. The chain plus two layoffs
(a relay that 429s or 5xxs is benched for 75 s, retried twice first) is the fix.
It is also why results are now sanity-checked: a blocked engine will serve a
plausible page of dictionary entries for the word "why", so any transport whose
hits share no distinctive word with the query is discarded and the next one is
tried.

## Reading the output

- `--json` returns `{query, providers_used, errors, count, results[]}` where each
  result carries `title`, `url`, `snippet`, `providers[]` (agreement), `rank`.
- **`providers` with more than one entry is your best signal** — two independent
  engines agreeing on a URL is worth more than rank 1 from one.
- **`errors` is not a failure** unless `count` is 0. One provider down is normal;
  the others cover it.
- `relaxed_from` in the JSON means your query was too specific and the tool
  retried with its three most distinctive words. That's it working, not failing.

## Behaviours worth relying on

| Behaviour | What it buys you |
|---|---|
| query relaxation on zero results | `"ffmpeg zoompan jitter smooth pan"` → 0 hits → auto-retry `"ffmpeg zoompan jitter"` → real StackOverflow answers |
| cross-provider agreement boost | duplicate results from independent engines float to the top |
| domain diversity cap (2 per host) | one blog cannot fill your result list |
| relevance gate | results matching only in the body are demoted below title matches |
| 1-hour disk cache | identical queries in a loop are free |

## Failure modes

| Symptom | Meaning | Action |
|---|---|---|
| `reader: HTTP 401/403` | the markdown reader refuses this IP | it is marked dead for the run and the CORS relays take over; nothing to do |
| `no proxy relayed: … HTTP 429/522` | volunteer relays are rate-limiting (they do this in bursts) | wait ~1 min; results are cached 1 h so a repeat costs nothing |
| `direct: off-topic` | the engine answered, but with results for a different query | treated as a failure on purpose — next transport tried |
| `count: 0`, no errors | genuinely nothing, or everything blocked | try `--group all`, or shorter/more generic words |
| one entry in `errors` | that provider failed | ignore; retry with `--no-cache` for a different mix |
| `# degraded: bing (...)` printed first | a provider failed but others answered | informational; the payload is still good |
| `pypi: HTTP 404` | `--provider pypi` is a package *lookup*: it takes a package name, not a query | pass the name |

## Notes for the agent

- Prefer `--group tech` for library/API/error questions — StackOverflow + GitHub
  beat a general index for that by a wide margin.
- Read the `snippet`, not just the title, before fetching. Half the time the
  snippet already answers the question and you save a fetch.
- Do not scrape: this is search, one query per intent, not a crawler.

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
