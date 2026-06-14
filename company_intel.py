"""Company deep-enrichment engine.

Goal: return ONLY trustworthy company data. Every field carries a confidence
level and a source. If a candidate cannot be verified against ground truth
(company name / orgnr / known phone), it is dropped rather than guessed.

Sources, in order of trust:
  1. allabolag company page (registry-grade fields: phone, email, homepage,
     social links, external links, description, purpose, addresses, ...)
  2. The official website itself (self-referenced links = high trust):
     LinkedIn/Instagram/Facebook outbound links, contact email/phone.
  3. Web search candidates (only kept if verified against ground truth).
"""

from __future__ import annotations

import re
import time
import unicodedata
import urllib.parse
from html import unescape
from typing import Any

import allabolag
import search_provider

_LEGAL_SUFFIXES = {
    "ab", "hb", "kb", "aktiebolag", "handelsbolag", "kommanditbolag",
    "ekonomisk", "forening", "förening", "stiftelse", "holding", "group",
    "gruppen", "i", "och", "the", "co", "as", "asa", "oy", "gmbh", "ltd",
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?46[\s\-]?|0)(?:\d[\s\-]?){6,12}\d")
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.S | re.I
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)

_SOCIAL_HOSTS = {
    "linkedin": ("linkedin.com/company", "linkedin.com/in"),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com",),
    "twitter": ("twitter.com", "x.com"),
    "youtube": ("youtube.com",),
}

# Generic/share/locale paths that are NOT a company's own profile.
_SOCIAL_REJECT = (
    "/share", "/sharer", "/sharearticle", "/sharing", "/intent", "/dialog",
    "/plugins", "/tr?", "/login", "/signup", "/help", "/about", "/legal",
    "/explore", "/hashtag", "/policies", "/uas/", "/feed", "/home", "/pin/",
    "/watch", "/results", "/embed", "/widgets",
)
# Bare hosts (no real path) we must reject, e.g. "se.linkedin.com".
_SOCIAL_BARE = (
    "linkedin.com", "instagram.com", "facebook.com", "twitter.com",
    "x.com", "youtube.com", "youtu.be",
)

# Hosts that are never a company's own site (directories/aggregators/social).
_BLOCKED_WEBSITE_HOSTS = (
    "allabolag.se", "ratsit.se", "merinfo.se", "hitta.se", "eniro.se",
    "proff.se", "linkedin.com", "facebook.com", "instagram.com",
    "wikipedia.org", "google.", "youtube.com", "bing.com", "twitter.com",
    "x.com", "bolagsfakta.se", "largestcompanies.se", "birthday.se",
    "cylex.se", "vainu.com", "bizzdo.se", "revieweuro.com", "yelp.",
    "foursquare.com", "tripadvisor.", "indeed.com", "glassdoor.",
    "crunchbase.com", "opencorporates.com", "infobel.com", "europages.",
    "wlw.se", "n.nu", "company-information.", "trustpilot.com",
    "booking.com", "tiktok.com", "pinterest.", "apple.com", "amazon.",
    "1177.se", "vården.se", "vardguiden", "kreditrapporten", "bizzy.se",
    "allabolag.", "merinfo.", "synna.se", "upplysning.se", "118100.se",
    "118118.se", "sajten.se", "zaubee.com", "nicelocal.", "biz.nf",
)


# ----------------------------------------------------------------- helpers

def _fold(value: str) -> str:
    """Lowercase and strip Swedish diacritics for robust matching."""
    value = (value or "").lower()
    for old, new in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u"), ("ø", "o"), ("æ", "ae")):
        value = value.replace(old, new)
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _name_tokens(company_name: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s]", " ", _fold(company_name))
    tokens = [t for t in cleaned.split() if len(t) >= 3 and t not in _LEGAL_SUFFIXES]
    return tokens


def _distinctive_tokens(company_name: str) -> list[str]:
    """Tokens that strongly identify the company (longest first)."""
    return sorted(set(_name_tokens(company_name)), key=len, reverse=True)


def _norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"[^\d+]", "", value)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0"):
        digits = "+46" + digits[1:]
    if not digits.startswith("+"):
        if digits.startswith("46"):
            digits = "+" + digits
        else:
            return value.strip()
    return digits


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_blocked_host(url: str) -> bool:
    host = _host(url)
    return any(b in host or b in url.lower() for b in _BLOCKED_WEBSITE_HOSTS)


def _fetch_text(url: str, timeout: int = 10) -> str:
    return search_provider._http_get(url, timeout=timeout)


