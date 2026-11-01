#!/usr/bin/env python3
"""
imgen.py — free, anonymous AI image generation, editing and compositing.

No API key. No account. No browser (unless you ask for --engine vheer).

  imgen.py gen   "a glass lighthouse at dusk" -o out.jpg [--ratio 16:9] [--engine api|vheer]
  imgen.py edit  in.jpg "repaint it as an oil painting" -o out.jpg
  imgen.py batch prompts.txt --outdir shots/ [--parallel 4] [--ratio 1:1]
  imgen.py sheet out.jpg a.jpg b.jpg c.jpg [--labels "A|B|C"] [--cols 3]
  imgen.py compare out.jpg before.jpg after.jpg [--labels "BEFORE|AFTER"]
  imgen.py probe

Exit codes: 0 ok · 1 usage/dependency · 2 network/upstream failure
"""
import argparse
import base64
import io
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://freeapi.pro"
TTI = API + "/api/tti"
PTI = API + "/api/pti"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]
MAX_UPLOAD_MB = 3.5
TIMEOUT = 180


# --------------------------------------------------------------------------- io
def _req(url, data=None, headers=None, timeout=TIMEOUT, method=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    if data is not None and not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def post_json(url, payload, timeout=TIMEOUT):
    body = json.dumps(payload).encode()
    code, raw, _ = _req(url, body, {"Content-Type": "application/json"}, timeout)
    try:
        js = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        js = {"raw": raw[:300].decode("utf-8", "replace")}
    return code, js


def download(url, path, timeout=TIMEOUT):
    code, raw, hdrs = _req(url, timeout=timeout)
    if code != 200 or not raw:
        raise RuntimeError(f"download failed: HTTP {code} for {url[:90]}")
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "image" not in ctype and not raw[:3] in (b"\xff\xd8\xff", b"\x89PN"):
        raise RuntimeError(f"not an image ({ctype}) from {url[:90]}")
    with open(path, "wb") as f:
        f.write(raw)
    return path, len(raw)


def human(n):
    return f"{n/1024:.0f}K" if n < 1024 * 1024 else f"{n/1048576:.1f}M"


# ------------------------------------------------------------------- generation
def gen_one(prompt, out, ratio="1:1", retries=2):
    """One text-to-image call. Returns (path, bytes, seconds)."""
    if ratio not in RATIOS:
        raise SystemExit(f"bad --ratio {ratio}; pick one of {', '.join(RATIOS)}")
    t0 = time.time()
    last = ""
    for attempt in range(retries + 1):
        code, js = post_json(TTI, {"prompt": prompt, "ratio": ratio})
        url = js.get("imageUrl") or js.get("url")
        if code == 200 and url:
            p, n = download(url, out)
            return p, n, time.time() - t0
        last = f"HTTP {code} {json.dumps(js)[:160]}"
        if code in (400, 401, 403):
            break
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"generation failed: {last}")


def cmd_gen(a):
    path, n, secs = gen_one(a.prompt, a.out, a.ratio)
    print(f"saved {path} ({human(n)}, {secs:.0f}s, {a.ratio})")
    if a.json:
        print(json.dumps({"ok": True, "out": path, "bytes": n, "ratio": a.ratio,
                          "seconds": round(secs, 1), "engine": "api"}))


def cmd_batch(a):
    prompts = []
    with open(a.prompts, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                prompts.append(line)
    if not prompts:
        raise SystemExit("no prompts found")
    os.makedirs(a.outdir, exist_ok=True)
    print(f"[batch] {len(prompts)} prompt(s), {a.parallel} at a time", file=sys.stderr)

    def job(i_p):
        i, p = i_p
        out = os.path.join(a.outdir, f"{i:03d}.jpg")
        try:
            path, n, secs = gen_one(p, out, a.ratio)
            return i, True, f"{path} ({human(n)}, {secs:.0f}s)"
        except Exception as e:
            return i, False, str(e)[:160]

    ok = 0
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        futs = [ex.submit(job, (i, p)) for i, p in enumerate(prompts, 1)]
        for f in as_completed(futs):
            i, good, msg = f.result()
            ok += good
            print(f"[{'ok' if good else 'FAIL'}] {i}: {msg}", file=sys.stderr)
    print(f"batch: {ok}/{len(prompts)} succeeded -> {a.outdir}")
    return 0 if ok else 2


def cmd_edit(a):
    src = a.image
    if not os.path.exists(src):
        raise SystemExit(f"no such file: {src}")
    size_mb = os.path.getsize(src) / 1048576
    if size_mb > MAX_UPLOAD_MB:
        raise SystemExit(
            f"input is {size_mb:.1f}MB; the endpoint accepts {MAX_UPLOAD_MB}MB. "
            f"Resize first: ffmpeg -i {src} -vf scale=1536:-2 small.jpg")
    b64 = base64.b64encode(open(src, "rb").read()).decode()
    t0 = time.time()
    code, js = post_json(PTI, {"prompt": a.prompt, "ratio": "auto", "imageBase64": b64},
                         timeout=TIMEOUT)
    url = js.get("imageUrl") or js.get("url")
    if code != 200 or not url:
        raise SystemExit(f"edit failed: HTTP {code} {json.dumps(js)[:200]}")
    path, n = download(url, a.out)
    print(f"saved {path} ({human(n)}, {time.time()-t0:.0f}s)  [input {size_mb:.2f}MB]")
    if a.json:
        print(json.dumps({"ok": True, "out": path, "bytes": n, "source": src}))


# ----------------------------------------------------------------- compositing
def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(p):
            from PIL import ImageFont
            return ImageFont.truetype(p, size)
    from PIL import ImageFont
    return ImageFont.load_default()


def _tile(path, w):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    return im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)


