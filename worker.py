"""Background worker for list/enrich jobs."""

from __future__ import annotations

import logging
import os
import threading
import time

import allabolag
import company_intel
import db
import person_intel

log = logging.getLogger("worker")

_lock = threading.Lock()
_running = False
_paused = threading.Event()
LIST_WORKERS = int(os.environ.get("SWEDENIRAN_LIST_WORKERS", "6"))
ENRICH_WORKERS = int(os.environ.get("SWEDENIRAN_ENRICH_WORKERS", "3"))
COMPANY_DEEP_WORKERS = int(os.environ.get("SWEDENIRAN_COMPANY_DEEP_WORKERS", "2"))


def start_worker() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True
    for index in range(LIST_WORKERS):
        threading.Thread(target=_list_loop, name=f"swedeniran-list-{index+1}", daemon=True).start()
    for index in range(ENRICH_WORKERS):
        threading.Thread(target=_enrich_loop, name=f"swedeniran-enrich-{index+1}", daemon=True).start()
    for index in range(COMPANY_DEEP_WORKERS):
        threading.Thread(target=_company_deep_loop, name=f"swedeniran-company-deep-{index+1}", daemon=True).start()
    threading.Thread(target=_company_deep_retry_loop, name="swedeniran-company-deep-retry", daemon=True).start()
    log.info(
        "background workers started (list=%s, enrich=%s, company_deep=%s)",
        LIST_WORKERS, ENRICH_WORKERS, COMPANY_DEEP_WORKERS,
    )


def pause() -> None:
    _paused.set()


def resume() -> None:
    _paused.clear()


def is_paused() -> bool:
    return _paused.is_set()


def _list_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue

            search = db.claim_queued_search()
            if not search:
                time.sleep(1.2)
                continue

            _list_persons(search)
        except Exception:
            log.exception("list worker loop error")
            time.sleep(4)


def _enrich_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            search = db.next_enriching_search()
            if not search:
                time.sleep(1.2)
                continue
            if db.is_skip_enrich(search["query"]):
                db.update_search(search["id"], status="stopped")
                continue
            _enrich_search(search)
        except Exception:
            log.exception("enrich worker loop error")
            time.sleep(4)


def _list_persons(search: dict) -> None:
    sid = search["id"]
    query = search["query"]
    exact = bool(search.get("exact_match", 1))
    scan_mode = search.get("scan_mode") or "fast"
    full_scan = scan_mode == "full"
    log.info("listing persons for %r (id=%s, exact=%s, scan=%s)", query, sid, exact, scan_mode)
    db.update_search(sid, status="listing", error=None, scanned_pages=0)
    consecutive_empty_exact_pages = 0
    scanned_pages = 0
    listed = 0

    try:
        first = allabolag.search_persons_page(query, 1)
        total = first.get("hits") or 0
        pages = first.get("pages") or 1
        db.update_search(sid, total_persons=total)
        fuzzy_suggestions: set[str] = set()

        for page in range(1, pages + 1):
            current = db.get_search(sid)
            if not current or current["status"] == "stopped" or _paused.is_set():
                db.update_search(sid, status="stopped")
                return

            batch = first if page == 1 else allabolag.search_persons_page(query, page)
            page_matches = 0
            for person in batch.get("businessPersons") or []:
                if not person.get("personId"):
                    continue
                if exact and not allabolag.name_matches_exact(query, person.get("name") or ""):
                    suggestion = _first_different_name_token(query, person.get("name") or "")
                    if suggestion:
                        fuzzy_suggestions.add(suggestion)
                    continue
                db.upsert_person(
                    sid,
                    person,
                    person_url=allabolag.person_url(person["personId"], person.get("name") or ""),
                )
                listed += 1
                page_matches += 1

            db.recount_search(sid)
            scanned_pages = page
            db.update_search(sid, scanned_pages=scanned_pages)
            if exact and not full_scan:
                consecutive_empty_exact_pages = (
                    consecutive_empty_exact_pages + 1 if page_matches == 0 else 0
                )
                if page > 3 and consecutive_empty_exact_pages >= 3:
                    break
            if page > 1:
                time.sleep(0.2)

        db.set_fuzzy_suggestions(sid, list(fuzzy_suggestions)[:120])
        db.update_search(
            sid,
            status="listed",
            persons_listed=listed,
            scan_completed_mode=scan_mode,
            scanned_pages=pages if full_scan else scanned_pages,
        )
        db.recount_search(sid)
        log.info("listed %s exact persons for %r (%s scan)", listed, query, scan_mode)
    except Exception as e:
        log.exception("list failed for %r", query)
        db.update_search(sid, status="error", error=str(e)[:500])


def _first_different_name_token(query: str, full_name: str) -> str | None:
    query_tokens = set(allabolag.normalize(query).split())
    query_value = next(iter(query_tokens), "")
    for raw in full_name.replace("-", " ").split():
        token = allabolag.normalize(raw)
        if len(token) < 3 or token in query_tokens:
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if not _looks_related(query_value, token):
            continue
        return raw.strip(" ,.;:()[]{}")
    return None


def _looks_related(query: str, token: str) -> bool:
    if not query or not token:
        return False
    prefix = max(3, min(len(query), 5))
    return token.startswith(query[:prefix]) or query.startswith(token[:prefix])


def _enrich_search(search: dict) -> None:
    sid = search["id"]
    person = db.claim_pending_person_for_search(sid)
    if not person:
        db.recount_search(sid)
        db.update_search(sid, status="done")
        log.info("enrichment complete for search %s (%s)", sid, search["query"])
        return

    _enrich_person(person)
    db.recount_search(sid)
    time.sleep(0.4)


