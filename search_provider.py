"""Web search abstraction for company enrichment.

Reliability strategy (no API key required, but keys are used if present):
  - Paid APIs first when a key exists: SerpAPI, Brave.
  - Keyless engines with rotation + polite global rate limiting + per-engine
    cooldown on 403/429: Mojeek, DuckDuckGo (html), DuckDuckGo (lite).

All keyless engines aggressively rate-limit scrapers, so we:
  * enforce a global minimum interval between outbound search requests,
  * rotate across engines and retry,
  * put an engine on cooldown after it blocks us.

Results from any provider are CANDIDATES only; the caller must verify them
against ground truth (company name / orgnr / phone) before storing anything.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any, Callable

# Rotate a small pool of realistic desktop User-Agents to look less bot-like.
_UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
]
UA = _UA_POOL[0]

# Public SearXNG instances (they proxy Google/Bing/etc). Rotated; many rate-limit.
_SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.rhscz.eu",
    "https://priv.au",
    "https://search.inetol.net",
    "https://opnxng.com",
    "https://baresearch.org",
    "https://search.hbubli.cc",
    "https://searx.work",
]


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env into os.environ (no dependency)."""
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_dotenv()

# Global politeness: minimum seconds between ANY two outbound search requests.
# Defaults are deliberately high: this runs a slow, keyless, low-volume pipeline
# (a handful of favorites per day) where reliability matters far more than speed.
# Large gaps keep us under the free engines' rate limits so jobs actually return
# data instead of being throttled to empty.
_MIN_INTERVAL = float(os.environ.get("SWEDENIRAN_SEARCH_MIN_INTERVAL", "8.0"))
_COOLDOWN = float(os.environ.get("SWEDENIRAN_SEARCH_COOLDOWN", "300"))

_rate_lock = threading.Lock()
_last_request_at = 0.0
_engine_cooldown: dict[str, float] = {}

# Keyless engines we rotate across (must match _keyless_search below).
_KEYLESS_ENGINES = ("mojeek", "ddg_html", "searxng", "ddg_lite")


def any_engine_available() -> bool:
    """True if a search backend is ready to use right now.

    A paid key is always considered available; otherwise at least one keyless
    engine must be off cooldown. Workers poll this so they idle (instead of
    storing empty results) while every free engine is being rate-limited.
    """
    if os.environ.get("SERPAPI_KEY") or os.environ.get("BRAVE_SEARCH_API_KEY"):
        return True
    now = time.time()
    return any(_engine_cooldown.get(name, 0.0) <= now for name in _KEYLESS_ENGINES)


def _throttle() -> None:
    global _last_request_at
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": random.choice(_UA_POOL),
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def provider_name() -> str:
    if os.environ.get("SERPAPI_KEY"):
        return "serpapi"
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        return "brave"
    return "keyless"


def web_search(query: str, count: int = 8) -> list[dict[str, str]]:
    """Return a list of {title, url, snippet} candidates. Never raises."""
    if os.environ.get("SERPAPI_KEY"):
        try:
            res = _serpapi(query, count)
            if res:
                return res
        except Exception:
            pass
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        try:
            res = _brave(query, count)
            if res:
                return res
        except Exception:
            pass
    return _keyless_search(query, count)


# ---------------------------------------------------------------- keyless engines

def _keyless_search(query: str, count: int) -> list[dict[str, str]]:
    engines: list[tuple[str, Callable[[str, int], list[dict[str, str]]]]] = [
        ("mojeek", _mojeek),
        ("ddg_html", _ddg_html),
        ("searxng", _searxng),
        ("ddg_lite", _ddg_lite),
    ]
    now = time.time()
    available = [(n, fn) for n, fn in engines if _engine_cooldown.get(n, 0) <= now]
    # If every engine is cooling down, fail fast (no throttle) so deep-enrich
    # jobs finish quickly and newly-favorited names get picked up promptly.
    # The retry loop will try again later once cooldowns expire.
    if not available:
        return []
    for name, fn in available:
        _throttle()
        try:
            results = fn(query, count)
        except urllib.error.HTTPError as e:
            if e.code in (202, 403, 429, 503):
                _engine_cooldown[name] = time.time() + _COOLDOWN
            continue
        except Exception:
            continue
        if results:
            return results[:count]
        # Empty result usually means a soft block; brief cooldown.
        _engine_cooldown[name] = time.time() + min(_COOLDOWN, 30)
    return []


_MOJEEK_RE = re.compile(r'<a class="ob"[^>]+href="(https?://[^"]+)"', re.I)
_MOJEEK_TITLE_RE = re.compile(r'<a class="title"[^>]+href="https?://[^"]+"[^>]*>(.*?)</a>', re.S | re.I)


