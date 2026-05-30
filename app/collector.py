"""RSS feed collector.

Fetches configured feeds, filters articles via matcher, and persists results.
Respects robots.txt and rate-limits to ≤ 1 req/sec per domain.
Stores only: title, url, source, published_date, snippet (≤300 chars), matched_keywords.
"""
from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx

from app.config import AppConfig, CollectionConfig
from app.matcher import article_is_relevant
from app.storage import load_articles, save_articles

logger = logging.getLogger(__name__)

# Cache for robots.txt parsers keyed by domain
_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}
# Track last request time per domain for rate-limiting
_LAST_REQUEST: dict[str, float] = {}


def _article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _get_robots(domain: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    if domain not in _ROBOTS_CACHE:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"https://{domain}/robots.txt"
        try:
            rp.set_url(robots_url)
            rp.read()
            logger.debug("Loaded robots.txt for %s", domain)
        except Exception as exc:
            logger.warning("Could not fetch robots.txt for %s: %s", domain, exc)
        _ROBOTS_CACHE[domain] = rp
    return _ROBOTS_CACHE[domain]


def _is_allowed(url: str, user_agent: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc
    rp = _get_robots(domain, user_agent)
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def _rate_limit(domain: str, delay: float) -> None:
    last = _LAST_REQUEST.get(domain, 0.0)
    wait = delay - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST[domain] = time.monotonic()


def _fetch_feed(
    url: str,
    cfg: CollectionConfig,
) -> feedparser.FeedParserDict | None:
    parsed_url = urllib.parse.urlparse(url)
    domain = parsed_url.netloc

    if not _is_allowed(url, cfg.user_agent):
        logger.warning("Robots.txt disallows fetching %s", url)
        return None

    _rate_limit(domain, cfg.request_delay_seconds)

    try:
        with httpx.Client(
            headers={"User-Agent": cfg.user_agent},
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
        feed = feedparser.parse(response.text)
        logger.info("Fetched feed %s: %d entries", url, len(feed.entries))
        return feed  # type: ignore[return-value]
    except Exception as exc:
        logger.error("Failed to fetch feed %s: %s", url, exc)
        return None


def _parse_date(entry: feedparser.FeedParserDict) -> str:
    """Best-effort ISO date string from a feedparser entry."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
    return datetime.now(tz=timezone.utc).isoformat()


def _snippet(entry: feedparser.FeedParserDict) -> str:
    """Extract up to 300 chars of body text from a feedparser entry."""
    for attr in ("summary", "description", "content"):
        raw: str = ""
        val = getattr(entry, attr, None)
        if val is None:
            continue
        if isinstance(val, list) and val:
            raw = val[0].get("value", "")
        elif isinstance(val, str):
            raw = val
        if raw:
            # Strip HTML tags simply
            import re

            clean = re.sub(r"<[^>]+>", " ", raw)
            clean = " ".join(clean.split())
            return clean[:300]
    return ""


def fetch_article_by_url(
    url: str,
    source_name: str,
    user_agent: str,
) -> dict[str, Any] | None:
    """Fetch a single article URL, return raw {title, snippet, published_date} or None."""
    import re

    _rate_limit(urllib.parse.urlparse(url).netloc, 1.0)
    try:
        with httpx.Client(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "lxml")

    # Title: prefer og:title, then <h1>, then <title>
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        t = soup.find("title")
        title = t.get_text(strip=True) if t else ""

    # Published date: <time datetime=...>, og:article:published_time, meta name=date
    pub_date = datetime.now(tz=timezone.utc).isoformat()
    for meta_attr, meta_name in [
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "DC.date"),
        ("name", "pubdate"),
    ]:
        m = soup.find("meta", attrs={meta_attr: meta_name})
        if m and m.get("content"):
            try:
                dt = datetime.fromisoformat(m["content"].replace("Z", "+00:00"))
                pub_date = dt.isoformat()
                break
            except ValueError:
                pass
    else:
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            try:
                dt = datetime.fromisoformat(
                    time_el["datetime"].replace("Z", "+00:00")
                )
                pub_date = dt.isoformat()
            except (ValueError, KeyError):
                pass

    # Snippet: prefer article body over og:description (og:description is often
    # a newsletter CTA or truncated teaser that may not contain keywords).
    # Strategy: extract first 300 chars of meaningful paragraph text; fall back
    # to og:description only if no body text found.
    for tag in soup(["nav", "header", "footer", "aside", "script", "style", "form"]):
        tag.decompose()
    paras = [
        p.get_text(" ", strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 50
    ]
    snippet = ""
    if paras:
        raw = " ".join(paras[:5])
        snippet = re.sub(r"\s+", " ", raw)[:300]
    if not snippet:
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            snippet = og_desc["content"].strip()[:300]

    return {
        "id": _article_id(url),
        "title": title,
        "url": url,
        "source": source_name,
        "published_date": pub_date,
        "snippet": snippet,
    }


def import_url(
    url: str,
    source_name: str,
    config: AppConfig,
    people: list[dict[str, Any]],
    articles_path: Any,
    graph_path: Any,
    graph_builder: Any,
) -> dict[str, Any]:
    """Import a single article by URL.  Returns result dict with status."""
    article_id = _article_id(url)
    existing = load_articles(articles_path)
    # Deduplicate by both generated hash AND stored URL
    if any(a["id"] == article_id or a.get("url") == url for a in existing):
        return {"status": "duplicate", "url": url}

    raw = fetch_article_by_url(url, source_name, config.collection.user_agent)
    if raw is None:
        return {"status": "fetch_error", "url": url}

    kw_dict = config.keywords.model_dump()
    is_rel, matched_kws, matched_persons = article_is_relevant(
        raw["title"], raw["snippet"], kw_dict, people
    )

    article = {
        **raw,
        "matched_keywords": matched_kws,
        "matched_persons": matched_persons,
    }

    # Always save when explicitly imported (bypass relevance filter)
    all_articles = existing + [article]
    save_articles(articles_path, all_articles)
    graph = graph_builder.build_graph(all_articles, people)
    from app.storage import save_graph
    save_graph(graph_path, graph)

    logger.info(
        "Imported article %r from %s (relevant=%s, keywords=%s)",
        raw["title"][:60], source_name, is_rel, matched_kws,
    )
    return {
        "status": "imported",
        "relevant": is_rel,
        "title": raw["title"],
        "published_date": raw["published_date"],
        "matched_keywords": matched_kws,
        "matched_persons": matched_persons,
    }


def collect_once(
    config: AppConfig,
    people: list[dict[str, Any]],
    articles_path: Any,
    graph_path: Any,
    graph_builder: Any,
) -> int:
    """Run a single collection pass.  Returns number of new articles added."""
    existing = load_articles(articles_path)
    seen_ids: set[str] = {a["id"] for a in existing}
    new_articles: list[dict[str, Any]] = []

    max_age_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        days=config.collection.max_article_age_days
    )
    kw_dict = config.keywords.model_dump()

    for source in config.sources:
        logger.info("Collecting from %s (%s)", source.name, source.rss_url)
        feed = _fetch_feed(source.rss_url, config.collection)
        if feed is None:
            continue

        for entry in feed.entries:
            url: str = getattr(entry, "link", "") or ""
            if not url:
                continue

            article_id = _article_id(url)
            if article_id in seen_ids:
                continue

            title: str = getattr(entry, "title", "") or ""
            snippet = _snippet(entry)
            pub_date_str = _parse_date(entry)

            # Age filter
            try:
                pub_dt = datetime.fromisoformat(pub_date_str)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < max_age_cutoff:
                    continue
            except ValueError:
                pass

            is_rel, matched_kws, matched_persons = article_is_relevant(
                title, snippet, kw_dict, people
            )
            if not is_rel:
                continue

            article: dict[str, Any] = {
                "id": article_id,
                "title": title,
                "url": url,
                "source": source.name,
                "published_date": pub_date_str,
                "snippet": snippet,
                "matched_keywords": matched_kws,
                "matched_persons": matched_persons,
            }
            new_articles.append(article)
            seen_ids.add(article_id)
            logger.info("Matched article: %r (%s)", title[:80], source.name)

    if new_articles:
        all_articles = existing + new_articles
        save_articles(articles_path, all_articles)
        # Rebuild graph
        graph = graph_builder.build_graph(all_articles, people)
        from app.storage import save_graph
        save_graph(graph_path, graph)
        logger.info("Added %d new articles; graph rebuilt", len(new_articles))
    else:
        logger.info("No new articles found in this collection pass")

    return len(new_articles)
