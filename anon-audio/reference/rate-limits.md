# anon-audio — rate limits and failure signatures

All measurements from a single EU datacenter IP, 2026-11-01. Per-IP behaviour:
your home connection may differ.

## Google Translate TTS (`translate.google.com/translate_tts`)

| Test | Result |
|---|---|
| 30 sequential, no delay | 30/30 → 200 |
| 20 concurrent | 20/20 → 200 |
| 40 concurrent, full-length text | 40/40 → 200 |
| 60 sequential | 4.8 s total (12.4 req/s), all 200 |
| 100 sequential | 8.2 s total (12.3 req/s), all 200 |
| cumulative for the day | ~275 requests, 0 failures |

**Hard limits**

- **200 characters per request exactly.** 200 → 200 OK; **201 → `400 Error 400 (Bad Request)!!1`**.
- **Any `Referer` header → `404`.** No UA is fine (200); a referrer is fatal.
- No published quota, no rate-limit headers. Historically: captchas (2015) and
  silent IP-level `503` blocks for low-volume users.

**Safe policy:** ≤10 req/s, ≤20 concurrent, cache by `(text, lang)`, treat
400/404/503 as "stop", not "retry".

## Edge TTS (`edge-tts`, Microsoft read-aloud)

| Test | Result |
|---|---|
| 10 sequential | 10/10 |
| 8 / 16 / 32 parallel | 8/8, 16/16, 32/32 — all OK |
| single request, 3,960 chars | 21 s → 250 s of audio |
| single request, 20,000 chars | <120 s → 1,790 s (29.8 min) of audio |
| warm latency | 0.9–1.7 s |

**No character cap found. No daily cap found at research volume.**

**Failure signatures**

- `403 Invalid response status` = endpoint/token drift, **not** throttling.
  Microsoft has moved the synthesis host twice; `edge-tts <= 7.2.x` fails while
  `--list-voices` keeps working (different host). Fix: `pip install -U edge-tts`
  (7.3.x uses the current Sec-MS-GEC v2 token). Verified working on 7.3.1.
- `429 Too Many Requests` = too many chunks at once (reported at ~140 chunks in
  an audiobook pipeline). Keep ≤4 concurrent, chunk ≤2,000 chars, back off.

## Cross-engine policy implemented in `tts.py`

```
--engine auto  →  edge (best quality)  →  on ANY failure  →  gtts  →  exit 0
```

Both engines are consumer features, not APIs. Neither is suitable as the sole
dependency of a commercial product without a real cloud key as a third fallback.
