"""Web tools for the eval scaffold: Tavily search + full-page fetch.

v1.1 additions:
  - CACHE: every search query and page fetch is cached on disk
    (search-cache.json). The exact same query/url returns the exact same
    payload without re-querying — free, deterministic, auditable.
  - LOGGING: callers receive (payload, cached) and record every tool call
    (query/url + full payload) into the per-case tool_log.
  - fetch_page(url): plain HTTP GET + HTML->text (stdlib only), truncated
    to FETCH_MAX_CHARS. No search credits consumed.

Executed by the scaffold (candidate models all run on Bedrock).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

HERE = Path(__file__).parent
CACHE_FILE = HERE / "search-cache.json"

TOOL_NAME = "web_search"
TOOL_DESCRIPTION = (
    "Search the web. Returns the top results as JSON: a list of objects "
    "with title, url, and content (a text snippet from the page). Use "
    "focused queries; call again with refined queries as needed.")
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The search query."},
    },
    "required": ["query"],
    "additionalProperties": False,
}

FETCH_TOOL_NAME = "fetch_page"
FETCH_TOOL_DESCRIPTION = (
    "Fetch a web page and return its full extracted content. Use it to "
    "verify facts that search snippets only hint at — tables, statistics, "
    "exact values. Pass a URL from search results.")
FETCH_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The page URL to fetch."},
    },
    "required": ["url"],
    "additionalProperties": False,
}

MAX_RESULTS = 5
# Full-page extraction. The cap is a context-safety ceiling, not a snippet:
# 30k chars ~ 7.5k tokens per page (few pages exceed it).
FETCH_MAX_CHARS = 30_000
# Tavily list price ~$8 per 1000 basic-search credits. Cached hits and page
# fetches cost nothing.
SEARCH_RATE_PER_1K_USD = 8.0

_lock = threading.Lock()
_keys: list[str] | None = None
_cycle = None
_dead: set[str] = set()

_cache_lock = threading.Lock()
_cache: dict[str, str] | None = None


# ------------------------------------------------------------------ cache

def _cache_load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache

def _cache_get(key: str) -> str | None:
    with _cache_lock:
        return _cache_load().get(key)

def _cache_put(key: str, value: str) -> None:
    with _cache_lock:
        cache = _cache_load()
        cache[key] = value
        tmp = Path(f"{CACHE_FILE}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False))
        tmp.replace(CACHE_FILE)


# ----------------------------------------------------------------- search

def _get_key() -> str:
    global _keys, _cycle
    with _lock:
        if _keys is None:
            raw = os.environ["TAVILY_API_KEYS"]
            _keys = [k.strip() for k in raw.split(",") if k.strip()]
            _cycle = itertools.cycle(_keys)
        for _ in range(len(_keys)):
            key = next(_cycle)
            if key not in _dead or len(_dead) == len(_keys):
                return key
        return next(_cycle)


def web_search(query: str, retries: int = 3) -> tuple[str, bool]:
    """One Tavily search. Returns (json payload, served_from_cache)."""
    cache_key = f"search:{query.strip()}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit, True
    last = None
    for attempt in range(retries + 1):
        key = _get_key()
        try:
            r = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query,
                      "max_results": MAX_RESULTS, "search_depth": "basic"},
                timeout=30)
            if r.status_code in (429, 432):
                with _lock:
                    _dead.add(key)
                raise RuntimeError(f"tavily throttled ({r.status_code})")
            r.raise_for_status()
            results = [
                {"title": item.get("title"), "url": item.get("url"),
                 "content": (item.get("content") or "")[:1200]}
                for item in r.json().get("results", [])]
            payload = json.dumps({"results": results}, ensure_ascii=False)
            _cache_put(cache_key, payload)
            return payload, False
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    # errors are NOT cached — a later retry may succeed
    return (json.dumps({"error": f"search failed: {type(last).__name__}: "
                                 f"{str(last)[:150]}"}), False)


# ------------------------------------------------------------------ fetch

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _extract_tavily(url: str) -> str | None:
    """Full-page extraction via the Tavily Extract API. None on failure."""
    r = httpx.post("https://api.tavily.com/extract",
                   json={"api_key": _get_key(), "urls": [url]},
                   timeout=45)
    r.raise_for_status()
    results = r.json().get("results", [])
    if results and results[0].get("raw_content"):
        return results[0]["raw_content"]
    return None


def _extract_plain(url: str) -> str:
    """Fallback: plain GET + stdlib HTML->text extraction."""
    r = httpx.get(
        url, timeout=25, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                               "10_15_7) AppleWebKit/537.36 (KHTML, like "
                               "Gecko) Chrome/126 Safari/537.36"})
    r.raise_for_status()
    if "html" in r.headers.get("content-type", ""):
        ex = _TextExtractor()
        ex.feed(r.text)
        return re.sub(r"\s{3,}", "  ", " ".join(ex.parts))
    return r.text


def fetch_page(url: str, retries: int = 2) -> tuple[str, bool]:
    """Full-page extraction (Tavily Extract, plain-GET fallback).

    Returns (json payload, from_cache). Cache key is versioned so v1.1's
    truncated 8k entries are never served for full-page requests.
    """
    cache_key = f"fetch-full:{url.strip()}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit, True
    last = None
    for attempt in range(retries + 1):
        try:
            text = None
            try:
                text = _extract_tavily(url)
            except Exception:  # noqa: BLE001 — fall through to plain GET
                text = None
            if not text:
                text = _extract_plain(url)
            payload = json.dumps(
                {"url": url, "content": text[:FETCH_MAX_CHARS],
                 "truncated": len(text) > FETCH_MAX_CHARS},
                ensure_ascii=False)
            _cache_put(cache_key, payload)
            return payload, False
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    return (json.dumps({"error": f"fetch failed: {type(last).__name__}: "
                                 f"{str(last)[:150]}"}), False)
