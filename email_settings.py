"""Runtime email settings (simulation toggle) — persisted outside company DB."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import env_loader  # noqa: F401

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_PATH = os.path.join(_ROOT, "data", "email_settings.json")
_DEFAULT_TO = "ghamari2004@gmail.com"
_lock = threading.Lock()


def _default_to() -> str:
    return (os.environ.get("EMAIL_SIMULATION_TO") or _DEFAULT_TO).strip()


def default_settings() -> dict[str, Any]:
    return {
        "simulation_enabled": True,
        "simulation_to": _default_to(),
    }


def _ensure_file() -> dict[str, Any]:
    settings = default_settings()
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    if not os.path.isfile(_SETTINGS_PATH):
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return settings
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    settings["simulation_enabled"] = bool(raw.get("simulation_enabled", True))
    to_addr = (raw.get("simulation_to") or "").strip()
    settings["simulation_to"] = to_addr or _default_to()
    return settings


def load_settings() -> dict[str, Any]:
    with _lock:
        return _ensure_file()


def save_settings(*, simulation_enabled: bool | None = None, simulation_to: str | None = None) -> dict[str, Any]:
    with _lock:
        current = _ensure_file()
        if simulation_enabled is not None:
            current["simulation_enabled"] = bool(simulation_enabled)
        if simulation_to is not None:
            addr = simulation_to.strip()
            if addr:
                current["simulation_to"] = addr
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return dict(current)
