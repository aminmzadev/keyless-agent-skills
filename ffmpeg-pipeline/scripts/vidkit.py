#!/usr/bin/env python3
"""
vidkit.py — the ffmpeg half of the pack: turn stills into video, and do the
boring media surgery that every agent ends up needing.

  vidkit.py video "a glass lighthouse at dusk" -o out.mp4 --shots 3 --seconds 4
  vidkit.py video --images shots/ -o out.mp4 --narration "voiceover.txt" --music bed.mp3
  vidkit.py slideshow a.jpg b.jpg c.jpg -o out.mp4 --seconds 3 --transition fade
  vidkit.py mux video.mp4 voice.mp3 -o final.mp4
  vidkit.py frames in.mp4 --out frames/ --every 2
  vidkit.py sheet in.mp4 -o sheet.jpg --cols 4 --count 8
  vidkit.py gif in.mp4 -o out.gif --start 1 --dur 3 --width 480
  vidkit.py normalize voice.mp3 -o clean.mp3
  vidkit.py speed voice.mp3 -o fast.mp3 --factor 1.2
  vidkit.py info file.mp4
  vidkit.py probe

Requires: ffmpeg + ffprobe on PATH. Image generation (for `video`) additionally
needs anon-image/scripts/imgen.py — it is auto-discovered next to this skill.

Exit codes: 0 ok · 1 usage/dependency · 2 ffmpeg failure
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RATIOS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960),
          "4:3": (1024, 768), "3:4": (768, 1024)}
FPS = 25
FADE = 0.8


def log(m):
    print(m, file=sys.stderr)


def at_least(text, minimum):
    """argparse type: a half of these numbers makes ffmpeg emit a 0-frame file."""
    n = float(text)
    if n < minimum:
        import argparse
        raise argparse.ArgumentTypeError(f"must be {minimum} or more (got {text})")
    return n


def need(binary):
    if not shutil.which(binary):
        raise SystemExit(f"missing dependency: {binary}"
                         + (" (apt install ffmpeg)" if binary.startswith("ffmpeg") else ""))


# Every ffmpeg call gets a wall clock. An agent has no terminal to notice a
# hung encoder, and a still-image zoompan on a 12k photo can run for hours.
TIMEOUT = int(os.environ.get("VIDKIT_FFMPEG_TIMEOUT") or 900)


def run(cmd, ok_codes=(0,), timeout=TIMEOUT):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        # the documented failure mode, in one place: `info` and `frames` call
        # ffprobe/ffmpeg without a need() check, and a bare FileNotFoundError
        # traceback is not "missing dependency: ffmpeg"
        raise SystemExit(f"missing dependency: {cmd[0]}"
                         + (" (apt install ffmpeg)"
                            if str(cmd[0]) in ("ffmpeg", "ffprobe") else ""))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd[0]} gave up after {timeout}s")
    if r.returncode not in ok_codes:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(f"{cmd[0]} failed: {tail[-1][:300] if tail else 'unknown'}")
    return r


def duration(path):
    """Real seconds, or a hard error.

    This used to return 0.0 on anything ffprobe could not parse. Every caller
    then compared that 0 against the other track's length and took the "audio is
    shorter" branch, so a corrupt mp3 was silently replaced by silence and the
    command exited 0 with a video that had no sound in it. A number nobody can
    measure is not a default, it is a failure.
    """
    need("ffprobe")
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    try:
        secs = float((r.stdout or "").strip())
    except ValueError:
        secs = 0.0
    if not secs > 0:
        raise RuntimeError(f"ffprobe reports no duration for {path} — is it a real media file?")
    return secs


def human(n):
    return f"{n/1024:.0f}K" if n < 1048576 else f"{n/1048576:.1f}M"


def find_imgen():
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../../anon-image/scripts/imgen.py", "../anon-image/scripts/imgen.py",
                "../../../anon-image/scripts/imgen.py"):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------- pieces
def fit_filter(w, h):
    """scale to cover, then crop — no letterboxing, ever."""
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1")


def motion_clip(img, out, w, h, secs, zoom=0.0015, direction="in"):
    """One still -> one moving clip (slow push-in or pull-out) at FPS."""
    d = max(1, int(secs * FPS))
    if direction == "out":
        z = f"if(eq(on,0),1.18,max(zoom-{zoom},1.0))"
    else:
        z = f"min(zoom+{zoom},1.18)"
    vf = (f"scale={w*2}:-2,zoompan=z='{z}':d={d}"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", img, "-t", f"{secs}",
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", out])
    return out


def xfade_chain(clips, out, secs, fade=FADE):
    """Crossfade N clips into one file."""
    if len(clips) == 1:
        shutil.copy(clips[0], out)
        return out
    parts, off, prev = [], secs - fade, "[0:v]"
    for i in range(1, len(clips)):
        parts.append(f"{prev}[{i}:v]xfade=transition=fade:duration={fade:.2f}"
                     f":offset={off:.3f}[v{i}]")
        prev, off = f"[v{i}]", off + secs - fade
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-filter_complex", ";".join(parts), "-map", prev,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", out]
    run(cmd)
    return out


# ---------------------------------------------------------------------- video
def cmd_video(a):
    need("ffmpeg")
    w, h = RATIOS.get(a.ratio, RATIOS["16:9"])
    tmp = tempfile.mkdtemp()
    try:
        # ---- images: from --images, or generate them
        imgs = []
        if a.images:
            if os.path.isdir(a.images):
                imgs = sorted(glob.glob(os.path.join(a.images, "*.jpg")) +
                              glob.glob(os.path.join(a.images, "*.png")))
            elif "," in a.images:
                imgs = [x.strip() for x in a.images.split(",")]
            else:
                imgs = [a.images]
            imgs = [i for i in imgs if os.path.exists(i)]
            if not imgs:
                raise SystemExit(f"no usable images in {a.images}")
        else:
            if not a.prompt:
                raise SystemExit("give a prompt, or --images <dir|file[,file]>")
            imgen = find_imgen()
            if not imgen:
                raise SystemExit("prompt mode needs anon-image/scripts/imgen.py next to this skill; "
                                 "or pass --images with your own pictures")
            for i in range(1, a.shots + 1):
                p = os.path.join(tmp, f"shot_{i}.jpg")
                log(f"[video] image {i}/{a.shots} …")
                pr = a.prompt if a.shots == 1 else f"{a.prompt}, shot {i} of {a.shots}, distinct cinematic angle"
                run([sys.executable, imgen, "gen", pr, "-o", p, "--ratio", a.ratio])
                imgs.append(p)

        # ---- motion
        clips = []
        for i, img in enumerate(imgs, 1):
            c = os.path.join(tmp, f"c_{i}.mp4")
            direction = "out" if a.kenburns == "alt" and i % 2 == 0 else "in"
            motion_clip(img, c, w, h, a.seconds, direction=direction)
            clips.append(c)
        silent = os.path.join(tmp, "silent.mp4")
        xfade_chain(clips, silent, a.seconds)

        # ---- audio bed
        narr = a.narration
        if narr and os.path.exists(narr) and os.path.getsize(narr) < 4096:
            with open(narr, encoding="utf-8") as fh:            # short file = text
                narr = fh.read().strip()
        voice_file = a.audio
        if narr and not voice_file:
            tts = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "../../anon-audio/scripts/tts.py"))
            if not os.path.exists(tts):
                raise SystemExit("--narration needs anon-audio/scripts/tts.py, or pass --audio file.mp3")
            voice_file = os.path.join(tmp, "voice.mp3")
            log("[video] synthesising narration …")
            run([sys.executable, tts, "say", narr, "-o", voice_file, "--voice", a.voice])
        elif narr and not voice_file:
            voice_file = narr   # a path that exists and is big enough → treat as audio

        if voice_file:
            if not os.path.exists(voice_file):
                raise SystemExit(f"no such audio: {voice_file}")
            vid_d, aud_d = duration(silent), duration(voice_file)
            loops = max(1, int(-(-aud_d // max(vid_d, 0.1))) + 1) if aud_d > vid_d else 1
            cmd = ["ffmpeg", "-v", "error", "-y"]
            if loops > 1:
                cmd += ["-stream_loop", str(loops - 1)]
            cmd += ["-i", silent, "-i", voice_file]
            audio_pad = aud_d < vid_d   # voice shorter than picture → pad with silence
            pad = "apad," if audio_pad else ""
            if a.music and os.path.exists(a.music):
                cmd += ["-i", a.music]
                fc = (f"[1:a]{pad}volume=1.0[v];[2:a]volume=0.12,aloop=loop=-1:size=2e9[m];"
                      "[v][m]amix=inputs=2:duration=first:dropout_transition=0[a]")
                cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
            elif audio_pad:
                cmd += ["-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]"]
            else:
                cmd += ["-map", "0:v", "-map", "1:a"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-shortest", a.out]
            run(cmd)
        else:
            shutil.copy(silent, a.out)

        d = duration(a.out)
        print(f"saved {a.out} ({human(os.path.getsize(a.out))}, {d:.1f}s, "
              f"{w}x{h}, {len(imgs)} shot(s){', narrated' if voice_file else ''})")
        if a.json:
            print(json.dumps({"ok": True, "out": a.out, "duration": round(d, 2),
                              "width": w, "height": h, "shots": len(imgs),
                              "narrated": bool(voice_file)}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_slideshow(a):
    a.prompt, a.images, a.shots = None, ",".join(a.images), len(a.images)
    a.narration = getattr(a, "narration", None)
    a.voice = getattr(a, "voice", "en-US-AriaNeural")
    a.music = getattr(a, "music", None)
    a.kenburns = "in"
    a.json = getattr(a, "json", False)
    return cmd_video(a)


def cmd_mux(a):
    need("ffmpeg")
    vd, ad = duration(a.video), duration(a.audio)
    loops = max(1, int(-(-ad // max(vd, 0.1))) + 1) if ad > vd else 1
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if loops > 1:
        cmd += ["-stream_loop", str(loops - 1)]
    cmd += ["-i", a.video, "-i", a.audio]
    if ad < vd:     # voice shorter than picture: pad with silence, keep full video
        cmd += ["-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-shortest", a.out]
    run(cmd)
    print(f"saved {a.out} ({human(os.path.getsize(a.out))}, {duration(a.out):.1f}s)")


def cmd_frames(a):
    need("ffmpeg")
    os.makedirs(a.out, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", a.video,
         "-vf", f"fps=1/{a.every}", os.path.join(a.out, "frame_%03d.jpg")])
    n = len(glob.glob(os.path.join(a.out, "frame_*.jpg")))
    print(f"wrote {n} frame(s) to {a.out}/")


def cmd_sheet(a):
    need("ffmpeg")
    tmp = tempfile.mkdtemp()
    try:
        d = duration(a.video)
        n = a.count
        stamps = [round(d * (i + 0.5) / n, 2) for i in range(n)]
        files = []
        for i, t in enumerate(stamps, 1):
            p = os.path.join(tmp, f"f{i:02d}.jpg")
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", a.video,
                 "-frames:v", "1", "-vf", "scale=480:-2", p])
            files.append(p)
        from_pil(a, files, stamps)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def from_pil(a, files, stamps):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit("contact sheet needs Pillow (pip install pillow)")
    cols = a.cols
    rows = (len(files) + cols - 1) // cols
    tiles = [Image.open(p).convert("RGB") for p in files]
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    pad, gap, cap = 20, 10, 28
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = pad * 2 + rows * (th + cap) + (rows - 1) * gap
    c = Image.new("RGB", (W, H), (14, 14, 16))
    d = ImageDraw.Draw(c)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except Exception:
        f = ImageFont.load_default()
    for i, (t, ts) in enumerate(zip(tiles, stamps)):
        r, col = divmod(i, cols)
        x, y = pad + col * (tw + gap), pad + r * (th + cap + gap)
        c.paste(t, (x, y))
        d.text((x + 2, y + th + 6), f"t={ts}s", font=f, fill=(230, 230, 230))
    c.save(a.out, quality=88)
    print(f"saved {a.out} ({W}x{H}, {len(files)} tiles)")


def cmd_gif(a):
    need("ffmpeg")
    vf = (f"fps={a.fps},scale={a.width}:-1:flags=lanczos,"
          f"split[a][b];[a]palettegen[p];[b][p]paletteuse")
    run(["ffmpeg", "-v", "error", "-y", "-ss", str(a.start), "-t", str(a.dur),
         "-i", a.video, "-vf", vf, "-loop", "0", a.out])
    print(f"saved {a.out} ({human(os.path.getsize(a.out))})")


def cmd_normalize(a):
    need("ffmpeg")
    run(["ffmpeg", "-v", "error", "-y", "-i", a.audio,
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "libmp3lame",
         "-b:a", "128k", a.out])
    print(f"saved {a.out} ({human(os.path.getsize(a.out))}, normalised to -16 LUFS)")


def cmd_speed(a):
    need("ffmpeg")
    f = a.factor
    filters, x = [], f
    while x > 2.0:                      # atempo accepts 0.5–2.0 per stage
        filters.append("atempo=2.0")
        x /= 2.0
    while x < 0.5:
        filters.append("atempo=0.5")
        x /= 0.5
    filters.append(f"atempo={x:.4f}")
    run(["ffmpeg", "-v", "error", "-y", "-i", a.audio, "-filter:a", ",".join(filters),
         "-c:a", "libmp3lame", "-b:a", "128k", a.out])
    print(f"saved {a.out} ({duration(a.out):.1f}s, {a.factor}x)")


def cmd_info(a):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate"
             ":format=duration,size,bit_rate", "-of", "json", a.file])
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    print(f"{a.file}: {human(int(fmt.get('size', 0)) or 0)}, "
          f"{float(fmt.get('duration', 0) or 0):.2f}s, "
          f"{int(fmt.get('bit_rate', 0) or 0)//1000} kbps")
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            print(f"  video: {s.get('codec_name')} {s.get('width')}x{s.get('height')} "
                  f"@ {s.get('r_frame_rate')}")
        else:
            print(f"  audio: {s.get('codec_name')} {s.get('sample_rate')}Hz "
                  f"{s.get('channels')}ch")


def cmd_probe(a):
    st = 0
    for b in ("ffmpeg", "ffprobe"):
        p = shutil.which(b)
        print(f"{b:<8}{'OK   ' + p if p else 'MISSING (apt install ffmpeg)'}")
        st = st or (0 if p else 2)
    try:
        import PIL
        print(f"pillow  OK   {PIL.__version__}")
    except ImportError:
        print("pillow  MISSING (pip install pillow) — only needed for contact sheets")
    print(f"imgen   {find_imgen() or 'not found (prompt mode disabled)'}")
    return st


def main():
    ap = argparse.ArgumentParser(description="ffmpeg pipeline for agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("video", help="stills (generated or given) -> moving video")
    v.add_argument("prompt", nargs="?", default=None)
    v.add_argument("-o", dest="out", default="out.mp4")
    v.add_argument("--images", default=None)
    v.add_argument("--shots", type=lambda t: int(at_least(t, 1)), default=3)
    v.add_argument("--seconds", type=lambda t: at_least(t, 0.1), default=4)
    v.add_argument("--ratio", default="16:9", choices=list(RATIOS))
    v.add_argument("--narration", default=None, help="text, or a file path")
    v.add_argument("--audio", default=None, help="existing narration audio file")
    v.add_argument("--voice", default="en-US-AriaNeural")
    v.add_argument("--music", default=None, help="background bed, mixed at 12%")
    v.add_argument("--kenburns", default="in", choices=["in", "alt"])
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_video)

    s = sub.add_parser("slideshow", help="existing images -> video")
    s.add_argument("images", nargs="+")
    s.add_argument("-o", dest="out", default="out.mp4")
    s.add_argument("--seconds", type=lambda t: at_least(t, 0.1), default=3)
    s.add_argument("--ratio", default="16:9", choices=list(RATIOS))
    s.add_argument("--narration", default=None)
    s.add_argument("--audio", default=None)
    s.add_argument("--voice", default="en-US-AriaNeural")
    s.add_argument("--music", default=None)
    s.set_defaults(func=cmd_slideshow)

    m = sub.add_parser("mux", help="attach audio to video")
    m.add_argument("video")
    m.add_argument("audio")
    m.add_argument("-o", dest="out", default="muxed.mp4")
    m.set_defaults(func=cmd_mux)

    f = sub.add_parser("frames", help="extract frames")
    f.add_argument("video")
    f.add_argument("--out", default="frames")
    f.add_argument("--every", type=lambda t: at_least(t, 0.01), default=1.0)
    f.set_defaults(func=cmd_frames)

    sh = sub.add_parser("sheet", help="contact sheet from a video")
    sh.add_argument("video")
    sh.add_argument("-o", dest="out", default="sheet.jpg")
    sh.add_argument("--cols", type=lambda t: int(at_least(t, 1)), default=4)
    sh.add_argument("--count", type=lambda t: int(at_least(t, 1)), default=8)
    sh.set_defaults(func=cmd_sheet)

    g = sub.add_parser("gif", help="video -> gif")
    g.add_argument("video")
    g.add_argument("-o", dest="out", default="out.gif")
    g.add_argument("--start", type=float, default=0)
    g.add_argument("--dur", type=lambda t: at_least(t, 0.1), default=3)
    g.add_argument("--width", type=lambda t: int(at_least(t, 16)), default=480)
    g.add_argument("--fps", type=lambda t: int(at_least(t, 1)), default=12)
    g.set_defaults(func=cmd_gif)

    n = sub.add_parser("normalize", help="loudness-normalise audio to -16 LUFS")
    n.add_argument("audio")
    n.add_argument("-o", dest="out", default="normalized.mp3")
    n.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("speed", help="change audio speed without pitch shift")
    sp.add_argument("audio")
    sp.add_argument("-o", dest="out", default="sped.mp3")
    sp.add_argument("--factor", type=lambda t: at_least(t, 0.01), default=1.2)
    sp.set_defaults(func=cmd_speed)

    i = sub.add_parser("info", help="ffprobe summary")
    i.add_argument("file")
    i.set_defaults(func=cmd_info)

    p = sub.add_parser("probe", help="health check")
    p.set_defaults(func=cmd_probe)

    a = ap.parse_args()
    try:
        sys.exit(a.func(a) or 0)
    except RuntimeError as e:
        log(f"error: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
