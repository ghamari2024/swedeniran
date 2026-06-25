"""Background worker for list/enrich jobs."""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import allabolag
import company_intel
import db
import person_intel
import search_provider
<<<<<<< HEAD
=======
import site_agent
import email_agent
import mailer
import screenshots
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602

log = logging.getLogger("worker")

_lock = threading.Lock()
_running = False
_paused = threading.Event()
LIST_WORKERS = int(os.environ.get("SWEDENIRAN_LIST_WORKERS", "6"))
<<<<<<< HEAD
ENRICH_WORKERS = int(os.environ.get("SWEDENIRAN_ENRICH_WORKERS", "3"))
=======
# Primary enrichment (allabolag company data). Each allabolag fetch is
# latency-bound (~3.5s server-side) and allabolag does NOT throttle a single IP
# even at 24+ concurrent requests (measured: no 429s, flat wall-time), so
# throughput scales with worker count up to a point. Past ~24 the per-person DB
# write bursts (replace_person_companies + score_person + recount_search)
# contend on WAL's single writer and net throughput drops, so 24 is the
# measured sweet spot. Override with SWEDENIRAN_ENRICH_WORKERS / *_DELAY.
ENRICH_WORKERS = int(os.environ.get("SWEDENIRAN_ENRICH_WORKERS", "24"))
# Per-person pause after each person is enriched (seconds).
ENRICH_PERSON_DELAY = float(os.environ.get("SWEDENIRAN_ENRICH_PERSON_DELAY", "0.05"))
# Per-company pause between company-contact fetches (seconds).
ENRICH_COMPANY_DELAY = float(os.environ.get("SWEDENIRAN_ENRICH_COMPANY_DELAY", "0.04"))
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
# Deep-enrichment runs deliberately slow and single-file on the free keyless
# engines: one company worker and one person worker so we never hammer the
# search backends in parallel. Companies are processed before people.
COMPANY_DEEP_WORKERS = int(os.environ.get("SWEDENIRAN_COMPANY_DEEP_WORKERS", "1"))
<<<<<<< HEAD
PERSON_DEEP_WORKERS = int(os.environ.get("SWEDENIRAN_PERSON_DEEP_WORKERS", "1"))
=======
PERSON_DEEP_WORKERS = int(os.environ.get("SWEDENIRAN_PERSON_DEEP_WORKERS", "0"))
CAMPAIGN_WORKERS = int(os.environ.get("SWEDENIRAN_CAMPAIGN_WORKERS", "2"))
EMAIL_DRAFT_WORKERS = int(os.environ.get("SWEDENIRAN_EMAIL_DRAFT_WORKERS", "2"))
IMAP_POLL_SECONDS = int(os.environ.get("IMAP_POLL_SECONDS", "300"))
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
# Seconds a deep worker idles when every search engine is rate-limited. We wait
# rather than burn through favorites storing empty results.
ENGINE_WAIT = int(os.environ.get("SWEDENIRAN_ENGINE_WAIT", "45"))
# When a job ran while engines were blocked, retry soon WITHOUT consuming the
# attempt budget, so throttling never causes us to give up on a favorite.
ENGINE_BLOCK_RETRY = int(os.environ.get("SWEDENIRAN_ENGINE_BLOCK_RETRY", "900"))


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
    for index in range(PERSON_DEEP_WORKERS):
        threading.Thread(target=_person_deep_loop, name=f"swedeniran-person-deep-{index+1}", daemon=True).start()
    threading.Thread(target=_company_deep_retry_loop, name="swedeniran-company-deep-retry", daemon=True).start()
