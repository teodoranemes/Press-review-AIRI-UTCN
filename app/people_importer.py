"""Alias generation and validation for people.json entries."""
from __future__ import annotations

import logging
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

_TITLE_VARIANTS = [
    "Prof. Dr. Ing.", "Prof. Dr.", "Prof. Ing.", "Prof.",
    "Dr. Ing.", "Dr.", "Ing.", "Conf. Dr.", "Conf.",
    "Lect. Dr.", "Asist. Dr.",
]


def _ascii_fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def generate_aliases(name: str) -> list[str]:
    """Generate matching aliases for a person name.

    Produces: full name, initial+last, LAST+first, title variants,
    and ASCII-folded (no-diacritics) versions of all of the above.
    For multi-part first names, also includes the shortened first-word-only form.
    """
    name = name.strip()
    parts = name.split()
    if len(parts) < 2:
        logger.warning("Cannot generate aliases for single-token name: %r", name)
        return [name]

    alias_set: set[str] = set()

    def _add(alias: str) -> None:
        alias_set.add(alias)
        folded = _ascii_fold(alias)
        if folded != alias:
            alias_set.add(folded)

    last   = parts[-1]
    firsts = parts[:-1]
    first  = firsts[0]

    _add(name)
    _add(f"{first[0]}. {last}")
    _add(f"{last.upper()}, {' '.join(firsts)}")

    for title in _TITLE_VARIANTS:
        _add(f"{title} {name}")
        if len(firsts) > 1:
            _add(f"{title} {first} {last}")

    if len(firsts) > 1:
        _add(f"{first} {last}")
        _add(f"{first[0]}. {last}")

    return sorted(alias_set)


def build_person_entry(
    name: str,
    role: str,
    research_unit: str | None,
    profile_url: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "research_unit": research_unit,
        "profile_url": profile_url,
        "aliases": generate_aliases(name),
    }


def validate_people(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate person list, regenerating aliases for entries that are missing them."""
    valid: list[dict[str, Any]] = []
    for i, person in enumerate(people):
        name = person.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            logger.warning("Skipping entry %d: missing or empty 'name'", i)
            continue
        if not isinstance(person.get("aliases"), list):
            logger.warning("Entry %d (%r): aliases missing, regenerating", i, name)
            person = build_person_entry(
                name, person.get("role", "Researcher"),
                person.get("research_unit"), person.get("profile_url", ""),
            )
        valid.append(person)
    return valid
