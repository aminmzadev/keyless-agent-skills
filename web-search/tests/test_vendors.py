#!/usr/bin/env python3
"""Strict, hermetic tests for the vendor search tier (keenable, firecrawl, exa).

Every test points the provider at a local HTTP server via the documented env
overrides, so nothing here touches the real vendors. The assertions are about
contract: what we send, what we accept, what we refuse, and what happens when a
vendor misbehaves.

Run:  python3 -m unittest discover -s web-search/tests -t .    (from repo root)
      python3 web-search/tests/test_vendors.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEARCH_PY = ROOT / "web-search" / "scripts" / "search.py"


def _load_search():
    spec = importlib.util.spec_from_file_location("websearch_search", SEARCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_search()


KEENABLE_PAYLOAD = {
    "results": [
        {"title": "Keenable Result One", "url": "https://one.example/a",
         "snippet": "x" * 5000},
        {"title": "Keenable Result Two", "url": "https://two.example/b",
         "description": "short description"},
        {"title": "no url, must be dropped", "url": "", "snippet": "zzz"},
    ]
}
FIRECRAWL_PAYLOAD = {
    "success": True,
    "data": {"web": [
        {"title": "Firecrawl One", "url": "https://one.example/a", "description": "dup of one"},
        {"title": "Firecrawl Two", "url": "https://three.example/c", "description": "third"},
    ]},
}
EXA_TEXT = (
    "Title: Exa One\nURL: https://one.example/a\nText: exa excerpt one\n\n"
    "Title: Exa Two\nURL: https://four.example/d\nHighlights: exa excerpt two\n"
)
EXA_ENVELOPE = {"jsonrpc": "2.0", "id": 1,
                "result": {"content": [{"type": "text", "text": EXA_TEXT}]}}


class _Vendor:
    """One local server that impersonates all three vendors."""

    def __init__(self):
        self.seen: list[dict] = []
        self.routes: dict[str, tuple[int, object]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                outer.seen.append({"path": self.path,
                                   "headers": {k.lower(): v for k, v in self.headers.items()},
                                   "body": body})
                status, payload = outer.routes.get(self.path, (404, {"error": "no route"}))
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05),
                         daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def route(self, path, payload, status=200):
        self.routes[path] = (status, payload)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class VendorTestBase(unittest.TestCase):
    def setUp(self):
        self.v = _Vendor()
        os.environ["KEENABLE_SEARCH_URL"] = f"{self.v.base}/v1/search/public"
        os.environ["FIRECRAWL_SEARCH_URL"] = f"{self.v.base}/v2/search"
        os.environ["EXA_MCP_URL"] = f"{self.v.base}/mcp"
        self.v.route("/v1/search/public", KEENABLE_PAYLOAD)
        self.v.route("/v2/search", FIRECRAWL_PAYLOAD)
        self.v.route("/mcp", EXA_ENVELOPE)

    def tearDown(self):
        self.v.close()
        for k in ("KEENABLE_SEARCH_URL", "FIRECRAWL_SEARCH_URL", "EXA_MCP_URL"):
            os.environ.pop(k, None)


class KeenableTests(VendorTestBase):
    def test_parses_results_and_drops_entries_without_url(self):
        rows = S.p_keenable("q", 5)
        self.assertEqual([r["url"] for r in rows],
                         ["https://one.example/a", "https://two.example/b"])
        self.assertEqual({r["provider"] for r in rows}, {"keenable"})
        self.assertEqual([r["rank"] for r in rows], [1, 2])

    def test_snippet_is_capped_but_longer_than_a_serp_teaser(self):
        rows = S.p_keenable("q", 5)
        self.assertEqual(len(rows[0]["snippet"]), S.VENDOR_SNIPPET_CAP)
        self.assertGreater(S.VENDOR_SNIPPET_CAP, 500)

    def test_identifies_itself_honestly(self):
        S.p_keenable("q", 3)
        req = self.v.seen[-1]
        self.assertEqual(req["headers"].get("x-keenable-title"), "keyless-agent-skills")
        self.assertNotIn("hermes", req["headers"].get("user-agent", "").lower())
        self.assertIn("keyless-agent-skills", req["headers"].get("user-agent", ""))

    def test_does_not_send_an_authorization_header(self):
        S.p_keenable("q", 3)
        self.assertNotIn("authorization", self.v.seen[-1]["headers"])

    def test_sends_the_query_and_max_results(self):
        S.p_keenable("ffmpeg zoompan", 7)
        body = json.loads(self.v.seen[-1]["body"])
        self.assertEqual(body["query"], "ffmpeg zoompan")
        self.assertEqual(body["max_results"], 7)

    def test_http_error_is_loud(self):
        self.v.route("/v1/search/public", {"error": "nope"}, status=500)
        with self.assertRaises(RuntimeError) as ctx:
            S.with_retries(lambda: S.p_keenable("q", 3), "keenable", retries=0)
        self.assertIn("500", str(ctx.exception))


class FirecrawlTests(VendorTestBase):
    def test_parses_data_web_shape(self):
        rows = S.p_firecrawl("q", 5)
        self.assertEqual([r["url"] for r in rows],
                         ["https://one.example/a", "https://three.example/c"])
        self.assertEqual({r["provider"] for r in rows}, {"firecrawl"})

    def test_accepts_a_flat_data_list_too(self):
        self.v.route("/v2/search", {"data": [{"title": "A", "url": "https://a.example/x",
                                              "description": "d"}]})
        rows = S.p_firecrawl("q", 5)
        self.assertEqual([r["url"] for r in rows], ["https://a.example/x"])

    def test_never_sends_an_authorization_header(self):
        S.p_firecrawl("q", 3)
        self.assertNotIn("authorization", self.v.seen[-1]["headers"])

    def test_rate_limit_is_an_error_not_an_empty_success(self):
        self.v.route("/v2/search", {"error": "rate limited"}, status=429)
        with self.assertRaises(RuntimeError):
            S.with_retries(lambda: S.p_firecrawl("q", 3), "firecrawl", retries=0)


class ExaTests(VendorTestBase):
    def test_parses_json_rpc_text_blocks(self):
        rows = S.p_exa("q", 5)
        self.assertEqual([r["url"] for r in rows],
                         ["https://one.example/a", "https://four.example/d"])
        self.assertEqual(rows[0]["title"], "Exa One")
        self.assertIn("exa excerpt one", rows[0]["snippet"])
        self.assertNotIn("Highlights:", rows[1]["snippet"])

    def test_sends_a_json_rpc_tools_call(self):
        S.p_exa("asyncio cancellation", 4)
        body = json.loads(self.v.seen[-1]["body"])
        self.assertEqual(body["method"], "tools/call")
        self.assertEqual(body["params"]["name"], "web_search_exa")
        self.assertEqual(body["params"]["arguments"]["numResults"], 4)

    def test_unparseable_payload_raises(self):
        self.v.route("/mcp", {"jsonrpc": "2.0", "id": 1, "result": {}})
        with self.assertRaises(RuntimeError):
            S.p_exa("q", 3)

    def test_payload_with_no_results_raises(self):
        self.v.route("/mcp", {"result": {"content": [{"type": "text",
                                                      "text": "nothing useful here"}]}})
        with self.assertRaises(RuntimeError) as ctx:
            S.p_exa("q", 3)
        self.assertIn("no parsable results", str(ctx.exception))


class VendorTierContractTests(VendorTestBase):
    """The tier is opt-in, self-contained, and degrades one vendor at a time."""

    def test_vendor_group_is_exactly_the_three_providers(self):
        self.assertEqual(S.GROUPS["vendor"], ["keenable", "firecrawl", "exa"])

    def test_vendor_providers_are_not_in_the_default_groups(self):
        self.assertNotIn("keenable", S.GROUPS["web"])
        self.assertNotIn("firecrawl", S.GROUPS["web"])
        self.assertNotIn("exa", S.GROUPS["web"])
        for name in ("keenable", "firecrawl", "exa"):
            self.assertNotIn(name, S.GROUPS["all"], f"{name} leaked into --all")

    def test_no_outer_retry_for_vendor_providers(self):
        for name in ("keenable", "firecrawl", "exa"):
            self.assertIn(name, S.NO_OUTER_RETRY)

    def test_every_vendor_provider_has_group_vendor(self):
        for name in ("keenable", "firecrawl", "exa"):
            self.assertEqual(S.PROVIDERS[name][1], "vendor")

    def test_search_merges_and_dedupes_across_vendors(self):
        res = S.search("vendor merge test", providers=["keenable", "firecrawl", "exa"],
                       limit=10, use_cache=False)
        self.assertEqual(res["count"], 4, res["results"])
        urls = [r["url"] for r in res["results"]]
        self.assertEqual(len(urls), len(set(urls)), "duplicate url survived merging")
        shared = [r for r in res["results"] if r["url"] == "https://one.example/a"][0]
        self.assertGreaterEqual(len(shared["providers"]), 2,
                                "a url returned by three vendors must show agreement")
        self.assertEqual(res["errors"], {})

    def test_one_vendor_failing_does_not_take_the_others_down(self):
        self.v.route("/v2/search", {"error": "boom"}, status=500)
        res = S.search("partial failure", providers=["keenable", "firecrawl"], limit=5,
                       use_cache=False)
        self.assertGreater(res["count"], 0)
        self.assertIn("firecrawl", res["errors"])
        self.assertNotIn("keenable", res["errors"])

    def test_all_vendors_failing_returns_no_results_and_errors(self):
        for path in ("/v1/search/public", "/v2/search", "/mcp"):
            self.v.route(path, {"error": "down"}, status=503)
        res = S.search("total outage zzzzz", providers=["keenable", "firecrawl", "exa"],
                       limit=5, use_cache=False)
        self.assertEqual(set(res["errors"]), {"keenable", "firecrawl", "exa"})
        self.assertEqual(res["count"], 0)

    def test_snippet_cap_applies_to_vendor_rows_only(self):
        vendor_row = S.p_keenable("q", 1)[0]
        plain = S.item("t", "https://x.example", "y" * 5000, "bing")
        self.assertEqual(len(vendor_row["snippet"]), S.VENDOR_SNIPPET_CAP)
        self.assertEqual(len(plain["snippet"]), 500)

    def test_vendor_requests_stay_within_a_sane_wall_clock(self):
        t0 = time.monotonic()
        S.search("timing", providers=["keenable", "firecrawl", "exa"], limit=5, use_cache=False)
        self.assertLess(time.monotonic() - t0, 10, "vendor tier should not stall the search")


if __name__ == "__main__":
    unittest.main(verbosity=2)
