"""Capture website screenshots for email marketing."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

log = logging.getLogger("screenshots")

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "screenshot.mjs")


def capture_site_screenshots(html_path: str, out_dir: str, *, timeout: int = 120) -> list[dict[str, Any]]:
    if not os.path.isfile(html_path):
        raise RuntimeError(f"HTML not found: {html_path}")
    if not os.path.isfile(_SCRIPT):
        raise RuntimeError(f"Screenshot script missing: {_SCRIPT}")
    os.makedirs(out_dir, exist_ok=True)
    payload = {"htmlPath": os.path.abspath(html_path), "outDir": os.path.abspath(out_dir)}
    proc = subprocess.run(
        ["node", _SCRIPT, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=os.path.dirname(os.path.dirname(_SCRIPT)),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "screenshot failed").strip()
        raise RuntimeError(err[:800])
    data = json.loads(proc.stdout.strip() or "{}")
    return list(data.get("shots") or [])


def list_shot_files(out_dir: str) -> list[tuple[str, str]]:
    """Return (cid_name, file_path) for known shot files."""
    mapping = [
        ("shot_hero", "hero.jpg"),
        ("shot_desktop", "desktop.jpg"),
        ("shot_mobile", "mobile.jpg"),
    ]
    out = []
    for cid, fname in mapping:
        p = os.path.join(out_dir, fname)
        if os.path.isfile(p):
            out.append((cid, p))
    return out


def shots_as_data_urls(out_dir: str) -> list[tuple[str, str]]:
    """Return (data_url, label) pairs for inline browser preview."""
    import base64

    mapping = [
        ("hero.jpg", "Startsida"),
        ("desktop.jpg", "Desktop"),
        ("mobile.jpg", "Mobil"),
    ]
    out: list[tuple[str, str]] = []
    for fname, label in mapping:
        path = os.path.join(out_dir, fname)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        out.append((f"data:image/jpeg;base64,{b64}", label))
    return out
