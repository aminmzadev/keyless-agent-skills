#!/usr/bin/env python3
"""Strict, hermetic tests for the classic provider layer that the vendor suite
does not cover: the Yahoo parser and the cookie jar that makes it work.

No network. The Yahoo fixture is real markup captured on 2026-11-02 (redirect
wrappers included), served from a local HTTP server; the jar is exercised
against a second local server that refuses to answer without its own cookie.

Run:  python3 web-search/tests/test_providers.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEARCH_PY = ROOT / "web-search" / "scripts" / "search.py"


def _load_search_module():
    spec = importlib.util.spec_from_file_location("websearch_search", SEARCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


search = _load_search_module()


# ------------------------------------------------------------------- fixtures
def _result_block(title, href, snippet, host="example.test"):
    return f'''
<div class="dd algo algo-sr sr">
  <div class="compTitle"><h3 class="title"><a href="{href}">{title}</a></h3></div>
  <div class="compText aAbs"><p>{snippet}</p></div>
</div>
'''


YAHOO_HTML = (
    '<html><head><title>sqlite wal mode - Yahoo Search</title></head><body>'
    + _result_block("Write-Ahead Logging - SQLite",
                    "https://r.search.yahoo.com/_ylt=AwrE1xQ/xRU=RB0AAb5ANyoA/RV=2/RE=1/RU=https%3a%2f%2fsqlite.org%2fwal.html/RK=2/RS=abc-",
                    "Thus, if an older version of SQLite attempts to connect")
    + _result_block("SQLite WAL Mode: How Write Ahead Logging Works",
                    "https://r.search.yahoo.com/_ylt=AwrE/RU=https%3a%2f%2fsqldocs.org%2fsqlite-write-ahead-logging%2f/RK=2/RS=def-",
                    "Write-ahead logging lets SQLite readers keep reading")
    # a Yahoo-owned link: must be dropped, not returned as a result
    + _result_block("More from Yahoo", "https://search.yahoo.com/search?p=sqlite", "yahoo own page")
    + '</body></html>'
)


class _Server:
    """Local HTTP server: /search serves the fixture, /cookie requires a cookie."""

    def __init__(self):
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: bytes, ctype="text/html; charset=utf-8", extra=None):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(self.path)
                if self.path.startswith("/cookie"):
                    # the Yahoo situation: 500 until the client keeps its cookie
                    if "session=1" in (self.headers.get("Cookie") or ""):
                        self._send(b'{"ok": true}', "application/json")
                    else:
                        body = b'{"error": "INKApi"}'
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Set-Cookie", "session=1; Path=/")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                self._send(YAHOO_HTML.encode())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05),
                         daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class YahooParserTests(unittest.TestCase):
    def setUp(self):
        self.server = _Server()
        self.url = self.server.base + "/search"
        self._orig = search.http
        def fake_http(url, headers=None, timeout=20):
            self.server.requests.append(url)
            with urllib.request.urlopen(self.url, timeout=timeout) as r:
                return r.read().decode()
        search.http = fake_http

    def tearDown(self):
        search.http = self._orig
        self.server.close()

    def test_results_are_parsed_and_redirects_unwrapped(self):
        out = search.p_yahoo("sqlite wal mode", 5)
        self.assertEqual(len(out), 2, [r["url"] for r in out])
        self.assertEqual(out[0]["url"], "https://sqlite.org/wal.html")
        self.assertEqual(out[1]["url"], "https://sqldocs.org/sqlite-write-ahead-logging/")
        self.assertEqual(out[0]["provider"], "yahoo")

    def test_yahoo_own_pages_are_dropped(self):
        urls = [r["url"] for r in search.p_yahoo("sqlite wal mode", 5)]
        self.assertFalse([u for u in urls if "yahoo.com" in u], urls)

    def test_titles_and_snippets_are_cleaned(self):
        out = search.p_yahoo("sqlite wal mode", 5)
        self.assertEqual(out[0]["title"], "Write-Ahead Logging - SQLite")
        self.assertIn("older version of SQLite", out[0]["snippet"])

    def test_limit_is_respected(self):
        self.assertEqual(len(search.p_yahoo("sqlite wal mode", 1)), 1)

    def test_empty_markup_returns_nothing_rather_than_raising(self):
        search.http = lambda *a, **k: "<html><body>no results</body></html>"
        self.assertEqual(search.p_yahoo("nothing at all", 5), [])

    def test_provider_is_registered_and_in_the_web_group(self):
        self.assertIn("yahoo", search.PROVIDERS)
        self.assertIn("yahoo", search.GROUPS["web"])


class RedirectUnwrapTests(unittest.TestCase):
    def test_yahoo_RU_wrapper(self):
        got = search._unwrap_engine_link(
            "https://r.search.yahoo.com/x/RU=https%3a%2f%2fexample.com%2fa%3fb%3d1/RK=2/RS=x-")
        self.assertEqual(got, "https://example.com/a?b=1")

    def test_duckduckgo_and_bing_still_work(self):
        self.assertEqual(search._unwrap_engine_link("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fa.test%2F"),
                         "https://a.test/")


class CookieJarTests(unittest.TestCase):
    """The bug this fixes: Yahoo answers 500 until it has set a cookie, and the
    retry only works if the client kept that cookie from the failed response."""

    def setUp(self):
        self.server = _Server()

    def tearDown(self):
        self.server.close()

    def test_http_keeps_cookies_between_calls(self):
        url = self.server.base + "/cookie"
        first = None
        try:
            search.http(url)
        except Exception as e:                      # the 500 that sets the cookie
            first = e
        self.assertIsNotNone(first, "the fixture was supposed to 500 first")
        second = search.http(url)                   # same session, cookie kept
        self.assertIn("ok", second)

    def test_http_uses_an_opener_with_a_cookie_processor(self):
        processors = [type(h).__name__ for h in search._OPENER.handlers]
        self.assertIn("HTTPCookieProcessor", processors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