def _clean_homepage(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        value = value.get("url") or value.get("href") or value.get("value")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if not value.startswith("http"):
        value = "https://" + value.lstrip("/")
    return value


# ----------------------------------------------------------------- allabolag tier

def _allabolag_company(orgnr: str) -> dict[str, Any]:
    pp = allabolag._parse_json_or_next(allabolag._fetch(f"{allabolag.BASE}/{orgnr}"))
    return pp.get("company") or {}


def _collect_allabolag_fields(co: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    src = f"{allabolag.BASE}/{co.get('orgnr') or ''}"

    homepage = _clean_homepage(co.get("homePage")) or _clean_homepage(co.get("webshopUrl"))
    if homepage:
        out["website"] = homepage
        out["website_confidence"] = "high"
        out["website_source"] = src

    phone = co.get("phone") or co.get("phone2") or co.get("mobile") or co.get("legalPhone")
    phone = _norm_phone(phone)
    if phone:
        out["phone"] = phone
        out["phone_source"] = src

    email = co.get("email")
    if email and _EMAIL_RE.fullmatch(email.strip()):
        out["email"] = email.strip().lower()
        out["email_source"] = src

    socials: dict[str, str] = {}
    for link in _iter_links(co.get("socialMediaLinks")) + _iter_links(co.get("externalLinks")):
        kind = _classify_social(link)
        if kind and kind not in socials:
            socials[kind] = link
    if socials:
        out["socials"] = socials
        out["socials_source"] = src
        if socials.get("linkedin"):
            out["linkedin_url"] = socials["linkedin"]
            out["linkedin_confidence"] = "high"

    description = (co.get("description") or "").strip() or (co.get("tagLine") or "").strip()
    if description:
        out["description"] = description[:1200]
    purpose = (co.get("purpose") or "").strip()
    if purpose:
        out["purpose"] = purpose[:1200]

    keywords = co.get("keywords")
    if isinstance(keywords, list):
        kw = [str(k).strip() for k in keywords if str(k).strip()]
        if kw:
            out["keywords"] = kw[:25]

    addr = co.get("visitorAddress") or co.get("postalAddress")
    if isinstance(addr, dict):
        parts = [addr.get("addressLine"), addr.get("zipCode"), addr.get("postPlace")]
        addr_str = ", ".join(p for p in parts if p)
        if addr_str:
            out["address"] = addr_str

    if co.get("foundationYear"):
        out["foundation_year"] = str(co["foundationYear"])

    # Announcements / news straight from registry (reliable).
    news = []
    for ann in co.get("announcements") or []:
        if isinstance(ann, dict):
            title = (ann.get("title") or ann.get("type") or "").strip()
            date = (ann.get("date") or ann.get("registrationDate") or "").strip()
            if title:
                news.append({"title": title, "date": date, "source": src})
    if news:
        out["news"] = news[:10]

    if co.get("gaselle"):
        out.setdefault("achievements", []).append(
            {"label": "Gasell company (DI Gasell)", "source": src}
        )
    certs = []
    for cert in (co.get("certifications") or []) + (co.get("certificates") or []):
        if isinstance(cert, dict) and (cert.get("name") or cert.get("title")):
            certs.append((cert.get("name") or cert.get("title")).strip())
        elif isinstance(cert, str) and cert.strip():
            certs.append(cert.strip())
    if certs:
        out["certifications"] = certs[:15]

    evidence.append({"type": "allabolag", "url": src, "confidence": "high"})
    out["_evidence"] = evidence
    return out


def _iter_links(value: Any) -> list[str]:
    links: list[str] = []
    if not value:
        return links
    if isinstance(value, str):
        if value.startswith("http"):
            links.append(value)
        return links
    if isinstance(value, dict):
        for key in ("url", "href", "link", "value"):
            if isinstance(value.get(key), str) and value[key].startswith("http"):
                links.append(value[key])
        return links
    if isinstance(value, list):
        for item in value:
            links.extend(_iter_links(item))
    return links


def _classify_social(url: str) -> str | None:
    low = url.lower().split("?")[0].split("#")[0]
    if any(bad in low for bad in _SOCIAL_REJECT):
        return None
    parsed = urllib.parse.urlparse(low)
    host = parsed.netloc
    path = parsed.path.strip("/")

    if "linkedin.com" in host:
        # Only real company/person/school pages with a slug.
        if re.match(r"^(company|in|school)/[^/]+", path):
            return "linkedin"
        return None
    if "instagram.com" in host:
        if path and path not in ("p", "reel", "reels", "tv", "accounts", "stories"):
            return "instagram"
        return None
    if "facebook.com" in host or "fb.com" in host:
        if path and path not in ("profile.php",) and not path.startswith("groups"):
            return "facebook"
        if path.startswith("profile.php"):
            return "facebook"
        return None
    if "twitter.com" in host or host == "x.com" or host.endswith(".x.com"):
        if path and path not in ("home", "search", "i", "compose", "messages"):
            return "twitter"
        return None
    if "youtube.com" in host:
        if re.match(r"^(channel|user|c|@)", path) or path.startswith("@"):
            return "youtube"
        return None
    return None


# ----------------------------------------------------------------- website tier

def _enrich_from_website(url: str, name_tokens: list[str]) -> dict[str, Any]:
    """Fetch the official site and pull self-referenced contact/social data."""
    out: dict[str, Any] = {}
    try:
        html = _fetch_text(url)
    except Exception:
        return out
    low = html.lower()

    title_m = _TITLE_RE.search(html)
    if title_m:
        out["website_title"] = _strip_tags(title_m.group(1))[:200]
    desc_m = _META_DESC_RE.search(html)
    if desc_m:
        out["website_description"] = unescape(desc_m.group(1)).strip()[:600]

    socials: dict[str, str] = {}
    for href in _HREF_RE.findall(html):
        kind = _classify_social(href)
        if kind and kind not in socials:
            socials[kind] = _abs_url(url, href)
    if socials:
        out["socials"] = socials
        if socials.get("linkedin"):
            out["linkedin_url"] = socials["linkedin"]
            out["linkedin_confidence"] = "high"  # self-referenced

    emails = [e.lower() for e in _EMAIL_RE.findall(html) if not e.lower().endswith((".png", ".jpg", ".gif"))]
    emails = [e for e in emails if "sentry" not in e and "example" not in e]
    if emails:
        out["website_emails"] = _dedupe(emails)[:5]

    phones = [_norm_phone(p) for p in _PHONE_RE.findall(html)]
    phones = [p for p in phones if p]
    if phones:
        out["website_phones"] = _dedupe(phones)[:5]

    out["website_verified"] = _page_mentions_company(low, name_tokens)
    return out


def _abs_url(base: str, href: str) -> str:
    try:
        return urllib.parse.urljoin(base, href)
    except Exception:
        return href


def _page_mentions_company(low_html: str, name_tokens: list[str]) -> bool:
    if not name_tokens:
        return False
    folded = _fold(low_html)
    return any(tok in folded for tok in name_tokens)


def _strip_tags(value: str) -> str:
    return unescape(_TAG_RE.sub("", value or "")).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# ----------------------------------------------------------------- search tier

def _registrable_domain(url: str) -> str:
    """Return the last two labels of the host, e.g. se.foo.com -> foo.com."""
    host = _host(url)
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    # Handle common 2-level TLDs lightly (e.g. co.uk); SE companies mostly .se/.com.
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net") and labels[-1] in ("uk", "se"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _discover_website(company_name: str, orgnr: str, municipality: str | None,
                      name_tokens: list[str], known_phone: str | None) -> dict[str, Any] | None:
    """Find the company's OWN website via search, strictly verified.

    Hard requirement to avoid directory false-positives: a distinctive company
    token must appear in the registrable domain itself (the company's own site),
    AND the page content must confirm the company (name or orgnr). Phone/name
    matches alone are NOT accepted because directories contain those too.
    """
    if not name_tokens:
        return None
    query = f'"{company_name}"' + (f" {municipality}" if municipality else "") + " sweden"
    checked = 0
    for cand in search_provider.web_search(query, count=10):
        url = cand.get("url") or ""
        if not url or _is_blocked_host(url):
            continue
        domain = _registrable_domain(url).replace("-", "").replace(".", "")
        domain_match = any(tok in domain for tok in name_tokens)
        if not domain_match:
            continue  # only accept the company's own-looking domain
        if checked >= 6:
            break
        checked += 1
        try:
            html = _fetch_text(url)
        except Exception:
            continue
        low = html.lower()
        name_ok = _page_mentions_company(low, name_tokens)
        orgnr_ok = bool(orgnr) and orgnr in re.sub(r"\D", "", low)
        if not (name_ok or orgnr_ok):
            continue
        confidence = "high" if orgnr_ok else "medium"
        return {
            "website": _root_url(url),
            "website_confidence": confidence,
            "website_source": cand.get("url"),
            "website_match": "orgnr+domain" if orgnr_ok else "name+domain",
        }
    return None


def _discover_linkedin(company_name: str, name_tokens: list[str]) -> dict[str, Any] | None:
    queries = [
        f'"{company_name}" linkedin',
        f'{company_name} sweden linkedin',
        f'"{company_name}" site:linkedin.com/company',
    ]
    seen: set[str] = set()
    for query in queries:
        for cand in search_provider.web_search(query, count=10):
            url = cand.get("url") or ""
            low = url.lower()
            if "linkedin.com/company/" not in low:
                continue
            slug = low.split("linkedin.com/company/", 1)[-1].split("/")[0].split("?")[0]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            slug_clean = _fold(slug).replace("-", "").replace("_", "")
            if any(tok in slug_clean for tok in name_tokens):
                clean = "https://www.linkedin.com/company/" + slug
                return {
                    "linkedin_url": clean,
                    "linkedin_confidence": "medium",
                    "linkedin_source": cand.get("url"),
                }
    return None


def _root_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if not p.scheme:
        return url
    return f"{p.scheme}://{p.netloc}"


# ----------------------------------------------------------------- orchestrator

def enrich_company(orgnr: str, company_name: str, municipality: str | None = None) -> dict[str, Any]:
    """Return verified company intel. Only confident fields are included."""
    result: dict[str, Any] = {"orgnr": orgnr, "company_name": company_name}
    evidence: list[dict[str, Any]] = []
    name_tokens = _distinctive_tokens(company_name)

    # Tier 1: allabolag registry fields.
    try:
        co = _allabolag_company(orgnr)
        ab = _collect_allabolag_fields(co)
        evidence.extend(ab.pop("_evidence", []))
        if not municipality:
            loc = co.get("location") or {}
            municipality = loc.get("municipality")
        result.update(ab)
    except Exception as e:
        result["_partial_error"] = f"allabolag: {str(e)[:160]}"

    known_phone = result.get("phone")

    # Tier 2/3: official website (allabolag-provided first, else discover).
    website = result.get("website")
    if not website:
        discovered = _discover_website(company_name, orgnr, municipality, name_tokens, known_phone)
        if discovered:
            result.update(discovered)
            website = discovered["website"]
            evidence.append({"type": "website-discovery", "url": discovered.get("website_source"),
                             "confidence": discovered.get("website_confidence")})

    if website:
        time.sleep(0.3)
        site = _enrich_from_website(website, name_tokens)
        # Merge socials (website self-links override / add).
        if site.get("socials"):
            merged = {**result.get("socials", {}), **site["socials"]}
            result["socials"] = merged
        if site.get("linkedin_url") and not result.get("linkedin_url"):
            result["linkedin_url"] = site["linkedin_url"]
            result["linkedin_confidence"] = site.get("linkedin_confidence", "high")
        for key in ("website_title", "website_description", "website_emails",
                    "website_phones", "website_verified"):
            if site.get(key) is not None:
                result[key] = site[key]
        if site.get("website_verified"):
            evidence.append({"type": "website", "url": website, "confidence": "high"})
        # Promote a website email if we had none from registry.
        if not result.get("email") and site.get("website_emails"):
            result["email"] = site["website_emails"][0]
            result["email_source"] = website

    # LinkedIn discovery if still unknown.
    if not result.get("linkedin_url"):
        time.sleep(0.3)
        li = _discover_linkedin(company_name, name_tokens)
        if li:
            result.update(li)
            evidence.append({"type": "linkedin-discovery", "url": li.get("linkedin_source"),
                             "confidence": li.get("linkedin_confidence")})

    # Translate Swedish "About" text to English (keep originals as *_sv).
    for field in ("description", "purpose"):
        value = result.get(field)
        if value:
            english = search_provider.translate_to_english(value, "sv")
            if english and english.strip().lower() != value.strip().lower():
                result[f"{field}_sv"] = value
                result[field] = english

    # Safety net: never keep a non-profile LinkedIn URL (e.g. locale homepage).
    if result.get("linkedin_url") and _classify_social(result["linkedin_url"]) != "linkedin":
        result.pop("linkedin_url", None)
        result.pop("linkedin_confidence", None)
    if result.get("socials"):
        result["socials"] = {
            k: v for k, v in result["socials"].items() if _classify_social(v)
        } or None
        if not result["socials"]:
            result.pop("socials")

    result["evidence"] = evidence
    result["search_provider"] = search_provider.provider_name()
    return result
