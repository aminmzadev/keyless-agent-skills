#!/usr/bin/env bash
# build.sh — turn the working pack into a *publishable* tree.
#
#   bash packaging/build.sh --owner your-github-handle [--name keyless-agent-skills]
#
# Why a generated tree instead of publishing the repo as-is:
#   * the skills CLI (`npx skills add owner/repo`) discovers skills inside
#     container directories ("skills/", ".claude/skills/", ".agents/skills/" …),
#     not in flat <name>/SKILL.md folders at the repo root
#   * npm wants a package.json, a files whitelist and no stray build junk
#   * the same tree serves pi (`pi install npm:@scope/pkg`), Claude Code
#     (.claude-plugin/marketplace.json), skills.sh and GitHub Releases
#
# Output: packaging/dist-repo/  (throwaway; regenerated on every run)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$(cd "$here/.." && pwd)"
out="$here/dist-repo"

# unscoped package name — checked free on the registry 2026-11-01
NAME="${NAME:-keyless-agent-skills}"
# the repo owner is whoever owns the repo; ask the authenticated gh CLI rather
# than hardcoding a handle that goes stale the moment someone forks this
OWNER="${OWNER:-$(command -v gh >/dev/null 2>&1 && gh api user -q .login 2>/dev/null || true)}"
VERSION="$(python3 -c "import json;print(json.load(open('$src/MANIFEST.json'))['version'])")"

while [ $# -gt 0 ]; do
  case "$1" in
    --owner)   OWNER="$2"; shift 2 ;;
    --name)    NAME="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$OWNER" ]; then
  echo "no repo owner: pass --owner <github-handle> (or authenticate gh first)" >&2
  exit 2
fi

SKILLS="skill-router anon-image anon-audio ffmpeg-pipeline web-search fetch-page deep-research"

rm -rf "$out"
mkdir -p "$out/skills" "$out/.claude-plugin"

for s in $SKILLS; do
  [ -f "$src/$s/SKILL.md" ] || { echo "missing skill: $s" >&2; exit 1; }
  mkdir -p "$out/skills/$s"
  # rsync is not guaranteed on a bare runner; tar+cp is
  # tests stay in the repo, not in the package: their path constants point at
  # the repo layout, so a shipped copy could not run where it lands
  ( cd "$src/$s" && tar -cf - --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='tests' --exclude='research_*.json' . ) | ( cd "$out/skills/$s" && tar -xf - )
  chmod +x "$out/skills/$s/scripts/"*.py 2>/dev/null || true
done

cp "$src/GUIDE.md" "$src/README.md" "$src/MANIFEST.json" "$src/install.sh" "$out/"

python3 "$here/make_package.py" "$src" "$out" "$OWNER" "$NAME" "$VERSION"

cp "$here/LICENSE" "$here/NOTICE" "$here/DISCLOSURE" "$out/"
# workflows live at the repo root (GitHub only reads .github/workflows there),
# and are copied into the generated tree so the package carries its own CI too
mkdir -p "$out/.github/workflows"
cp "$src/.github/workflows/"*.yml "$out/.github/workflows/"

# the _cachekit copies the three caching skills expect next to their scripts
for s in web-search fetch-page deep-research; do
  cp "$out/skills/_shared/cachectl.py" "$out/skills/$s/scripts/_cachekit.py"
  chmod +x "$out/skills/$s/scripts/_cachekit.py" "$out/skills/_shared/cachectl.py"
done

echo "built: $out"
find "$out/skills" -name SKILL.md | sort
