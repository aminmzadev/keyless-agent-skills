#!/usr/bin/env python3
# ---
# Published as the `fetch-page` skill. Canonical source: deep-research/scripts/.
# Edit there, then run ./sync.sh (synced 2026-11-01).
# ---
"""
fetch.py — turn any URL into clean markdown for an agent, with three fallbacks.

  1. direct      : urllib + browser UA, gzip, redirect following, HTML->text
  2. reader      : r.jina.ai markdown -> CORS-proxied HTML (allorigins, cors.lol,
                   codetabs), whichever answers first
  3. archive     : Wayback Machine snapshot, for pages that are gone or blocked

Handles HTML, PDF (pdftotext or pypdf) and plain text. Caches to disk so a
research loop doesn't hammer the same host twice.

  fetch.py https://example.com
  fetch.py https://a.com https://b.com --out pages/ --json
  fetch.py https://example.com --links --max-chars 8000
  fetch.py https://example.com --no-cache --json --quiet

Exit codes: 0 at least one page fetched · 2 all failed
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CACHE_ROOT = os.environ.get("AGENT_SKILLS_CACHE") or os.path.expanduser(
    "~/.cache/agent-skills")
CACHE = os.path.join(CACHE_ROOT, "fetch")
JINA = "https://r.jina.ai/"
# Free CORS relays: the page is fetched from *their* IP, so datacenter blocks and
# "enable JavaScript" walls often still serve us the HTML. Volunteers — flaky.
HTML_PROXIES = [
    ("allorigins", "https://api.allorigins.win/raw?url={}"),
    ("cors.lol", "https://api.cors.lol/?url={}"),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest={}"),
]
WAYBACK = "https://archive.org/wayback/available?url="
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "head",
             "nav", "footer", "aside", "form", "button", "select", "figure"}
BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "blockquote", "pre",
         "table", "ul", "ol", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
BOILER = re.compile(
    r"(cookie|consent|subscribe|newsletter|sign in|log in|all rights reserved|"
    r"©\s?\d{4}|privacy policy|terms of service|accept all|advertisement|"
    r"we use cookies|javascript is disabled|enable javascript|skip to content|"
    r"share on (facebook|twitter|x|linkedin)|read more$)", re.I)


# ------------------------------------------------------------------ html reader
class Reader(HTMLParser):
    def __init__(self, keep_links=False):
        super().__init__(convert_charrefs=True)
        self.keep_links = keep_links
        self.stack, self.out, self.title, self.meta = [], [], [], {}
        self.links, self._href, self._hreftext = [], None, ""
        self._skip = 0
        self._in_pre = 0
        self._in_title = False
        self._buf = []

    # -- helpers
    def _flush(self):
        t = "".join(self._buf)
        self._buf = []
        return t

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "pre":
            self._in_pre += 1
        if tag in BLOCK:
            txt = self._flush().strip()
            if txt:
                self.out.append(txt)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append(f"\n{'#' * int(tag[1])} ")
        if tag == "li":
            self.out.append("- ")
        if tag == "a" and self.keep_links and a.get("href"):
            self._href, self._hreftext = a["href"], ""
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            v = a.get("content") or ""
            if k and v and k in ("og:title", "og:description", "description",
                                 "author", "article:published_time", "og:site_name"):
                self.meta[k] = v[:500]

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "pre" and self._in_pre:
            self._in_pre -= 1
        if tag == "a" and self._href:
            if self._hreftext.strip():
                self.links.append((re.sub(r"\s+", " ", self._hreftext).strip()[:120], self._href))
            self._href, self._hreftext = None, ""
        if tag in BLOCK:
            txt = self._flush().strip()
            if txt:
                self.out.append(txt)

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
            return
        if self._href is not None:
            self._hreftext += data
        if self._in_pre:
            self._buf.append(data)
        else:
            self._buf.append(re.sub(r"[ \t\r\f\v]+", " ", data))

    def text(self):
        txt = self._flush().strip()
        if txt:
            self.out.append(txt)
        raw = " ".join(self.out)
        lines, seen_recent = [], []
        for ln in raw.split("\n"):
            ln = re.sub(r"[ \t]+", " ", ln).strip()
            if not ln:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if len(ln) < 3 or BOILER.search(ln) and len(ln) < 200:
                continue
            if ln in seen_recent:
                continue
            seen_recent = (seen_recent + [ln])[-40:]
            if ln.startswith("#") or ln.startswith("- "):
                lines.append(ln)
            else:
                lines.append(ln)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_markdown(html, keep_links=False, base=None):
    r = Reader(keep_links=keep_links)
    try:
        r.feed(html)
    except Exception:
        pass
    title = re.sub(r"\s+", " ", "".join(r.title)).strip() or r.meta.get("og:title", "")
    body = r.text()
    links = r.links
    if keep_links and base:
        # half the links on a normal page are relative; a caller that wants to
        # follow them needs absolute URLs or the whole hop-2 step is useless
        fixed = []
        for anchor, href in links:
            try:
                absu = urllib.parse.urljoin(base, href)
            except Exception:
                continue
            if absu.startswith(("http://", "https://")):
                fixed.append((anchor, absu))
        links = fixed
    return title, body, r.meta, links


# ------------------------------------------------------------------ transports
def _open(url, timeout, headers=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
         "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, identity"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        return r.status, raw, dict(r.headers), r.geturl()


def pdf_to_text(raw):
    tmp = None
    try:
        if shutil.which("pdftotext"):
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            os.write(fd, raw)
            os.close(fd)
            r = subprocess.run(["pdftotext", "-layout", tmp, "-"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw))
            return "\n\n".join((p.extract_text() or "") for p in reader.pages)
        except ImportError:
            pass
    except Exception:
        pass
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return None


def fetch_direct(url, timeout, keep_links):
    t0 = time.time()
    status, raw, hdrs, final = _open(url, timeout)
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf") or raw[:4] == b"%PDF":
        txt = pdf_to_text(raw)
        if not txt:
            raise RuntimeError("PDF but no text extractor (apt install poppler-utils)")
        return {"title": os.path.basename(urllib.parse.urlparse(url).path) or url,
                "text": re.sub(r"\n{3,}", "\n\n", txt), "meta": {"content_type": "pdf"},
                "links": [], "source": "direct", "final_url": final,
                "status": status, "elapsed": round(time.time() - t0, 2)}
    if "json" in ctype:
        try:
            return {"title": url, "text": json.dumps(json.loads(raw), indent=1)[:60000],
                    "meta": {"content_type": "json"}, "links": [], "source": "direct",
                    "final_url": final, "status": status, "elapsed": round(time.time() - t0, 2)}
        except Exception:
            pass
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        enc = m.group(1)
    html = raw.decode(enc, "replace")
    title, body, meta, links = html_to_markdown(html, keep_links, final)
    if len(body) < 200:
        raise RuntimeError(f"thin content ({len(body)} chars) — probably JS-rendered")
    return {"title": title or url, "text": body, "meta": meta, "links": links,
            "source": "direct", "final_url": final, "status": status,
            "elapsed": round(time.time() - t0, 2)}


_JINA_STATE = {"dead": False, "why": ""}


def _reader_budget_sleep(i):
    time.sleep(0.8 * (i + 1))


def fetch_reader(url, timeout, keep_links=False):
    """Stage 2. Two independent relays, because either can vanish:

      a) r.jina.ai  — renders the page (JS too) from its own IP, returns markdown
      b) CORS proxy — relays the raw HTML, which we convert ourselves

    The reader is a third-party service; when it starts answering 401/429 to
    datacenter IPs we mark it dead for the run and go straight to the proxies.
    """
    errors = []
    # (a) markdown reader
    if not _JINA_STATE["dead"]:
        try:
            t0 = time.time()
            status, raw, hdrs, final = _open(JINA + url, max(timeout, 40),
                                             headers={"Accept": "text/plain"})
            txt = raw.decode("utf-8", "replace")
            if status == 200 and len(txt) >= 100:
                title = url
                m = re.search(r"^Title:\s*(.+)$", txt, re.M)
                if m:
                    title = m.group(1).strip()
                body = re.sub(r"^(Title|URL Source|Markdown Content):.*\n", "", txt,
                              flags=re.M).strip()
                if len(body) >= 200:
                    return {"title": title, "text": body,
                            "meta": {"content_type": "markdown"}, "links": [],
                            "source": "reader:jina", "final_url": url, "status": status,
                            "elapsed": round(time.time() - t0, 2)}
                errors.append(f"reader:jina thin ({len(body)}B)")
            else:
                errors.append(f"reader:jina {status}/{len(txt)}B")
                if status in (401, 402, 403, 429):
                    _JINA_STATE["dead"], _JINA_STATE["why"] = True, f"HTTP {status}"
        except urllib.error.HTTPError as e:
            errors.append(f"reader:jina HTTP {e.code}")
            if e.code in (401, 402, 403, 429):
                _JINA_STATE["dead"], _JINA_STATE["why"] = True, f"HTTP {e.code}"
        except Exception as e:
            errors.append(f"reader:jina {str(e)[:60]}")

    # (b) CORS relay of the raw HTML
    for i, (name, tpl) in enumerate(HTML_PROXIES):
        try:
            t0 = time.time()
            status, raw, hdrs, final = _open(tpl.format(urllib.parse.quote(url, safe="")),
                                            timeout)
            ctype = (hdrs.get("Content-Type") or "").lower()
            if "pdf" in ctype or raw[:4] == b"%PDF":
                txt = pdf_to_text(raw)
                if txt:
                    return {"title": os.path.basename(urllib.parse.urlparse(url).path) or url,
                            "text": re.sub(r"\n{3,}", "\n\n", txt),
                            "meta": {"content_type": "pdf"}, "links": [],
                            "source": f"reader:{name}", "final_url": url,
                            "status": status, "elapsed": round(time.time() - t0, 2)}
            html = raw.decode("utf-8", "replace")
            title, body, meta, links = html_to_markdown(html, keep_links, url)
            if len(body) >= 200:
                return {"title": title or url, "text": body, "meta": meta,
                        "links": links[:200], "source": f"reader:{name}",
                        "final_url": url, "status": status,
                        "elapsed": round(time.time() - t0, 2)}
            errors.append(f"reader:{name} thin ({len(body)}B)")
        except urllib.error.HTTPError as e:
            errors.append(f"reader:{name} HTTP {e.code}")
        except Exception as e:
            errors.append(f"reader:{name} {str(e)[:60]}")
        _reader_budget_sleep(i)
    raise RuntimeError("; ".join(errors[-3:]) or "reader exhausted")


def fetch_archive(url, timeout, keep_links):
    t0 = time.time()
    _, raw, _, _ = _open(WAYBACK + urllib.parse.quote(url, safe=""), timeout)
    js = json.loads(raw.decode("utf-8", "replace"))
    snap = (js.get("archived_snapshots") or {}).get("closest") or {}
    if not snap.get("url"):
        raise RuntimeError("no Wayback snapshot")
    res = fetch_direct(snap["url"], timeout, keep_links)
    res["source"] = "wayback"
    res["final_url"] = snap["url"]
    res["elapsed"] = round(time.time() - t0, 2)
    res["snapshot_ts"] = snap.get("timestamp")
    return res


# ----------------------------------------------------------------------- cache
def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")


def cache_get(url, ttl):
    p = cache_path(url)
    if not (ttl and os.path.exists(p)):
        return None
    try:
        d = json.load(open(p))
        if time.time() - d.get("_ts", 0) <= ttl:
            d["cached"] = True
            d["source"] = d.get("source", "cache")
            return d
    except Exception:
        pass
    return None


def cache_put(url, data):
    os.makedirs(CACHE, exist_ok=True)
    d = dict(data)
    d["_ts"] = time.time()
    try:
        json.dump(d, open(cache_path(url), "w"))
    except Exception:
        pass


# -------------------------------------------------------------------- the thing
def fetch(url, timeout=25, max_chars=20000, keep_links=False, ttl=86400,
          use_reader=True, use_archive=True, retries=1):
    hit = cache_get(url, ttl)
    # a cached entry only remembers links if it was collected with keep_links;
    # a caller that needs them (research --follow) must not inherit a linkless hit
    if hit and keep_links and not hit.get("_links_collected"):
        hit = None
    if hit:
        hit["text"] = (hit.get("text") or "")[:max_chars]
        hit["cached"] = True
        return hit

    chain = [("direct", lambda: fetch_direct(url, timeout, keep_links))]
    if use_reader:
        chain.append(("reader", lambda: fetch_reader(url, max(timeout, 40), keep_links)))
    if use_archive:
        chain.append(("archive", lambda: fetch_archive(url, timeout, keep_links)))

    errors = []
    for name, fn in chain:
        for attempt in range(retries + 1):
            try:
                res = fn()
                if not (res.get("text") or "").strip():
                    raise RuntimeError("empty text")
                res["text"] = res["text"][:max_chars]
                res["chars"] = len(res["text"])
                res["ok"] = True
                res["url"] = url
                res["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                res["errors"] = errors
                if keep_links:
                    res["_links_collected"] = True
                cache_put(url, res)
                return res
            except urllib.error.HTTPError as e:
                errors.append(f"{name}: HTTP {e.code}")
                if e.code in (401, 403, 404, 410):
                    break
            except Exception as e:
                errors.append(f"{name}: {str(e)[:110]}")
            time.sleep(0.8 * (attempt + 1))

    return {"ok": False, "url": url, "title": "", "text": "", "chars": 0,
            "source": None, "errors": errors, "links": []}


# ------------------------------------------------------------------------- cli
def to_markdown(res):
    fm = [f"url: {res['url']}"]
    for k in ("title", "final_url", "source", "chars", "fetched_at", "snapshot_ts"):
        if res.get(k):
            fm.append(f"{k}: {res[k]}")
    body = f"---\n" + "\n".join(fm) + "\n---\n\n"
    if res.get("title"):
        body += f"# {res['title']}\n\n"
    body += res["text"]
    if res.get("links"):
        body += "\n\n## Links\n" + "\n".join(f"- [{t}]({u})" for t, u in res["links"][:80])
    return body


def main():
    ap = argparse.ArgumentParser(description="resilient URL -> markdown for agents")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default=None, help="directory for .md files")
    ap.add_argument("--max-chars", type=int, default=20000)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--ttl", type=int, default=86400, help="cache TTL seconds (0 disables)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-reader", action="store_true")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="store_true", help="print the extracted text")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    ttl = 0 if a.no_cache else a.ttl
    results = []
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        futs = {ex.submit(fetch, u, a.timeout, a.max_chars, a.links, ttl,
                          not a.no_reader, not a.no_archive): u for u in a.urls}
        for f in as_completed(futs):
            results.append(f.result())

    if a.out:
        os.makedirs(a.out, exist_ok=True)
    for r in results:
        if r.get("ok") and a.out:
            name = re.sub(r"[^a-zA-Z0-9]+", "_", r.get("title") or
                          urllib.parse.urlparse(r["url"]).netloc)[:60].strip("_") or "page"
            p = os.path.join(a.out, name + ".md")
            i = 1
            while os.path.exists(p):
                i += 1
                p = os.path.join(a.out, f"{name}_{i}.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(to_markdown(r))
            r["file"] = p

    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "text"} for r in results],
                         indent=1))
    elif not a.quiet:
        for r in results:
            if r.get("ok"):
                print(f"[ok]   {r['chars']:>6} chars · {r['source']:<8} · "
                      f"{r['elapsed'] if not r.get('cached') else 0:.1f}s · {r['title'][:70]}")
                if r.get("file"):
                    print(f"       -> {r['file']}")
            else:
                print(f"[fail] {r['url'][:90]}")
                for e in r["errors"][-3:]:
                    print(f"       {e}")
    if a.text:
        for r in results:
            if r.get("ok"):
                print(f"\n===== {r['url']} =====\n{r['text']}")

    return 0 if any(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
