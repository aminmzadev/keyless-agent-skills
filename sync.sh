#!/usr/bin/env bash
# sync.sh — keep the standalone skills in sync with the canonical scripts.
#
# Canonical home:   deep-research/scripts/{search,fetch}.py
# Published copies: web-search/scripts/search.py, fetch-page/scripts/fetch.py
#
# The copies exist so each zip is self-contained (an agent that only installs
# web-search should not need deep-research on disk). Edit the canonical file,
# then run ./sync.sh. It is idempotent: unchanged files are left untouched.
set -euo pipefail
cd "$(dirname "$0")"
stamp="$(date -u +%Y-%m-%d)"

inject() { # src dst skillname
  local src="$1" dst="$2" name="$3"
  python3 - "$src" "$dst" "$name" "$stamp" <<'PY'
import sys
src, dst, name, stamp = sys.argv[1:5]
lines = open(src, encoding="utf-8").read().splitlines(keepends=True)
if len(lines) > 1 and lines[1].startswith("# ---"):
    body = lines[1:]
    # drop an existing injected banner (4 lines: '# ---', 'published as…', 'canonical…', '# ---')
    while body and body[0].startswith("#"):
        body.pop(0)
else:
    body = lines[1:]
banner = [f"# ---\n",
          f"# Published as the `{name}` skill. Canonical source: deep-research/scripts/.\n",
          f"# Edit there, then run ./sync.sh (synced {stamp}).\n",
          f"# ---\n"]
open(dst, "w", encoding="utf-8").writelines(lines[:1] + banner + body)
PY
  chmod +x "$dst"
}

inject deep-research/scripts/search.py web-search/scripts/search.py web-search
inject deep-research/scripts/fetch.py  fetch-page/scripts/fetch.py  fetch-page
cp deep-research/reference/providers.md web-search/reference/providers.md

# the shared cache tool ships with every skill that caches, so a skill installed
# on its own can still run `python3 scripts/_cachekit.py stats`
for d in deep-research/scripts web-search/scripts fetch-page/scripts; do
  cp shared/cachectl.py "$d/_cachekit.py"
  chmod +x "$d/_cachekit.py"
done

echo "synced: web-search/scripts/search.py, fetch-page/scripts/fetch.py,"\
     "web-search/reference/providers.md, */scripts/_cachekit.py"
