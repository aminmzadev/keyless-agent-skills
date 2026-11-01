#!/usr/bin/env python3
"""
tts.py — free, anonymous text-to-speech with automatic engine fallback.

Engine A: Microsoft Edge neural voices via `edge-tts` (322 voices, 74 languages, best quality)
Engine B: Google Translate TTS via plain HTTP  (pure stdlib, 200-char chunks, always available)
Engine C: Piper neural TTS, fully offline      (opt-in: pip install piper-tts + one 63 MB voice)

  tts.py say "Hello there" -o out.mp3
  tts.py say --text-file script.txt -o out.mp3 --voice fa-IR-DilaraNeural
  tts.py say "text" -o out.mp3 --engine gtts --lang pl        # force pure-curl route
  tts.py say "text" -o out.mp3 --engine edge --rate -10% --pitch +0Hz
  tts.py voices --lang fa
  tts.py concat a.mp3 b.mp3 -o joined.mp3
  tts.py say "text" -o out.mp3 --engine piper        # no network at all
  tts.py fetch-voice en_US-amy-low                   # download a Piper voice (~63 MB)
  tts.py probe

Design notes (measured — see reference/rate-limits.md):
  * Piper is the offline leg: it needs no network once the voice file exists, so it is
    what you use on a plane, in an air-gapped container, or when both consumer
    endpoints decide to hate your IP. ~2.3 s for a sentence on this box (CPU only).
  * Google caps a request at exactly 200 characters; we split at 190 on sentence boundaries.
  * Google returns HTTP 404 if you send any Referer header, so we never do.
  * Edge fails with 403 on endpoint/token drift (needs edge-tts >= 7.3.1) and 429 on
    large chunked jobs. Both are handled by falling back to Google automatically.

Exit codes: 0 ok · 1 usage/dependency · 2 both engines failed
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

PIPER_DIR = os.environ.get("PIPER_DATA_DIR") or os.path.expanduser(
    "~/.cache/agent-skills/piper")
PIPER_DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "en_US-amy-low")
GTTS = "https://translate.google.com/translate_tts"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
GTTS_CHUNK = 190          # 200 is the hard cliff; leave room for the trailing word
GTTS_RETRY = 2
EDGE_MIN_VERSION = "7.3.1"

FALLBACK_VOICES = [
    ("en-US-AriaNeural", "Female", "en-US", "Aria"),
    ("en-US-GuyNeural", "Male", "en-US", "Guy"),
    ("en-US-JennyNeural", "Female", "en-US", "Jenny"),
    ("en-GB-RyanNeural", "Male", "en-GB", "Ryan"),
    ("en-GB-SoniaNeural", "Female", "en-GB", "Sonia"),
    ("pl-PL-ZofiaNeural", "Female", "pl-PL", "Zofia"),
    ("pl-PL-MarekNeural", "Male", "pl-PL", "Marek"),
    ("de-DE-KatjaNeural", "Female", "de-DE", "Katja"),
    ("fr-FR-DeniseNeural", "Female", "fr-FR", "Denise"),
    ("es-ES-ElviraNeural", "Female", "es-ES", "Elvira"),
    ("fa-IR-DilaraNeural", "Female", "fa-IR", "Dilara"),
    ("fa-IR-FaridNeural", "Male", "fa-IR", "Farid"),
    ("ar-SA-ZariyahNeural", "Female", "ar-SA", "Zariyah"),
    ("hi-IN-SwaraNeural", "Female", "hi-IN", "Swara"),
    ("ru-RU-SvetlanaNeural", "Female", "ru-RU", "Svetlana"),
    ("zh-CN-XiaoxiaoNeural", "Female", "zh-CN", "Xiaoxiao"),
    ("ja-JP-NanamiNeural", "Female", "ja-JP", "Nanami"),
    ("tr-TR-EmelNeural", "Female", "tr-TR", "Emel"),
]


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def have(x):
    return shutil.which(x) is not None


# ------------------------------------------------------------------ engine A
def edge_available():
    if not have("edge-tts"):
        return False, "edge-tts not installed (pip install edge-tts)"
    r = run(["edge-tts", "--version"])
    ver = (r.stdout + r.stderr).strip().split()[-1] if (r.stdout or r.stderr) else ""
    return True, ver


def edge_say(text, out, voice, rate, pitch, timeout=300):
    cmd = ["edge-tts", "--text", text, "--voice", voice,
           "--rate", rate, "--pitch", pitch, "--write-media", out]
    try:
        r = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("edge-tts timed out")
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(f"edge-tts failed: {err[-1][:140] if err else 'unknown'}")
    return out


def edge_voices(lang=None):
    if not have("edge-tts"):
        return [v for v in FALLBACK_VOICES if not lang or lang.lower() in v[2].lower()]
    r = run(["edge-tts", "--list-voices"], timeout=60)
    rows = []
    for line in r.stdout.splitlines()[2:]:
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 3 and "Neural" in parts[0]:
            rows.append((parts[0], parts[1], parts[0].split("-")[0] + "-" + parts[0].split("-")[1],
                         parts[0].split("-")[-1].replace("Neural", "")))
    if lang:
        rows = [x for x in rows if lang.lower() in x[0].lower()]
    return rows or FALLBACK_VOICES


# ------------------------------------------------------------------ engine B
def split_for_gtts(text, limit=GTTS_CHUNK):
    text = re.sub(r"\s+", " ", text).strip()
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?;:。！？])\s+", text):
        while len(s) > limit:
            if cur:
                out.append(cur)
                cur = ""
            cut = s.rfind(" ", 0, limit)
            cut = limit if cut < 60 else cut
            out.append(s[:cut])
            s = s[cut:].lstrip()
        if len(cur) + len(s) + 1 <= limit:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    return [c for c in out if c]


def gtts_chunk(text, lang, path, retries=GTTS_RETRY):
    qs = urllib.parse.urlencode({"ie": "UTF-8", "q": text, "tl": lang, "client": "tw-ob"})
    url = f"{GTTS}?{qs}"
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})  # no Referer, ever
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"ID") or len(raw) > 2000:
                with open(path, "wb") as f:
                    f.write(raw)
                return path
            last = f"non-audio body ({len(raw)}B)"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 404):
                raise RuntimeError(
                    "HTTP %d — check the language code '%s' (Google has no voice for "
                    "some languages, e.g. fa/vi) and keep chunks under 200 chars" % (e.code, lang))
        except Exception as e:
            last = str(e)[:100]
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"gtts chunk failed: {last}")


def concat_audio(paths, out):
    if have("ffmpeg") and len(paths) > 1:
        tmp = tempfile.mkdtemp()
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for p in paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        r = run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                 "-i", lst, "-c:a", "libmp3lame", "-b:a", "96k", out])
        if r.returncode == 0 and os.path.exists(out):
            shutil.rmtree(tmp, ignore_errors=True)
            return out
        log("[gtts] ffmpeg concat failed, falling back to byte concat")
        shutil.rmtree(tmp, ignore_errors=True)
    with open(out, "wb") as w:
        for p in paths:
            with open(p, "rb") as r:
                shutil.copyfileobj(r, w)
    return out


def gtts_say(text, out, lang="en"):
    chunks = split_for_gtts(text)
    tmp = tempfile.mkdtemp()
    parts = []
    log(f"[gtts] {len(chunks)} chunk(s), lang={lang}")
    for i, c in enumerate(chunks, 1):
        p = os.path.join(tmp, f"{i:03d}.mp3")
        gtts_chunk(c, lang, p)
        parts.append(p)
    concat_audio(parts, out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


# --------------------------------------------------------------------- piper
def piper_models():
    """Voices present on disk: [(name, path)]. Empty list means 'not installed'."""
    if not os.path.isdir(PIPER_DIR):
        return []
    out = []
    for f in sorted(os.listdir(PIPER_DIR)):
        if f.endswith(".onnx"):
            out.append((f[:-5], os.path.join(PIPER_DIR, f)))
    return out


def piper_available():
    """Two things must be true: the package imports, and a voice is on disk."""
    try:
        import piper  # noqa: F401
    except Exception as e:
        return False, f"piper not installed (pip install piper-tts): {str(e)[:80]}"
    models = piper_models()
    if not models:
        return False, (f"no Piper voice in {PIPER_DIR} — run: "
                       f"tts.py fetch-voice {PIPER_DEFAULT_VOICE}")
    return True, f"{len(models)} voice(s)"


def piper_say(text, out, voice=None, timeout=600):
    """Synthesise with Piper, offline. Writes a temp WAV then converts to the
    requested container with ffmpeg; without ffmpeg it keeps .wav."""
    voice = voice or PIPER_DEFAULT_VOICE
    models = dict(piper_models())
    model_path = models.get(voice)
    if not model_path:
        if not models:
            raise RuntimeError(f"no Piper voice in {PIPER_DIR}; "
                               f"run: tts.py fetch-voice {voice}")
        # tolerate a bare 'amy' or a different quality tag
        cand = [p for n, p in models.items() if voice.split("-")[0] in n]
        if not cand:
            raise RuntimeError(f"voice '{voice}' not found; have: {', '.join(models)}")
        model_path = cand[0]

    from piper import PiperVoice
    import wave
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        t0 = time.time()
        v = PiperVoice.load(model_path)
        with wave.open(wav, "wb") as wf:
            v.synthesize_wav(text, wf)
        if time.time() - t0 > timeout:
            raise RuntimeError("piper timed out")
        if out.lower().endswith(".wav") or not have("ffmpeg"):
            if not out.lower().endswith(".wav"):
                out = os.path.splitext(out)[0] + ".wav"
            shutil.move(wav, out)
            wav = None
        else:
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                 "-codec:a", "libmp3lame", "-q:a", "4", out], check=True)
        return out          # may differ from the request when ffmpeg is absent
    finally:
        if wav and os.path.exists(wav):
            os.unlink(wav)


def cmd_fetch_voice(a):
    """Download a Piper voice into the shared cache. Network needed once."""
    os.makedirs(PIPER_DIR, exist_ok=True)
    cmd = [sys.executable, "-m", "piper.download_voices", a.voice,
           "--download-dir", PIPER_DIR]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("download failed — is piper-tts installed?\n"
                         + (r.stderr or r.stdout)[-600:])
    models = piper_models()
    print(f"{len(models)} voice(s) in {PIPER_DIR}")
    for name, path in models:
        print(f"  {name}\t{os.path.getsize(path)//1024//1024} MB")


# ---------------------------------------------------------------------- cmds
def dur(path):
    if not have("ffprobe"):
        return None
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def cmd_say(a):
    text = a.text or ""
    if a.text_file:
        with open(a.text_file, encoding="utf-8") as f:
            text = f.read()
    text = text.strip()
    if not text:
        raise SystemExit("nothing to say: pass text or --text-file")
    out = a.out or f"speech_{int(time.time())}.mp3"
    t0 = time.time()
    used = None
    errors = []

    if a.engine == "auto":
        engines = ["edge", "gtts"] + (["piper"] if piper_available()[0] else [])
    elif a.engine == "offline":
        engines = ["piper"]
    else:
        engines = [a.engine]
    for eng in engines:
        try:
            if eng == "edge":
                ok, ver = edge_available()
                if not ok:
                    raise RuntimeError(ver)
                edge_say(text, out, a.voice, a.rate, a.pitch)
            elif eng == "piper":
                ok, why = piper_available()
                if not ok:
                    raise RuntimeError(why)
                out = piper_say(text, out, a.piper_voice) or out
            else:
                gtts_say(text, out, a.lang)
            used = eng
            break
        except Exception as e:
            errors.append(f"{eng}: {e}")
            log(f"[{eng}] failed → {e}")

    if not used:
        raise SystemExit("all engines failed:\n  " + "\n  ".join(errors))

    secs = time.time() - t0
    size = os.path.getsize(out)
    d = dur(out)
    extra = f", {d:.1f}s audio" if d else ""
    print(f"saved {out} ({size//1024}K, engine={used}, {secs:.1f}s{extra})")
    if a.json:
        print(json.dumps({"ok": True, "out": out, "engine": used, "bytes": size,
                          "duration": round(d, 2) if d else None,
                          "seconds": round(secs, 2), "chars": len(text)}))


def cmd_voices(a):
    if not a.offline:
        rows = edge_voices(a.lang)
        for r in rows[:a.limit]:
            print("\t".join(r))
        print(f"# {len(rows)} edge voice(s)" + (f" matching '{a.lang}'" if a.lang else ""))
    models = piper_models()
    if a.offline or models:
        print(f"# {len(models)} piper voice(s) in {PIPER_DIR}")
    for name, path in models:
        print(f"piper\t{name}\t{path}")


def cmd_concat(a):
    missing = [p for p in a.files if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"missing: {missing}")
    concat_audio(a.files, a.out)
    d = dur(a.out)
    print(f"saved {a.out} ({os.path.getsize(a.out)//1024}K"
          + (f", {d:.1f}s" if d else "") + f", {len(a.files)} file(s))")


def cmd_probe(a):
    status = 0
    ok, ver = edge_available()
    if ok:
        try:
            t0 = time.time()
            edge_say("probe", os.path.join(tempfile.gettempdir(), "edge_probe.mp3"),
                     "en-US-AriaNeural", "+0%", "+0Hz", timeout=90)
            print(f"edge-tts        OK   v{ver}  {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"edge-tts        FAIL v{ver}  {e}")
            status = 2
    else:
        print(f"edge-tts        MISSING  {ver}")
    okp, whyp = piper_available()
    if okp:
        try:
            t0 = time.time()
            p = piper_say("probe", os.path.join(tempfile.gettempdir(), "piper_probe.mp3"))
            print(f"piper (offline) OK   {os.path.getsize(p)//1024}K in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"piper (offline) FAIL {e}")
            status = 2
    else:
        print(f"piper (offline) MISSING  {whyp}")
    for lang in ("en",):
        try:
            t0 = time.time()
            p = os.path.join(tempfile.gettempdir(), "gtts_probe.mp3")
            gtts_chunk("probe", lang, p)
            print(f"gtts ({lang})      OK   {os.path.getsize(p)//1024}K in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"gtts ({lang})      FAIL {e}")
            status = 2
    print(f"ffmpeg          {'OK' if have('ffmpeg') else 'MISSING (needed for long text / concat)'}")
    return status


def main():
    ap = argparse.ArgumentParser(description="free anonymous text-to-speech")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("say", help="text -> mp3")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--text-file", default=None)
    s.add_argument("-o", dest="out", default=None)
    s.add_argument("--voice", default="en-US-AriaNeural")
    s.add_argument("--rate", default="+0%")
    s.add_argument("--pitch", default="+0Hz")
    s.add_argument("--engine", choices=["auto", "edge", "gtts", "piper", "offline"],
                   default="auto", help="offline = piper only, no network")
    s.add_argument("--piper-voice", default=PIPER_DEFAULT_VOICE)
    s.add_argument("--lang", default="en", help="language for the gtts engine")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_say)

    v = sub.add_parser("voices", help="list edge voices")
    v.add_argument("--lang", default=None)
    v.add_argument("--limit", type=int, default=60)
    v.add_argument("--offline", action="store_true", help="piper voices only")
    v.set_defaults(func=cmd_voices)

    c = sub.add_parser("concat", help="join audio files")
    c.add_argument("files", nargs="+")
    c.add_argument("-o", dest="out", default="joined.mp3")
    c.set_defaults(func=cmd_concat)

    fv = sub.add_parser("fetch-voice", help="download a Piper voice (offline TTS)")
    fv.add_argument("voice", nargs="?", default=PIPER_DEFAULT_VOICE)
    fv.set_defaults(func=cmd_fetch_voice)

    p = sub.add_parser("probe", help="health check")
    p.set_defaults(func=cmd_probe)

    a = ap.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
