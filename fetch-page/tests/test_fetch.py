#!/usr/bin/env python3
"""Strict, hermetic tests for the fetch-page deadline and the Firecrawl vendor stage.

No network: every test runs against a local HTTP server on 127.0.0.1, including a
server that accepts a connection and never answers (the bug this suite exists for).

Run:  python3 -m unittest discover -s fetch-page/tests -t .   (from the repo root)
      python3 fetch-page/tests/test_fetch.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FETCH_PY = ROOT / "fetch-page" / "scripts" / "fetch.py"


def _load_fetch_module():
    spec = importlib.util.spec_from_file_location("fetchpage_fetch", FETCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch_mod = _load_fetch_module()


# ---------------------------------------------------------------- test servers
class _Site:
    """Configurable origin server. Records what was requested and when."""

    def __init__(self):
        self.requests: list[str] = []
        self.vendor_requests = 0
        self.vendor_status = 200
        self.vendor_payload: dict = {}
        self.page_html = "<html><head><title>t</title></head><body><p>hi</p></body></html>"
        self.site = self

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # silence
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                body = outer.page_html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append(self.path)
                if self.path.startswith("/v2/scrape"):
                    outer.vendor_requests += 1
                    self.rfile.read(length)
                    if outer.vendor_status != 200:
                        msg = b'{"error":"nope"}'
                        self.send_response(outer.vendor_status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    payload = json.dumps(outer.vendor_payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _HangingServer:
    """Accepts TCP connections and never writes a byte — a dead relay or DNS black hole."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._conns = []
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
                self._conns.append(conn)          # hold it open, never respond
            except (socket.timeout, OSError):
                continue

    def close(self):
        self._stop.set()
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


VENDOR_OK = {"success": True, "data": {
    "markdown": "# Vendor Page\n\n" + ("real content " * 40),
    "metadata": {"title": "Vendor Title", "statusCode": 200, "url": "https://example.test/final"},
}}


class DeadlineTests(unittest.TestCase):
    """A stage that ignores its socket timeouts must not be able to hang the caller."""

    def setUp(self):
        self.hang = _HangingServer()

    def tearDown(self):
        self.hang.close()

    def test_deadline_cuts_a_hanging_stage(self):
        t0 = time.monotonic()
        res = fetch_mod.fetch(f"http://127.0.0.1:{self.hang.port}/never",
                              timeout=30, deadline=2, ttl=0,
                              use_reader=False, use_archive=False, use_vendor=False, retries=0)
        elapsed = time.monotonic() - t0
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("deadline_exceeded"), res)
        self.assertLess(elapsed, 2 + 3.0, f"deadline did not cut the stage: {elapsed:.1f}s")
        self.assertTrue(any("deadline" in e or "no answer" in e for e in res["errors"]), res["errors"])

    def test_deadline_is_wall_clock_not_per_stage(self):
        """Four stages must not add up to four deadlines."""
        t0 = time.monotonic()
        res = fetch_mod.fetch(f"http://127.0.0.1:{self.hang.port}/never",
                              timeout=30, deadline=2, ttl=0,
                              use_reader=True, use_archive=True, use_vendor=True, retries=0)
        elapsed = time.monotonic() - t0
        self.assertFalse(res["ok"])
        self.assertLess(elapsed, 2 + 3.0, f"chain outlived the global deadline: {elapsed:.1f}s")

    def test_deadline_does_not_fire_on_a_fast_page(self):
        """The deadline must not be a timeout: a fast stage still succeeds."""
        site = _Site()
        try:
            site.page_html = ("<html><head><title>fast</title></head><body>"
                              + "<p>" + ("fast page text " * 40) + "</p></body></html>")
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/fast", timeout=5, deadline=10, ttl=0,
                                  use_reader=False, use_archive=False, use_vendor=False,
                                  retries=0)
            self.assertTrue(res["ok"], res)
            self.assertFalse(res.get("deadline_exceeded"))
            self.assertEqual(res["source"], "direct")
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            site.close()


class VendorStageTests(unittest.TestCase):
    def setUp(self):
        self.site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"

    def tearDown(self):
        self.site.close()
        os.environ.pop("FIRECRAWL_SCRAPE_URL", None)
        os.environ.pop("FETCH_VENDOR", None)

    def _thin_page(self):
        """Make the direct stage give up: <200 chars of body reads as JS-rendered."""
        self.site.page_html = "<html><head><title>x</title></head><body>too thin</body></html>"

    def test_vendor_is_used_when_direct_fails(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertEqual(res["title"], "Vendor Title")
        self.assertEqual(res["final_url"], "https://example.test/final")
        self.assertEqual(self.site.vendor_requests, 1)
        self.assertIn("real content", res["text"])

    def test_direct_wins_and_vendor_is_never_called(self):
        self.site.page_html = ("<html><head><title>Good</title></head><body>"
                               + "<p>" + ("plenty of real text " * 30) + "</p></body></html>")
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "direct")
        self.assertEqual(self.site.vendor_requests, 0, "vendor was called although direct worked")

    def test_no_vendor_flag_disables_the_stage(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, use_vendor=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.vendor_requests, 0)
        self.assertFalse(any(e.startswith("vendor:") for e in res["errors"]), res["errors"])

    def test_vendor_env_kill_switch(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        os.environ["FETCH_VENDOR"] = "0"
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.vendor_requests, 0)

    def test_vendor_output_is_capped_by_max_chars(self):
        self._thin_page()
        huge = "# Big\n\n" + ("x" * 422279)          # the real RFC-sized payload we measured
        self.site.vendor_payload = {"data": {"markdown": huge,
                                            "metadata": {"title": "Big", "statusCode": 200}}}
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=20, deadline=25, ttl=0,
                              use_reader=False, use_archive=False, max_chars=20000, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["chars"], 20000)
        self.assertEqual(len(res["text"]), 20000)
        self.assertLess(len(json.dumps(res)), 20000 * 2, "the caller must not receive 422k chars")

    def test_vendor_http_error_fails_over(self):
        self._thin_page()
        self.site.vendor_status = 500
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertTrue(any("vendor" in e and "500" in e for e in res["errors"]), res["errors"])
        self.assertEqual(self.site.vendor_requests, 1)

    def test_vendor_empty_markdown_is_an_error_not_a_result(self):
        self._thin_page()
        self.site.vendor_payload = {"data": {"markdown": "   ", "metadata": {}}}
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertTrue(any("empty markdown" in e for e in res["errors"]), res["errors"])

    def test_vendor_request_carries_our_own_identity(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                        use_reader=False, use_archive=False, retries=0)
        ua = fetch_mod.VENDOR_UA
        self.assertIn("keyless-agent-skills", ua)
        self.assertNotIn("hermes", ua.lower(), "must not borrow another product's identity")

    def test_cache_round_trip_respects_the_cap(self):
        self._thin_page()
        self.site.vendor_payload = {"data": {"markdown": "# t\n" + ("y" * 5000),
                                             "metadata": {"title": "T", "statusCode": 200}}}
        first = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=600,
                               use_reader=False, use_archive=False, max_chars=1000, retries=0)
        self.assertTrue(first["ok"])
        second = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=600,
                                 use_reader=False, use_archive=False, max_chars=400, retries=0)
        self.assertTrue(second.get("cached"), "second call should be a cache hit")
        self.assertEqual(second["chars"], 400, "a cached page must still honour max_chars")


class StageShareTests(unittest.TestCase):
    """A hanging stage must not starve the stages behind it (the g2.com case)."""

    def test_hanging_reader_does_not_starve_the_vendor_stage(self):
        site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{site.base}/v2/scrape"
        site.page_html = "<html><body>too thin</body></html>"
        site.vendor_payload = VENDOR_OK
        original = fetch_mod.fetch_reader
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: time.sleep(60)
        try:
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/page", timeout=30, deadline=6, ttl=0,
                                  use_archive=False, retries=0)
            elapsed = time.monotonic() - t0
        finally:
            fetch_mod.fetch_reader = original
            site.close()
            os.environ.pop("FIRECRAWL_SCRAPE_URL", None)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertEqual(site.vendor_requests, 1, "the vendor stage never got its turn")
        self.assertLess(elapsed, 6 + 3, f"a 6s deadline took {elapsed:.1f}s")

    def test_reader_share_is_bounded_even_when_it_would_answer_late(self):
        """The reader cannot spend the whole deadline and leave nothing for vendor."""
        site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{site.base}/v2/scrape"
        site.page_html = "<html><body>too thin</body></html>"
        site.vendor_payload = VENDOR_OK
        original = fetch_mod.fetch_reader
        def slow_reader(url, t, keep_links=False):
            time.sleep(t)
            raise RuntimeError("late answer")
        fetch_mod.fetch_reader = slow_reader
        try:
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/page", timeout=30, deadline=8, ttl=0,
                                  use_archive=False, retries=0)
            elapsed = time.monotonic() - t0
        finally:
            fetch_mod.fetch_reader = original
            site.close()
            os.environ.pop("FIRECRAWL_SCRAPE_URL", None)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        # reader share is 0.40 * 8 = 3.2s, so the vendor stage still had room
        self.assertLess(elapsed, 8)


