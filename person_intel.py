"""Person deep-enrichment engine.

Goal: for a FAVORITED person, find their own public profiles — LinkedIn,
Instagram, Facebook, Twitter/X — via web search, verified against ground truth.

Common Iranian/Swedish names are extremely ambiguous, so every query is scoped
with "sweden" and, when available, a disambiguator pulled from allabolag data
(one of the person's companies or their home municipality/county). A candidate
is only kept when the person's name is confirmed in the profile slug or the
result title/snippet; matches that also mention a disambiguator are trusted
("high"), name-only matches stay "medium"/"low".

Mirrors company_intel.enrich_company: returns ONLY confident fields, every one
carrying a confidence level and a source URL.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Any

import company_intel as ci
import search_provider


def _name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s]", " ", ci._fold(name))
    return [t for t in cleaned.split() if len(t) >= 2]


def _first_last(tokens: list[str]) -> tuple[str, str]:
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], tokens[0]
    return tokens[0], tokens[-1]


def _text_has_name(text: str, first: str, last: str) -> bool:
    folded = ci._fold(text or "")
    return bool(first) and bool(last) and first in folded and last in folded


def _slug_has_name(slug: str, first: str, last: str) -> bool:
    s = ci._fold(slug).replace("-", "").replace("_", "").replace(".", "")
    return bool(first) and bool(last) and first in s and last in s


def _disamb_hit(text: str, disamb_tokens: set[str]) -> bool:
    if not disamb_tokens:
        return False
    folded = ci._fold(text or "")
    return any(tok in folded for tok in disamb_tokens)


def _profile_slug(url: str, kind: str) -> str | None:
    low = url.lower().split("?")[0].split("#")[0]
    parsed = urllib.parse.urlparse(low)
    segments = [seg for seg in parsed.path.strip("/").split("/") if seg]
    if not segments:
        return None
    if kind == "linkedin":
        if len(segments) >= 2 and segments[0] == "in":
            return segments[1]
        return None
    # instagram / facebook / twitter: the first path segment is the handle.
    return segments[0]


def _canonical_url(kind: str, slug: str, original: str) -> str:
    if kind == "linkedin":
        return f"https://www.linkedin.com/in/{slug}"
    if kind == "instagram":
        return f"https://www.instagram.com/{slug}"
    if kind == "facebook":
        # profile.php?id=... carries the real id in the query string.
        if slug == "profile.php":
            return original
        return f"https://www.facebook.com/{slug}"
    if kind == "twitter":
        return f"https://twitter.com/{slug}"
    return original


def _discover_social(
    kind: str,
    queries: list[str],
    name_tokens: list[str],
    disamb_tokens: set[str],
    *,
    slug_check: bool,
) -> dict[str, Any] | None:
    """Search for the person's profile of `kind`, strictly verified.

    Ranking (higher wins, 3 short-circuits): a confirmed name match plus a
    disambiguator is best; a slug name match alone is solid; a text-only name
    match without any disambiguator is risky for common names and kept last.
    """
    first, last = _first_last(name_tokens)
    if not first:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    seen: set[str] = set()
    for query in queries:
        if not query.strip():
            continue
        for cand in search_provider.web_search(query, count=10):
            url = cand.get("url") or ""
            if ci._classify_social(url) != kind:
                continue
            slug = _profile_slug(url, kind)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            text = f"{cand.get('title', '')} {cand.get('snippet', '')}"
            slug_ok = slug_check and _slug_has_name(slug, first, last)
            text_ok = _text_has_name(text, first, last)
            if not (slug_ok or text_ok):
                continue
            disamb_ok = _disamb_hit(text, disamb_tokens) or _disamb_hit(slug, disamb_tokens)
            if disamb_ok and (slug_ok or text_ok):
                rank, conf = 3, "high"
            elif slug_ok:
                rank, conf = 2, "medium"
            elif text_ok and disamb_ok:
                rank, conf = 2, "medium"
            else:
                rank, conf = 1, "low"
            out = {
                "url": _canonical_url(kind, slug, url),
                "confidence": conf,
                "source": url,
                "snippet": (cand.get("snippet") or "").strip()[:300],
            }
            if best is None or rank > best[0]:
                best = (rank, out)
                if rank >= 3:
                    return out
    return best[1] if best else None


def enrich_person(
    name: str,
    *,
    companies: list[dict[str, Any]] | None = None,
    cities: list[str] | None = None,
) -> dict[str, Any]:
    """Return verified personal intel for a favorite. Only confident fields."""
    result: dict[str, Any] = {"name": name}
    evidence: list[dict[str, Any]] = []
    name_tokens = _name_tokens(name)
    if not name_tokens:
        result["socials"] = {}
        result["evidence"] = evidence
        result["search_provider"] = search_provider.provider_name()
        return result

    companies = companies or []
    cities = cities or []

    # Disambiguators: distinctive company tokens + home cities (for verifying
    # candidates) and full company name / city (for steering queries).
    disamb_tokens: set[str] = set()
    company_names: list[str] = []
    for c in companies[:3]:
        cn = (c.get("company_name") or "").strip()
        if cn:
            company_names.append(cn)
            for tok in ci._distinctive_tokens(cn):
                disamb_tokens.add(tok)
    city_list: list[str] = []
    for city in cities:
        city = (city or "").strip()
        if city and city not in city_list:
            city_list.append(city)
            disamb_tokens.add(ci._fold(city))
    primary_disamb = company_names[0] if company_names else (city_list[0] if city_list else "")

    socials: dict[str, str] = {}

    # LinkedIn — highest value; person slugs usually contain the real name.
    li_queries = [
        f'"{name}" linkedin sweden',
        f'"{name}" {primary_disamb} linkedin' if primary_disamb else f"{name} sweden linkedin",
        f'"{name}" site:linkedin.com/in',
    ]
    li = _discover_social("linkedin", li_queries, name_tokens, disamb_tokens, slug_check=True)
    if li:
        result["linkedin_url"] = li["url"]
        result["linkedin_confidence"] = li["confidence"]
        result["linkedin_source"] = li["source"]
        if li.get("snippet"):
            result["headline"] = li["snippet"]
        socials["linkedin"] = li["url"]
        evidence.append({"type": "linkedin", "url": li["source"], "confidence": li["confidence"]})
    time.sleep(0.3)

    # Instagram — handles rarely equal the name, so verify via title/snippet.
    ig_queries = [
        f'"{name}" instagram sweden',
        f'"{name}" {primary_disamb} instagram' if primary_disamb else f"{name} sweden instagram",
    ]
    ig = _discover_social("instagram", ig_queries, name_tokens, disamb_tokens, slug_check=False)
    if ig:
        result["instagram_url"] = ig["url"]
        result["instagram_confidence"] = ig["confidence"]
        result["instagram_source"] = ig["source"]
        socials["instagram"] = ig["url"]
        evidence.append({"type": "instagram", "url": ig["source"], "confidence": ig["confidence"]})
    time.sleep(0.3)

    # Facebook + Twitter/X — lower priority, single query each.
    fb_query = f'"{name}" facebook sweden' + (f" {primary_disamb}" if primary_disamb else "")
    fb = _discover_social("facebook", [fb_query], name_tokens, disamb_tokens, slug_check=False)
    if fb:
        socials.setdefault("facebook", fb["url"])
        evidence.append({"type": "facebook", "url": fb["source"], "confidence": fb["confidence"]})
    time.sleep(0.3)
    tw_query = f'"{name}" twitter sweden' + (f" {primary_disamb}" if primary_disamb else "")
    tw = _discover_social("twitter", [tw_query], name_tokens, disamb_tokens, slug_check=False)
    if tw:
        socials.setdefault("twitter", tw["url"])
        evidence.append({"type": "twitter", "url": tw["source"], "confidence": tw["confidence"]})

    # Translate a Swedish headline to English (keep original as *_sv).
    if result.get("headline"):
        english = search_provider.translate_to_english(result["headline"], "sv")
        if english and english.strip().lower() != result["headline"].strip().lower():
            result["headline_sv"] = result["headline"]
            result["headline"] = english

    result["socials"] = {k: v for k, v in socials.items() if v}
    result["evidence"] = evidence
    result["search_provider"] = search_provider.provider_name()
    return result
