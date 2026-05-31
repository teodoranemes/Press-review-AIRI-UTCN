"""Keyword and person matching logic.

All matching is case-insensitive and diacritics-insensitive, using word boundaries.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn").lower()


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    norm = normalize(term)
    escaped = re.escape(norm)
    if escaped not in _PATTERN_CACHE:
        _PATTERN_CACHE[escaped] = re.compile(r"\b" + escaped + r"\b")
    return _PATTERN_CACHE[escaped]


def term_matches(term: str, text: str) -> bool:
    return bool(_word_boundary_pattern(term).search(normalize(text)))


def match_keywords(keywords: list[str], title: str, body: str) -> list[str]:
    combined = f"{title} {body}"
    return [kw for kw in keywords if term_matches(kw, combined)]


def match_persons(people: list[dict[str, Any]], title: str, body: str) -> list[str]:
    combined = f"{title} {body}"
    matched: list[str] = []
    for person in people:
        for alias in person.get("aliases", []):
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
    """Decide whether an article is relevant to the institute.

    Rules:
    - Any institute keyword matches -> relevant.
    - Any research_unit keyword matches -> relevant.
    - Any university keyword matches AND at least one tracked person matches -> relevant.
    """
    institute_kws    = keywords_config.get("institute", [])
    university_kws   = keywords_config.get("university", [])
    research_unit_kws = keywords_config.get("research_units", [])

    hit_institute     = match_keywords(institute_kws, title, body)
    hit_university    = match_keywords(university_kws, title, body)
    hit_research_units = match_keywords(research_unit_kws, title, body)
    hit_persons       = match_persons(people, title, body)

    all_kw_hits = hit_institute + hit_university + hit_research_units

    if hit_institute:
        return True, all_kw_hits, hit_persons
    if hit_research_units:
        return True, all_kw_hits, hit_persons
    if hit_university and hit_persons:
        return True, all_kw_hits, hit_persons

    return False, [], []
