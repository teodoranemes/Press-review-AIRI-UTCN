#!/usr/bin/env python3
"""Scrape https://airi.utcluj.ro/people and produce data/people.json.

Usage:
    python scripts/import_people.py [--output data/people.json]

If the page is unreachable, the script exits with a non-zero code and a
clear message.  In that case, use data/people.sample.json as a fallback.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.people_importer import build_person_entry, validate_people

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("import_people")

PEOPLE_URL = "https://airi.utcluj.ro/people"
USER_AGENT = "AIRI-PressReview/1.0 (+https://airi.utcluj.ro)"
REQUEST_DELAY = 1.0  # seconds between requests


def _get(url: str, client: httpx.Client) -> httpx.Response | None:
    try:
        time.sleep(REQUEST_DELAY)
        resp = client.get(url)
        resp.raise_for_status()
        return resp
    except httpx.HTTPError as exc:
        logger.error("HTTP error fetching %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error fetching %s: %s", url, exc)
        return None


def _extract_role_from_section(section_header: str) -> str:
    """Map section heading text to a canonical role string."""
    header = section_header.lower()
    if "researcher" in header or "cercetator" in header:
        return "Researcher"
    if "staff" in header or "personal" in header:
        return "Staff"
    if "student" in header:
        return "Student"
    if "external" in header or "extern" in header:
        return "External"
    return "Researcher"


def _extract_research_unit(card: Tag) -> str | None:
    """Try to extract research unit from a person card."""
    # Common patterns: a subtitle element, a span with class containing 'unit'/'department'
    for selector in [
        "p.research-unit",
        "span.unit",
        "span.department",
        "p.subtitle",
        ".card-subtitle",
        ".person-unit",
        "p:nth-of-type(2)",
    ]:
        el = card.select_one(selector)
        if el:
            text = el.get_text(strip=True)
            if text and len(text) < 120:
                return text
    return None


def _scrape_people(html: str, base_url: str) -> list[dict]:
    """Parse the people listing page and return raw person dicts."""
    soup = BeautifulSoup(html, "html.parser")
    people: list[dict] = []

    # Strategy: find all person cards.  The AIRI site uses different possible
    # layouts.  We try multiple heuristics and log warnings rather than crashing.

    # Heuristic 1: look for cards grouped under role headings
    # (h2/h3 with role name, followed by a grid/list of person cards)
    current_role = "Researcher"

    # Try to find person links that match the URL pattern /people/staff/<Name>
    person_links = soup.find_all("a", href=lambda h: h and "/people/staff/" in h)
    if not person_links:
        # Broader search: any link containing /people/
        person_links = soup.find_all("a", href=lambda h: h and "/people/" in h and h.count("/") >= 3)

    if not person_links:
        logger.warning("No person links found on the page; the HTML structure may have changed.")
        # Fall back: try to find any elements that look like name cards
        for card in soup.select(".person-card, .team-member, .staff-card, article"):
            name_el = card.select_one("h2, h3, h4, .name, .person-name")
            if name_el:
                name_text = name_el.get_text(strip=True)
                if name_text and len(name_text.split()) >= 2:
                    people.append({
                        "name": name_text,
                        "role": current_role,
                        "research_unit": _extract_research_unit(card),
                        "profile_url": "",
                    })
        return people

    seen_urls: set[str] = set()
    for link in person_links:
        href = link.get("href", "")
        if not href:
            continue

        # Build absolute URL
        if href.startswith("http"):
            profile_url = href
        else:
            profile_url = base_url.rstrip("/") + "/" + href.lstrip("/")

        if profile_url in seen_urls:
            continue
        seen_urls.add(profile_url)

        # Extract name from link text or nearby heading
        name_text = link.get_text(strip=True)
        if not name_text:
            # Look for a heading near the link
            parent = link.parent
            for _ in range(3):
                if parent is None:
                    break
                heading = parent.find(["h2", "h3", "h4"])
                if heading:
                    name_text = heading.get_text(strip=True)
                    break
                parent = parent.parent

        if not name_text or len(name_text.split()) < 2:
            logger.warning("Skipping link with no extractable name: %s", profile_url)
            continue

        # Try to determine role from a nearby section heading
        role = current_role
        # Walk up the DOM to find a section heading
        parent = link.parent
        for _ in range(6):
            if parent is None:
                break
            heading = parent.find_previous_sibling(["h2", "h3"])
            if heading:
                role = _extract_role_from_section(heading.get_text(strip=True))
                break
            prev = parent.find_previous(["h2", "h3"])
            if prev:
                role = _extract_role_from_section(prev.get_text(strip=True))
                break
            parent = parent.parent if isinstance(parent, Tag) else None

        # Try to extract research unit from the card enclosing the link
        research_unit: str | None = None
        card_el = link.parent
        for _ in range(4):
            if card_el is None:
                break
            unit = _extract_research_unit(card_el)
            if unit:
                research_unit = unit
                break
            card_el = card_el.parent if isinstance(card_el, Tag) else None

        people.append({
            "name": name_text,
            "role": role,
            "research_unit": research_unit,
            "profile_url": profile_url,
        })

    return people


def main(output_path: Path) -> int:
    """Scrape and write people.json.  Returns exit code (0 = success)."""
    logger.info("Fetching people page: %s", PEOPLE_URL)

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        resp = _get(PEOPLE_URL, client)

    if resp is None:
        logger.error(
            "Could not fetch %s. "
            "Use data/people.sample.json as fallback by copying it to data/people.json.",
            PEOPLE_URL,
        )
        return 1

    raw_people = _scrape_people(resp.text, "https://airi.utcluj.ro")

    if not raw_people:
        logger.error(
            "No people extracted from %s. "
            "The HTML structure may have changed; inspect the page manually.",
            PEOPLE_URL,
        )
        return 1

    logger.info("Extracted %d raw entries; building aliases...", len(raw_people))

    enriched: list[dict] = []
    for p in raw_people:
        try:
            entry = build_person_entry(
                name=p["name"],
                role=p.get("role", "Researcher"),
                research_unit=p.get("research_unit"),
                profile_url=p.get("profile_url", ""),
            )
            enriched.append(entry)
        except Exception as exc:
            logger.warning("Skipping %r due to error: %s", p.get("name"), exc)

    validated = validate_people(enriched)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(validated, fh, ensure_ascii=False, indent=2)

    logger.info(
        "Wrote %d people to %s (aliases per person: min=%d, avg=%.1f, max=%d)",
        len(validated),
        output_path,
        min((len(p["aliases"]) for p in validated), default=0),
        sum(len(p["aliases"]) for p in validated) / max(len(validated), 1),
        max((len(p["aliases"]) for p in validated), default=0),
    )

    if len(validated) < 100:
        logger.warning(
            "Only %d people found (expected ≥100). "
            "Scraper may need updating if the site structure changed.",
            len(validated),
        )

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import AIRI people from website.")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent.parent / "data" / "people.json"),
        help="Output path for people.json",
    )
    args = parser.parse_args()
    sys.exit(main(Path(args.output)))