class StageReportingTests(unittest.TestCase):
    """`deadline_exceeded` means "the call was cut", not "a stage was cut".

    A call that skips a hung stage and still returns a page is a success; the
    abandoned stages belong in `stages_cut`, otherwise every g2.com fetch looks
    like a partial answer and agents retry work that already succeeded.
    """

    def setUp(self):
        self.site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"
        self.original_reader = fetch_mod.fetch_reader

    def tearDown(self):
        fetch_mod.fetch_reader = self.original_reader
        self.site.close()
        os.environ.pop("FIRECRAWL_SCRAPE_URL", None)

    def test_success_after_a_cut_stage_is_not_a_deadline_trip(self):
        self.site.page_html = "<html><body>too thin</body></html>"
        self.site.vendor_payload = VENDOR_OK
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: time.sleep(60)
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=30, deadline=6, ttl=0,
                              use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertFalse(res["deadline_exceeded"], res)
        self.assertIn("reader", res["stages_cut"], res)
        self.assertNotIn("vendor", res["stages_cut"], res)

    def test_clean_success_reports_no_cut_stages(self):
        self.site.page_html = ("<html><head><title>fast</title></head><body><p>"
                               + "fast page text " * 40 + "</p></body></html>")
        res = fetch_mod.fetch(f"{self.site.base}/fast", timeout=5, deadline=10, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["deadline_exceeded"], False)
        self.assertEqual(res["stages_cut"], [])

    def test_total_miss_still_flags_the_deadline_and_names_the_stage(self):
        hang = _HangingServer()
        try:
            res = fetch_mod.fetch(f"http://127.0.0.1:{hang.port}/never",
                                  timeout=30, deadline=2, ttl=0,
                                  use_reader=False, use_archive=False, use_vendor=False,
                                  retries=0)
        finally:
            hang.close()
        self.assertFalse(res["ok"])
        self.assertTrue(res["deadline_exceeded"], res)
        self.assertIn("direct", res["stages_cut"], res)

    def test_cli_json_carries_both_fields(self):
        self.site.page_html = ("<html><head><title>fast</title></head><body><p>"
                               + "cli page text " * 40 + "</p></body></html>")
        out = subprocess.run([sys.executable, str(FETCH_PY), f"{self.site.base}/cli",
                              "--json", "--quiet", "--no-cache"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        row = json.loads(out.stdout)[0]
        self.assertTrue(row["ok"], row)
        self.assertIn("stages_cut", row)
        self.assertFalse(row["deadline_exceeded"])


class UnitTests(unittest.TestCase):
    def test_stage_timeout_is_a_runtime_error(self):
        self.assertTrue(issubclass(fetch_mod.StageTimeout, RuntimeError))

    def test_run_with_budget_returns_the_value(self):
        self.assertEqual(fetch_mod._run_with_budget(lambda: 42, 5), 42)

    def test_run_with_budget_propagates_exceptions(self):
        with self.assertRaises(ValueError):
            fetch_mod._run_with_budget(lambda: (_ for _ in ()).throw(ValueError("boom")), 5)

    def test_run_with_budget_abandons_a_hung_callable(self):
        t0 = time.monotonic()
        with self.assertRaises(fetch_mod.StageTimeout):
            fetch_mod._run_with_budget(lambda: time.sleep(30), 1, "hang")
        self.assertLess(time.monotonic() - t0, 2.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
