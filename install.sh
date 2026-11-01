#!/usr/bin/env bash
# install.sh — optional dependencies for the pack. Nothing here is required for a
# skill to *run*; each one degrades gracefully without it. What these buy you:
#
#   ffmpeg/ffprobe   video, audio muxing, mp3 conversion        (ffmpeg-pipeline, anon-audio)
#   poppler          local PDF text extraction                  (fetch-page)
#   pillow           contact sheets, before/after images        (anon-image)
#   edge-tts         the best online voice tier                 (anon-audio)
#   piper-tts + voice  fully offline speech synthesis           (anon-audio --engine piper)
#
# Usage:
#   ./install.sh              # check + install the usual suspects
#   ./install.sh --piper      # also install the offline TTS engine (pip + ~63 MB voice)
#   ./install.sh --check      # report only, change nothing
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PIPER_DIR="${PIPER_DATA_DIR:-$HOME/.cache/agent-skills/piper}"
PIPER_VOICE="${PIPER_VOICE:-en_US-amy-low}"
DO_PIPER=0
CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --piper) DO_PIPER=1 ;;
    --check|--dry-run|-n) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  esac
done

say()  { printf '%-20s %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }
pyhave() { python3 -c "import $1" >/dev/null 2>&1; }

echo "== checking =="
have python3 && say python3 "$(python3 -V 2>&1)" || { echo "python3 is required"; exit 1; }
have ffmpeg   && say ffmpeg  "$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)" || say ffmpeg MISSING
have ffprobe  && say ffprobe ok || say ffprobe MISSING
have edge-tts && say edge-tts "$(edge-tts --version 2>&1 | head -1)" || say edge-tts MISSING
have pdftotext && say pdftotext ok || say pdftotext MISSING
pyhave PIL     && say pillow ok   || say pillow MISSING
pyhave piper   && say piper-tts ok || say piper-tts "MISSING (optional, offline TTS)"

if [ "$CHECK_ONLY" = "1" ]; then echo; echo "--check: nothing changed."; exit 0; fi

echo
echo "== installing what is missing =="
SUDO=""
[ "$(id -u)" = "0" ] || { have sudo && SUDO="sudo -n"; }
if ! have ffmpeg || ! have pdftotext; then
  if have apt-get; then
    $SUDO apt-get update -qq >/dev/null 2>&1
    $SUDO apt-get install -y -qq ffmpeg poppler-utils >/dev/null 2>&1 \
      && say ffmpeg "installed (with poppler)" || say ffmpeg "install failed — run apt-get yourself"
  elif have brew;  then brew install ffmpeg >/dev/null 2>&1 && say ffmpeg installed
  elif have dnf;   then $SUDO dnf install -y ffmpeg >/dev/null 2>&1 && say ffmpeg installed
  else say ffmpeg "no package manager found — install it manually"; fi
fi
pyhave PIL || { python3 -m pip install --quiet pillow 2>/dev/null && say pillow installed; }
have edge-tts || { python3 -m pip install --quiet --upgrade "edge-tts>=7.3.1" 2>/dev/null \
  && say edge-tts installed; }

if [ "$DO_PIPER" = "1" ]; then
  echo
  echo "== offline TTS (piper) =="
  python3 -m pip install --quiet --upgrade piper-tts 2>/dev/null \
    && say piper-tts installed || say piper-tts "pip install failed"
  mkdir -p "$PIPER_DIR"
  if ls "$PIPER_DIR"/*.onnx >/dev/null 2>&1; then
    say voice "already present in $PIPER_DIR"
  else
    say voice "downloading $PIPER_VOICE (~63 MB, once)"
    python3 -m piper.download_voices "$PIPER_VOICE" --download-dir "$PIPER_DIR" >/dev/null 2>&1 \
      && say voice "$PIPER_VOICE → $PIPER_DIR" || say voice "download failed — rerun with --piper"
  fi
fi

echo
echo "== verifying =="
for s in skill-router anon-image anon-audio ffmpeg-pipeline web-search fetch-page deep-research; do
  printf '%-16s ' "$s"
  case "$s" in
    skill-router)
      python3 "$DIR/skill-router/scripts/route.py" --list >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    anon-image)
      python3 "$DIR/anon-image/scripts/imgen.py" probe >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    anon-audio)
      python3 "$DIR/anon-audio/scripts/tts.py" probe >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    ffmpeg-pipeline)
      python3 "$DIR/ffmpeg-pipeline/scripts/vidkit.py" probe >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    web-search)
      python3 "$DIR/web-search/scripts/search.py" --list-providers >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    fetch-page)
      python3 "$DIR/fetch-page/scripts/fetch.py" --help >/dev/null 2>&1 && echo ok || echo "check manually" ;;
    deep-research)
      python3 "$DIR/deep-research/scripts/research.py" --help >/dev/null 2>&1 && echo ok || echo "check manually" ;;
  esac
done
echo
echo "next: python3 skill-router/scripts/route.py \"what you want to do\""