<<<<<<< HEAD
    threading.Thread(target=_person_deep_retry_loop, name="swedeniran-person-deep-retry", daemon=True).start()
    log.info(
        "background workers started (list=%s, enrich=%s, company_deep=%s, person_deep=%s)",
        LIST_WORKERS, ENRICH_WORKERS, COMPANY_DEEP_WORKERS, PERSON_DEEP_WORKERS,
=======
    if PERSON_DEEP_WORKERS > 0:
        for index in range(PERSON_DEEP_WORKERS):
            threading.Thread(target=_person_deep_loop, name=f"swedeniran-person-deep-{index+1}", daemon=True).start()
        threading.Thread(target=_person_deep_retry_loop, name="swedeniran-person-deep-retry", daemon=True).start()
    for index in range(CAMPAIGN_WORKERS):
        threading.Thread(target=_campaign_loop, name=f"swedeniran-campaign-{index+1}", daemon=True).start()
    for index in range(EMAIL_DRAFT_WORKERS):
        threading.Thread(target=_email_draft_loop, name=f"swedeniran-email-draft-{index+1}", daemon=True).start()
    threading.Thread(target=_email_send_loop, name="swedeniran-email-send", daemon=True).start()
    threading.Thread(target=_imap_poll_loop, name="swedeniran-imap-poll", daemon=True).start()
    log.info(
        "background workers started (list=%s, enrich=%s, company_deep=%s, person_deep=%s, campaign=%s, email_draft=%s)",
        LIST_WORKERS, ENRICH_WORKERS, COMPANY_DEEP_WORKERS, PERSON_DEEP_WORKERS, CAMPAIGN_WORKERS, EMAIL_DRAFT_WORKERS,
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
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
            page_exact = 0
            for person in batch.get("businessPersons") or []:
                pid = person.get("personId")
                if not pid:
                    continue
                if exact and not allabolag.name_matches_exact(query, person.get("name") or ""):
                    suggestion = _first_different_name_token(query, person.get("name") or "")
                    if suggestion:
                        fuzzy_suggestions.add(suggestion)
                    continue
                # Exact-name hit. Count it for the scan-stop heuristic even if we
                # de-dup it away below, so we don't stop scanning prematurely.
                page_exact += 1
                # Global de-dup: if another search already owns this person, skip
                # — never re-add or re-enrich the same person twice.
                owner = db.person_owner_search(pid)
                if owner is not None and owner != sid:
                    continue
                db.upsert_person(
                    sid,
                    person,
                    person_url=allabolag.person_url(pid, person.get("name") or ""),
                )
                listed += 1

            db.recount_search(sid)
            scanned_pages = page
            db.update_search(sid, scanned_pages=scanned_pages)
            if exact and not full_scan:
                consecutive_empty_exact_pages = (
                    consecutive_empty_exact_pages + 1 if page_exact == 0 else 0
                )
                if page > 3 and consecutive_empty_exact_pages >= 3:
                    break
            if page > 1:
                time.sleep(0.2)

        db.set_fuzzy_suggestions(sid, list(fuzzy_suggestions)[:120])
        # Auto-enrich requested searches (e.g. surname seeds) flow straight into
        # enrichment instead of stopping at 'listed' — but never skip-listed
        # names, and only when something new was actually listed.
        final_status = "listed"
        if search.get("auto_enrich") and listed > 0 and not db.is_skip_enrich(query):
            db.reset_persons_for_enrich(sid)
            db.prioritize_search_enrich(sid)
            final_status = "enriching"
        db.update_search(
            sid,
            status=final_status,
            persons_listed=listed,
            scan_completed_mode=scan_mode,
            scanned_pages=pages if full_scan else scanned_pages,
        )
        db.recount_search(sid)
        log.info("listed %s new persons for %r (%s scan) -> %s",
                 listed, query, scan_mode, final_status)
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
    time.sleep(ENRICH_PERSON_DELAY)


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
            if not cached:
                time.sleep(ENRICH_COMPANY_DELAY)
        db.replace_person_companies(pid, rows)
        db.set_person_detail_status(pid, "done")
        try:
            db.score_person(pid, name)
        except Exception as e:
            log.warning("scoring failed %s: %s", pid, e)
    except Exception as e:
        log.warning("enrich failed %s: %s", pid, e)
        db.set_person_detail_status(pid, "error", str(e)[:300])


COMPANY_DEEP_MAX_ATTEMPTS = int(os.environ.get("SWEDENIRAN_COMPANY_DEEP_MAX_ATTEMPTS", "30"))
PERSON_DEEP_MAX_ATTEMPTS = int(os.environ.get("SWEDENIRAN_PERSON_DEEP_MAX_ATTEMPTS", "24"))


def _backoff_delay(attempts: int) -> int:
    """Exponential backoff capped at 6h: 15m, 30m, 1h, 2h, 4h, 6h, ..."""
    return min(6 * 3600, 900 * (2 ** min(max(attempts, 1) - 1, 5)))


def _store_company_attempts(pid: str, attempts: int) -> None:
    with db.connect() as con:
        con.execute("UPDATE persons SET company_deep_attempts=? WHERE person_id=?", (attempts, pid))


# ----------------------------------------------------------------- company phase

def _company_deep_loop() -> None:
    """Company deep-enrich — favorites only. The higher-priority phase.

    Idles while every search engine is rate-limited so we never store empty
    results just because the free backends are temporarily blocked.
    """
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            if not search_provider.any_engine_available():
                time.sleep(ENGINE_WAIT)
                continue
            person = db.claim_company_deep_person()
            if not person:
                time.sleep(2.0)
                continue
            _company_deep_person(person)
        except Exception:
            log.exception("company-deep worker loop error")
            time.sleep(4)


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
    prior = int(person.get("company_deep_attempts") or 0)
    log.info("company deep-enrich %s (%s companies, prior attempts %s)",
             person.get("name"), len(targets), prior)

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
            log.warning("company intel failed %s: %s", orgnr, e)
        time.sleep(2.0)

    # Resolved == every company has a website OR a LinkedIn page.
    refreshed = db.get_person(pid) or {}
    unresolved = 0
    for company in refreshed.get("companies") or []:
        if not company.get("orgnr"):
            continue
        intel = company.get("intel") or {}
        if not (intel.get("website") or intel.get("linkedin_url")):
            unresolved += 1

    if unresolved == 0:
        db.set_company_deep_status(pid, "done")
        _store_company_attempts(pid, prior)
        log.info("company deep-enrich %s: resolved (%s companies)",
                 person.get("name"), len(targets))
        return

    # Throttled mid-job: retry soon WITHOUT consuming an attempt, so engine
    # rate-limiting can never make us give up on a favorite.
    if not search_provider.any_engine_available():
        db.mark_company_deep_retry(pid, db.now() + ENGINE_BLOCK_RETRY, prior)
        log.info("company deep-enrich %s: engines blocked, retry in %ss (attempt kept at %s)",
                 person.get("name"), ENGINE_BLOCK_RETRY, prior)
        return

    attempts = prior + 1
    if attempts < COMPANY_DEEP_MAX_ATTEMPTS:
        delay = _backoff_delay(attempts)
        db.mark_company_deep_retry(pid, db.now() + delay, attempts)
        log.info("company deep-enrich %s: %s unresolved, retry in %ss (attempt %s)",
                 person.get("name"), unresolved, delay, attempts)
    else:
        db.set_company_deep_status(pid, "done")
        _store_company_attempts(pid, attempts)
        log.info("company deep-enrich %s: max attempts (%s) reached, finalizing best-effort",
                 person.get("name"), attempts)


def _company_deep_retry_loop() -> None:
    """Continuously flip due 'retry' favorites back into the company queue."""
    while True:
        try:
            if not _paused.is_set():
                n = db.requeue_due_company_deep_retries()
                if n:
                    log.info("re-queued %s favorite(s) for company deep retry", n)
        except Exception:
            log.exception("company-deep retry loop error")
        time.sleep(60)


# ----------------------------------------------------------------- person phase

def _person_deep_loop() -> None:
    """Person deep-enrich — runs strictly AFTER all company work is drained.

    db.claim_person_deep_person() returns nothing while any favorite still has
    company-phase work, so companies always finish first; people are then
    processed one-by-one in the gaps.
    """
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            if not search_provider.any_engine_available():
                time.sleep(ENGINE_WAIT)
                continue
            person = db.claim_person_deep_person()
            if not person:
                time.sleep(3.0)
                continue
            _person_deep_person(person)
        except Exception:
            log.exception("person-deep worker loop error")
            time.sleep(4)


def _person_deep_person(person: dict) -> None:
    pid = person["person_id"]
    if not person.get("is_favorite"):
        db.set_person_deep_status(pid, "idle")
        return
    full = db.get_person(pid) or {}
    companies = full.get("companies") or []
    targets = [c for c in companies if c.get("orgnr")]
    cities: list[str] = []
    for company in companies:
        for key in ("municipality", "county"):
            value = company.get(key)
            if value and value not in cities:
                cities.append(value)
    prior = int(person.get("person_deep_attempts") or 0)
    log.info("person deep-enrich %s (prior attempts %s)", person.get("name"), prior)

    found = False
    try:
        info = person_intel.enrich_person(
            person.get("name") or "",
            companies=targets,
            cities=cities,
        )
        db.upsert_person_intel(pid, info)
        found = bool(info.get("linkedin_url") or info.get("instagram_url") or info.get("socials"))
        log.info("person intel %s: li=%s ig=%s socials=%s",
                 person.get("name"), bool(info.get("linkedin_url")),
                 bool(info.get("instagram_url")), len(info.get("socials") or {}))
    except Exception as e:
        log.warning("person intel failed %s: %s", pid, e)

    if found:
        db.finalize_person_deep(pid, prior)
        log.info("person deep-enrich %s: profiles found", person.get("name"))
        return

    # Throttled: retry soon without consuming an attempt.
    if not search_provider.any_engine_available():
        db.mark_person_deep_retry(pid, db.now() + ENGINE_BLOCK_RETRY, prior)
        log.info("person deep-enrich %s: engines blocked, retry in %ss (attempt kept at %s)",
                 person.get("name"), ENGINE_BLOCK_RETRY, prior)
        return

    attempts = prior + 1
    if attempts < PERSON_DEEP_MAX_ATTEMPTS:
        delay = _backoff_delay(attempts)
        db.mark_person_deep_retry(pid, db.now() + delay, attempts)
        log.info("person deep-enrich %s: nothing found, retry in %ss (attempt %s)",
                 person.get("name"), delay, attempts)
    else:
        db.finalize_person_deep(pid, attempts)
        log.info("person deep-enrich %s: max attempts (%s) reached, finalizing",
                 person.get("name"), attempts)


def _person_deep_retry_loop() -> None:
    """Continuously flip due 'retry' favorites back into the person queue."""
    while True:
        try:
            if not _paused.is_set():
                n = db.requeue_due_person_deep_retries()
                if n:
                    log.info("re-queued %s favorite(s) for person deep retry", n)
        except Exception:
            log.exception("person-deep retry loop error")
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


# ----------------------------------------------------------------- CRM campaigns

def _campaign_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            recovered = db.recover_stuck_campaign_companies()
            if recovered:
                log.info("recovered %s stuck campaign job(s)", recovered)
            job = db.claim_pending_campaign_company()
            if not job:
                time.sleep(2.0)
                continue
            _run_campaign_company(job)
        except Exception:
            log.exception("campaign worker loop error")
            time.sleep(4)


def _run_campaign_company(job: dict) -> None:
    row_id = job["id"]
    campaign_id = job["campaign_id"]
    orgnr = job["orgnr"]
    work_dir = job.get("work_dir") or db.campaign_work_dir(campaign_id, orgnr)
    model = job.get("agent_model")
    company = job.get("company_snapshot") or {}
    if isinstance(company, str):
        try:
            company = json.loads(company)
        except Exception:
            company = {}

    name = company.get("company_name") or orgnr
    log.info("campaign site job %s for %r (campaign %s, status=%s)", orgnr, name, campaign_id, job["status"])

    try:
        if job["status"] == "improving":
            refine = (job.get("refine_prompt") or "").strip()
            if not refine:
                raise RuntimeError("Missing refine prompt")
            agent_id, status = site_agent.refine_site(
                work_dir,
                job.get("agent_id"),
                refine,
                model=model,
            )
            prompt_used = refine
            event_type = "improved"
        else:
            prompt = site_agent.build_generation_prompt(
                base_prompt=job.get("base_prompt") or "",
                system_prompt=job.get("agent_system_prompt"),
                company=company,
            )
            agent_id, status = site_agent.generate_site(work_dir, prompt, model=model)
            prompt_used = job.get("base_prompt") or ""
            event_type = "generated"

        if status != "finished":
            raise RuntimeError(f"Agent run ended with status: {status}")

        current_version = int(job.get("current_version") or 0)
        new_version = current_version + 1
        version_dir = db.campaign_version_dir(campaign_id, orgnr, new_version)
        site_agent.snapshot_work_dir(work_dir, version_dir)

        if not site_agent.site_has_index(version_dir):
            raise RuntimeError("Agent finished but index.html was not created")

        from datetime import datetime
        ts_label = datetime.now().strftime("%Y-%m-%d %H:%M")
        preview_path = f"/api/campaigns/{campaign_id}/companies/{orgnr}/site/index.html"
        message = (
            f"Website {'improved' if event_type == 'improved' else 'generated'} "
            f"at {ts_label} (v{new_version}). Preview: {preview_path}"
        )
        db.finish_campaign_company_success(
            row_id,
            agent_id=agent_id,
            version=new_version,
            version_dir=version_dir,
            prompt_used=prompt_used,
            event_type=event_type,
            message=message,
        )
        log.info("campaign site done %s v%s (%s)", orgnr, new_version, event_type)
    except Exception as e:
        log.warning("campaign site failed %s: %s", orgnr, e)
        db.finish_campaign_company_error(row_id, str(e))


def _email_draft_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            db.recover_stuck_email_jobs()
            job = db.claim_pending_email_draft()
            if not job:
                time.sleep(2.0)
                continue
            _run_email_draft(job)
        except Exception:
            log.exception("email draft worker loop error")
            time.sleep(4)


def _run_email_draft(job: dict) -> None:
    row_id = job["id"]
    campaign_id = job["campaign_id"]
    orgnr = job["orgnr"]
    company = job.get("company_snapshot") or {}
    if isinstance(company, str):
        try:
            company = json.loads(company)
        except Exception:
            company = {}

    name = company.get("company_name") or orgnr
    log.info("email draft job %s for %r (campaign %s)", orgnr, name, campaign_id)

    try:
        version = int(job.get("current_version") or 0)
        if version <= 0:
            raise RuntimeError("No generated website yet")

        site_dir = db.campaign_version_dir(campaign_id, orgnr, version)
        if not site_agent.site_has_index(site_dir):
            raise RuntimeError("Site index.html missing")

        preview_path = f"/api/campaigns/{campaign_id}/companies/{orgnr}/site/index.html"
        public_base = (os.environ.get("PUBLIC_BASE_URL") or "http://127.0.0.1:8787").rstrip("/")
        preview_url = f"{public_base}{preview_path}"

        shots_dir = db.campaign_shots_dir(campaign_id, orgnr)
        html_path = os.path.join(site_dir, "index.html")
        try:
            screenshots.capture_site_screenshots(html_path, shots_dir)
        except Exception as e:
            log.warning("screenshots failed for %s: %s", orgnr, e)

        work_dir = db.campaign_email_work_dir(campaign_id, orgnr)
        base_prompt = job.get("email_prompt") or job.get("base_prompt") or ""
        if job.get("refine_prompt"):
            agent_id, raw_draft = email_agent.refine_email_draft(
                work_dir,
                job.get("agent_id"),
                job["refine_prompt"],
                model=job.get("email_model") or job.get("agent_model"),
            )
            prompt_used = job["refine_prompt"]
        else:
            if not base_prompt.strip():
                raise RuntimeError("Save an email prompt before generating drafts")
            prompt = email_agent.build_email_prompt(
                base_prompt=base_prompt,
                system_prompt=job.get("email_system_prompt"),
                company=company,
                preview_url=preview_url,
            )
            try:
                agent_id, raw_draft = email_agent.generate_email_draft(
                    work_dir,
                    prompt,
                    model=job.get("email_model") or job.get("agent_model"),
                )
            except Exception as agent_err:
                log.warning("email agent failed for %s, using fallback: %s", orgnr, agent_err)
                agent_id = None
                raw_draft = email_agent.fallback_draft(company, preview_url)

        draft = email_agent.normalize_draft(raw_draft, preview_url)
        # Only the hero shot is embedded (render uses shots[0]); extra inline
        # images raise the image-to-text ratio and trigger Gmail Promotions.
        shot_files = screenshots.list_shot_files(shots_dir)[:1]
        shot_meta = [(cid, label) for cid, path in shot_files for label in [cid.replace("shot_", "").title()]]
        from_addr = mailer.smtp_config().get("from") or ""
        body_html = email_agent.render_email_html(draft, from_addr=from_addr, shots=shot_meta)
        body_text = email_agent.draft_to_plain_text(draft)

        db.finish_email_draft_success(
            row_id,
            agent_id=agent_id,
            subject=draft["subject"],
            body_html=body_html,
            body_text=body_text,
            draft_json=draft,
        )
        log.info("email draft ready %s", orgnr)
    except Exception as e:
        log.warning("email draft failed %s: %s", orgnr, e)
        db.finish_email_draft_error(row_id, str(e))


def _email_send_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(0.8)
                continue
            ok, reason = mailer.can_send_now()
            if not ok:
                time.sleep(min(30, max(5, mailer.rate_status().get("wait_seconds") or 10)))
                continue
            job = db.claim_sendable_email()
            if not job:
                time.sleep(3.0)
                continue
            _run_email_send(job)
        except Exception:
            log.exception("email send worker loop error")
            time.sleep(4)


def _run_email_send(job: dict) -> None:
    row_id = job["id"]
    campaign_id = job["campaign_id"]
    orgnr = job["orgnr"]
    stored_recipient = (job.get("recipient_email") or "").strip()
    send_to, sim = mailer.resolve_send_to(stored_recipient)
    if not send_to:
        db.finish_email_failed(row_id, "No recipient email", hard_bounce=False)
        return
    log.info(
        "sending email to %s (%s)%s",
        send_to,
        orgnr,
        " [SIMULATION]" if sim else "",
    )
    try:
        shots_dir = db.campaign_shots_dir(campaign_id, orgnr)
        inline = [(cid, path, "jpeg") for cid, path in screenshots.list_shot_files(shots_dir)[:1]]
        subject = job.get("subject") or "Webbplatsförslag"
        if sim:
            subject = f"[SIM · {orgnr}] {subject}"
        msg_id = mailer.send_campaign_email(
            to=send_to,
            subject=subject,
            body_text=job.get("body_text") or "",
            body_html=job.get("body_html") or "",
            inline_images=inline or None,
        )
        db.finish_email_sent(
            row_id,
            message_id_header=msg_id,
            recipient=send_to,
            orgnr=orgnr,
            campaign_id=campaign_id,
            simulation=sim,
            original_recipient=stored_recipient,
        )
        log.info("email sent %s -> %s", orgnr, send_to)
    except Exception as e:
        hard = mailer.is_hard_bounce_error(e)
        db.finish_email_failed(row_id, str(e), hard_bounce=hard)
        log.warning("email send failed %s: %s", orgnr, e)


def _imap_poll_loop() -> None:
    while True:
        try:
            if _paused.is_set():
                time.sleep(1.0)
                continue
            mailer.poll_imap_inbox()
        except Exception:
            log.exception("imap poll error")
        time.sleep(IMAP_POLL_SECONDS)
