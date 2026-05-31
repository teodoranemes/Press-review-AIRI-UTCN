"""Named-entity extraction for researcher names (optional, not used in default flow).

Uses spaCy ro_core_news_sm to detect PERSON entities in article text.
Extracted names are stored in data/extracted_persons.json for manual review.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("ro_core_news_sm")
        except Exception as exc:
            logger.warning("spaCy NER unavailable: %s", exc)
            _nlp = False
    return _nlp if _nlp else None


_TITLE_PREFIX = re.compile(
    r"^(prof\.?\s*(univ\.?\s*)?|dr\.?\s*|conf\.?\s*(dr\.?\s*)?|lect\.?\s*(dr\.?\s*)?"
    r"|asist\.?\s*(dr\.?\s*)?|ing\.?\s*|acad\.?\s*|drd\.?\s*|phd\.?\s*"
    r"|mr\.?\s*|mrs\.?\s*|ms\.?\s*|cercet\.?\s*(st\.?\s*)?)+",
    re.IGNORECASE,
)

_ROLE_PREFIX = re.compile(
    r"^(rectorul|prorectorul|decanul|directorul|conducatorul|coordonatorul"
    r"|seful|managerul|presedintele|cercetatorii|reprezentantii|coordonatorii)\s+",
    re.IGNORECASE,
)

_JUNK_WORDS = {
    "joint", "director", "manager", "coordinator", "the", "and", "of",
    "in", "la", "din", "cu", "pe", "al", "lui", "ei", "sa",
    "universitatea", "institutul", "factory", "center", "institute",
    "research", "lab", "business", "media", "news", "press", "online", "digital",
}


def _clean_span(raw: str) -> list[str]:
    parts = re.split(r"\s+[ss]i\s+|,\s*[ss]i\s+", raw, flags=re.IGNORECASE)
    names: list[str] = []
    for part in parts:
        part = re.sub(r",.*$", "", part.strip(" ,.")).strip()
        part = _ROLE_PREFIX.sub("", part).strip()
        part = _TITLE_PREFIX.sub("", part).strip(" ,.-")
        words = part.split()
        if len(words) < 2 or len(words) > 5:
            continue
        if not all(w[0].isupper() for w in words if len(w) > 1):
            continue
        if any(w.lower().rstrip(".") in _JUNK_WORDS for w in words):
            continue
        if any(w.upper() == w and len(w) >= 2 for w in words):
            continue
        if len(part) < 5 or len(part) > 55:
            continue
        names.append(part)
    return names


def extract_persons(text: str) -> list[str]:
    """Return deduplicated person names found in text. Returns [] if spaCy unavailable."""
    nlp = _get_nlp()
    if nlp is None:
        return []
    doc = nlp(text[:2000])
    seen: set[str] = set()
    results: list[str] = []
    for ent in doc.ents:
        if ent.label_ != "PERSON":
            continue
        for name in _clean_span(ent.text):
            if name not in seen:
                seen.add(name)
                results.append(name)
    return results


def update_extracted_persons(
    article_id: str,
    article_title: str,
    names: list[str],
    extracted_path: Any,
) -> list[dict[str, Any]]:
    """Merge newly found names into extracted_persons.json."""
    import json
    from pathlib import Path

    path = Path(extracted_path)
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    by_name: dict[str, dict[str, Any]] = {p["name"]: p for p in existing}
    for name in names:
        if name not in by_name:
            by_name[name] = {"name": name, "source": "auto", "articles": [article_id], "mention_count": 1}
            logger.info("Extracted new person: %r (from %r)", name, article_title[:60])
        else:
            entry = by_name[name]
            if article_id not in entry.get("articles", []):
                entry.setdefault("articles", []).append(article_id)
                entry["mention_count"] = len(entry["articles"])

    updated = list(by_name.values())
    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated
