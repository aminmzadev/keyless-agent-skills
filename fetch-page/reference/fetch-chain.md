# fetch-page — transport details

## Stage 1: direct

- Headers sent: browser `User-Agent`, `Accept: text/html,application/xhtml+xml,application/pdf,*/*`,
  `Accept-Language`, `Accept-Encoding: gzip, identity`
- Transparent gzip decompression (also sniffs the `1f 8b` magic bytes, because
  some hosts gzip without saying so)
- Redirects followed; `final_url` records where you landed
- Content-type routing: `pdf` → PDF extractor; `json` → pretty-printed text;
  everything else → HTML reader
- The reader is a `html.parser` subclass: it skips
  `script/style/noscript/svg/iframe/template/head/nav/footer/aside/form/button/select/figure`,
  emits block boundaries for `p/div/br/li/tr/section/article/blockquote/pre/table`,
  prefixes headings with `#` and list items with `-`, collects `og:`/`meta`
  metadata, and collapses 3+ blank lines
- Boilerplate filter drops lines matching cookie/consent/subscribe/newsletter/
  sign-in/all-rights-reserved/©/privacy/terms/advertisement patterns, plus
  consecutive duplicate lines
- Rejects a page under 200 characters as "thin" and falls through

## Stage 2: relays (two kinds, four endpoints)

The reader is a third party. It is the part of this chain most likely to stop
working, so there are two independent ways of getting a page from someone else's
IP, tried in order:

| Order | Endpoint | Returns | Notes |
|---|---|---|---|
| 2a | `r.jina.ai/<url>` | markdown (`Title:`, `URL Source:`, `Markdown Content:`) | renders JS with a real browser; free tier ≈ 20 req/min. **Answered `401 You have been blocked` from this network on 2026-11-01** — the code marks it dead for the rest of the run and moves on |
| 2b | `api.allorigins.win/raw?url=` | raw HTML → local extractor | volunteer relay; 502/522 in bursts |
| 2c | `api.cors.lol/?url=` | raw HTML → local extractor | volunteer relay; 429 when hammered |
| 2d | `api.codetabs.com/v1/proxy?quest=` | raw HTML → local extractor | mostly 522; kept as a last resort |

Dead ends, tested and dropped: `corsproxy.io` (`401`), `whateverorigin.org`
(`400` — demands an `Origin` header), `api.marginalia.nu` (timeout).
`source` in the JSON tells you which one won: `reader:jina`, `reader:allorigins`,
`reader:cors.lol`, `reader:codetabs`.

If *all* relays fail, the error string lists what each one said
(`no proxy relayed: …`) — that is a diagnostic, not a crash.

## Stage 3: archive

`https://archive.org/wayback/available?url=<url>` → `archived_snapshots.closest.url`
→ re-run stage 1 against the snapshot, then relabel `source: wayback` and record
`snapshot_ts`. Always check the timestamp before presenting an archived page as
current information.

## What is deliberately *not* here

- **No headless browser.** Playwright costs ~400 MB, needs system libraries, and
  dies on turn boundaries in most agent sandboxes. The reader proxy covers the
  same ground for a fraction of the cost. (`anon-image` keeps an optional
  Playwright path for Vheer's UI; that is the only place it appears.)
- **No cookie jar or login flow.** If a page needs an account, this skill says so
  instead of pretending.
- **No robots.txt crawling.** It fetches the URLs you name, one at a time, and
  never follows links by itself.