def _mojeek(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query})
    html = _http_get(f"https://www.mojeek.com/search?{params}")
    urls = _MOJEEK_RE.findall(html)
    titles = [_strip_tags(t) for t in _MOJEEK_TITLE_RE.findall(html)]
    out = []
    for i, url in enumerate(urls):
        out.append({"title": titles[i] if i < len(titles) else "", "url": url, "snippet": ""})
    return out


_searxng_idx = 0
_SEARX_HTML_RE = re.compile(r'<article[^>]+class="result[^"]*"[^>]*>.*?<a[^>]+href="(https?://[^"]+)"', re.S | re.I)


def _searxng(query: str, count: int) -> list[dict[str, str]]:
    """Query ONE rotating SearXNG instance (JSON, short timeout) per call.

    Trying many instances/formats per call caused multi-minute hangs, so we hit
    a single instance with a tight timeout and rotate on each subsequent call.
    Raises HTTPError on 429 so the caller can cool the whole engine down.
    """
    global _searxng_idx
    base = _SEARXNG_INSTANCES[_searxng_idx % len(_SEARXNG_INSTANCES)]
    _searxng_idx = (_searxng_idx + 1) % len(_SEARXNG_INSTANCES)
    params = urllib.parse.urlencode(
        {"q": query, "language": "en", "safesearch": "0", "format": "json"}
    )
    raw = _http_get(f"{base}/search?{params}", timeout=6)
    data = json.loads(raw)
    return [
        {"title": r.get("title") or "", "url": r["url"], "snippet": r.get("content") or ""}
        for r in data.get("results", []) if r.get("url")
    ][:count]


_DDG_LINK_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_DDG_LITE_RE = re.compile(r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _ddg_html(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query, "kl": "se-sv"})
    html = _http_get(f"https://html.duckduckgo.com/html/?{params}")
    return _parse_ddg(html, _DDG_LINK_RE)


def _ddg_lite(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query, "kl": "se-sv"})
    html = _http_get(f"https://lite.duckduckgo.com/lite/?{params}")
    return _parse_ddg(html, _DDG_LITE_RE)


def _parse_ddg(html: str, link_re: re.Pattern) -> list[dict[str, str]]:
    links = link_re.findall(html)
    snippets = _DDG_SNIPPET_RE.findall(html)
    out: list[dict[str, str]] = []
    for index, (href, title) in enumerate(links):
        url = _ddg_unwrap(href)
        if not url:
            continue
        snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
        out.append({"title": _strip_tags(title), "url": url, "snippet": snippet})
    return out


def _ddg_unwrap(href: str) -> str | None:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        return qs.get("uddg", [None])[0]
    if parsed.scheme in ("http", "https"):
        return href
    return None


# ---------------------------------------------------------------- paid APIs

def _serpapi(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode(
        {"q": query, "num": count, "engine": "google", "hl": "en", "gl": "se",
         "api_key": os.environ["SERPAPI_KEY"]}
    )
    _throttle()
    data = json.loads(_http_get(f"https://serpapi.com/search.json?{params}"))
    out = []
    for item in data.get("organic_results") or []:
        if item.get("link"):
            out.append({"title": item.get("title") or "", "url": item["link"],
                        "snippet": item.get("snippet") or ""})
    return out


def _brave(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query, "count": count, "country": "se"})
    _throttle()
    raw = _http_get(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"Accept": "application/json",
                 "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]},
    )
    data = json.loads(raw)
    out = []
    for item in (data.get("web") or {}).get("results") or []:
        if item.get("url"):
            out.append({"title": item.get("title") or "", "url": item["url"],
                        "snippet": item.get("description") or ""})
    return out


# ---------------------------------------------------------------- translation

_translate_cache: dict[str, str] = {}


def translate_to_english(text: str, source: str = "sv") -> str | None:
    """Translate text to English via Google's public gtx endpoint. Best-effort."""
    text = (text or "").strip()
    if not text:
        return None
    key = f"{source}:{text}"
    if key in _translate_cache:
        return _translate_cache[key]
    try:
        params = urllib.parse.urlencode(
            {"client": "gtx", "sl": source, "tl": "en", "dt": "t", "q": text}
        )
        raw = _http_get(
            f"https://translate.googleapis.com/translate_a/single?{params}", timeout=12
        )
        data = json.loads(raw)
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        translated = translated.strip()
        if translated:
            _translate_cache[key] = translated
            return translated
    except Exception:
        return None
    return None


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(value: str) -> str:
    return unescape(_TAG_RE.sub("", value or "")).strip()
