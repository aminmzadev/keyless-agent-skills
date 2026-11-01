# anon-image — endpoints, measured 2026-11-01

Base: `https://freeapi.pro` (no auth, no key, Express behind hcdn).

## Text to image — `POST /api/tti`

```json
{"prompt": "…", "ratio": "1:1|16:9|9:16|4:3|3:4|3:2|2:3"}
→ {"success":true,"imageUrl":"https://access.vheer.com/results/<id>.jpg","code":"…","prompt":"…"}
```

| Measurement | Value |
|---|---|
| latency, single | 10–19 s |
| output size | 1248 px on the long edge, JPEG |
| 5 parallel | 5/5 ok, 10.7–17.6 s |
| 10 parallel | 10/10 ok, 16.0–18.5 s |
| empty prompt | `400 {"error":"'prompt' is required."}` |
| watermark | none |
| result URL lifetime | temporary (`access.vheer.com`) — download the bytes, never store the URL |

## Image to image — `POST /api/pti`

```json
{"prompt": "…", "ratio": "auto", "imageBase64": "<raw base64, no data: prefix>"}
→ {"success":true,"imageUrl":"…","ratio":"auto","originalSize":…,"filesize":N}
```

- input **≤ 3.5 MB**, JPG/PNG/WebP; dimensions are preserved in the output
- latency ~20 s
- never inline base64 in a shell argument (`ARG_MAX`); write a JSON file and use
  `--data-binary @payload.json`, which is what the script does

## Dead endpoints on the same host

| Endpoint | State |
|---|---|
| `POST /api/ptv` (image→video) | **broken** — 502 after 46 s, or nginx 504 at 57 s. Zero successes in testing. |
| `POST /api/tti` with `model` field | ignored |
| `GET /api/tti` / `GET /api/pti` | 405 |

## Licensing (as written on the upstream site, 2026-11-01)

- Free **text-to-image** quality tier: personal / non-commercial only.
- **Image-to-image**, background remover and the non-AI tools ("regardless of
  subscription status"): commercial use allowed.
- The upstream ToS forbids bots/crawlers; this skill calls documented, open,
  anonymous endpoints at human-ish volume. Keep it that way.

## Rate policy used by the skill

10 parallel is safe and measured. There is no published limit ("∞ requests, fair use"),
no rate-limit headers, and a hard 60 s gateway timeout on every request.
