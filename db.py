"""SQLite persistence for swedeniran."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from translations import (
    INDUSTRY_TRANSLATIONS,
    industry_filter_value,
    translate_industries,
    translate_industry,
    translate_role,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "swedeniran.db")


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def now() -> int:
    return int(time.time())


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued',
                total_persons INTEGER DEFAULT 0,
                persons_listed INTEGER DEFAULT 0,
                details_done INTEGER DEFAULT 0,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                search_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                age INTEGER,
                year_of_birth INTEGER,
                gender TEXT,
                number_of_roles INTEGER,
                detail_status TEXT NOT NULL DEFAULT 'idle',
                error TEXT,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (search_id) REFERENCES searches(id)
            );

            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT NOT NULL,
                orgnr TEXT,
                company_name TEXT NOT NULL,
                role TEXT,
                revenue_ksek INTEGER,
                profit_ksek INTEGER,
                revenue_year TEXT,
                employees TEXT,
                phone TEXT,
                email TEXT,
                homepage TEXT,
                municipality TEXT,
                county TEXT,
                allabolag_url TEXT,
                UNIQUE(person_id, orgnr, role),
                FOREIGN KEY (person_id) REFERENCES persons(person_id)
            );

            CREATE INDEX IF NOT EXISTS idx_persons_search ON persons(search_id);
            CREATE INDEX IF NOT EXISTS idx_companies_person ON companies(person_id);

            CREATE TABLE IF NOT EXISTS hidden_names (
                name TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skip_enrich_names (
                name TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_intel (
                orgnr TEXT PRIMARY KEY,
                company_name TEXT,
                website TEXT,
                website_confidence TEXT,
                linkedin_url TEXT,
                linkedin_confidence TEXT,
                email TEXT,
                phone TEXT,
                socials TEXT,
                description TEXT,
                purpose TEXT,
                keywords TEXT,
                address TEXT,
                news TEXT,
                certifications TEXT,
                website_emails TEXT,
                website_phones TEXT,
                evidence TEXT,
                data TEXT,
                search_provider TEXT,
                enriched_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS person_intel (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                linkedin_url TEXT,
                linkedin_confidence TEXT,
                instagram_url TEXT,
                instagram_confidence TEXT,
                socials TEXT,
                headline TEXT,
                evidence TEXT,
                data TEXT,
                search_provider TEXT,
                enriched_at INTEGER
            );
            """
        )
        _ensure_columns(
            con,
            "searches",
            {
                "exact_match": "INTEGER DEFAULT 1",
                "source": "TEXT DEFAULT 'manual'",
                "fuzzy_suggestions": "TEXT",
                "scan_mode": "TEXT DEFAULT 'fast'",
                "scan_completed_mode": "TEXT",
                "scanned_pages": "INTEGER DEFAULT 0",
                "enrich_queued_at": "INTEGER",
            },
        )
        _ensure_columns(
            con,
            "persons",
            {
                "latest_year": "INTEGER",
                "latest_revenue_ksek": "INTEGER",
                "total_profit_ksek": "INTEGER",
                "employees_total": "INTEGER",
                "employees_max": "INTEGER",
                "active_company_count": "INTEGER",
                "company_count": "INTEGER",
                "industries": "TEXT",
                "counties": "TEXT",
                "municipalities": "TEXT",
                "company_types": "TEXT",
                "person_url": "TEXT",
                "is_spam": "INTEGER DEFAULT 0",
                "is_favorite": "INTEGER DEFAULT 0",
                "review_updated_at": "TEXT",
                "iranian_score": "INTEGER",
                "company_deep_status": "TEXT DEFAULT 'idle'",
                "company_deep_queued_at": "INTEGER",
                "company_deep_updated_at": "INTEGER",
                "company_deep_error": "TEXT",
                "company_deep_attempts": "INTEGER DEFAULT 0",
                "company_deep_next_retry_at": "INTEGER",
            },
        )
        _ensure_columns(
            con,
            "companies",
            {
                "industries": "TEXT",
                "nace_industries": "TEXT",
                "company_type": "TEXT",
                "status": "TEXT",
                "registration_date": "TEXT",
                "foundation_year": "TEXT",
            },
        )
        con.execute("UPDATE searches SET status='listed' WHERE status='details'")
        con.execute("UPDATE searches SET status='queued' WHERE status='listing'")
        con.execute("UPDATE persons SET detail_status='pending' WHERE detail_status='enriching'")
        con.execute("UPDATE persons SET company_deep_status='queued' WHERE company_deep_status='running'")
        # Resume any due retries on boot.
        con.execute(
            "UPDATE persons SET company_deep_status='queued' "
            "WHERE company_deep_status='retry' AND COALESCE(company_deep_next_retry_at,0) <= ?",
            (now(),),
        )