def _enrich_person(person: dict) -> None:
    pid = person["person_id"]
    name = person["name"]
    log.info("enriching %s (%s)", name, pid)
    try:
        role_person = allabolag.get_person_roles(pid, name)
        rows = allabolag.extract_company_roles(role_person)
        for row in rows:
            orgnr = row.get("orgnr") or ""
            if not orgnr:
                continue
            cached = db.get_cached_company(orgnr)
            contact = _cached_fields(cached) if cached else allabolag.get_company_contact(orgnr)
            row.update({key: value for key, value in contact.items() if value is not None})
            time.sleep(0.18)
        db.replace_person_companies(pid, rows)
        db.set_person_detail_status(pid, "done")
        try:
            db.score_person(pid, name)
        except Exception as e:
            log.warning("scoring failed %s: %s", pid, e)
    except Exception as e:
        log.warning("enrich failed %s: %s", pid, e)
        db.set_person_detail_status(pid, "error", str(e)[:300])


def _company_deep_loop() -> None:
    """Deep-enrich companies, FAVORITES ONLY, highest priority first."""
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            person = db.claim_company_deep_person()
            if not person:
                time.sleep(1.0)
                continue
            _company_deep_person(person)
        except Exception:
            log.exception("company-deep worker loop error")
            time.sleep(4)


COMPANY_DEEP_MAX_ATTEMPTS = int(os.environ.get("SWEDENIRAN_COMPANY_DEEP_MAX_ATTEMPTS", "12"))


def _company_deep_person(person: dict) -> None:
    pid = person["person_id"]
    # Hard guard: never enrich a non-favorite.
    if not person.get("is_favorite"):
        db.set_company_deep_status(pid, "idle")
        return
    full = db.get_person(pid)
    companies = (full or {}).get("companies") or []
    targets = [c for c in companies if c.get("orgnr")]
    targets.sort(key=lambda c: (c.get("revenue_ksek") or 0), reverse=True)
    attempts = int(person.get("company_deep_attempts") or 0) + 1
    log.info("company deep-enrich %s (%s companies, attempt %s)",
             person.get("name"), len(targets), attempts)
    errors = 0
    for company in targets:
        orgnr = company.get("orgnr")
        try:
            intel = company_intel.enrich_company(
                orgnr,
                company.get("company_name") or "",
                company.get("municipality"),
            )
            db.upsert_company_intel(orgnr, intel)
        except Exception as e:
            errors += 1
            log.warning("company intel failed %s: %s", orgnr, e)
        time.sleep(1.0)

    # Person-level intel (LinkedIn/Instagram/...) — same favorite-only job.
    # Runs regardless of company outcomes; uses the person's companies and
    # home cities as disambiguators for these highly ambiguous names.
    try:
        cities: list[str] = []
        for company in (full or {}).get("companies") or []:
            for key in ("municipality", "county"):
                value = company.get(key)
                if value and value not in cities:
                    cities.append(value)
        intel = person_intel.enrich_person(
            person.get("name") or "",
            companies=targets,
            cities=cities,
        )
        db.upsert_person_intel(pid, intel)
    except Exception as e:
        log.warning("person intel failed %s: %s", pid, e)

    if errors and errors == len(targets) and targets:
        db.set_company_deep_status(pid, "error", f"{errors} companies failed")
        return

    # Re-read to judge completeness: a company is "resolved" once it has a
    # website OR a LinkedIn page. Anything still missing -> schedule a retry so
    # the keyless crawler keeps trying as engines recover (until max attempts).
    refreshed = db.get_person(pid) or {}
    unresolved = 0
    for company in refreshed.get("companies") or []:
        if not company.get("orgnr"):
            continue
        intel = company.get("intel") or {}
        if not (intel.get("website") or intel.get("linkedin_url")):
            unresolved += 1

    if unresolved and attempts < COMPANY_DEEP_MAX_ATTEMPTS:
        # Exponential backoff capped at 6h: 15m, 30m, 1h, 2h, 4h, 6h, ...
        delay = min(6 * 3600, 900 * (2 ** min(attempts - 1, 5)))
        db.mark_company_deep_retry(pid, db.now() + delay, attempts)
        log.info("company deep-enrich %s: %s unresolved, retry in %ss (attempt %s)",
                 person.get("name"), unresolved, delay, attempts)
    else:
        db.set_company_deep_status(pid, "done")
        with db.connect() as con:
            con.execute("UPDATE persons SET company_deep_attempts=? WHERE person_id=?", (attempts, pid))


def _company_deep_retry_loop() -> None:
    """Continuously flip due 'retry' favorites back into the queue."""
    while True:
        try:
            if not _paused.is_set():
                n = db.requeue_due_company_deep_retries()
                if n:
                    log.info("re-queued %s favorite(s) for company deep retry", n)
        except Exception:
            log.exception("company-deep retry loop error")
        time.sleep(60)


def _cached_fields(row: dict | None) -> dict:
    if not row:
        return {}
    return {
        "employees": row.get("employees"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "homepage": row.get("homepage"),
        "municipality": row.get("municipality"),
        "county": row.get("county"),
        "industries": row.get("industries"),
        "nace_industries": row.get("nace_industries"),
        "company_type": row.get("company_type"),
        "status": row.get("status"),
        "registration_date": row.get("registration_date"),
        "foundation_year": row.get("foundation_year"),
    }
