"""RSS feed collector and article importer."""
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

_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}
_LAST_REQUEST: dict[str, float] = {}


def _article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _get_robots(domain: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    if domain not in _ROBOTS_CACHE:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.set_url(f"https://{domain}/robots.txt")
            rp.read()
        except Exception as exc:
            logger.warning("Could not fetch robots.txt for %s: %s", domain, exc)
        _ROBOTS_CACHE[domain] = rp
    return _ROBOTS_CACHE[domain]


def _is_allowed(url: str, user_agent: str) -> bool:
    domain = urllib.parse.urlparse(url).netloc
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


def _fetch_feed(url: str, cfg: CollectionConfig) -> feedparser.FeedParserDict | None:
    domain = urllib.parse.urlparse(url).netloc
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
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(tz=timezone.utc).isoformat()


def _snippet(entry: feedparser.FeedParserDict) -> str:
    import re
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
            clean = re.sub(r"<[^>]+>", " ", raw)
            return " ".join(clean.split())[:300]
    return ""


def fetch_article_by_url(
    url: str,
    source_name: str,
    user_agent: str,
) -> dict[str, Any] | None:
    """Scrape a single article page. Returns raw article dict or None on failure.

    Also returns _match_body (up to 1000 chars of body text) used only for
    relevance matching — it is not stored in articles.json.
    """
    import re

    _rate_limit(urllib.parse.urlparse(url).netloc, 1.0)
    response = None
    # Retry without SSL verification if the first attempt fails on certificate error
    for verify in (True, False):
        try:
            with httpx.Client(
                headers={"User-Agent": user_agent},
                follow_redirects=True,
                timeout=15.0,
                verify=verify,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
            break
        except httpx.ConnectError as exc:
            if "CERTIFICATE" in str(exc).upper() and verify:
                continue
            logger.error("Failed to fetch %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            return None
    if response is None:
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "lxml")

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

    import re as _re
    _RO_MONTHS = {
        "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4,
        "mai": 5, "iunie": 6, "iulie": 7, "august": 8,
        "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12,
    }

    def _parse_ro_date(text: str) -> "datetime | None":
        m = _re.search(
            r"(\d{1,2})\s+(ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie)\s+(\d{4})",
            text, _re.I,
        )
        if m:
            try:
                return datetime(int(m.group(3)), _RO_MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass
        m = _re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
        if m:
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass
        return None

    pub_date = datetime.now(tz=timezone.utc).isoformat()
    _date_found = False

    for meta_attr, meta_name in [
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "DC.date"),
        ("name", "pubdate"),
    ]:
        m = soup.find("meta", attrs={meta_attr: meta_name})
        if m and m.get("content"):
            try:
                pub_date = datetime.fromisoformat(m["content"].replace("Z", "+00:00")).isoformat()
                _date_found = True
                break
            except ValueError:
                pass

    if not _date_found:
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            try:
                pub_date = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00")).isoformat()
                _date_found = True
            except (ValueError, KeyError):
                pass

    # Fallback: scan short visible elements for Romanian text dates (e.g. "22 Decembrie 2025")
    if not _date_found:
        for el in soup.find_all(["span", "div", "p", "li", "time"]):
            text_val = el.get_text(" ", strip=True)
            if len(text_val) < 60:
                dt = _parse_ro_date(text_val)
                if dt:
                    pub_date = dt.isoformat()
                    break

    for tag in soup(["nav", "header", "footer", "aside", "script", "style", "form"]):
        tag.decompose()
    paras = [
        p.get_text(" ", strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 50
    ]
    snippet = ""
    match_body = ""
    if paras:
        raw = " ".join(paras[:5])
        clean = re.sub(r"\s+", " ", raw)
        snippet = clean[:300]
        match_body = re.sub(r"\s+", " ", " ".join(paras[:10]))[:1000]
    if not snippet:
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            snippet = og_desc["content"].strip()[:300]
            match_body = match_body or snippet

    return {
        "id": _article_id(url),
        "title": title,
        "url": url,
        "source": source_name,
        "published_date": pub_date,
        "snippet": snippet,
        "_match_body": match_body,
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
    """Import a single article by URL. Returns result dict with status."""
    article_id = _article_id(url)
    existing = load_articles(articles_path)
    if any(a["id"] == article_id or a.get("url") == url for a in existing):
        return {"status": "duplicate", "url": url}

    raw = fetch_article_by_url(url, source_name, config.collection.user_agent)
    if raw is None:
        return {"status": "fetch_error", "url": url}

    kw_dict = config.keywords.model_dump()
    # Use extended body for matching; only snippet is stored
    match_body = raw.pop("_match_body", raw["snippet"])
    is_rel, matched_kws, matched_persons = article_is_relevant(
        raw["title"], match_body, kw_dict, people
    )

    article = {**raw, "matched_keywords": matched_kws, "matched_persons": matched_persons}

    # Always save when explicitly imported (bypass relevance filter)
    all_articles = existing + [article]
    save_articles(articles_path, all_articles)
    from app.storage import save_graph
    graph = graph_builder.build_graph(all_articles, people)
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


def search_and_import(
    config: AppConfig,
    people: list[dict[str, Any]],
    articles_path: Any,
    graph_path: Any,
    graph_builder: Any,
) -> dict[str, Any]:
    """Search Google (SerpAPI or CSE) for relevant articles and import new ones."""
    cse = config.google_cse
    use_serpapi = bool(cse.serpapi_key)
    use_cse     = bool(cse.api_key and cse.cx)

    if not use_serpapi and not use_cse:
        return {"error": "No search method configured (serpapi_key or api_key+cx)"}

    allowed_domains: set[str] = set()
    for src in config.sources:
        import urllib.parse as _up2
        d = _up2.urlparse(src.homepage).netloc.lstrip("www.")
        if d:
            allowed_domains.add(d)

    BLOCKED_DOMAINS = {
        "instagram.com", "facebook.com", "twitter.com", "x.com",
        "linkedin.com", "ro.linkedin.com", "youtube.com",
        "soundcloud.com", "tiktok.com", "pinterest.com",
        "airicorsets.com", "airi.net", "airi.utcluj.ro",
        "amazon.com", "emag.ro", "wikipedia.org",
    }

    inst_kw  = " OR ".join(f'"{k}"' for k in config.keywords.institute[:4])
    roai_kw  = '"RO AI Factory" OR "HRIA" OR "Hubul Roman de Inteligenta Artificiala"'
    combined = f"({inst_kw}) OR ({roai_kw})"

    # Site-specific queries for each configured press source, plus a broad .ro sweep
    site_queries  = [f"({combined}) site:{d}" for d in list(allowed_domains)]
    broad_queries = [f"({combined}) site:.ro"]
    base_queries  = site_queries + broad_queries
    kw_dict = config.keywords.model_dump()

    stats = {"searched": 0, "imported": 0, "duplicate": 0, "fetch_error": 0}
    seen_urls: set[str] = {a.get("url", "") for a in load_articles(articles_path)}

    for query in base_queries:
        try:
            results = _serpapi_search(query, cse.serpapi_key) if use_serpapi else _google_cse_search(query, cse.api_key, cse.cx)
            stats["searched"] += len(results)
        except Exception as exc:
            logger.error("Search failed for %r: %s", query, exc)
            continue

        for item in results:
            url = item["url"]
            item_domain = urllib.parse.urlparse(url).netloc.lstrip("www.")

            if item_domain in BLOCKED_DOMAINS:
                continue
            in_allowlist = any(item_domain == d or item_domain.endswith("." + d) for d in allowed_domains)
            if not in_allowlist and not item_domain.endswith(".ro"):
                continue

            # Pre-filter on title before fetching the full article body
            pre_title = item.get("title", "")
            all_kws = kw_dict.get("institute", []) + kw_dict.get("university", []) + kw_dict.get("research_units", [])
            from app.matcher import term_matches
            if not any(term_matches(kw, pre_title) for kw in all_kws):
                continue

            if url in seen_urls:
                stats["duplicate"] += 1
                continue

            result = import_url(
                url=url,
                source_name=item.get("source", ""),
                config=config,
                people=people,
                articles_path=articles_path,
                graph_path=graph_path,
                graph_builder=graph_builder,
            )
            seen_urls.add(url)
            if result["status"] == "imported":
                stats["imported"] += 1
            elif result["status"] == "duplicate":
                stats["duplicate"] += 1
            else:
                stats["fetch_error"] += 1

    logger.info("Search complete: %s", stats)
    return stats


def _google_cse_search(query: str, api_key: str, cx: str, num: int = 10) -> list[dict[str, Any]]:
    import urllib.parse as _up
    params = {"key": api_key, "cx": cx, "q": query, "num": num}
    with httpx.Client(timeout=15.0) as client:
        r = client.get("https://www.googleapis.com/customsearch/v1", params=params)
        r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return [
        {"title": i.get("title", ""), "url": i.get("link", ""), "source": _up.urlparse(i.get("link", "")).netloc.lstrip("www.")}
        for i in data.get("items", [])
    ]


def _serpapi_search(query: str, api_key: str, num: int = 10) -> list[dict[str, Any]]:
    import urllib.parse as _up
    params = {"api_key": api_key, "engine": "google", "q": query, "num": num, "gl": "ro", "hl": "ro"}
    with httpx.Client(timeout=15.0) as client:
        r = client.get("https://serpapi.com/search", params=params)
        r.raise_for_status()
    data = r.json()
    return [
        {"title": i.get("title", ""), "url": i.get("link", ""), "source": _up.urlparse(i.get("link", "")).netloc.lstrip("www.")}
        for i in data.get("organic_results", [])
    ]


def collect_once(
    config: AppConfig,
    people: list[dict[str, Any]],
    articles_path: Any,
    graph_path: Any,
    graph_builder: Any,
) -> int:
    """Run one RSS collection pass. Returns number of new articles saved."""
    existing = load_articles(articles_path)
    seen_ids: set[str] = {a["id"] for a in existing}
    new_articles: list[dict[str, Any]] = []

    max_age_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.collection.max_article_age_days)
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

            try:
                pub_dt = datetime.fromisoformat(pub_date_str)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < max_age_cutoff:
                    continue
            except ValueError:
                pass

            is_rel, matched_kws, matched_persons = article_is_relevant(title, snippet, kw_dict, people)
            if not is_rel:
                continue

            new_articles.append({
                "id": article_id,
                "title": title,
                "url": url,
                "source": source.name,
                "published_date": pub_date_str,
                "snippet": snippet,
                "matched_keywords": matched_kws,
                "matched_persons": matched_persons,
            })
            seen_ids.add(article_id)
            logger.info("Matched article: %r (%s)", title[:80], source.name)

    if new_articles:
        all_articles = existing + new_articles
        save_articles(articles_path, all_articles)
        from app.storage import save_graph
        graph = graph_builder.build_graph(all_articles, people)
        save_graph(graph_path, graph)
        logger.info("Added %d new articles; graph rebuilt", len(new_articles))
    else:
        logger.info("No new articles found in this collection pass")

    return len(new_articles)
