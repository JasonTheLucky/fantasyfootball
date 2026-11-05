"""Shared helpers for the test suite.

Not named test*.py, so unittest discovery does not collect it as a test module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

ARTIFACT = "recent_form_rankings.json"
MISSING_ARTIFACT = (
    f"data/{ARTIFACT} is missing or unreadable; run python scripts/fetch_espn.py"
)


def import_fetch_espn():
    """Import scripts/fetch_espn.py without making scripts/ a package.

    scripts/ must not become a package: the workflow runs it as
    `python scripts/fetch_espn.py`. The module is import-safe -- stdlib only,
    module level defines constants and functions, main() is guarded.
    """
    path = str(REPO_ROOT / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    import fetch_espn

    return fetch_espn


def load_json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(relative: str) -> str:
    """UTF-8 explicitly: these documents carry em dashes and a middle dot."""
    with open(REPO_ROOT / relative, encoding="utf-8") as handle:
        return handle.read()


def load_artifact_or_fail(case) -> dict:
    """Load the rankings artifact, failing (never skipping) when it is absent.

    Skipping would let a broken pipeline report a green suite, which is the
    specific failure mode these tests exist to catch.
    """
    try:
        return load_json(ARTIFACT)
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{MISSING_ARTIFACT} ({exc})") from exc


def all_entries(artifact: dict) -> list[dict]:
    return [e for rows in (artifact.get("rankings") or {}).values() for e in rows]


def is_degraded(artifact: dict) -> bool:
    """True when a scoring period failed to fetch.

    The scheduled Action commits whatever it produced, including a degraded
    artifact, so completeness assertions skip rather than fail in that state.
    """
    return bool((artifact.get("weeks") or {}).get("failed"))
