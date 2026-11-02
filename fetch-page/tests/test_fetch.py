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
import zlib
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

# Nothing listens on port 1, so an un-redirected vendor call fails instantly and
# offline. Tests that want a vendor to answer point these at their local _Site.
DEAD_VENDOR = "http://127.0.0.1:1"


def setUpModule():
    os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
    os.environ["EXA_MCP_URL"] = DEAD_VENDOR + "/mcp"


def tearDownModule():
    os.environ.pop("FIRECRAWL_SCRAPE_URL", None)
    os.environ.pop("EXA_MCP_URL", None)


# ---------------------------------------------------------------- test servers
class _Site:
    """Configurable origin server. Records what was requested and when."""

    def __init__(self):
        self.requests: list[str] = []
        self.vendor_requests = 0
        self.vendor_status = 200
        self.vendor_payload: dict = {}
        self.exa_requests = 0
        self.exa_bodies: list[dict] = []
        self.exa_status = 200
        self.exa_payload: dict = {"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": -1, "message": "exa not configured here"}}
        self.exa_sse = False          # answer as text/event-stream instead of JSON
        self.exa_raw: str | None = None
        self.exa_headers: dict = {}
        self.page_html = "<html><head><title>t</title></head><body><p>hi</p></body></html>"
        self.site = self

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # silence
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                if self.path.startswith("/json"):
                    body = json.dumps(outer.exa_payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
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
                elif self.path.startswith("/mcp"):
                    outer.exa_requests += 1
                    outer.exa_headers = dict(self.headers)
                    raw_body = self.rfile.read(length)
                    try:
                        outer.exa_bodies.append(json.loads(raw_body.decode() or "{}"))
                    except json.JSONDecodeError:
                        outer.exa_bodies.append({"__unparsed__": raw_body.decode()})
                    if outer.exa_status != 200:
                        msg = b'{"error":"nope"}'
                        self.send_response(outer.exa_status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    if outer.exa_raw is not None:
                        payload = outer.exa_raw.encode()
                    else:
                        blob = json.dumps(outer.exa_payload)
                        payload = (f"event: message\ndata: {blob}\n\n".encode()
                                   if outer.exa_sse else blob.encode())
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/event-stream" if outer.exa_sse else "application/json")
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


EXA_OK = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text":
    "# Exa Page\n\nURL: https://example.test/paid\nPublished: 2026-10-01\n\n"
    + "page body from exa " * 40}], "isError": False}}


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
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
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
            os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
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
            os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        # reader share is 0.40 * 8 = 3.2s, so the vendor stage still had room
        self.assertLess(elapsed, 8)