def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def add_search(query: str, source: str = "manual", exact_match: bool = True) -> int | None:
    q = query.strip()
    if not q:
        return None
    ts = now()
    with connect() as con:
        con.execute("DELETE FROM hidden_names WHERE lower(name) = lower(?)", (q,))
        try:
            cur = con.execute(
                """
                INSERT INTO searches
                    (query, status, total_persons, persons_listed, details_done,
                     exact_match, source, scan_mode, created_at, updated_at)
                VALUES (?, 'queued', 0, 0, 0, ?, ?, 'fast', ?, ?)
                """,
                (q, 1 if exact_match else 0, source, ts, ts),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = con.execute("SELECT id FROM searches WHERE query = ?", (q,)).fetchone()
            return row["id"] if row else None


def get_search(search_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        return _decode_search(dict(row)) if row else None


def find_search_by_query(query: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM searches WHERE lower(query) = lower(?)", (query.strip(),)).fetchone()
        return _decode_search(dict(row)) if row else None


def list_searches() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM searches ORDER BY created_at ASC").fetchall()
        return [_decode_search(dict(r)) for r in rows]


def list_hidden_names() -> set[str]:
    with connect() as con:
        rows = con.execute("SELECT name FROM hidden_names").fetchall()
        return {row["name"].lower() for row in rows}


def update_search(search_id: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [search_id]
    with connect() as con:
        con.execute(f"UPDATE searches SET {cols} WHERE id = ?", vals)


def prioritize_search_enrich(search_id: int, queue_at: int | None = None) -> None:
    """Move a search to the front of the enrich queue."""
    update_search(search_id, enrich_queued_at=queue_at or now())


def get_stats() -> dict[str, Any]:
    with connect() as con:
        search_statuses = {
            row["status"]: row["count"]
            for row in con.execute(
                "SELECT status, COUNT(*) AS count FROM searches GROUP BY status"
            )
        }
        person_statuses = {
            row["detail_status"]: row["count"]
            for row in con.execute(
                "SELECT detail_status, COUNT(*) AS count FROM persons GROUP BY detail_status"
            )
        }
        row = con.execute(
            """
            SELECT
                COUNT(*) AS total_names,
                COALESCE(SUM(persons_listed), 0) AS total_people_listed,
                COALESCE(SUM(details_done), 0) AS total_people_enriched,
                COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS error_names
            FROM searches
            """
        ).fetchone()
        scan_modes = {
            row["scan_completed_mode"] or row["scan_mode"] or "fast": row["count"]
            for row in con.execute(
                """
                SELECT scan_completed_mode, scan_mode, COUNT(*) AS count
                FROM searches
                GROUP BY scan_completed_mode, scan_mode
                """
            )
        }
        library_unlisted = con.execute(
            "SELECT COUNT(*) AS count FROM searches WHERE status='queued'"
        ).fetchone()["count"]
        return {
            "total_names": row["total_names"],
            "new_names": library_unlisted,
            "search_statuses": search_statuses,
            "person_statuses": person_statuses,
            "total_people_listed": row["total_people_listed"],
            "total_people_enriched": row["total_people_enriched"],
            "pending_enrich": person_statuses.get("pending", 0),
            "currently_enriching_people": person_statuses.get("enriching", 0),
            "error_names": row["error_names"],
            "scan_modes": scan_modes,
        }


def list_skip_enrich_names() -> set[str]:
    with connect() as con:
        rows = con.execute("SELECT name FROM skip_enrich_names").fetchall()
        return {row["name"].lower() for row in rows}


def is_skip_enrich(name: str) -> bool:
    return name.strip().lower() in list_skip_enrich_names()


def add_skip_enrich_names(names: list[str]) -> list[str]:
    """Permanently exclude names from enrichment. Does not delete scraped data."""
    added: list[str] = []
    ts = now()
    with connect() as con:
        for raw in names:
            name = raw.strip()
            if not name:
                continue
            con.execute(
                "INSERT OR IGNORE INTO skip_enrich_names (name, created_at) VALUES (?, ?)",
                (name, ts),
            )
            search = con.execute(
                "SELECT id FROM searches WHERE lower(query) = lower(?)", (name,)
            ).fetchone()
            if search:
                sid = search["id"]
                con.execute(
                    """
                    UPDATE persons
                    SET detail_status = CASE
                        WHEN detail_status = 'enriching' THEN 'pending'
                        ELSE detail_status
                    END,
                    updated_at = ?
                    WHERE search_id = ?
                    """,
                    (ts, sid),
                )
                con.execute(
                    "UPDATE searches SET status='stopped', updated_at=? WHERE id=?",
                    (ts, sid),
                )
            added.append(name)
    return added


def release_stuck_enriching_persons() -> int:
    with connect() as con:
        cur = con.execute(
            "UPDATE persons SET detail_status='pending', updated_at=? WHERE detail_status='enriching'",
            (now(),),
        )
        return cur.rowcount


def retry_enrich_errors_for_active_searches() -> int:
    skip = list_skip_enrich_names()
    with connect() as con:
        rows = con.execute(
            "SELECT id, query FROM searches WHERE status='enriching'"
        ).fetchall()
        active_ids = [row["id"] for row in rows if row["query"].lower() not in skip]
        if not active_ids:
            return 0
        placeholders = ",".join("?" * len(active_ids))
        cur = con.execute(
            f"""
            UPDATE persons
            SET detail_status='pending', error=NULL, updated_at=?
            WHERE detail_status='error' AND search_id IN ({placeholders})
            """,
            [now(), *active_ids],
        )
        return cur.rowcount


def bulk_update_searches_by_names(names: list[str], status: str) -> list[dict[str, Any]]:
    cleaned = [name.strip() for name in names if name and name.strip()]
    if not cleaned:
        return []
    with connect() as con:
        rows = []
        for name in cleaned:
            row = con.execute("SELECT * FROM searches WHERE query = ?", (name,)).fetchone()
            if not row:
                continue
            con.execute(
                "UPDATE searches SET status=?, updated_at=? WHERE id=?",
                (status, now(), row["id"]),
            )
            updated = con.execute("SELECT * FROM searches WHERE id=?", (row["id"],)).fetchone()
            if updated:
                rows.append(_decode_search(dict(updated)))
        return rows


def queue_full_scan_by_names(names: list[str]) -> list[dict[str, Any]]:
    cleaned = [name.strip() for name in names if name and name.strip()]
    if not cleaned:
        return []
    rows = []
    with connect() as con:
        for name in cleaned:
            search = con.execute("SELECT * FROM searches WHERE lower(query) = lower(?)", (name,)).fetchone()
            if not search:
                ts = now()
                con.execute("DELETE FROM hidden_names WHERE lower(name) = lower(?)", (name,))
                cur = con.execute(
                    """
                    INSERT INTO searches
                        (query, status, total_persons, persons_listed, details_done,
                         exact_match, source, scan_mode, created_at, updated_at)
                    VALUES (?, 'queued', 0, 0, 0, 1, 'manual', 'full', ?, ?)
                    """,
                    (name, ts, ts),
                )
                search = con.execute("SELECT * FROM searches WHERE id = ?", (cur.lastrowid,)).fetchone()
            if not search:
                continue
            con.execute(
                """
                UPDATE searches
                SET status='queued', scan_mode='full', scanned_pages=0, error=NULL, updated_at=?
                WHERE id=?
                """,
                (now(), search["id"]),
            )
            updated = con.execute("SELECT * FROM searches WHERE id=?", (search["id"],)).fetchone()
            if updated:
                rows.append(_decode_search(dict(updated)))
    return rows


def delete_search(search_id: int) -> None:
    with connect() as con:
        search = con.execute("SELECT query FROM searches WHERE id = ?", (search_id,)).fetchone()
        if search and search["query"]:
            con.execute(
                "INSERT OR REPLACE INTO hidden_names (name, created_at) VALUES (?, ?)",
                (search["query"].strip(), now()),
            )
        person_ids = [r["person_id"] for r in con.execute(
            "SELECT person_id FROM persons WHERE search_id = ?", (search_id,)
        )]
        for person_id in person_ids:
            con.execute("DELETE FROM companies WHERE person_id = ?", (person_id,))
        con.execute("DELETE FROM persons WHERE search_id = ?", (search_id,))
        con.execute("DELETE FROM searches WHERE id = ?", (search_id,))


def delete_names(names: list[str]) -> dict[str, Any]:
    deleted = []
    with connect() as con:
        for raw in names:
            name = raw.strip()
            if not name:
                continue
            con.execute(
                "INSERT OR REPLACE INTO hidden_names (name, created_at) VALUES (?, ?)",
                (name, now()),
            )
            search = con.execute("SELECT id FROM searches WHERE lower(query) = lower(?)", (name,)).fetchone()
            if search:
                person_ids = [r["person_id"] for r in con.execute(
                    "SELECT person_id FROM persons WHERE search_id = ?", (search["id"],)
                )]
                for person_id in person_ids:
                    con.execute("DELETE FROM companies WHERE person_id = ?", (person_id,))
                con.execute("DELETE FROM persons WHERE search_id = ?", (search["id"],))
                con.execute("DELETE FROM searches WHERE id = ?", (search["id"],))
            deleted.append(name)
    return {"deleted": deleted}


def clear_search_people(search_id: int) -> None:
    with connect() as con:
        person_ids = [r["person_id"] for r in con.execute(
            "SELECT person_id FROM persons WHERE search_id = ?", (search_id,)
        )]
        for person_id in person_ids:
            con.execute("DELETE FROM companies WHERE person_id = ?", (person_id,))
        con.execute("DELETE FROM persons WHERE search_id = ?", (search_id,))
        con.execute(
            """
            UPDATE searches
            SET total_persons=0, persons_listed=0, details_done=0, error=NULL,
                fuzzy_suggestions=NULL, updated_at=?, exact_match=1
            WHERE id=?
            """,
            (now(), search_id),
        )


def set_fuzzy_suggestions(search_id: int, suggestions: list[str]) -> None:
    cleaned = sorted(dict.fromkeys(x.strip() for x in suggestions if x and x.strip()), key=str.lower)
    with connect() as con:
        con.execute(
            "UPDATE searches SET fuzzy_suggestions=?, updated_at=? WHERE id=?",
            (json.dumps(cleaned, ensure_ascii=False), now(), search_id),
        )


def upsert_person(search_id: int, p: dict[str, Any], person_url: str | None = None) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO persons
                (person_id, search_id, name, age, year_of_birth, gender,
                 number_of_roles, detail_status, person_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                search_id=excluded.search_id,
                name=excluded.name,
                age=excluded.age,
                year_of_birth=excluded.year_of_birth,
                gender=excluded.gender,
                number_of_roles=excluded.number_of_roles,
                person_url=COALESCE(excluded.person_url, persons.person_url),
                updated_at=excluded.updated_at
            """,
            (
                p["personId"],
                search_id,
                p.get("name") or "",
                p.get("age"),
                p.get("yearOfBirth"),
                p.get("gender"),
                p.get("numberOfRoles"),
                person_url,
                now(),
            ),
        )


def list_persons(search_id: int) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT *
            FROM persons
            WHERE search_id = ?
            ORDER BY (latest_revenue_ksek IS NULL), latest_revenue_ksek DESC, name COLLATE NOCASE
            """,
            (search_id,),
        ).fetchall()
        return [_decode_person(dict(r)) for r in rows]


def list_enriched_persons() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT p.*, s.query AS search_query
            FROM persons p
            JOIN searches s ON s.id = p.search_id
            WHERE p.detail_status = 'done'
            ORDER BY (p.latest_revenue_ksek IS NULL), p.latest_revenue_ksek DESC, p.name COLLATE NOCASE
            """
        ).fetchall()
        return [_decode_person(dict(r)) for r in rows]


_AUDITOR_EXISTS = (
    "EXISTS (SELECT 1 FROM companies c WHERE c.person_id = p.person_id "
    "AND lower(COALESCE(c.role, '')) LIKE '%revisor%')"
)


def list_enriched_persons_page(
    *,
    limit: int = 50,
    offset: int = 0,
    sort_key: str = "latest_revenue_ksek",
    sort_dir: str = "desc",
    view: str = "main",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = filters or {}
    where = ["p.detail_status = 'done'"]
    values: list[Any] = []

    if view == "spam":
        where.append("COALESCE(p.is_spam, 0) = 1")
    elif view == "favorites":
        where.append("COALESCE(p.is_favorite, 0) = 1")
        where.append("COALESCE(p.is_spam, 0) = 0")
    elif view == "auditor":
        where.append("COALESCE(p.is_spam, 0) = 0")
        where.append(_AUDITOR_EXISTS)
    else:
        where.append("COALESCE(p.is_spam, 0) = 0")
        where.append(f"NOT {_AUDITOR_EXISTS}")

    def add_number_filter(field: str, op: str, value: Any) -> None:
        if value is None or value == "":
            return
        where.append(f"p.{field} {op} ?")
        values.append(value)

    add_number_filter("latest_revenue_ksek", ">=", filters.get("rev_min_ksek"))
    add_number_filter("latest_revenue_ksek", "<=", filters.get("rev_max_ksek"))
    add_number_filter("employees_total", ">=", filters.get("emp_min"))
    add_number_filter("employees_total", "<=", filters.get("emp_max"))
    add_number_filter("age", ">=", filters.get("age_min"))
    add_number_filter("age", "<=", filters.get("age_max"))

    if filters.get("year"):
        where.append("p.latest_year = ?")
        values.append(filters["year"])
    if filters.get("gender"):
        where.append("p.gender = ?")
        values.append(filters["gender"])
    if filters.get("has_revenue"):
        where.append("p.latest_revenue_ksek IS NOT NULL")
    if filters.get("has_employees"):
        where.append("COALESCE(p.employees_total, 0) > 0")
    if filters.get("active_only"):
        where.append("COALESCE(p.active_company_count, 0) > 0")

    if filters.get("industry"):
        sv_industry = industry_filter_value(filters["industry"]) or filters["industry"]
        where.append("p.industries LIKE ?")
        values.append(f'%"{sv_industry}"%')

    for field, key in (("counties", "county"), ("company_types", "company_type")):
        if filters.get(key):
            where.append(f"p.{field} LIKE ?")
            values.append(f'%"{filters[key]}"%')

    if filters.get("text"):
        needle = f"%{filters['text'].lower()}%"
        industry_clauses = []
        industry_values = []
        for sv, en in INDUSTRY_TRANSLATIONS.items():
            if filters["text"].lower() in en.lower() or filters["text"].lower() in sv.lower():
                industry_clauses.append("p.industries LIKE ?")
                industry_values.append(f'%"{sv}"%')
        industry_sql = ""
        if industry_clauses:
            industry_sql = " OR " + " OR ".join(industry_clauses)
        where.append(
            f"""
            (
                lower(p.name) LIKE ?
                OR lower(s.query) LIKE ?
                OR lower(COALESCE(p.industries, '')) LIKE ?
                OR lower(COALESCE(p.counties, '')) LIKE ?
                OR lower(COALESCE(p.municipalities, '')) LIKE ?
                OR lower(COALESCE(p.company_types, '')) LIKE ?
                {industry_sql}
            )
            """
        )
        values.extend([needle] * 6)
        values.extend(industry_values)

    sort_columns = {
        "name": "p.name COLLATE NOCASE",
        "latest_revenue_ksek": "p.latest_revenue_ksek",
        "latest_year": "p.latest_year",
        "employees_total": "p.employees_total",
        "industries": "p.industries COLLATE NOCASE",
        "counties": "p.counties COLLATE NOCASE",
        "age": "p.age",
        "iranian_score": "p.iranian_score",
    }
    sort_col = sort_columns.get(sort_key, "p.latest_revenue_ksek")
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    null_order = f"({sort_col.split()[0]} IS NULL), " if sort_key != "name" else ""
    order_by = f"{null_order}{sort_col} {direction}, p.name COLLATE NOCASE ASC"
    where_sql = " AND ".join(where)
    safe_limit = max(1, min(int(limit or 50), 250))
    safe_offset = max(0, int(offset or 0))

    with connect() as con:
        total = con.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM persons p
            JOIN searches s ON s.id = p.search_id
            WHERE {where_sql}
            """,
            values,
        ).fetchone()["total"]
        rows = con.execute(
            f"""
            SELECT p.*, s.query AS search_query, {_AUDITOR_EXISTS} AS is_auditor
            FROM persons p
            JOIN searches s ON s.id = p.search_id
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            values + [safe_limit, safe_offset],
        ).fetchall()
        return {
            "persons": [_decode_person(dict(r)) for r in rows],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
        }


def enriched_people_filter_options() -> dict[str, list[Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT latest_year, industries, counties, company_types
            FROM persons
            WHERE detail_status = 'done'
            """
        ).fetchall()
    years: set[int] = set()
    industries: set[str] = set()
    counties: set[str] = set()
    company_types: set[str] = set()
    for row in rows:
        if row["latest_year"]:
            years.add(row["latest_year"])
        industries.update(str(x) for x in _json_list(row["industries"]) if x)
        counties.update(str(x) for x in _json_list(row["counties"]) if x)
        company_types.update(str(x) for x in _json_list(row["company_types"]) if x)
    industry_options = [
        {"value": sv, "label": translate_industry(sv) or sv}
        for sv in industries
    ]
    industry_options.sort(key=lambda item: item["label"].lower())
    return {
        "years": sorted(years, reverse=True),
        "industries": industry_options,
        "counties": sorted(counties),
        "company_types": sorted(company_types),
    }


def get_person(person_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM persons WHERE person_id = ?", (person_id,)).fetchone()
        if not row:
            return None
        person = _decode_person(dict(row))
        companies = con.execute(
            "SELECT * FROM companies WHERE person_id = ? ORDER BY (revenue_ksek IS NULL), revenue_ksek DESC, company_name",
            (person_id,),
        ).fetchall()
        intel_rows = {
            r["orgnr"]: _decode_company_intel(dict(r))
            for r in con.execute("SELECT * FROM company_intel")
        }
        decoded = []
        for c in companies:
            company = _decode_company(dict(c))
            intel = intel_rows.get(company.get("orgnr"))
            if intel:
                company["intel"] = intel
            decoded.append(company)
        person["companies"] = _merge_company_roles(decoded)
        person["is_auditor"] = any(
            "auditor" in str(r).lower()
            for c in person["companies"]
            for r in (c.get("roles") or [])
        )
        person["company_deep_status"] = row["company_deep_status"] if "company_deep_status" in row.keys() else None
        intel_row = con.execute(
            "SELECT * FROM person_intel WHERE person_id = ?", (person_id,)
        ).fetchone()
        person["intel"] = _decode_person_intel(dict(intel_row)) if intel_row else None
        return person


def backfill_iranian_scores(only_missing: bool = True) -> int:
    """Compute and store iranian_score for enriched persons.

    Writes only the iranian_score column; never touches scraped fields.
    """
    from iranian_score import iranian_score

    with connect() as con:
        query = "SELECT person_id, name FROM persons WHERE detail_status = 'done'"
        if only_missing:
            query += " AND iranian_score IS NULL"
        rows = con.execute(query).fetchall()
        updated = 0
        for row in rows:
            score = iranian_score(row["name"])
            con.execute(
                "UPDATE persons SET iranian_score = ? WHERE person_id = ?",
                (score, row["person_id"]),
            )
            updated += 1
        return updated


AUTO_SPAM_THRESHOLD = int(os.environ.get("SWEDENIRAN_AUTOSPAM_THRESHOLD", "40"))


def score_person(
    person_id: str,
    name: str,
    *,
    auto_spam_threshold: int | None = AUTO_SPAM_THRESHOLD,
) -> int:
    """Compute and store iranian_score for one person and auto-spam if low.

    When the score is below `auto_spam_threshold`, the person is flagged as
    spam (unless they are a favorite, which are never auto-spammed). Returns
    the computed score.
    """
    from iranian_score import iranian_score

    score = iranian_score(name)
    with connect() as con:
        if auto_spam_threshold is not None and score < auto_spam_threshold:
            con.execute(
                """
                UPDATE persons
                SET iranian_score = ?,
                    is_spam = CASE WHEN COALESCE(is_favorite, 0) = 1 THEN COALESCE(is_spam, 0) ELSE 1 END,
                    review_updated_at = ?
                WHERE person_id = ?
                """,
                (score, now(), person_id),
            )
        else:
            con.execute(
                "UPDATE persons SET iranian_score = ? WHERE person_id = ?",
                (score, person_id),
            )
    return score


def auto_spam_below(threshold: int) -> int:
    """Flag enriched, non-favorite persons whose score is below threshold.

    Reversible: only sets the is_spam flag. Favorites are never auto-spammed.
    """
    ts = now()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE persons
            SET is_spam = 1, review_updated_at = ?
            WHERE detail_status = 'done'
              AND COALESCE(is_favorite, 0) = 0
              AND COALESCE(is_spam, 0) = 0
              AND iranian_score IS NOT NULL
              AND iranian_score < ?
            """,
            (ts, int(threshold)),
        )
        return cur.rowcount


def score_summary() -> dict[str, Any]:
    with connect() as con:
        row = con.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN iranian_score IS NOT NULL THEN 1 ELSE 0 END), 0) AS scored,
                COALESCE(SUM(CASE WHEN COALESCE(is_spam, 0) = 1 THEN 1 ELSE 0 END), 0) AS spam
            FROM persons
            WHERE detail_status = 'done'
            """
        ).fetchone()
        return {"total": row["total"], "scored": row["scored"], "spam": row["spam"]}


def mark_person_spam(person_id: str) -> bool:
    ts = now()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE persons
            SET is_spam = 1, review_updated_at = ?
            WHERE person_id = ? AND detail_status = 'done'
            """,
            (ts, person_id),
        )
        return cur.rowcount > 0


def restore_person_from_spam(person_id: str) -> bool:
    ts = now()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE persons
            SET is_spam = 0, review_updated_at = ?
            WHERE person_id = ? AND detail_status = 'done'
            """,
            (ts, person_id),
        )
        return cur.rowcount > 0


def set_person_favorite(person_id: str, favorite: bool) -> bool:
    ts = now()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE persons
            SET is_favorite = ?, review_updated_at = ?
            WHERE person_id = ? AND detail_status = 'done'
            """,
            (1 if favorite else 0, ts, person_id),
        )
        return cur.rowcount > 0


def set_person_detail_status(person_id: str, status: str, error: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "UPDATE persons SET detail_status = ?, error = ?, updated_at = ? WHERE person_id = ?",
            (status, error, now(), person_id),
        )


# ---------------------------------------------------------------- company deep enrich

def queue_favorite_company_deep(person_id: str | None = None, queue_at: int | None = None) -> int:
    """Queue company deep-enrichment for favorites only (never others).

    If person_id is given, queue just that person (must be favorite).
    Returns number of people queued.
    """
    ts = queue_at or now()
    with connect() as con:
        if person_id:
            cur = con.execute(
                """
                UPDATE persons
                SET company_deep_status='queued', company_deep_queued_at=?,
                    company_deep_error=NULL, company_deep_updated_at=?
                WHERE person_id=? AND COALESCE(is_favorite,0)=1
                """,
                (ts, ts, person_id),
            )
            return cur.rowcount
        cur = con.execute(
            """
            UPDATE persons
            SET company_deep_status='queued', company_deep_queued_at=?,
                company_deep_error=NULL, company_deep_updated_at=?
            WHERE COALESCE(is_favorite,0)=1
              AND COALESCE(company_deep_status,'idle') NOT IN ('queued','running','done')
            """,
            (ts, ts),
        )
        return cur.rowcount


def claim_company_deep_person() -> dict[str, Any] | None:
    """Claim one queued favorite for company deep-enrichment (highest priority first)."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """
            SELECT * FROM persons
            WHERE company_deep_status='queued' AND COALESCE(is_favorite,0)=1
            ORDER BY COALESCE(company_deep_queued_at,0) DESC, updated_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE persons SET company_deep_status='running', company_deep_updated_at=? WHERE person_id=?",
            (now(), row["person_id"]),
        )
        return dict(row)


def set_company_deep_status(person_id: str, status: str, error: str | None = None) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE persons
            SET company_deep_status=?, company_deep_error=?, company_deep_updated_at=?
            WHERE person_id=?
            """,
            (status, error, now(), person_id),
        )


def mark_company_deep_retry(person_id: str, next_retry_at: int, attempts: int) -> None:
    """Schedule a favorite to be re-checked later (keyless engines are flaky)."""
    with connect() as con:
        con.execute(
            """
            UPDATE persons
            SET company_deep_status='retry', company_deep_updated_at=?,
                company_deep_next_retry_at=?, company_deep_attempts=?
            WHERE person_id=?
            """,
            (now(), next_retry_at, attempts, person_id),
        )


def requeue_due_company_deep_retries() -> int:
    """Flip due 'retry' favorites back to 'queued'. Returns count re-queued."""
    with connect() as con:
        cur = con.execute(
            """
            UPDATE persons SET company_deep_status='queued'
            WHERE company_deep_status='retry'
              AND COALESCE(is_favorite,0)=1
              AND COALESCE(company_deep_next_retry_at,0) <= ?
            """,
            (now(),),
        )
        return cur.rowcount


def company_deep_status_counts() -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT COALESCE(company_deep_status,'idle') AS status, COUNT(*) AS c
            FROM persons WHERE COALESCE(is_favorite,0)=1
            GROUP BY COALESCE(company_deep_status,'idle')
            """
        ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    counts["favorites_total"] = sum(counts.values())
    return counts


def upsert_company_intel(orgnr: str, intel: dict[str, Any]) -> None:
    if not orgnr:
        return
    with connect() as con:
        con.execute(
            """
            INSERT INTO company_intel
                (orgnr, company_name, website, website_confidence, linkedin_url,
                 linkedin_confidence, email, phone, socials, description, purpose,
                 keywords, address, news, certifications, website_emails,
                 website_phones, evidence, data, search_provider, enriched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(orgnr) DO UPDATE SET
                company_name=excluded.company_name,
                -- Discoverable fields are flaky (search throttling): never wipe a
                -- previously verified value with a new NULL/empty result.
                website=COALESCE(excluded.website, company_intel.website),
                website_confidence=COALESCE(excluded.website_confidence, company_intel.website_confidence),
                linkedin_url=COALESCE(excluded.linkedin_url, company_intel.linkedin_url),
                linkedin_confidence=COALESCE(excluded.linkedin_confidence, company_intel.linkedin_confidence),
                email=COALESCE(excluded.email, company_intel.email),
                phone=COALESCE(excluded.phone, company_intel.phone),
                socials=CASE WHEN excluded.socials IN ('{}', '', NULL) THEN company_intel.socials ELSE excluded.socials END,
                description=COALESCE(excluded.description, company_intel.description),
                purpose=COALESCE(excluded.purpose, company_intel.purpose),
                keywords=CASE WHEN excluded.keywords IN ('[]', '', NULL) THEN company_intel.keywords ELSE excluded.keywords END,
                address=COALESCE(excluded.address, company_intel.address),
                news=CASE WHEN excluded.news IN ('[]', '', NULL) THEN company_intel.news ELSE excluded.news END,
                certifications=CASE WHEN excluded.certifications IN ('[]', '', NULL) THEN company_intel.certifications ELSE excluded.certifications END,
                website_emails=CASE WHEN excluded.website_emails IN ('[]', '', NULL) THEN company_intel.website_emails ELSE excluded.website_emails END,
                website_phones=CASE WHEN excluded.website_phones IN ('[]', '', NULL) THEN company_intel.website_phones ELSE excluded.website_phones END,
                evidence=excluded.evidence,
                data=excluded.data,
                search_provider=excluded.search_provider,
                enriched_at=excluded.enriched_at
            """,
            (
                orgnr,
                intel.get("company_name"),
                intel.get("website"),
                intel.get("website_confidence"),
                intel.get("linkedin_url"),
                intel.get("linkedin_confidence"),
                intel.get("email"),
                intel.get("phone"),
                json.dumps(intel.get("socials") or {}, ensure_ascii=False),
                intel.get("description"),
                intel.get("purpose"),
                json.dumps(intel.get("keywords") or [], ensure_ascii=False),
                intel.get("address"),
                json.dumps(intel.get("news") or [], ensure_ascii=False),
                json.dumps(intel.get("certifications") or [], ensure_ascii=False),
                json.dumps(intel.get("website_emails") or [], ensure_ascii=False),
                json.dumps(intel.get("website_phones") or [], ensure_ascii=False),
                json.dumps(intel.get("evidence") or [], ensure_ascii=False),
                json.dumps(intel, ensure_ascii=False),
                intel.get("search_provider"),
                now(),
            ),
        )


def get_company_intel(orgnr: str) -> dict[str, Any] | None:
    if not orgnr:
        return None
    with connect() as con:
        row = con.execute("SELECT * FROM company_intel WHERE orgnr=?", (orgnr,)).fetchone()
        return _decode_company_intel(dict(row)) if row else None


def _decode_company_intel(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("keywords", "news", "certifications", "website_emails", "website_phones", "evidence"):
        row[key] = _json_list(row.get(key))
    try:
        row["socials"] = json.loads(row.get("socials") or "{}")
    except (TypeError, ValueError):
        row["socials"] = {}
    return row


def upsert_person_intel(person_id: str, intel: dict[str, Any]) -> None:
    """Store verified personal profiles for a favorite (additive / non-wiping).

    Discoverable fields are flaky (search throttling), so a fresh NULL/empty
    result never overwrites a previously verified value.
    """
    if not person_id:
        return
    with connect() as con:
        con.execute(
            """
            INSERT INTO person_intel
                (person_id, name, linkedin_url, linkedin_confidence, instagram_url,
                 instagram_confidence, socials, headline, evidence, data,
                 search_provider, enriched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(person_id) DO UPDATE SET
                name=excluded.name,
                linkedin_url=COALESCE(excluded.linkedin_url, person_intel.linkedin_url),
                linkedin_confidence=COALESCE(excluded.linkedin_confidence, person_intel.linkedin_confidence),
                instagram_url=COALESCE(excluded.instagram_url, person_intel.instagram_url),
                instagram_confidence=COALESCE(excluded.instagram_confidence, person_intel.instagram_confidence),
                socials=CASE WHEN excluded.socials IN ('{}', '', NULL) THEN person_intel.socials ELSE excluded.socials END,
                headline=COALESCE(excluded.headline, person_intel.headline),
                evidence=excluded.evidence,
                data=excluded.data,
                search_provider=excluded.search_provider,
                enriched_at=excluded.enriched_at
            """,
            (
                person_id,
                intel.get("name"),
                intel.get("linkedin_url"),
                intel.get("linkedin_confidence"),
                intel.get("instagram_url"),
                intel.get("instagram_confidence"),
                json.dumps(intel.get("socials") or {}, ensure_ascii=False),
                intel.get("headline"),
                json.dumps(intel.get("evidence") or [], ensure_ascii=False),
                json.dumps(intel, ensure_ascii=False),
                intel.get("search_provider"),
                now(),
            ),
        )


def get_person_intel(person_id: str) -> dict[str, Any] | None:
    if not person_id:
        return None
    with connect() as con:
        row = con.execute(
            "SELECT * FROM person_intel WHERE person_id = ?", (person_id,)
        ).fetchone()
        return _decode_person_intel(dict(row)) if row else None


def _decode_person_intel(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = _json_list(row.get("evidence"))
    try:
        row["socials"] = json.loads(row.get("socials") or "{}")
    except (TypeError, ValueError):
        row["socials"] = {}
    return row


def replace_person_companies(person_id: str, companies: list[dict[str, Any]]) -> None:
    with connect() as con:
        con.execute("DELETE FROM companies WHERE person_id = ?", (person_id,))
        for c in companies:
            con.execute(
                """
                INSERT OR REPLACE INTO companies
                (person_id, orgnr, company_name, role, revenue_ksek, profit_ksek, revenue_year,
                 employees, phone, email, homepage, municipality, county, allabolag_url,
                 industries, nace_industries, company_type, status, registration_date, foundation_year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_id,
                    c.get("orgnr"),
                    c.get("company_name") or "",
                    c.get("role"),
                    c.get("revenue_ksek"),
                    c.get("profit_ksek"),
                    c.get("revenue_year"),
                    c.get("employees"),
                    c.get("phone"),
                    c.get("email"),
                    c.get("homepage"),
                    c.get("municipality"),
                    c.get("county"),
                    c.get("allabolag_url"),
                    json.dumps(c.get("industries") or [], ensure_ascii=False),
                    json.dumps(c.get("nace_industries") or [], ensure_ascii=False),
                    c.get("company_type"),
                    c.get("status"),
                    c.get("registration_date"),
                    c.get("foundation_year"),
                ),
            )
        update_person_aggregates(con, person_id)


def get_cached_company(orgnr: str) -> dict[str, Any] | None:
    if not orgnr:
        return None
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM companies
            WHERE orgnr = ? AND (industries IS NOT NULL OR employees IS NOT NULL OR status IS NOT NULL)
            ORDER BY id DESC
            LIMIT 1
            """,
            (orgnr,),
        ).fetchone()
        return _decode_company(dict(row)) if row else None


def update_person_aggregates(con: sqlite3.Connection, person_id: str) -> None:
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM companies WHERE person_id = ?", (person_id,)
    )]
    # Collapse multiple roles in the same company so each company is counted once.
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        orgnr = (row.get("orgnr") or "").strip()
        key = orgnr or f"__row_{row.get('id')}"
        if key not in unique:
            unique[key] = row
    rows = list(unique.values())
    revenue_sum = 0
    profit_sum = 0
    has_revenue = False
    has_profit = False
    latest_year = None
    employees_total = 0
    employees_max = None
    active_count = 0
    industries: set[str] = set()
    counties: set[str] = set()
    municipalities: set[str] = set()
    company_types: set[str] = set()

    for row in rows:
        if row.get("revenue_ksek") is not None:
            revenue_sum += int(row["revenue_ksek"])
            has_revenue = True
        if row.get("profit_ksek") is not None:
            profit_sum += int(row["profit_ksek"])
            has_profit = True
        year = _as_int(row.get("revenue_year"))
        if year and (latest_year is None or year > latest_year):
            latest_year = year
        employees = _as_int(row.get("employees"))
        if employees is not None:
            employees_total += employees
            employees_max = employees if employees_max is None else max(employees_max, employees)
        if (row.get("status") or "").upper() == "ACTIVE":
            active_count += 1
        for item in _json_list(row.get("industries")):
            if item:
                industries.add(str(item))
        if row.get("county"):
            counties.add(row["county"])
        if row.get("municipality"):
            municipalities.add(row["municipality"])
        if row.get("company_type"):
            company_types.add(row["company_type"])

    con.execute(
        """
        UPDATE persons SET
            latest_year=?,
            latest_revenue_ksek=?,
            total_profit_ksek=?,
            employees_total=?,
            employees_max=?,
            active_company_count=?,
            company_count=?,
            industries=?,
            counties=?,
            municipalities=?,
            company_types=?,
            updated_at=?
        WHERE person_id=?
        """,
        (
            latest_year,
            revenue_sum if has_revenue else None,
            profit_sum if has_profit else None,
            employees_total if employees_total else None,
            employees_max,
            active_count,
            len(rows),
            json.dumps(sorted(industries), ensure_ascii=False),
            json.dumps(sorted(counties), ensure_ascii=False),
            json.dumps(sorted(municipalities), ensure_ascii=False),
            json.dumps(sorted(company_types), ensure_ascii=False),
            now(),
            person_id,
        ),
    )


def backfill_person_aggregates() -> None:
    with connect() as con:
        person_ids = [r["person_id"] for r in con.execute("SELECT person_id FROM persons")]
        for person_id in person_ids:
            update_person_aggregates(con, person_id)


def reset_persons_for_enrich(search_id: int) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE persons
            SET detail_status='pending', error=NULL, updated_at=?
            WHERE search_id=? AND detail_status IN ('idle', 'error')
            """,
            (now(), search_id),
        )


def pending_persons_for_search(search_id: int, limit: int = 1) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM persons
            WHERE search_id = ? AND detail_status = 'pending'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (search_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def claim_pending_person_for_search(search_id: int) -> dict[str, Any] | None:
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """
            SELECT * FROM persons
            WHERE search_id = ? AND detail_status = 'pending'
            ORDER BY updated_at ASC
            LIMIT 1
            """,
            (search_id,),
        ).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE persons SET detail_status='enriching', updated_at=? WHERE person_id=? AND detail_status='pending'",
            (now(), row["person_id"]),
        )
        claimed = con.execute("SELECT * FROM persons WHERE person_id=?", (row["person_id"],)).fetchone()
        return dict(claimed) if claimed else None


def recount_search(search_id: int) -> None:
    with connect() as con:
        listed = con.execute(
            "SELECT COUNT(*) AS c FROM persons WHERE search_id = ?", (search_id,)
        ).fetchone()["c"]
        done = con.execute(
            """
            SELECT COUNT(*) AS c FROM persons
            WHERE search_id = ? AND detail_status = 'done'
            """,
            (search_id,),
        ).fetchone()["c"]
        con.execute(
            "UPDATE searches SET persons_listed = ?, details_done = ?, updated_at = ? WHERE id = ?",
            (listed, done, now(), search_id),
        )


def claim_queued_search() -> dict[str, Any] | None:
    """Atomically claim one queued search for listing."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """
            SELECT * FROM searches
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE searches SET status='listing', updated_at=? WHERE id=? AND status='queued'",
            (now(), row["id"]),
        )
        claimed = con.execute("SELECT * FROM searches WHERE id=?", (row["id"],)).fetchone()
        return _decode_search(dict(claimed)) if claimed else None


