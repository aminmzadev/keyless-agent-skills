#!/usr/bin/env bash
# test-all.sh — smoke-test every skill end to end. Non-zero exit if a core test fails.
# Usage: ./test-all.sh [--fast]     (--fast skips the two slow network generations)
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
# a synthetic still so --fast and offline runs can still exercise the video skill
mkimg() {
  python3 - "$1" <<'PY'
import sys
from PIL import Image, ImageDraw
im = Image.new("RGB", (800, 600), (24, 32, 48))
d = ImageDraw.Draw(im)
for i in range(600):
    d.line([(0, i), (800, i)], fill=(24 + i // 12, 32 + i // 20, 48 + i // 8))
d.ellipse([300, 200, 500, 400], fill=(220, 200, 150))
im.save(sys.argv[1], quality=90)
PY
}
FAST=0; [ "${1:-}" = "--fast" ] && FAST=1
pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
sk()   { printf '  \033[33mSKIP\033[0m %s\n' "$1"; skip=$((skip+1)); }
run()  { local name="$1"; shift; if "$@" >"$work/log" 2>&1; then ok "$name"; else bad "$name ($(tail -1 "$work/log" | cut -c1-90))"; fi; }

echo "== anon-image =="
if [ "$FAST" = 0 ]; then
  run "gen 16:9"        python3 "$here/anon-image/scripts/imgen.py" gen "a red bicycle against a white wall" -o "$work/a.jpg" --ratio 16:9
  run "edit"            python3 "$here/anon-image/scripts/imgen.py" edit "$work/a.jpg" "make it night time" -o "$work/b.jpg"
else sk "gen/edit (--fast)"; fi
[ -f "$work/a.jpg" ] || { mkimg "$work/a.jpg" && run "synthetic still" true; }
[ -f "$work/c.jpg" ] || cp "$work/a.jpg" "$work/c.jpg"
run "sheet"             python3 "$here/anon-image/scripts/imgen.py" sheet "$work/sheet.jpg" "$work/a.jpg" "$work/c.jpg" --labels "a|b"

echo "== packaging / install-ability =="
run "SKILL.md frontmatter parses" python3 "$here/packaging/lint_skills.py" "$here"
run "workflow yaml parses"   python3 "$here/packaging/lint_workflows.py" "$here"
if command -v npm >/dev/null; then
  run "publishable tree builds" bash -c "bash '$here/packaging/build.sh' --owner ci --name keyless-agent-skills >/dev/null && cd '$here/packaging/dist-repo' && npm pack --dry-run 2>&1 | grep -q 'total files'"
else sk "publishable tree (npm not installed)"; fi

echo "== skill-router =="
run "router list"       python3 "$here/skill-router/scripts/route.py" --list
run "router image"      python3 "$here/skill-router/scripts/route.py" "make me a logo for a coffee shop"
run "router audio"      python3 "$here/skill-router/scripts/route.py" "I need a voiceover for a podcast"
run "router fetch"      python3 "$here/skill-router/scripts/route.py" "read this url and give me the text" --json
run "router meta"       python3 "$here/skill-router/scripts/route.py" "which skill handles PDFs"
if python3 "$here/skill-router/scripts/route.py" "hello there friend" >/dev/null 2>&1; then
  bad "router refuses to guess (matched a nonsense intent)"
else ok "router refuses to guess"; fi

echo "== shared cache =="
run "cache stats"       python3 "$here/shared/cachectl.py" stats
run "cache kv roundtrip" bash -c "echo '{\"t\":42}' | python3 '$here/shared/cachectl.py' put selftest probe >/dev/null && python3 '$here/shared/cachectl.py' get selftest probe | grep -q 42"
run "cache prune"       python3 "$here/shared/cachectl.py" prune --days 3650
run "cache clear ns"    python3 "$here/shared/cachectl.py" clear selftest
run "cache env root"    env AGENT_SKILLS_CACHE="$work/cachetest" python3 "$here/shared/cachectl.py" stats
run "cachekit vendored" python3 "$here/web-search/scripts/_cachekit.py" stats

echo "== anon-audio =="
run "edge or gtts"      python3 "$here/anon-audio/scripts/tts.py" say "Testing the audio skill end to end." -o "$work/v.mp3"
run "gtts forced"       python3 "$here/anon-audio/scripts/tts.py" say "Pure standard library speech, chunked correctly." -o "$work/g.mp3" --engine gtts
run "voices list"       python3 "$here/anon-audio/scripts/tts.py" voices --lang en --limit 3
if python3 -c "import piper" >/dev/null 2>&1 && ls "${PIPER_DATA_DIR:-$HOME/.cache/agent-skills/piper}"/*.onnx >/dev/null 2>&1; then
  run "piper offline"   python3 "$here/anon-audio/scripts/tts.py" say "Offline speech, no network required." -o "$work/piper.mp3" --engine offline --json
else
  sk "piper offline (pip install piper-tts + ./install.sh --piper)"
fi
[ -f "$work/v.mp3" ] && [ -f "$work/g.mp3" ] && run "concat" python3 "$here/anon-audio/scripts/tts.py" concat "$work/v.mp3" "$work/g.mp3" -o "$work/join.mp3"

echo "== ffmpeg-pipeline =="
run "probe"             python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" probe
if command -v ffmpeg >/dev/null; then
  run "slideshow"       python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" slideshow "$work/a.jpg" "$work/c.jpg" -o "$work/s.mp4" --seconds 2
  run "mux narration"   python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" mux "$work/s.mp4" "$work/v.mp3" -o "$work/m.mp4"
  run "info"            python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" info "$work/m.mp4"
  run "sheet from video" python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" sheet "$work/m.mp4" -o "$work/vs.jpg" --cols 2 --count 2
  run "gif"             python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" gif "$work/m.mp4" -o "$work/g.gif" --dur 1 --width 320
  run "normalize"       python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" normalize "$work/v.mp3" -o "$work/n.mp3"
  run "speed"           python3 "$here/ffmpeg-pipeline/scripts/vidkit.py" speed "$work/n.mp3" -o "$work/f.mp3" --factor 1.2
else sk "ffmpeg tests (not installed: run ./install.sh)"; fi

echo "== web-search (standalone) =="
run "search standalone" python3 "$here/web-search/scripts/search.py" "ffmpeg zoompan" --limit 3 --json
run "search --all"      python3 "$here/web-search/scripts/search.py" "static site generator" --all --limit 3 --json
run "search groups"     python3 "$here/web-search/scripts/search.py" "python dataclass" --group tech --limit 2 --json

echo "== fetch-page (standalone) =="
run "fetch standalone"  python3 "$here/fetch-page/scripts/fetch.py" https://example.com --json
run "fetch fallback"    python3 "$here/fetch-page/scripts/fetch.py" https://vheer.com/terms --max-chars 400 --json
run "fetch to disk"     python3 "$here/fetch-page/scripts/fetch.py" https://example.com --out "$work/pages" --json

echo "== deep-research =="
cat > "$work/follow_pick.py" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["HERE"], "deep-research", "scripts"))
import research as R
doc = {"url": "https://blog.example.com/post", "links": [
    ("Read more", "https://other.example.org/x"),                          # anchor too short
    ("Sign in", "https://blog.example.com/auth/signin"),                   # junk
    ("FFmpeg zoompan documentation", "https://ffmpeg.org/zoompan.html"),   # the right pick
    ("Related: zoompan tricks", "https://blog.example.com/zoompantricks"), # same host penalty
    ("photo", "https://cdn.example.com/a.png"),                            # binary
    ("FFmpeg zoompan filter reference", "https://ffmpeg.org/ffmpeg-filters.html"),
]}
picked = R.pick_links(doc, "ffmpeg zoompan jitter", 2)
urls = [p["url"] for p in picked]
assert any(u.startswith("https://ffmpeg.org/") for u in urls), urls
assert not any("signin" in u or u.endswith(".png") for u in urls), urls
assert len(picked) <= 2, urls
assert all(p["score"] > 0 for p in picked), picked
print("picked:", urls)
PY
HERE="$here" run "follow picker (offline)" python3 "$work/follow_pick.py"
run "search web"        python3 "$here/deep-research/scripts/search.py" "ffmpeg zoompan" --limit 3 --json
run "search tech"       python3 "$here/deep-research/scripts/search.py" "python subprocess timeout" --group tech --limit 3 --json
run "provider list"     python3 "$here/deep-research/scripts/search.py" --list-providers
run "fetch html"        python3 "$here/deep-research/scripts/fetch.py" https://example.com --json
run "fetch+fallback"    python3 "$here/deep-research/scripts/fetch.py" https://vheer.com/terms --max-chars 500 --json
if [ "$FAST" = 0 ]; then
  run "research run"    python3 "$here/deep-research/scripts/research.py" "what is ffmpeg zoompan used for" --depth 1 --max-sources 3 -o "$work/r.md"
  # --follow must actually resolve relative links into hop-2 sources
  run "follow hop2"     bash -c "python3 '$here/deep-research/scripts/research.py' 'why does ffmpeg zoompan jitter and how to fix it' --depth 1 --max-sources 3 --follow 2 --follow-budget 4 -o '$work/rf.md'; grep -q '| hop |' '$work/rf.md'"
else sk "research run + follow (--fast)"; fi

echo
printf '== %d passed, %d failed, %d skipped ==\n' "$pass" "$fail" "$skip"
[ -d "$work" ] && rm -rf "$work"
[ "$fail" -eq 0 ]
