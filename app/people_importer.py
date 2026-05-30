"""People importer: alias generation logic used by scripts/import_people.py."""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

_TITLE_VARIANTS = [
    "Prof. Dr. Ing.",
    "Prof. Dr.",
    "Prof. Ing.",
    "Prof.",
    "Dr. Ing.",
    "Dr.",
    "Ing.",
    "Conf. Dr.",
    "Conf.",
    "Lect. Dr.",
    "Asist. Dr.",
]


def _ascii_fold(text: str) -> str:
    """Return ASCII-folded version of text (strip diacritics)."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def generate_aliases(name: str) -> list[str]:
    """Generate the full set of matching aliases for a person's full name.

    Alias rules:
    - ``FirstName LastName``
    - ``F. LastName`` (first-name initial)
    - ``LASTNAME, FirstName`` (uppercase last name)
    - Title variants: ``Prof. Dr. Ing. FirstName LastName`` etc.
    - For multi-word names (e.g. "Doina Liana Pisla"):
      include both full form and dropped-middle version.
    - Each form is also included with ASCII-folded diacritics.
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

    # Determine last name (last token) and first names (everything before)
    last = parts[-1]
    firsts = parts[:-1]
    first = firsts[0]

    # Base: full name
    _add(name)

    # F. LastName
    _add(f"{first[0]}. {last}")

    # LASTNAME, FirstName [Middle...]
    _add(f"{last.upper()}, {' '.join(firsts)}")

    # Title variants
    for title in _TITLE_VARIANTS:
        _add(f"{title} {name}")
        if len(firsts) > 1:
            # Dropped-middle variant with title
            _add(f"{title} {first} {last}")

    # Multi-word first name: dropped-middle (FirstName LastName only)
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
    """Build a person dict with generated aliases."""
    aliases = generate_aliases(name)
    return {
        "name": name,
        "role": role,
        "research_unit": research_unit,
        "profile_url": profile_url,
        "aliases": aliases,
    }


def validate_people(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate a list of person dicts, skipping malformed entries with warnings."""
    valid: list[dict[str, Any]] = []
    for i, person in enumerate(people):
        name = person.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            logger.warning("Skipping entry %d: missing or empty 'name'", i)
            continue
        if not isinstance(person.get("aliases"), list):
            logger.warning(
                "Entry %d (%r): 'aliases' missing or not a list — regenerating",
                i,
                name,
            )
            role = person.get("role", "Researcher")
            research_unit = person.get("research_unit")
            profile_url = person.get("profile_url", "")
            person = build_person_entry(name, role, research_unit, profile_url)
        valid.append(person)
    return valid