def next_enriching_search() -> dict[str, Any] | None:
    skip = list_skip_enrich_names()
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM searches
            WHERE status = 'enriching'
            ORDER BY COALESCE(enrich_queued_at, 0) DESC, created_at ASC
            """
        ).fetchall()
        for row in rows:
            if row["query"].lower() in skip:
                continue
            return _decode_search(dict(row))
        return None


def _decode_search(row: dict[str, Any]) -> dict[str, Any]:
    row["fuzzy_suggestions"] = _json_list(row.get("fuzzy_suggestions"))
    return row


def _decode_person(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("counties", "municipalities", "company_types"):
        row[key] = _json_list(row.get(key))
    row["industries"] = translate_industries(_json_list(row.get("industries")))
    row["is_spam"] = bool(row.get("is_spam"))
    row["is_favorite"] = bool(row.get("is_favorite"))
    if "is_auditor" in row:
        row["is_auditor"] = bool(row.get("is_auditor"))
    if not row.get("person_url") and row.get("person_id") and row.get("name"):
        row["person_url"] = f"https://www.allabolag.se/befattningshavare/{_slugify(row['name'])}/-/{row['person_id']}"
    return row


def _decode_company(row: dict[str, Any]) -> dict[str, Any]:
    row["role"] = translate_role(row.get("role"))
    row["industries"] = translate_industries(_json_list(row.get("industries")))
    row["nace_industries"] = _json_list(row.get("nace_industries"))
    return row


# Priority used to pick the primary (current) role when a person holds several
# roles in the same company (e.g. was CEO, now board member). Higher wins.
_ROLE_PRIORITY: dict[str, int] = {
    "CEO": 100,
    "External CEO": 95,
    "Deputy CEO": 90,
    "External deputy CEO": 85,
    "Chairman": 80,
    "Owner": 70,
    "General partner": 60,
    "General partner (LP)": 60,
    "Limited partner": 55,
    "Board member": 50,
    "Deputy board member": 45,
    "Authorized signatory": 40,
    "External signatory": 38,
    "Manager": 35,
    "Shareholder": 30,
    "Auditor": 20,
    "Lead auditor": 20,
    "Deputy auditor": 15,
    "Lay auditor": 15,
}


def _role_rank(role: str | None) -> int:
    return _ROLE_PRIORITY.get((role or "").strip(), 10)


def _merge_company_roles(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows describing the same company (same orgnr) into one entry.

    A person can hold several roles in one company. Each company must appear
    once; all held roles are kept as `roles` (history) and the most senior one
    is exposed as `role` (the current/primary role).
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for c in companies:
        orgnr = (c.get("orgnr") or "").strip()
        key = orgnr or f"__noorg_{id(c)}"
        if key not in merged:
            c["roles"] = []
            merged[key] = c
            order.append(key)
        entry = merged[key]
        role = c.get("role")
        if role and role not in entry["roles"]:
            entry["roles"].append(role)
    for key in order:
        entry = merged[key]
        roles = entry.get("roles") or ([entry["role"]] if entry.get("role") else [])
        roles = sorted(dict.fromkeys(roles), key=lambda r: (-_role_rank(r), r))
        entry["roles"] = roles
        entry["role"] = roles[0] if roles else entry.get("role")
    return [merged[key] for key in order]


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str) and "-" in value:
            value = value.rsplit("-", 1)[-1]
        return int(float(str(value).replace(" ", "")))
    except (TypeError, ValueError):
        return None


def _slugify(value: str) -> str:
    value = (value or "").lower()
    for old, new in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        value = value.replace(old, new)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "person"