class ExaStageTests(unittest.TestCase):
    """Stage 4: Exa's anonymous MCP reader, ahead of Wayback."""

    def setUp(self):
        self.site = _Site()
        os.environ["EXA_MCP_URL"] = f"{self.site.base}/mcp"
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"
        self.site.page_html = "<html><body>too thin</body></html>"
        self.site.vendor_status = 500           # Firecrawl fails, Exa must catch
        self.site.exa_payload = EXA_OK
        self.orig = (fetch_mod.fetch_direct, fetch_mod.fetch_reader, fetch_mod.fetch_archive)
        fetch_mod.fetch_direct = lambda url, t, keep_links=False: (_ for _ in ()).throw(RuntimeError("blocked"))
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: (_ for _ in ()).throw(RuntimeError("blocked"))

    def tearDown(self):
        fetch_mod.fetch_direct, fetch_mod.fetch_reader, fetch_mod.fetch_archive = self.orig
        self.site.close()
        os.environ["EXA_MCP_URL"] = DEAD_VENDOR + "/mcp"
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"

    def _fetch(self, **kw):
        kw.setdefault("timeout", 10)
        kw.setdefault("deadline", 20)
        return fetch_mod.fetch(f"{self.site.base}/page", ttl=0, retries=0, **kw)

    def test_exa_answers_when_the_other_vendors_fail(self):
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertEqual(res["title"], "Exa Page")
        self.assertGreater(res["chars"], 400)
        self.assertEqual(self.site.exa_requests, 1)

    def test_request_is_a_jsonrpc_call_with_a_real_character_budget(self):
        self.site.vendor_status = 503
        self._fetch()
        body = self.site.exa_bodies[0]
        self.assertEqual(body["method"], "tools/call")
        self.assertEqual(body["params"]["name"], "web_fetch_exa")
        args = body["params"]["arguments"]
        self.assertEqual(args["urls"], [f"{self.site.base}/page"])
        # the default is ~3,000 chars; a fetch stage must ask for far more
        self.assertGreaterEqual(args["maxCharacters"], 20000)

    def test_server_sent_events_body_is_parsed(self):
        self.site.vendor_status = 503
        self.site.exa_sse = True
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertIn("page body from exa", res["text"])

    def test_exa_runs_before_wayback(self):
        calls = []
        fetch_mod.fetch_archive = lambda url, t, keep_links=False: (
            calls.append("archive") or (_ for _ in ()).throw(RuntimeError("no snapshot")))
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertEqual(calls, [], "Wayback was tried before Exa")

    def test_jsonrpc_error_fails_over_to_wayback(self):
        self.site.exa_payload = {"jsonrpc": "2.0", "id": 1,
                                 "error": {"code": -32000, "message": "rate limited"}}
        self.site.vendor_status = 503
        fetch_mod.fetch_archive = lambda url, t, keep_links=False: {
            "title": "archived", "text": "archive copy " * 40,
            "meta": {"content_type": "markdown", "vendor": "wayback", "status_code": 200},
            "links": [], "source": "archive:wayback", "final_url": url,
            "status": 200, "elapsed": 0.1}
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "archive:wayback")
        self.assertTrue(any("exa" in e for e in res["errors"]), res["errors"])

    def test_exa_carries_our_identity_and_no_credential(self):
        self.site.vendor_status = 503
        self._fetch()
        headers = {k.lower(): v for k, v in self.site.exa_headers.items()}
        self.assertNotIn("authorization", headers, "sent a credential Exa never asked for")
        self.assertIn("keyless-agent-skills", headers.get("user-agent", ""))
        self.assertIn("text/event-stream", headers.get("accept", ""))

    def test_no_vendor_disables_the_exa_stage_too(self):
        res = self._fetch(use_vendor=False, use_archive=False)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.exa_requests, 0)

    def test_exa_is_never_retried(self):
        """One attempt per borrowed endpoint, even when it errors."""
        self.site.vendor_status = 503
        self.site.exa_payload = {"jsonrpc": "2.0", "id": 1,
                                 "error": {"code": -1, "message": "slow down"}}
        res = self._fetch(use_archive=False)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.exa_requests, 1, res["errors"])

    def test_exa_preamble_is_stripped(self):
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertNotIn("Published: 2026-10-01", res["text"])
        self.assertNotIn("URL: https://example.test/paid", res["text"])
        self.assertTrue(res["text"].startswith("# Exa Page"), res["text"][:80])


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
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"

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


