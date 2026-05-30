"""Keyword and person matching logic.

All matching is deterministic, case-insensitive, diacritics-insensitive,
and word-boundary aware.  No LLM, no fuzzy matching, no NER.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Pre-compiled pattern cache to avoid recompiling for every article
_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def normalize(text: str) -> str:
    """Return NFD-decomposed text with combining marks stripped, lower-cased."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return stripped.lower()


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    """Return a compiled regex that matches *term* at word boundaries (normalized)."""
    norm = normalize(term)
    escaped = re.escape(norm)
    key = escaped
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = re.compile(r"\b" + escaped + r"\b")
    return _PATTERN_CACHE[key]


def term_matches(term: str, text: str) -> bool:
    """Return True iff *term* appears at a word boundary in *text* (NFD, lower)."""
    pattern = _word_boundary_pattern(term)
    return bool(pattern.search(normalize(text)))


def match_keywords(
    keywords: list[str],
    title: str,
    body: str,
) -> list[str]:
    """Return the subset of *keywords* that match title or body."""
    combined = f"{title} {body}"
    return [kw for kw in keywords if term_matches(kw, combined)]


def match_persons(
    people: list[dict[str, Any]],
    title: str,
    body: str,
) -> list[str]:
    """Return a list of person names whose aliases match title or body."""
    combined = f"{title} {body}"
    matched: list[str] = []
    for person in people:
        aliases: list[str] = person.get("aliases", [])
        for alias in aliases:
            if term_matches(alias, combined):
                matched.append(person["name"])
                break
    return matched


def article_is_relevant(
    title: str,
    body: str,
    keywords_config: dict[str, list[str]],
    people: list[dict[str, Any]],
) -> tuple[bool, list[str], list[str]]:
    """Decide whether an article should enter the press review.

    Returns:
        (is_relevant, matched_keywords, matched_persons)

    Rules:
    - Any institute keyword matches → relevant.
    - Any research_unit keyword matches → relevant.
    - Any university keyword matches AND at least one person matches → relevant.
    - Otherwise → not relevant.
    """
    institute_kws = keywords_config.get("institute", [])
    university_kws = keywords_config.get("university", [])
    research_unit_kws = keywords_config.get("research_units", [])

    hit_institute = match_keywords(institute_kws, title, body)
    hit_university = match_keywords(university_kws, title, body)
    hit_research_units = match_keywords(research_unit_kws, title, body)
    hit_persons = match_persons(people, title, body)

    all_kw_hits = hit_institute + hit_university + hit_research_units

    if hit_institute:
        return True, all_kw_hits, hit_persons
    if hit_research_units:
        return True, all_kw_hits, hit_persons
    if hit_university and hit_persons:
        return True, all_kw_hits, hit_persons

    return False, [], []