def cmd_sheet(a):
    from PIL import Image, ImageDraw
    paths = a.images
    labels = a.labels.split("|") if a.labels else [os.path.basename(p) for p in paths]
    if len(labels) < len(paths):
        labels += [""] * (len(paths) - len(labels))
    cols = min(a.cols or len(paths), len(paths))
    pad, gap, cap = 24, 14, 46
    tw = a.width
    tiles = [_tile(p, tw) for p in paths]
    th = max(t.height for t in tiles)
    rows = (len(tiles) + cols - 1) // cols
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = pad * 2 + rows * (th + cap) + (rows - 1) * gap + (54 if a.title else 0)
    canvas = Image.new("RGB", (W, H), (16, 16, 18))
    d = ImageDraw.Draw(canvas)
    y0 = pad
    if a.title:
        d.text((pad, pad - 6), a.title, font=_font(30), fill=(245, 245, 245))
        y0 += 54
    f = _font(19)
    for i, (t, lab) in enumerate(zip(tiles, labels)):
        r, c = divmod(i, cols)
        x = pad + c * (tw + gap)
        y = y0 + r * (th + cap + gap)
        canvas.paste(t, (x, y + (th - t.height) // 2))
        d.rectangle([x - 1, y - 1, x + tw, y + th], outline=(64, 64, 72))
        if lab:
            d.text((x + 2, y + th + 10), lab, font=f, fill=(232, 232, 232))
    canvas.save(a.out, quality=90, optimize=True)
    print(f"saved {a.out} ({canvas.size[0]}x{canvas.size[1]}, "
          f"{human(os.path.getsize(a.out))}, {len(tiles)} tile(s))")


def cmd_compare(a):
    a.images = [a.before, a.after]
    if not a.labels:
        a.labels = "BEFORE|AFTER"
    a.cols = 2
    a.title = a.title or ""
    cmd_sheet(a)


def cmd_probe(a):
    print("== anon-image probe ==")
    t0 = time.time()
    try:
        path, n, secs = gen_one("a small red cube on white, product photo",
                                os.path.join(os.environ.get("TMPDIR", "/tmp"), "imgen_probe.jpg"))
        print(f"gen   /api/tti   OK   {human(n)} in {secs:.0f}s")
        out = None
    except Exception as e:
        print(f"gen   /api/tti   FAIL {e}")
        out = 2
    try:
        c, raw, _ = _req(API + "/api/voices", timeout=20)
        print(f"meta  /api/voices HTTP {c}")
    except Exception as e:
        print(f"meta  /api/voices FAIL {e}")
    return out or 0


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="free anonymous image generation + editing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="text -> image")
    g.add_argument("prompt")
    g.add_argument("-o", dest="out", default=None)
    g.add_argument("--ratio", default="1:1")
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_gen)

    b = sub.add_parser("batch", help="one prompt per line")
    b.add_argument("prompts")
    b.add_argument("--outdir", default="out")
    b.add_argument("--ratio", default="1:1")
    b.add_argument("--parallel", type=int, default=4)
    b.set_defaults(func=cmd_batch)

    e = sub.add_parser("edit", help="image + instruction -> image")
    e.add_argument("image")
    e.add_argument("prompt")
    e.add_argument("-o", dest="out", default=None)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_edit)

    s = sub.add_parser("sheet", help="contact sheet with labels")
    s.add_argument("out")
    s.add_argument("images", nargs="+")
    s.add_argument("--labels", default=None)
    s.add_argument("--cols", type=int, default=None)
    s.add_argument("--width", type=int, default=440)
    s.add_argument("--title", default=None)
    s.set_defaults(func=cmd_sheet)

    c = sub.add_parser("compare", help="two-up before/after")
    c.add_argument("out")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--labels", default=None)
    c.add_argument("--width", type=int, default=700)
    c.add_argument("--title", default=None)
    c.set_defaults(func=cmd_compare)

    p = sub.add_parser("probe", help="health check")
    p.set_defaults(func=cmd_probe)

    a = ap.parse_args()
    if getattr(a, "out", 1) is None:
        a.out = f"gen_{int(time.time())}.jpg"
    try:
        sys.exit(a.func(a) or 0)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
