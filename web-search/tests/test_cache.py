#!/usr/bin/env python3
"""Hermetic tests for the search cache and the response-size ceiling.

Two bugs these lock down:

  * the entry was written straight to its final path, so a reader could open a
    half-written file, and a crash mid-write left that truncated entry behind;
  * provider bodies were read with no cap, so one broken endpoint answering
    with a huge body could take the process down.

Run:  python3 web-search/tests/test_cache.py -v
      python3 -m unittest discover -s web-search/tests -t .   (from repo root)
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_search():
    spec = importlib.util.spec_from_file_location(
        "websearch_search_cache", ROOT / "web-search" / "scripts" / "search.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_search()


class _FakeResponse:
    """Just enough of an http.client response to test the capped reader."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.reads = []

    def read(self, n=None):
        self.reads.append(n)
        chunk = self.payload[:n] if n is not None else self.payload
        self.payload = self.payload[len(chunk):]
        return chunk


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="searchcache-")
        self._old_dir = S.CACHE_DIR
        S.CACHE_DIR = self.tmp

    def tearDown(self):
        S.CACHE_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self):
        payload = {"query": "q", "results": [{"title": "t"}], "count": 1}
        S._cache_put("k1", payload)
        self.assertEqual(S._cache_get("k1"), payload)

    def test_the_entry_records_its_schema(self):
        S._cache_put("k2", {"count": 0})
        path = S._cache_path("k2")
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(entry["schema"], S.CACHE_SCHEMA)
        self.assertIn("_ts", entry)

    def test_an_entry_without_a_schema_is_ignored(self):
        S._cache_put("k3", {"count": 1})
        path = S._cache_path("k3")
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        entry.pop("schema")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        self.assertIsNone(S._cache_get("k3"), "an old-shape entry must not be served")

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self):
        path = S._cache_path("k4")
        os.makedirs(S.CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"_ts": 1, "payload": ')     # truncated by a dead writer
        self.assertIsNone(S._cache_get("k4"))

    def test_a_write_that_dies_midway_leaves_no_debris_and_keeps_the_old_entry(self):
        S._cache_put("k5", {"count": "old"})
        path = S._cache_path("k5")
        with open(path, encoding="utf-8") as f:
            before = f.read()

        real_dump = json.dump

        def dying_dump(obj, fp, **kw):
            fp.write('{"half": ')
            raise OSError("disk full")

        json.dump = dying_dump
        try:
            S._cache_put("k5", {"count": "new"})       # must not raise
        finally:
            json.dump = real_dump

        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(S.CACHE_DIR) if n.startswith(".tmp-")], [])
        self.assertEqual(S._cache_get("k5"), {"count": "old"})


class NormalizeTests(unittest.TestCase):
    """One malformed provider must not take down the whole search.

    `dedupe()` indexes r["url"], `rank()` indexes r["title"], and both run
    outside the per-provider try — so a provider returning anything but the
    documented shape used to raise straight out of search().
    """

    def setUp(self):
        self._providers = dict(S.PROVIDERS)

    def tearDown(self):
        S.PROVIDERS.clear()
        S.PROVIDERS.update(self._providers)

    def test_normalize_drops_junk_and_keeps_the_good_rows(self):
        rows = S.normalize(["bare string", None, 42, {}, {"title": "t"},
                            {"url": "https://a.example/1", "title": "ok"}], "p")
        self.assertEqual([r["url"] for r in rows], ["", "https://a.example/1"])
        self.assertEqual(rows[0]["provider"], "p")
        self.assertEqual(rows[1]["provider"], "p")

    def test_normalize_keeps_provider_extras(self):
        rows = S.normalize([{"url": "https://a.example/2", "title": "t",
                             "published": "2026-01-01", "extra": {"k": 1}}], "p")
        self.assertEqual(rows[0]["published"], "2026-01-01")
        self.assertEqual(rows[0]["extra"], {"k": 1})

    def test_normalize_refuses_a_non_list(self):
        for bad in (None, "string", {"a": 1}, 42):
            self.assertEqual(S.normalize(bad, "p"), [])

    def test_search_survives_a_broken_provider(self):
        def junk(q, n):
            return ["a bare string", {"title": "no url"},
                    {"url": "https://a.example/1", "title": "good"}]

        S.PROVIDERS["junk"] = (junk, 1.0)
        res = S.search("anything", providers=["junk"], limit=5, use_cache=False)
        # the point is that search() returns at all: it used to raise TypeError
        # out of dedupe(). A url-less row is kept by title, which is by design.
        self.assertEqual(res["errors"], {}, res)
        self.assertEqual([r["url"] for r in res["results"]],
                         ["", "https://a.example/1"], res)


class CappedReadTests(unittest.TestCase):
    def setUp(self):
        self._cap = S.MAX_BODY

    def tearDown(self):
        S.MAX_BODY = self._cap

    def test_a_normal_body_is_decoded(self):
        self.assertEqual(S._decode_capped(_FakeResponse(b'{"ok": 1}')), '{"ok": 1}')

    def test_an_oversized_body_is_refused_without_buffering_it(self):
        S.MAX_BODY = 64
        resp = _FakeResponse(b"x" * 5000)
        with self.assertRaises(S.BodyTooLarge):
            S._decode_capped(resp)                        # default cap comes from MAX_BODY
        self.assertEqual(resp.reads, [65], "must ask for cap+1 bytes, not the whole body")

    def test_a_body_exactly_at_the_cap_is_fine(self):
        S.MAX_BODY = 64
        self.assertEqual(len(S._decode_capped(_FakeResponse(b"y" * 64))), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
