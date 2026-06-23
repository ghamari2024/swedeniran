"""Generate campaign websites via Cursor SDK (Node local agent)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

import env_loader  # noqa: F401 — load .env before reading CURSOR_API_KEY

log = logging.getLogger("site_agent")

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "campaign_agent.mjs")
_DEFAULT_MODEL = os.environ.get("SWEDENIRAN_CAMPAIGN_MODEL", "composer-2")


def _api_key() -> str:
    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "CURSOR_API_KEY is not set — copy .env.example to .env and add your key "
            "from https://cursor.com/dashboard/integrations then restart the server"
        )
    return key


def agent_status() -> dict[str, Any]:
    """Preflight check for CRM campaign site generation."""
    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    node_modules = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node_modules", "@cursor", "sdk")
    node_ok = shutil.which("node") is not None
    script_ok = os.path.isfile(_SCRIPT)
    ready = bool(key and node_ok and script_ok and os.path.isdir(node_modules))
    issues: list[str] = []
    if not key:
        issues.append("CURSOR_API_KEY missing in .env")
    if not node_ok:
        issues.append("Node.js not found on PATH")
    if not os.path.isdir(node_modules):
        issues.append("Run npm install in the project root")
    if not script_ok:
        issues.append("Campaign agent script missing")
    return {
        "ready": ready,
        "model": resolve_model(None),
        "issues": issues,
        "env_path": os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        "has_api_key": bool(key),
    }


def resolve_model(preferred: str | None = None) -> str:
    if preferred and preferred.strip():
        return preferred.strip()
    return _DEFAULT_MODEL


def build_generation_prompt(
    *,
    base_prompt: str,
    system_prompt: str | None,
    company: dict[str, Any],
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
        )
        if value
    )

    parts = []
    if system_prompt:
        parts.append(f"System instructions:\n{system_prompt.strip()}\n")
    parts.append(
        "Build a responsive, modern, single-page business website for this Swedish company.\n"
        "Requirements:\n"
        "- Create `index.html` in the current working directory (this folder).\n"
        "- Use inline CSS (and inline JS only if needed) so the page is self-contained.\n"
        "- Mobile-first responsive layout, professional typography, clear CTA sections.\n"
        "- Use the company's real name, address, phone, and email from the data below.\n"
        "- If a current website URL is provided, mirror its brand tone but improve the design.\n"
        "- Language: Swedish unless the user prompt says otherwise.\n"
    )
    parts.append(f"User campaign prompt:\n{base_prompt.strip()}\n")
    parts.append(f"Company data:\n{company_block}\n")
    return "\n".join(parts)


def build_refine_prompt(refine_prompt: str) -> str:
    return (
        "Improve the existing website in this working directory.\n"
        "Keep `index.html` as the entry point. Apply these changes:\n\n"
        f"{refine_prompt.strip()}"
    )


def _invoke_node(
    *,
    action: str,
    work_dir: str,
    prompt: str,
    model: str,
    agent_id: str | None = None,
    timeout: int = 3600,
) -> tuple[str, str]:
    if not os.path.isfile(_SCRIPT):
        raise RuntimeError(f"Campaign agent script missing: {_SCRIPT}")
    _api_key()  # validate early
    payload = {
        "action": action,
        "workDir": os.path.abspath(work_dir),
        "prompt": prompt,
        "model": model,
    }
    if agent_id:
        payload["agentId"] = agent_id
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            ["node", _SCRIPT, json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=os.path.dirname(os.path.dirname(_SCRIPT)),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Agent timed out after {timeout}s") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "agent failed").strip()
        raise RuntimeError(err[:800])
    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid agent response: {proc.stdout[:300]}") from e
    return str(data.get("agentId") or agent_id or ""), str(data.get("status") or "error")


def generate_site(
    work_dir: str,
    prompt: str,
    model: str | None = None,
) -> tuple[str, str]:
    resolved = resolve_model(model)
    os.makedirs(work_dir, exist_ok=True)
    return _invoke_node(action="generate", work_dir=work_dir, prompt=prompt, model=resolved)


def refine_site(
    work_dir: str,
    agent_id: str | None,
    refine_prompt: str,
    model: str | None = None,
) -> tuple[str, str]:
    resolved = resolve_model(model)
    prompt = build_refine_prompt(refine_prompt)
    os.makedirs(work_dir, exist_ok=True)
    action = "refine" if agent_id else "generate"
    return _invoke_node(
        action=action,
        work_dir=work_dir,
        prompt=prompt,
        model=resolved,
        agent_id=agent_id,
    )


def snapshot_work_dir(work_dir: str, version_dir: str) -> None:
    """Copy agent work output into an immutable version folder."""
    if os.path.isdir(version_dir):
        shutil.rmtree(version_dir)
    if os.path.isdir(work_dir):
        shutil.copytree(work_dir, version_dir)
    else:
        os.makedirs(version_dir, exist_ok=True)


def site_has_index(version_dir: str) -> bool:
    return os.path.isfile(os.path.join(version_dir, "index.html"))
