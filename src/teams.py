"""
Canonical WM 2026 team registry.

Usage:
    from src.teams import resolve, canonical_en, get_iso2, all_teams

    code = resolve("Bosnien-Herzegowina")   # → "BIH"
    en   = canonical_en("BIH")              # → "Bosnia-Herzegovina"
    iso2 = get_iso2("BIH")                  # → "ba"
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY: list[dict] = []
_BY_CODE: dict[str, dict] = {}
_ALIAS_INDEX: dict[str, str] = {}  # normalized_alias → code


def _norm(s: str) -> str:
    """Normalize for fuzzy matching: NFD strip, lowercase, collapse separators."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    # unify separators: &, -, / → " and "
    s = re.sub(r"\s*[&/]\s*", " and ", s)
    s = re.sub(r"\s*-\s*", " and ", s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load() -> None:
    global _REGISTRY, _BY_CODE, _ALIAS_INDEX
    path = Path(__file__).parent / "teams.json"
    _REGISTRY = json.loads(path.read_text(encoding="utf-8"))
    for entry in _REGISTRY:
        code = entry["code"]
        _BY_CODE[code] = entry
        for alias in entry["aliases"]:
            key = _norm(alias)
            if key in _ALIAS_INDEX and _ALIAS_INDEX[key] != code:
                logger.warning(
                    "teams.json alias collision %r → %s vs %s",
                    alias, _ALIAS_INDEX[key], code,
                )
            _ALIAS_INDEX[key] = code


_load()


def resolve(name: str) -> str:
    """
    Return the FIFA code for any known name variant.
    Logs a WARNING for unknowns but returns the original string so callers
    don't crash — a WARNING here means teams.json needs updating.
    """
    code = _ALIAS_INDEX.get(_norm(name.strip()))
    if code is None:
        logger.warning("teams.resolve: no match for %r", name)
        return name
    return code


def canonical_en(code: str) -> str:
    """uanalyse-canonical English name for a FIFA code."""
    entry = _BY_CODE.get(code)
    if entry is None:
        logger.warning("teams.canonical_en: unknown code %r", code)
        return code
    return entry["canonical_en"]


def canonical_de(code: str) -> str:
    """German display name for a FIFA code."""
    entry = _BY_CODE.get(code)
    if entry is None:
        return code
    return entry["canonical_de"]


def get_iso2(code: str) -> str | None:
    """ISO 3166-1 alpha-2 (for flagcdn.com) for a FIFA code."""
    return _BY_CODE.get(code, {}).get("iso2")


def get_group(code: str) -> str | None:
    return _BY_CODE.get(code, {}).get("group")


def all_teams() -> list[dict]:
    return list(_REGISTRY)


def registry_as_js_map() -> dict:
    """
    Returns {canonical_en: iso2} for all 48 teams — used to generate the
    TEAM_ISO constant in app.js at build time if needed.
    """
    return {e["canonical_en"]: e["iso2"] for e in _REGISTRY}
