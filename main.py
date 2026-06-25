"""swedeniran panel — FastAPI app."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import email_agent
import env_loader  # noqa: F401 — load .env at startup
import mailer
import site_agent
import worker
from names import IRANIAN_FIRST_NAMES, IRANIAN_SURNAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="swedeniran")
STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class AddSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=80)
    source: str = "manual"


class BulkNamesBody(BaseModel):
    names: list[str] = Field(default_factory=list)
    source: str = "manual"


class FavoriteBody(BaseModel):
    favorite: bool = True


class AutoSpamBody(BaseModel):
    threshold: int = 40
    rescore: bool = True


class CreateCampaignBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=80)
    orgnrs: list[str] = Field(min_length=1, max_length=500)


class UpdateCampaignBody(BaseModel):
    base_prompt: str = Field(min_length=1, max_length=8000)
    agent_system_prompt: Optional[str] = Field(default=None, max_length=4000)
    agent_model: Optional[str] = Field(default=None, max_length=80)


class RefineCampaignBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class SendCampaignEmailBody(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    to: Optional[str] = Field(default=None, max_length=200)


class UpdateCampaignEmailBody(BaseModel):
    email_prompt: str = Field(min_length=1, max_length=8000)
    email_system_prompt: Optional[str] = Field(default=None, max_length=4000)
    email_model: Optional[str] = Field(default=None, max_length=80)


class EmailSelectBody(BaseModel):
    selected: bool = True


class EmailSelectBulkBody(BaseModel):
    orgnrs: Optional[list[str]] = None
    selected: bool = True


class EmailSimulationBody(BaseModel):
    enabled: bool


def _campaign_filters_from_query(
    *,
    view: str = "main",
    rev_min: Optional[float] = None,
    rev_max: Optional[float] = None,
    emp_min: Optional[int] = None,
    emp_max: Optional[int] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    year: Optional[int] = None,
    industry: Optional[str] = None,
    category: Optional[str] = None,
    county: Optional[str] = None,
    company_type: Optional[str] = None,
    gender: Optional[str] = None,
    has_revenue: bool = False,
    active_only: bool = False,
    has_employees: bool = False,
    text: Optional[str] = None,
) -> dict:
    return {
        "rev_min_ksek": int(rev_min * 1000) if rev_min is not None else None,
        "rev_max_ksek": int(rev_max * 1000) if rev_max is not None else None,
        "emp_min": emp_min,
        "emp_max": emp_max,
        "age_min": age_min,
        "age_max": age_max,
        "year": year,
        "industry": industry,
        "category": category,
        "county": county,
        "company_type": company_type,
        "gender": gender,
        "has_revenue": has_revenue,
        "active_only": active_only,
        "has_employees": has_employees,
        "text": text.strip() if text else None,
    }


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    db.add_skip_enrich_names(["Ali", "mohammad"])
    db.release_stuck_enriching_persons()
    db.retry_enrich_errors_for_active_searches()
    db.backfill_person_aggregates()
    # Auto-queue deep-enrichment for all existing favorites: companies first
    # (higher priority), then personal profiles once company work is drained.
    db.queue_favorite_company_deep()
<<<<<<< HEAD
    db.queue_favorite_person_deep()
=======
    db.heal_no_recipient_failures()
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
    worker.start_worker()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/names")
def api_names():
    searches = {s["query"].lower(): s for s in db.list_searches()}
    hidden = db.list_hidden_names()
    names = []
    for name in IRANIAN_FIRST_NAMES:
        if name.lower() in hidden:
            continue
        search = searches.get(name.lower())
        names.append(
            {
                "name": name,
                "search_id": search["id"] if search else None,
                "status": search["status"] if search else "idle",
                "persons_listed": search["persons_listed"] if search else 0,
                "details_done": search["details_done"] if search else 0,
                "scan_mode": search.get("scan_mode", "fast") if search else "fast",
                "scan_completed_mode": search.get("scan_completed_mode") if search else None,
                "scanned_pages": search.get("scanned_pages", 0) if search else 0,
            }
        )
    return {"names": names}


@app.get("/api/searches")
def api_searches():
    return {"searches": db.list_searches(), "worker_paused": worker.is_paused()}


@app.get("/api/stats")
def api_stats():
    stats = db.get_stats()
    stats["worker_paused"] = worker.is_paused()
    return stats


@app.get("/api/searches/{search_id}")
def api_search(search_id: int):
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "search not found")
    return search


@app.get("/api/searches/{search_id}/persons")
def api_persons(search_id: int):
    if not db.get_search(search_id):
        raise HTTPException(404, "search not found")
    return {"persons": db.list_persons(search_id)}


@app.get("/api/people/enriched")
def api_enriched_people(
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    sort_key: str = "latest_revenue_ksek",
    sort_dir: str = "desc",
    view: str = Query("main", pattern="^(main|spam|favorites|auditor)$"),
    rev_min: Optional[float] = None,
    rev_max: Optional[float] = None,
    emp_min: Optional[int] = None,
    emp_max: Optional[int] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    year: Optional[int] = None,
    industry: Optional[str] = None,
    category: Optional[str] = None,
    county: Optional[str] = None,
    company_type: Optional[str] = None,
    gender: Optional[str] = None,
    has_revenue: bool = False,
    active_only: bool = False,
    has_employees: bool = False,
    text: Optional[str] = None,
):
    return db.list_enriched_persons_page(
        limit=limit,
        offset=offset,
        sort_key=sort_key,
        sort_dir=sort_dir,
        view=view,
        filters={
            "rev_min_ksek": int(rev_min * 1000) if rev_min is not None else None,
            "rev_max_ksek": int(rev_max * 1000) if rev_max is not None else None,
            "emp_min": emp_min,
            "emp_max": emp_max,
            "age_min": age_min,
            "age_max": age_max,
            "year": year,
            "industry": industry,
            "category": category,
            "county": county,
            "company_type": company_type,
            "gender": gender,
            "has_revenue": has_revenue,
            "active_only": active_only,
            "has_employees": has_employees,
            "text": text.strip() if text else None,
        },
    )


@app.get("/api/people/enriched/options")
def api_enriched_people_options():
    return db.enriched_people_filter_options()


@app.get("/api/persons/{person_id}")
def api_person(person_id: str):
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(404, "person not found")
    return person


@app.post("/api/persons/{person_id}/spam")
def api_mark_person_spam(person_id: str):
    if not db.mark_person_spam(person_id):
        raise HTTPException(404, "person not found")
    return {"ok": True, "person_id": person_id, "is_spam": True}


@app.post("/api/persons/{person_id}/restore")
def api_restore_person(person_id: str):
    if not db.restore_person_from_spam(person_id):
        raise HTTPException(404, "person not found")
    return {"ok": True, "person_id": person_id, "is_spam": False}


@app.post("/api/persons/{person_id}/favorite")
def api_set_person_favorite(person_id: str, body: FavoriteBody):
    if not db.set_person_favorite(person_id, body.favorite):
        raise HTTPException(404, "person not found")
<<<<<<< HEAD
    # Auto-queue deep-enrichment when a person is favorited: companies first,
    # then personal profiles (the person phase waits until companies drain).
=======
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
    if body.favorite:
        db.queue_favorite_company_deep(person_id)
        db.queue_favorite_person_deep(person_id)
    return {"ok": True, "person_id": person_id, "is_favorite": body.favorite}


@app.post("/api/favorites/company-deep-enrich")
def api_company_deep_enrich_all():
    queued = db.queue_favorite_company_deep()
<<<<<<< HEAD
    person_queued = db.queue_favorite_person_deep()
    return {
        "ok": True,
        "queued": queued,
        "person_queued": person_queued,
=======
    return {
        "ok": True,
        "queued": queued,
>>>>>>> 889baf6c6b853446e64f902bb6c39ec653cb4602
        **db.company_deep_status_counts(),
    }


@app.post("/api/persons/{person_id}/company-deep-enrich")
def api_company_deep_enrich_one(person_id: str):
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(404, "person not found")
    if not person.get("is_favorite"):
        raise HTTPException(400, "deep-enrichment is for favorites only")
    db.queue_favorite_company_deep(person_id)
    db.queue_favorite_person_deep(person_id)
    return {"ok": True, "person_id": person_id, **db.company_deep_status_counts()}


@app.get("/api/company-deep/status")
def api_company_deep_status():
    return db.company_deep_status_counts()


@app.get("/api/person-deep/status")
def api_person_deep_status():
    return db.person_deep_status_counts()


@app.post("/api/people/score")
def api_score_people(rescore: bool = False):
    scored = db.backfill_iranian_scores(only_missing=not rescore)
    return {"scored": scored, **db.score_summary()}


@app.post("/api/people/auto-spam")
def api_auto_spam(body: AutoSpamBody):
    scored = db.backfill_iranian_scores(only_missing=not body.rescore)
    spammed = db.auto_spam_below(body.threshold)
    return {"scored": scored, "spammed": spammed, "threshold": body.threshold, **db.score_summary()}


@app.post("/api/searches")
def api_add_search(body: AddSearchBody):
    sid = db.add_search(body.query.strip(), source=body.source or "manual", exact_match=True)
    if sid is None:
        raise HTTPException(400, "invalid query")
    return db.get_search(sid)


@app.post("/api/surnames/seed")
def api_seed_surnames():
    """Queue the curated Iranian surnames for listing + auto-enrichment.

    Surname searches list and then flow straight into enrichment. Persons are
    de-duplicated globally, so anyone already captured by a first-name search is
    not added again. Re-running is safe: existing searches are left untouched.
    """
    new_searches = []
    already = 0
    for surname in IRANIAN_SURNAMES:
        existing = db.find_search_by_query(surname)
        if existing:
            already += 1
            continue
        sid = db.add_search(surname, source="surname", exact_match=True, auto_enrich=True)
        if sid is not None:
            new_searches.append(db.get_search(sid))
    return {
        "queued_new": len(new_searches),
        "already_existing": already,
        "total_surnames": len(IRANIAN_SURNAMES),
        "searches": new_searches,
    }


@app.post("/api/searches/bulk")
def api_add_searches_bulk(body: BulkNamesBody):
    created = []
    for name in body.names:
        clean = name.strip()
        if not clean:
            continue
        sid = db.add_search(clean, source=body.source or "manual", exact_match=True)
        if sid:
            created.append(db.get_search(sid))
    return {"searches": created}


@app.post("/api/searches/{search_id}/enrich")
def api_enrich(search_id: int):
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "search not found")
    if db.is_skip_enrich(search["query"]):
        raise HTTPException(400, "enrichment disabled for this name")
    if search["persons_listed"] == 0:
        raise HTTPException(400, "list this name before enriching")
    db.reset_persons_for_enrich(search_id)
    db.prioritize_search_enrich(search_id)
    db.update_search(search_id, status="enriching", error=None)
    return db.get_search(search_id)


@app.post("/api/searches/enrich-bulk")
def api_enrich_bulk(body: BulkNamesBody):
    enriched = []
    names = [raw.strip() for raw in body.names if raw.strip()]
    base_ts = db.now()
    for index, clean in enumerate(names):
        if db.is_skip_enrich(clean):
            continue
        search = db.find_search_by_query(clean)
        if not search or not search["persons_listed"]:
            continue
        db.reset_persons_for_enrich(search["id"])
        # First selected name gets the highest priority.
        db.prioritize_search_enrich(search["id"], queue_at=base_ts + (len(names) - index))
        db.update_search(search["id"], status="enriching", error=None)
        enriched.append(db.get_search(search["id"]))
    return {"searches": enriched}


@app.post("/api/searches/stop-bulk")
def api_stop_bulk(body: BulkNamesBody):
    return {"searches": db.bulk_update_searches_by_names(body.names, "stopped")}


@app.post("/api/searches/resume-bulk")
def api_resume_bulk(body: BulkNamesBody):
    resumed = []
    for raw in body.names:
        search = db.find_search_by_query(raw.strip())
        if not search:
            continue
        if db.is_skip_enrich(search["query"]):
            db.update_search(search["id"], status="stopped", error=None)
            continue
        status = "enriching" if search["persons_listed"] and search["details_done"] < search["persons_listed"] else "queued"
        if search["persons_listed"] and search["details_done"] >= search["persons_listed"]:
            status = "listed"
        db.update_search(search["id"], status=status, error=None)
        resumed.append(db.get_search(search["id"]))
    return {"searches": resumed}


@app.post("/api/searches/delete-bulk")
def api_delete_bulk(body: BulkNamesBody):
    return db.delete_names(body.names)


@app.post("/api/searches/full-scan-bulk")
def api_full_scan_bulk(body: BulkNamesBody):
    return {"searches": db.queue_full_scan_by_names(body.names)}


@app.post("/api/searches/{search_id}/stop")
def api_stop(search_id: int):
    if not db.get_search(search_id):
        raise HTTPException(404, "search not found")
    db.update_search(search_id, status="stopped")
    return db.get_search(search_id)


@app.post("/api/searches/{search_id}/resume")
def api_resume(search_id: int):
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "search not found")
    status = "enriching" if search["details_done"] < search["persons_listed"] and search["persons_listed"] else "queued"
    if search["persons_listed"] and search["status"] == "stopped":
        status = "listed"
    db.update_search(search_id, status=status, error=None)
    return db.get_search(search_id)


@app.post("/api/searches/{search_id}/clean")
def api_clean(search_id: int):
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "search not found")
    db.clear_search_people(search_id)
    db.update_search(search_id, status="queued", exact_match=1, error=None)
    return db.get_search(search_id)


@app.delete("/api/searches/{search_id}")
def api_delete(search_id: int):
    if not db.get_search(search_id):
        raise HTTPException(404, "search not found")
    db.delete_search(search_id)
    return {"ok": True}


@app.post("/api/worker/pause")
def api_pause_worker():
    worker.pause()
    return {"paused": True}


@app.post("/api/worker/resume")
def api_resume_worker():
    worker.resume()
    return {"paused": False}


@app.get("/api/campaigns/candidates")
def api_campaign_candidates(
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    sort_key: str = "revenue_ksek",
    sort_dir: str = "desc",
    view: str = Query("main", pattern="^(main|spam|favorites|auditor)$"),
    rev_min: Optional[float] = None,
    rev_max: Optional[float] = None,
    emp_min: Optional[int] = None,
    emp_max: Optional[int] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    year: Optional[int] = None,
    industry: Optional[str] = None,
    category: Optional[str] = None,
    county: Optional[str] = None,
    company_type: Optional[str] = None,
    gender: Optional[str] = None,
    has_revenue: bool = False,
    active_only: bool = False,
    has_employees: bool = False,
    text: Optional[str] = None,
):
    filters = _campaign_filters_from_query(
        view=view,
        rev_min=rev_min,
        rev_max=rev_max,
        emp_min=emp_min,
        emp_max=emp_max,
        age_min=age_min,
        age_max=age_max,
        year=year,
        industry=industry,
        category=category,
        county=county,
        company_type=company_type,
        gender=gender,
        has_revenue=has_revenue,
        active_only=active_only,
        has_employees=has_employees,
        text=text,
    )
    return db.list_campaign_candidate_companies(
        view=view,
        filters=filters,
        limit=limit,
        offset=offset,
        sort_key=sort_key,
        sort_dir=sort_dir,
    )


@app.post("/api/campaigns")
def api_create_campaign(body: CreateCampaignBody):
    try:
        campaign = db.create_campaign_from_orgnrs(
            name=body.name,
            category=body.category,
            orgnrs=body.orgnrs,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return campaign


@app.patch("/api/campaigns/{campaign_id}")
def api_update_campaign(campaign_id: int, body: UpdateCampaignBody):
    campaign = db.update_campaign_prompt(
        campaign_id,
        base_prompt=body.base_prompt,
        agent_system_prompt=body.agent_system_prompt,
        agent_model=body.agent_model,
    )
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return campaign


@app.get("/api/campaigns")
def api_list_campaigns():
    return {"campaigns": db.list_campaigns()}


@app.get("/api/campaigns/agent-status")
def api_campaign_agent_status():
    return site_agent.agent_status()


@app.get("/api/campaigns/email-status")
def api_campaign_email_status(verify: bool = Query(default=False)):
    return mailer.email_status(verify=verify)


@app.get("/api/campaigns/email/simulation")
def api_get_email_simulation():
    return mailer.simulation_info()


@app.patch("/api/campaigns/email/simulation")
def api_set_email_simulation(body: EmailSimulationBody):
    return mailer.set_simulation_enabled(body.enabled)


@app.patch("/api/campaigns/{campaign_id}/email-prompt")
def api_update_campaign_email_prompt(campaign_id: int, body: UpdateCampaignEmailBody):
    campaign = db.update_campaign_email_prompt(
        campaign_id,
        email_prompt=body.email_prompt,
        email_system_prompt=body.email_system_prompt,
        email_model=body.email_model,
    )
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return campaign


@app.post("/api/campaigns/{campaign_id}/email/generate-drafts")
def api_generate_email_drafts(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if not (campaign.get("email_prompt") or "").strip():
        raise HTTPException(400, "save an email prompt before generating drafts")
    status = mailer.email_status()
    if not status["ready"]:
        detail = "; ".join(status["issues"]) or "SMTP not configured"
        raise HTTPException(400, detail)
    db.ensure_email_messages(campaign_id)
    queued = db.queue_email_drafts(campaign_id)
    return {
        "ok": True,
        "queued": queued,
        "counts": db.email_counts(campaign_id),
    }


@app.get("/api/campaigns/{campaign_id}/email/messages")
def api_list_email_messages(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    db.ensure_email_messages(campaign_id)
    db.heal_no_recipient_failures(campaign_id)
    sim = mailer.simulation_info()
    messages = []
    for msg in db.list_campaign_email_messages(campaign_id):
        stored = msg.get("recipient_email") or ""
        msg["simulation"] = sim
        msg["display_recipient"] = mailer.display_recipient(stored)
        msg["original_recipient"] = stored
        messages.append(msg)
    return {
        "messages": messages,
        "counts": db.email_counts(campaign_id),
        "rate": mailer.rate_status(),
        "simulation": sim,
    }


@app.get("/api/campaigns/{campaign_id}/email/status")
def api_campaign_email_queue_status(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return {
        "email_status": campaign.get("email_status"),
        "counts": db.email_counts(campaign_id),
        "rate": mailer.rate_status(),
        "smtp": mailer.email_status(),
        "simulation": mailer.simulation_info(),
    }


@app.get("/api/campaigns/{campaign_id}/companies/{orgnr}/email/preview")
def api_email_preview(campaign_id: int, orgnr: str):
    msg = db.get_campaign_email_message(campaign_id, orgnr)
    if not msg:
        raise HTTPException(404, "email message not found")
    if msg.get("status") not in ("draft_ready", "queued", "sending", "sent", "replied") and not msg.get("body_html"):
        raise HTTPException(404, "no draft preview yet")
    from_addr = mailer.smtp_config().get("from") or ""
    html = email_agent.preview_html_for_message(
        campaign_id,
        orgnr,
        msg,
        from_addr=from_addr,
    )
    return HTMLResponse(html)


@app.post("/api/campaigns/{campaign_id}/companies/{orgnr}/email/refine")
def api_email_refine(campaign_id: int, orgnr: str, body: RefineCampaignBody):
    msg = db.request_email_refine(campaign_id, orgnr, body.prompt)
    if not msg:
        raise HTTPException(400, "cannot refine this email now")
    return {"ok": True, "message": msg}


@app.post("/api/campaigns/{campaign_id}/companies/{orgnr}/email/select")
def api_email_select(campaign_id: int, orgnr: str, body: EmailSelectBody):
    msg = db.set_email_message_selected(campaign_id, orgnr, body.selected)
    if not msg:
        raise HTTPException(404, "email message not found")
    return {"ok": True, "message": msg}


@app.post("/api/campaigns/{campaign_id}/email/select-bulk")
def api_email_select_bulk(campaign_id: int, body: EmailSelectBulkBody):
    updated = db.set_email_messages_selected_bulk(
        campaign_id, body.orgnrs, body.selected
    )
    return {"ok": True, "updated": updated}


@app.post("/api/campaigns/{campaign_id}/email/send")
def api_queue_email_send(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    status = mailer.email_status()
    if not status["ready"]:
        detail = "; ".join(status["issues"]) or "SMTP not configured"
        raise HTTPException(400, detail)
    queued = db.queue_email_send(campaign_id)
    if queued == 0:
        raise HTTPException(400, "no selected draft-ready messages to send")
    return {
        "ok": True,
        "queued": queued,
        "counts": db.email_counts(campaign_id),
        "rate": mailer.rate_status(),
    }


@app.get("/api/campaigns/{campaign_id}")
def api_get_campaign(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return campaign


@app.post("/api/campaigns/{campaign_id}/run")
def api_run_campaign(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    if not (campaign.get("base_prompt") or "").strip():
        raise HTTPException(400, "save a site prompt before running the campaign")
    status = site_agent.agent_status()
    if not status["ready"]:
        detail = "; ".join(status["issues"]) or "campaign agent not ready"
        raise HTTPException(400, detail)
    queued = db.queue_campaign(campaign_id)
    return {"ok": True, "queued": queued, "campaign": db.get_campaign(campaign_id)}


@app.get("/api/campaigns/{campaign_id}/companies/{orgnr}/events")
def api_campaign_company_events(campaign_id: int, orgnr: str):
    company = db.get_campaign_company(campaign_id, orgnr)
    if not company:
        raise HTTPException(404, "company not found in campaign")
    return {"events": db.list_campaign_events(company["id"])}


@app.post("/api/campaigns/{campaign_id}/companies/{orgnr}/refine")
def api_campaign_refine(campaign_id: int, orgnr: str, body: RefineCampaignBody):
    company = db.request_campaign_refine(campaign_id, orgnr, body.prompt)
    if not company:
        raise HTTPException(400, "cannot refine this company now")
    return {"ok": True, "company": company}


@app.post("/api/campaigns/{campaign_id}/companies/{orgnr}/email")
def api_campaign_send_email(campaign_id: int, orgnr: str, body: SendCampaignEmailBody):
    company = db.get_campaign_company(campaign_id, orgnr)
    if not company:
        raise HTTPException(404, "company not found in campaign")
    status = mailer.email_status()
    if not status["ready"]:
        detail = "; ".join(status["issues"]) or "SMTP not configured"
        raise HTTPException(400, detail)
    snap = company.get("company_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = {}
    recipient = (body.to or snap.get("email") or "").strip()
    if not recipient:
        raise HTTPException(400, "no recipient email — add one in the send form or enrich company data")
    try:
        mailer.send_email(to=recipient, subject=body.subject, body=body.body)
    except Exception as e:
        raise HTTPException(502, f"email send failed: {e}") from e
    db.log_campaign_email_sent(campaign_id, orgnr, recipient, body.subject)
    return {"ok": True, "to": recipient}


@app.get("/api/campaigns/{campaign_id}/companies/{orgnr}/site/{path:path}")
def api_campaign_site_file(campaign_id: int, orgnr: str, path: str):
    company = db.get_campaign_company(campaign_id, orgnr)
    if not company:
        raise HTTPException(404, "company not found in campaign")
    version = company.get("current_version") or 0
    if version <= 0:
        raise HTTPException(404, "no site generated yet")
    base_dir = db.get_campaign_site_dir(campaign_id, orgnr, version)
    if not base_dir:
        raise HTTPException(404, "site directory missing")
    safe_path = path or "index.html"
    full_path = os.path.normpath(os.path.join(base_dir, safe_path))
    base_norm = os.path.normpath(base_dir)
    if not full_path.startswith(base_norm + os.sep) and full_path != base_norm:
        raise HTTPException(403, "invalid path")
    if not os.path.isfile(full_path):
        raise HTTPException(404, "file not found")
    return FileResponse(full_path)