class RewriteMappingTests(unittest.TestCase):
    """rewrite_candidates is pure: URL in, machine-readable twin out."""

    def test_github_blob_becomes_raw(self):
        out = fetch_mod.rewrite_candidates("https://github.com/FFmpeg/FFmpeg/blob/master/README.md")
        self.assertEqual(out[0][0], "gh-raw")
        self.assertEqual(out[0][1], "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/README.md")
        self.assertEqual(out[0][2], "raw")

    def test_github_repo_page_is_not_rewritten(self):
        self.assertEqual(fetch_mod.rewrite_candidates("https://github.com/FFmpeg/FFmpeg"), [])

    def test_stackoverflow_question_becomes_the_api(self):
        out = fetch_mod.rewrite_candidates("https://stackoverflow.com/questions/1976007/what-chars")
        self.assertEqual(out[0][0], "stackexchange")
        self.assertEqual(out[0][1],
                         "https://api.stackexchange.com/2.3/questions/1976007"
                         "?site=stackoverflow&filter=withbody")

    def test_subdomain_stackexchange_keeps_its_site(self):
        out = fetch_mod.rewrite_candidates("https://math.stackexchange.com/questions/42/is-it-true")
        self.assertIn("site=math", out[0][1])

    def test_wikipedia_gets_the_rest_summary(self):
        out = fetch_mod.rewrite_candidates("https://en.wikipedia.org/wiki/FFmpeg")
        self.assertEqual(out[0][0], "wikipedia-summary")
        self.assertTrue(out[0][1].startswith("https://en.wikipedia.org/api/rest_v1/page/summary/"))

    def test_youtube_watch_and_short_forms(self):
        a = fetch_mod.rewrite_candidates("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        b = fetch_mod.rewrite_candidates("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(a[0][0], b[0][0], "watch and youtu.be must map to the same rewrite")
        self.assertIn("v=dQw4w9WgXcQ", a[0][1])

    def test_x_gets_mirror_candidates_in_order(self):
        out = fetch_mod.rewrite_candidates("https://x.com/rustlang/status/1")
        self.assertEqual([c[0] for c in out], ["nitter", "nitter-privacydev", "xcancel"])

    def test_arxiv_abs_gets_the_pdf(self):
        out = fetch_mod.rewrite_candidates("https://arxiv.org/abs/1706.03762")
        self.assertEqual(out[0][1], "https://arxiv.org/pdf/1706.03762")

    def test_ordinary_urls_have_no_twin(self):
        for u in ("https://example.com/a/b", "https://docs.python.org/3/library/asyncio.html",
                  "https://news.ycombinator.com/item?id=1"):
            self.assertEqual(fetch_mod.rewrite_candidates(u), [], u)

    def test_priority_split(self):
        before, after = fetch_mod.order_rewrites(
            fetch_mod.rewrite_candidates("https://github.com/a/b/blob/c/d.md"))
        self.assertEqual([c[0] for c in before], ["gh-raw"])
        before, after = fetch_mod.order_rewrites(
            fetch_mod.rewrite_candidates("https://en.wikipedia.org/wiki/X"))
        self.assertEqual(before, [])
        self.assertEqual([c[0] for c in after], ["wikipedia-summary"])


class RewriteFetchTests(unittest.TestCase):
    """The fetching half: renderers, priority, and running before the direct stage."""

    def setUp(self):
        self.site = _Site()

    def tearDown(self):
        self.site.close()

    def test_github_raw_is_served_before_direct(self):
        target = f"{self.site.base}/README.md"
        self.site.page_html = "# README\n\n" + "real file content " * 40
        orig = (fetch_mod.rewrite_candidates, fetch_mod.fetch_direct)
        fetch_mod.rewrite_candidates = lambda url: [("gh-raw", target, "raw")]
        fetch_mod.fetch_direct = lambda url, t, keep_links=False: (_ for _ in ()).throw(
            RuntimeError("github nav sandwich"))
        try:
            res = fetch_mod.fetch(f"{self.site.base}/blob/page", timeout=10, deadline=20, ttl=0,
                                  use_reader=False, use_vendor=False, use_archive=False, retries=0)
        finally:
            fetch_mod.rewrite_candidates, fetch_mod.fetch_direct = orig
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "rewrite:gh-raw")
        self.assertIn("real file content", res["text"])
        self.assertEqual(res["title"], "README.md")

    def test_stackexchange_json_is_rendered_as_a_qa_page(self):
        payload = {"items": [
            {"title": "What characters are forbidden?&lt;b&gt;", "link": "https://so.test/q/1",
             "score": 42, "answer_count": 3, "is_answered": True,
             "body": "<p>The forbidden characters are <code>&lt; &gt; : &quot;</code></p>"},
            {"answer_id": 9, "score": 17, "is_accepted": True,
             "owner": {"display_name": "someone"},
             "body": "<p>Use a whitelist.</p>"},
        ]}
        self.site.exa_payload = payload                     # reused generic JSON responder
        res = fetch_mod.fetch_rewrite("stackexchange", f"{self.site.base}/json", "json", 10)
        self.assertEqual(res["source"], "rewrite:stackexchange")
        self.assertIn("# What characters are forbidden?", res["text"])
        self.assertIn("42 votes", res["text"])
        self.assertIn("## Answer 1 (accepted)", res["text"])
        self.assertIn("Use a whitelist.", res["text"])
        self.assertNotIn("<p>", res["text"])

    def test_stackexchange_with_no_items_is_an_error(self):
        self.site.exa_payload = {"items": []}
        with self.assertRaises(RuntimeError):
            fetch_mod.fetch_rewrite("stackexchange", f"{self.site.base}/json", "json", 10)

    def test_wikipedia_summary_says_it_is_only_a_summary(self):
        self.site.exa_payload = {"title": "FFmpeg", "extract": "FFmpeg is a free software project."}
        res = fetch_mod.fetch_rewrite("wikipedia-summary", f"{self.site.base}/json", "json", 10)
        self.assertIn("# FFmpeg", res["text"])
        self.assertIn("lede only", res["text"])
        self.assertTrue(res["meta"].get("summary"))

    def test_youtube_oembed_does_not_pretend_to_be_the_video(self):
        self.site.exa_payload = {"title": "A video", "author_name": "A channel"}
        res = fetch_mod.fetch_rewrite("youtube-oembed", f"{self.site.base}/json", "json", 10)
        self.assertIn("by A channel", res["text"])
        self.assertIn("not the video", res["text"])


