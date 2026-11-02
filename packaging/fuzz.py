#!/usr/bin/env python3
"""fuzz.py — hammer the pure parsers with hostile values, then check what broke.

This is not a unit test: nothing here asserts a particular output. It asserts
that a parser survives input it did not choose. The rule it enforces:

    a string that comes from a web page, a search API or a filename
    may contain anything, and must reach the caller as text, as a clean
    refusal, or as a note in `errors` — never as an unhandled exception,
    and never as a broken document.

Every case feeds *structurally valid* inputs with hostile *values*, which is the
shape of the real threat: the dicts these functions consume are built by our own
code, but their strings come from strangers.

    python3 packaging/fuzz.py            # 60 rounds per case
    python3 packaging/fuzz.py 300        # longer
    python3 packaging/fuzz.py --seed 7

Exit codes: 0 clean · 1 something escaped.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import random
import string
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# a fuzz run must never touch the real cache or the network
os.environ.setdefault("AGENT_SKILLS_CACHE", os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "agent-skills-fuzz"))
os.environ["FETCH_VENDOR"] = "0"

NASTY = ("\x00\x01\x02\x07\x08\x0b\x0c\x1b\r\n\t "        # control chars, incl. NUL and CRLF
         "\\\"'`|/<>[]{}()#$%&*+=~^:;,.-_"              # markdown/YAML/shell metacharacters
         "\u200b\u2028\u2029\ufeff\u00a0"                # invisible + line separators
         "\u00e9\u00df\u00e6\u0141\u0e01\u4e2d\U0001f600"  # accents, CJK, emoji
         "0123456789" + string.ascii_letters)


def load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def junk(rng, n=None, alphabet=NASTY):
    return "".join(rng.choice(alphabet) for _ in range(n if n is not None else rng.randint(0, 300)))


def junk_url(rng):
    return rng.choice([
        "https://e.example/" + junk(rng, rng.randint(0, 40), string.printable),
        "http://" + junk(rng, 10, string.ascii_lowercase) + ".example/x?y=1#z",
        "https://e.example/" + "a" * rng.choice([1, 500, 4000]),
        "file:///etc/passwd", "ftp://x/y", "data:text/html,<b>x", "//x/y", "", "   ",
        "https://user:pw@e.example:8443/a", "HTTPS://E.EXAMPLE",
        "https://e.example/\u4e2d\u6587?\u00e9=1",
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description="fuzz the pack's parsers")
    ap.add_argument("rounds", nargs="?", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)

    F = load("fuzz_fetch", "deep-research/scripts/fetch.py")
    S = load("fuzz_search", "deep-research/scripts/search.py")
    R = load("fuzz_research", "deep-research/scripts/research.py")
    I = load("fuzz_imgen", "anon-image/scripts/imgen.py")
    RO = load("fuzz_route", "skill-router/scripts/route.py")

    def rows(rng):
        return [S.item(junk(rng, 60), junk_url(rng), junk(rng, 200), junk(rng, 8),
                       rng.randint(0, 5)) for _ in range(rng.randint(0, 4))]

    def doc(rng):
        return {"url": junk_url(rng), "title": junk(rng, rng.randint(0, 120)),
                "text": junk(rng, rng.randint(0, 900)), "chars": rng.choice([0, 5, 10**6]),
                "source": junk(rng, 12), "hop": rng.choice([1, 2]),
                "search_title": junk(rng, 40)}

    def res(rng):
        return {"question": junk(rng, 60), "queries": [junk(rng, 20) for _ in range(rng.randint(0, 3))],
                "sources": [doc(rng) for _ in range(rng.randint(0, 3))],
                "summaries": {junk_url(rng): [junk(rng, 80)] for _ in range(rng.randint(0, 2))},
                "numbers": [(junk(rng, 8), rng.randint(0, 3)) for _ in range(rng.randint(0, 3))],
                "failures": [{"url": junk_url(rng), "errors": [junk(rng, 30)]}
                             for _ in range(rng.randint(0, 2))],
                "errors": {junk(rng, 8): junk(rng, 40)} if rng.random() < .5 else {},
                "followed": [{"url": junk_url(rng), "anchor": junk(rng, 30),
                              "from": junk_url(rng), "chars": 10} for _ in range(rng.randint(0, 2))],
                "_json_path": junk(rng, 20), "group": junk(rng, 8),
                "providers": [junk(rng, 8)]}

    cases = [
        # ---- fetch.py
        ("check_url", F.check_url, lambda: junk_url(rng)),
        ("redact", F.redact, lambda: junk_url(rng)),
        ("yaml_scalar", F.yaml_scalar, lambda: junk(rng)),
        ("to_markdown", F.to_markdown, lambda: doc(rng)),
        ("media_kind", F.media_kind, lambda: (junk(rng, 16).encode("utf-8", "ignore")[:16], junk(rng, 20))),
        ("_media_name", F._media_name, lambda: (rng.randint(-5, 2000), junk_url(rng),
                                                rng.choice([".jpg", ".mp4", "", ".x" * 20]))),
        ("media_kind html", F.media_kind, lambda: (b"<html>nope", "text/html")),
        # bytes that are not gzip are an OSError: that is the right exception class
        # for a decode failure, and _open() already catches it
        ("_gunzip_capped", lambda b: F._gunzip_capped(b, cap=4096),
         lambda: junk(rng, rng.randint(0, 400)).encode("utf-8", "ignore"),
         (SystemExit, ValueError, OSError)),
        ("pdf_extract_stdlib", F.pdf_extract_stdlib,
         lambda: junk(rng, rng.randint(0, 2000)).encode("utf-8", "ignore"),
         (SystemExit, ValueError, OSError)),
        ("wall_reason", F.wall_reason, lambda: junk(rng, rng.randint(0, 1500))),
        ("reddit_target", F.reddit_target, lambda: junk_url(rng)),
        ("x_target", F.x_target, lambda: junk_url(rng)),
        ("order_rewrites", F.order_rewrites,
         lambda: [(junk(rng, 10), junk_url(rng), {}) for _ in range(rng.randint(0, 4))]),
        ("cache_path", F.cache_path, lambda: junk(rng, 100)),
        # ---- search.py
        ("normalize", S.normalize,
         lambda: ([{"title": junk(rng, 60), "url": junk_url(rng), "snippet": junk(rng, 200),
                    "provider": junk(rng, 8)} for _ in range(rng.randint(0, 3))]
                  + [None, "bare", 42][: rng.randint(0, 3)], junk(rng, 8))),
        # rows are built with the documented constructor: the shape is guaranteed,
        # the *values* are hostile, which is the shape of the real threat
        ("dedupe", S.dedupe, lambda: rows(rng)),
        ("rank", S.rank, lambda: (rows(rng), junk(rng, 40))),
        ("trim", S.trim, lambda: (rows(rng), rng.choice([1, 3, 10]), 2)),
        ("item", S.item, lambda: (junk(rng, 30), junk_url(rng), junk(rng, 30), junk(rng, 6))),
        ("norm_url", S.norm_url, lambda: junk_url(rng)),
        # ---- research.py
        ("expand", R.expand, lambda: (junk(rng, 60), rng.choice([1, 2, 3]))),
        ("terms_of", R.terms_of, lambda: junk(rng, 500)),
        ("split_sentences", R.split_sentences, lambda: junk(rng, 2000)),
        ("cell", R.cell, lambda: (junk(rng, 200), rng.choice([5, 40, 70]))),
        ("relevant", R.relevant, lambda: (doc(rng), junk(rng, 40))),
        ("summarise", R.summarise,
         lambda: ([doc(rng) for _ in range(rng.randint(0, 3))], junk(rng, 40))),
        ("numbers", R.numbers, lambda: [doc(rng) for _ in range(rng.randint(0, 3))]),
        ("report", R.to_markdown, lambda: res(rng)),
        # ---- imgen / route
        ("imgen check_out", I.check_out,
         lambda: rng.choice(["/tmp", "/dev/null", "", "/tmp/fuzz-" + junk(rng, 6, string.ascii_lowercase) + ".jpg"])),
        ("route tokens", RO.tokens, lambda: junk(rng, 300)),
        ("route phrase_hit", RO.phrase_hit, lambda: (junk(rng, 60), junk(rng, 80))),
        ("route is_meta", RO.is_meta, lambda: junk(rng, 90)),
        ("route frontmatter", RO.frontmatter, lambda: junk(rng, 400)),
    ]

    failures = []
    for case in cases:
        name, fn, make = case[:3]
        refusals = case[3] if len(case) > 3 else (SystemExit, ValueError)
        for _ in range(a.rounds):
            try:
                args = make()
                fn(*args) if isinstance(args, tuple) else fn(args)
            except refusals as e:
                # a clean refusal is a pass: these are the documented "no"
                if a.verbose:
                    print(f"  refused {name}: {type(e).__name__}: {e}")
                continue
            except Exception as e:                              # noqa: BLE001
                failures.append((name, traceback.format_exc()))
                break

    if not failures:
        print(f"clean: {len(cases)} parsers x {a.rounds} rounds "
              f"(seed {a.seed}), no unhandled exception")
        return 0
    print(f"{len(failures)} parser(s) leaked an exception:")
    for name, tb in failures:
        last = [l for l in tb.strip().splitlines() if l.strip()][-1]
        print(f"  {name}: {last}")
        if a.verbose:
            print(tb)
    return 1


if __name__ == "__main__":
    sys.exit(main())
