"""Fetch persons and company roles from allabolag.se (public registry data)."""

from __future__ import annotations

import json
import random
import re
import unicodedata
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://www.allabolag.se"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)

_build_id: str | None = None
_build_id_at = 0.0


class _Redirect308Handler(urllib.request.HTTPRedirectHandler):
    """Follow HTTP 308 redirects (urllib in Python < 3.11 raises on 308)."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, code, msg, headers)


_opener = urllib.request.build_opener(_Redirect308Handler())


def _fetch(url: str, retries: int = 5, timeout: int = 25) -> str:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json,text/html,*/*",
                    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
                },
            )
            with _opener.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                # Throttled. Moderate backoff with jitter so parallel enrich
                # workers desync instead of retrying in lockstep. Kept short so
                # a single 429 doesn't idle a worker for tens of seconds.
                time.sleep(min(15, 4 * (attempt + 1)) + random.uniform(0, 2))
            else:
                time.sleep(3 * (attempt + 1) + random.uniform(0, 1))
        except Exception as e:
            last_err = e
            time.sleep(3 * (attempt + 1) + random.uniform(0, 1))
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def _parse_json_or_next(html: str) -> dict[str, Any]:
    html = html.strip()
    if html.startswith("{"):
        data = json.loads(html)
        if "pageProps" in data:
            return data["pageProps"]
        props = data.get("props")
        if isinstance(props, dict) and "pageProps" in props:
            return props["pageProps"]
        return data
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("no JSON or __NEXT_DATA__ in response")
    data = json.loads(m.group(1))
    return data.get("pageProps") or data["props"]["pageProps"]


def get_build_id(force: bool = False) -> str:
    global _build_id, _build_id_at
    if _build_id and not force and (time.time() - _build_id_at) < 3600:
        return _build_id
    html = _fetch(f"{BASE}/what/Homayoun")
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError("could not read buildId from allabolag")
    data = json.loads(m.group(1))
    _build_id = data["buildId"]
    _build_id_at = time.time()
    return _build_id


def slugify_name(name: str) -> str:
    s = name.lower()
    for old, new in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        s = s.replace(old, new)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "person"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    for old, new in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        value = value.replace(old, new)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def name_matches_exact(query: str, full_name: str) -> bool:
    query_tokens = normalize(query).split()
    name_tokens = set(normalize(full_name).split())
    return bool(query_tokens) and all(token in name_tokens for token in query_tokens)


def person_url(person_id: str, name: str) -> str:
    return f"{BASE}/befattningshavare/{slugify_name(name)}/-/{person_id}"


def search_persons_page(query: str, page: int = 1) -> dict[str, Any]:
    build_id = get_build_id()
    params = urllib.parse.urlencode({"q": query, "bppage": page})
    url = f"{BASE}/_next/data/{build_id}/search.json?{params}"
    raw = _fetch(url)
    data = json.loads(raw)
    return data["pageProps"]["rolePersons"]


def iter_search_persons(query: str, delay: float = 0.6):
    """Yield person dicts from all search result pages."""
    first = search_persons_page(query, 1)
    total_pages = first.get("pages") or 1
    yield from first.get("businessPersons") or []
    for page in range(2, total_pages + 1):
        time.sleep(delay)
        batch = search_persons_page(query, page)
        yield from batch.get("businessPersons") or []


def get_person_roles(person_id: str, name: str) -> dict[str, Any]:
    """Return rolePerson payload with company roles and revenue."""
    build_id = get_build_id()
    urls = [
        f"{BASE}/_next/data/{build_id}/role/{person_id}.json?"
        + urllib.parse.urlencode({"roleId": person_id}),
        f"{BASE}/befattningshavare/{slugify_name(name)}/-/{person_id}",
    ]
    last_err: Exception | None = None
    for url in urls:
        try:
            pp = _parse_json_or_next(_fetch(url))
            if "rolePerson" in pp:
                return pp["rolePerson"]
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"person {person_id}: {last_err}")


def extract_company_roles(role_person: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    roles = role_person.get("roles") or {}

    def add_role(r: dict[str, Any], group_name: str = "") -> None:
        if r.get("type") != "Company":
            return
        orgnr = r.get("id") or ""
        revenue, profit, year = None, None, None
        accounts = r.get("companyAccounts") or []
        if accounts:
            latest = accounts[0]
            year = latest.get("year")
            for acc in latest.get("accounts") or []:
                if acc.get("code") == "SDI":
                    revenue = _safe_int(acc.get("amount"))
                elif acc.get("code") == "TR":
                    try:
                        profit = int(float(acc.get("amount") or 0))
                    except (TypeError, ValueError):
                        profit = None
        rows.append(
            {
                "orgnr": orgnr,
                "company_name": r.get("name") or "",
                "role": r.get("role") or group_name,
                "revenue_ksek": revenue,
                "profit_ksek": profit,
                "revenue_year": year,
                "allabolag_url": f"{BASE}/{orgnr}" if orgnr else "",
            }
        )

    if isinstance(roles, list):
        for r in roles:
            add_role(r)
    else:
        for group in roles.get("roleGroups") or []:
            group_name = group.get("name") or ""
            for r in group.get("roles") or []:
                add_role(r, group_name)
    return rows


def get_company_contact(orgnr: str) -> dict[str, Any]:
    if not orgnr:
        return {}
    try:
        pp = _parse_json_or_next(_fetch(f"{BASE}/{orgnr}"))
        co = pp.get("company") or {}
        loc = co.get("location") or {}
        status = co.get("status")
        if isinstance(status, dict):
            status = status.get("status")
        company_type = co.get("companyType")
        if isinstance(company_type, dict):
            company_type = company_type.get("name") or company_type.get("code")
        industries = []
        for item in co.get("industries") or co.get("proffIndustries") or []:
            if isinstance(item, dict) and item.get("name"):
                industries.append(item["name"])
            elif isinstance(item, str):
                industries.append(item)
        return {
            "phone": co.get("phone") or co.get("mobile"),
            "email": co.get("email"),
            "homepage": co.get("homePage"),
            "employees": co.get("numberOfEmployees") or co.get("employees"),
            "municipality": loc.get("municipality"),
            "county": loc.get("county"),
            "industries": industries,
            "nace_industries": co.get("naceIndustries") or co.get("naceCategories") or [],
            "company_type": company_type,
            "status": status,
            "registration_date": co.get("registrationDate"),
            "foundation_year": co.get("foundationYear"),
        }
    except Exception:
        return {}


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except (TypeError, ValueError):
        return None
