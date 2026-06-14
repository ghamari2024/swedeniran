"""swedeniran panel — FastAPI app."""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import worker
from names import IRANIAN_FIRST_NAMES

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


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    db.add_skip_enrich_names(["Ali", "mohammad"])
    db.release_stuck_enriching_persons()
    db.retry_enrich_errors_for_active_searches()
    db.backfill_person_aggregates()
    # Auto-queue company deep-enrichment for all existing favorites.
    db.queue_favorite_company_deep()
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
    # Auto-queue company deep-enrichment when a person is favorited.
    if body.favorite:
        db.queue_favorite_company_deep(person_id)
    return {"ok": True, "person_id": person_id, "is_favorite": body.favorite}


@app.post("/api/favorites/company-deep-enrich")
def api_company_deep_enrich_all():
    queued = db.queue_favorite_company_deep()
    return {"ok": True, "queued": queued, **db.company_deep_status_counts()}


@app.post("/api/persons/{person_id}/company-deep-enrich")
def api_company_deep_enrich_one(person_id: str):
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(404, "person not found")
    if not person.get("is_favorite"):
        raise HTTPException(400, "company deep-enrichment is for favorites only")
    db.queue_favorite_company_deep(person_id)
    return {"ok": True, "person_id": person_id, **db.company_deep_status_counts()}


@app.get("/api/company-deep/status")
def api_company_deep_status():
    return db.company_deep_status_counts()


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