class WallTests(unittest.TestCase):
    """A relay answering 200 with a wall is not an answer."""

    def test_signup_walls_are_named(self):
        page = ("Final URL: https://stackoverflow.com/questions/1\n\n"
                "# Join Stack Overflow\n\nSign up with Google\nSign up with GitHub\n"
                + "filler " * 200)
        self.assertEqual(fetch_mod.wall_reason(page), "sign up with google")

    def test_challenge_pages_are_named(self):
        page = "<title>Just a moment...</title> Checking your browser before accessing" + " x" * 400
        self.assertIsNotNone(fetch_mod.wall_reason(page))

    def test_relay_error_wrappers_are_named(self):
        page = "Title: \n\nWarning: Target URL returned error 403: Forbidden\n"
        self.assertIsNotNone(fetch_mod.wall_reason(page))

    def test_real_pages_are_not_walls(self):
        for page in ("# Documentation\n\nInstall with pip install ffmpeg-python.\n" + "text " * 300,
                     "# Sign in with Google — OAuth guide\n\nThis page explains the flow. "
                     + "Long body. " * 300):
            self.assertIsNone(fetch_mod.wall_reason(page), page[:50])

    def test_a_wall_stage_is_rejected_and_the_chain_moves_on(self):
        site = _Site()
        site.page_html = ("<html><body><h1>Join Stack Overflow</h1>"
                          "<a>Sign up with Google</a> Sign up with GitHub"
                          + "<p>" + "navigation boilerplate " * 40 + "</p></body></html>")
        try:
            res = fetch_mod.fetch(f"{site.base}/walled", timeout=5, deadline=8, ttl=0,
                                  use_reader=False, use_vendor=False, use_archive=False,
                                  use_rewrite=False, retries=0)
        finally:
            site.close()
        self.assertFalse(res["ok"], res)
        self.assertTrue(any("wall page" in e for e in res["errors"]), res["errors"])
        self.assertEqual(res["text"], "")


class StdlibPdfTests(unittest.TestCase):
    """PDF text without poppler or pypdf — the Termux / slim container case."""

    @staticmethod
    def _mini_pdf(text=b"Hello PDF world"):
        content = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
        comp = zlib.compress(content)
        head = (b"%PDF-1.7\n1 0 obj\n<< /Length " + str(len(comp)).encode()
                + b" /Filter /FlateDecode >>\nstream\n")
        return head + comp + b"\nendstream\nendobj\n%%EOF\n"

    def test_flate_decode_stream_is_read(self):
        out = fetch_mod.pdf_extract_stdlib(self._mini_pdf())
        self.assertIn("Hello PDF world", out)

    def test_tex_ligature_codes_are_decoded(self):
        # \x0c is TeX's T1 slot for the "fi" ligature: "efficient" arrives as e\x0ccient
        out = fetch_mod.pdf_extract_stdlib(self._mini_pdf(b"e\x0ecient e\x0cne"))
        self.assertIn("efficient", out)
        self.assertIn("efine", out)

    def test_garbage_pdf_returns_empty_rather_than_raising(self):
        self.assertEqual(fetch_mod.pdf_extract_stdlib(b"not a pdf at all"), "")

    def test_pdf_to_text_falls_back_when_pdftotext_and_pypdf_are_missing(self):
        # a real page's worth of text, not one line: the fallback only accepts a
        # result long enough to be an answer rather than a stray caption
        orig_which = fetch_mod.shutil.which
        fetch_mod.shutil.which = lambda name: None
        try:
            txt = fetch_mod.pdf_to_text(self._mini_pdf(b"Hello PDF world " * 8))
        finally:
            fetch_mod.shutil.which = orig_which
        self.assertTrue(txt and "Hello PDF world" in txt, txt)


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
