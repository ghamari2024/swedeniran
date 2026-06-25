"""Generate personalized campaign emails via Cursor SDK."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
from typing import Any

import env_loader  # noqa: F401

log = logging.getLogger("email_agent")

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "email_agent.mjs")
_DRAFT_FILE = "email_draft.json"
_DEFAULT_MODEL = os.environ.get("SWEDENIRAN_CAMPAIGN_MODEL", "composer-2")


def resolve_model(preferred: str | None = None) -> str:
    if preferred and preferred.strip():
        return preferred.strip()
    return _DEFAULT_MODEL


def build_email_prompt(
    *,
    base_prompt: str,
    system_prompt: str | None,
    company: dict[str, Any],
    preview_url: str,
) -> str:
    industries = company.get("industries")
    if isinstance(industries, list):
        industries_text = ", ".join(str(x) for x in industries if x)
    else:
        industries_text = str(industries or "")

    company_block = "\n".join(
        f"- {label}: {value}"
        for label, value in (
            ("Company name", company.get("company_name")),
            ("Org number", company.get("orgnr")),
            ("Website", company.get("website")),
            ("Address", company.get("address")),
            ("Phone", company.get("phone")),
            ("Email", company.get("email")),
            ("Municipality", company.get("municipality")),
            ("County", company.get("county")),
            ("Industries", industries_text),
            ("Description", company.get("description")),
            ("Preview URL", preview_url),
        )
        if value
    )

    parts = []
    if system_prompt:
        parts.append(f"System instructions:\n{system_prompt.strip()}\n")
    parts.append(
        "Write a personalized B2B outreach email for this Swedish company.\n"
        "Output ONLY a valid JSON file named `email_draft.json` in the current directory.\n"
        "JSON schema:\n"
        "{\n"
        '  "subject": "short compelling subject line",\n'
        '  "greeting": "Hej [name],",\n'
        '  "paragraphs": ["paragraph 1", "paragraph 2"],\n'
        '  "cta_label": "button text for preview link"\n'
        "}\n"
        "Rules:\n"
        "- Language: Swedish unless the user prompt says otherwise.\n"
        "- Professional, concise, human tone — not spammy.\n"
        "- Mention we prepared a website preview for them.\n"
        "- Do NOT include HTML; plain text strings only.\n"
        "- Keep subject under 80 characters.\n"
    )
    parts.append(f"User email prompt:\n{base_prompt.strip()}\n")
    parts.append(f"Company data:\n{company_block}\n")
    return "\n".join(parts)


def build_refine_prompt(refine_prompt: str) -> str:
    return (
        "Improve the existing `email_draft.json` in this directory.\n"
        "Keep valid JSON with keys: subject, greeting, paragraphs (array), cta_label.\n"
        "Apply these changes:\n\n"
        f"{refine_prompt.strip()}"
    )


def _invoke_node(
    *,
    action: str,
    work_dir: str,
    prompt: str,
    model: str,
    agent_id: str | None = None,
    timeout: int = 600,
) -> tuple[str, str]:
    if not os.path.isfile(_SCRIPT):
        raise RuntimeError(f"Email agent script missing: {_SCRIPT}")
    payload = {
        "action": action,
        "workDir": os.path.abspath(work_dir),
        "prompt": prompt,
        "model": model,
    }
    if agent_id:
        payload["agentId"] = agent_id
    env = os.environ.copy()
    proc = subprocess.run(
        ["node", _SCRIPT, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=os.path.dirname(os.path.dirname(_SCRIPT)),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "email agent failed").strip()
        raise RuntimeError(err[:800])
    data = json.loads(proc.stdout.strip() or "{}")
    return str(data.get("agentId") or agent_id or ""), str(data.get("status") or "error")


def _load_draft(work_dir: str) -> dict[str, Any]:
    path = os.path.join(work_dir, _DRAFT_FILE)
    if not os.path.isfile(path):
        raise RuntimeError("Agent did not create email_draft.json")
    raw = open(path, encoding="utf-8").read()
    # Strip markdown fences if agent wrapped JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("email_draft.json must be a JSON object")
    return data


def fallback_draft(company: dict[str, Any], preview_url: str) -> dict[str, Any]:
    name = company.get("company_name") or "ert företag"
    return {
        "subject": f"Förslag på webbplats för {name}",
        "greeting": f"Hej {name},",
        "paragraphs": [
            "Jag hoppas att det här meddelandet når er väl.",
            "Vi har tagit fram ett förslag på en uppdaterad webbplats för ert företag "
            "som bättre speglar er verksamhet och gör det enklare för kunder att hitta er.",
            "Länken nedan visar ett utkast — gärna hör av er om ni vill gå vidare.",
        ],
        "cta_label": "Se webbplatsförslaget",
        "preview_url": preview_url,
    }


def normalize_draft(data: dict[str, Any], preview_url: str) -> dict[str, Any]:
    paragraphs = data.get("paragraphs") or []
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    return {
        "subject": str(data.get("subject") or "").strip()[:200],
        "greeting": str(data.get("greeting") or "Hej,").strip(),
        "paragraphs": [str(p).strip() for p in paragraphs if str(p).strip()],
        "cta_label": str(data.get("cta_label") or "Se förslaget").strip(),
        "preview_url": preview_url,
    }


def draft_to_plain_text(draft: dict[str, Any]) -> str:
    lines = [draft.get("greeting") or "Hej,"]
    lines.extend(draft.get("paragraphs") or [])
    url = draft.get("preview_url") or ""
    if url:
        lines.append("")
        lines.append(f"{draft.get('cta_label') or 'Länk'}: {url}")
    return "\n\n".join(lines)


def render_email_html(
    draft: dict[str, Any],
    *,
    from_addr: str,
    shots: list[tuple[str, str]] | None = None,
) -> str:
    """Render HTML email. shots = list of (cid, label)."""
    greeting = html.escape(draft.get("greeting") or "Hej,")
    paras = "".join(
        f"<p style=\"margin:0 0 14px;line-height:1.55;color:#1a1a1a;\">{html.escape(p)}</p>"
        for p in (draft.get("paragraphs") or [])
    )
    preview_url = html.escape(draft.get("preview_url") or "")
    cta = html.escape(draft.get("cta_label") or "Se förslaget")
    from_esc = html.escape(from_addr or "")

    gallery = ""
    if shots:
        # Single image only: a high image-to-text ratio is a strong Gmail
        # Promotions/bulk signal. Keep one preview thumbnail, link to the rest.
        src, label = shots[0]
        if src.startswith("data:") or src.startswith("http://") or src.startswith("https://"):
            img_src = src
        else:
            img_src = f"cid:{html.escape(src)}"
        gallery = (
            f'<p style="margin:0 0 14px;">'
            f'<img src="{img_src}" alt="{html.escape(label)}" '
            f'style="max-width:100%;" />'
            f"</p>"
        )

    cta_block = ""
    if preview_url:
        # Plain inline text link (no colored button) reads as personal mail.
        cta_block = (
            f'<p style="margin:0 0 14px;line-height:1.55;color:#1a1a1a;">'
            f'{cta}: <a href="{preview_url}">{preview_url}</a>'
            f"</p>"
        )

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;font-family:Arial,sans-serif;color:#1a1a1a;">
<div style="max-width:600px;margin:0;padding:0;">
<p style="margin:0 0 14px;">{greeting}</p>
{paras}
{gallery}
{cta_block}
<p style="margin:16px 0 0;">Med vänliga hälsningar,<br>{from_esc}</p>
</div></body></html>"""


def generate_email_draft(
    work_dir: str,
    prompt: str,
    model: str | None = None,
) -> tuple[str, dict[str, Any]]:
    resolved = resolve_model(model)
    os.makedirs(work_dir, exist_ok=True)
    agent_id, status = _invoke_node(
        action="generate", work_dir=work_dir, prompt=prompt, model=resolved
    )
    if status != "finished":
        raise RuntimeError(f"Email agent ended with status: {status}")
    return agent_id, _load_draft(work_dir)


def preview_html_for_message(
    campaign_id: int,
    orgnr: str,
    msg: dict[str, Any],
    *,
    from_addr: str,
) -> str:
    """Rebuild preview HTML with embedded screenshot data URLs (browser-safe)."""
    import db
    import screenshots

    draft = msg.get("draft_json") or {}
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except (TypeError, ValueError):
            draft = {}
    if not draft:
        draft = {
            "greeting": "Hej,",
            "paragraphs": [],
            "cta_label": "Se förslaget",
            "preview_url": "",
        }
    shots_dir = db.campaign_shots_dir(campaign_id, orgnr)
    shot_meta = screenshots.shots_as_data_urls(shots_dir)
    return render_email_html(draft, from_addr=from_addr, shots=shot_meta or None)


def refine_email_draft(
    work_dir: str,
    agent_id: str | None,
    refine_prompt: str,
    model: str | None = None,
) -> tuple[str, dict[str, Any]]:
    resolved = resolve_model(model)
    prompt = build_refine_prompt(refine_prompt)
    action = "refine" if agent_id else "generate"
    agent_id, status = _invoke_node(
        action=action,
        work_dir=work_dir,
        prompt=prompt,
        model=resolved,
        agent_id=agent_id,
    )
    if status != "finished":
        raise RuntimeError(f"Email agent ended with status: {status}")
    return agent_id, _load_draft(work_dir)
