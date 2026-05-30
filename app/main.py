"""FastAPI application entry point."""
from __future__ import annotations

import json
import logging
import logging.config
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import collector, graph_builder, scheduler
from app.config import load_config, resolve_people_path
from app.people_importer import build_person_entry, validate_people
from app.storage import load_articles, load_graph, load_json, save_json

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
ARTICLES_PATH = DATA_DIR / "articles.json"
GRAPH_PATH = DATA_DIR / "graph.json"
PEOPLE_PATH = DATA_DIR / "people.json"
CONFIG_PATH = DATA_DIR / "config.json"

# ---------------------------------------------------------------------------
# Application state (populated at startup, mutated by write endpoints)
# ---------------------------------------------------------------------------
_app_config: Any = None
_people: list[dict[str, Any]] = []
# Lock for concurrent writes to shared state / JSON files
_write_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _app_config, _people

    _app_config = load_config()
    people_path = resolve_people_path()
    _people = json.loads(people_path.read_text(encoding="utf-8"))
    logger.info("Loaded %d people from %s", len(_people), people_path)

    # Run one immediate collection pass in a background thread so the server
    # becomes ready immediately without waiting for all RSS feeds to respond.
    def _startup_collect() -> None:
        try:
            n = collector.collect_once(
                _app_config, _people, ARTICLES_PATH, GRAPH_PATH, graph_builder
            )
            logger.info("Startup collection: %d new articles", n)
        except Exception as exc:
            logger.exception("Startup collection failed: %s", exc)

    threading.Thread(target=_startup_collect, daemon=True).start()

    scheduler.start(
        _app_config,
        _people,
        ARTICLES_PATH,
        GRAPH_PATH,
        graph_builder,
        collector,
    )

    yield

    scheduler.stop()


app = FastAPI(
    title="AIRI@UTCN Press Review",
    description=(
        "Automated press review microservice for the Artificial Intelligence "
        "Research Institute at UTCN. Accepts persons/keywords/sources via API; "
        "no external site integration required."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PersonIn(BaseModel):
    name: str
    role: str = "Researcher"
    research_unit: str | None = None
    profile_url: str = ""
    # If aliases not provided, they are generated automatically.
    aliases: list[str] | None = None


class KeywordsIn(BaseModel):
    institute: list[str] = Field(default_factory=list)
    university: list[str] = Field(default_factory=list)
    research_units: list[str] = Field(default_factory=list)


class SourceIn(BaseModel):
    name: str
    rss_url: str
    homepage: str = ""


class ImportUrlIn(BaseModel):
    url: str
    source_name: str = ""


# ---------------------------------------------------------------------------
# READ endpoints
# ---------------------------------------------------------------------------


@app.get("/api/articles")
def get_articles(
    date_filter: str | None = Query(None, alias="date", description="YYYY-MM-DD"),
    source: str | None = Query(None),
    person: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    """Return paginated articles, optionally filtered by date / source / person."""
    articles: list[dict[str, Any]] = load_articles(ARTICLES_PATH)

    if date_filter:
        articles = [a for a in articles if a.get("published_date", "").startswith(date_filter)]
    if source:
        articles = [a for a in articles if source.lower() in a.get("source", "").lower()]
    if person:
        articles = [
            a for a in articles
            if person.lower() in " ".join(a.get("matched_persons", [])).lower()
        ]

    articles.sort(key=lambda a: a.get("published_date", ""), reverse=True)
    total = len(articles)
    start = (page - 1) * page_size
    return JSONResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "articles": articles[start: start + page_size],
    })


@app.get("/api/persons")
def get_persons() -> JSONResponse:
    """Return the current list of tracked persons with their aliases."""
    return JSONResponse(_people)


@app.get("/api/keywords")
def get_keywords() -> JSONResponse:
    """Return the current keyword configuration."""
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse(_app_config.keywords.model_dump())


@app.get("/api/sources")
def get_sources() -> JSONResponse:
    """Return the current list of RSS sources."""
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse([s.model_dump() for s in _app_config.sources])


@app.get("/api/graph")
def get_graph() -> JSONResponse:
    """Return graph JSON ({nodes, edges}) for Cytoscape.js or any graph renderer."""
    return JSONResponse(load_graph(GRAPH_PATH))


def _date_range(date_str: str, period: str) -> tuple[str, str]:
    """Return (start_date, end_date) ISO strings for the given period anchor."""
    import calendar
    from datetime import timedelta as _td
    anchor = date.fromisoformat(date_str)
    if period == "week":
        monday = anchor - _td(days=anchor.weekday())
        sunday = monday + _td(days=6)
        return monday.isoformat(), sunday.isoformat()
    if period == "month":
        first = anchor.replace(day=1)
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        last = anchor.replace(day=last_day)
        return first.isoformat(), last.isoformat()
    # default: day
    return date_str, date_str


@app.get("/api/digest")
def get_digest(
    date_str: str = Query(..., alias="date", description="YYYY-MM-DD"),
    period: str = Query("day", description="day | week | month"),
) -> JSONResponse:
    """Return digest grouped by source.

    - ``period=day``   — articles published on *date* (default)
    - ``period=week``  — articles from Monday to Sunday of the week containing *date*
    - ``period=month`` — articles from the entire month of *date*
    """
    if period not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="period must be day, week or month")
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — expected YYYY-MM-DD")

    start_str, end_str = _date_range(date_str, period)

    articles: list[dict[str, Any]] = load_articles(ARTICLES_PATH)
    period_articles = [
        a for a in articles
        if start_str <= a.get("published_date", "")[:10] <= end_str
    ]
    # Sort newest-first within each source group
    period_articles.sort(key=lambda a: a.get("published_date", ""), reverse=True)

    by_source: dict[str, list[dict[str, Any]]] = {}
    for a in period_articles:
        by_source.setdefault(a.get("source", "Unknown"), []).append(a)

    entries = [
        {
            "nr": idx,
            "source": src,
            "articles": [
                {
                    "title": a["title"],
                    "url": a["url"],
                    "published_date": a.get("published_date", "")[:10],
                    "snippet": a.get("snippet", ""),
                    "matched_keywords": a.get("matched_keywords", []),
                    "matched_persons": a.get("matched_persons", []),
                }
                for a in arts
            ],
        }
        for idx, (src, arts) in enumerate(by_source.items(), start=1)
    ]

    return JSONResponse({
        "date": date_str,
        "period": period,
        "date_start": start_str,
        "date_end": end_str,
        "total_articles": len(period_articles),
        "entries": entries,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# WRITE endpoints — called by the Strapi/Next.js site or any admin client
# ---------------------------------------------------------------------------


@app.put("/api/persons")
def replace_persons(persons: list[PersonIn]) -> JSONResponse:
    """Replace the entire persons list.

    Aliases are auto-generated if not supplied.
    The new list is persisted to data/people.json and takes effect immediately.

    Example (Strapi webhook or cron):
        PUT /api/persons
        [ {"name": "Ioan Letia", "role": "Researcher", "research_unit": "SW4AI"}, ... ]
    """
    global _people
    raw: list[dict[str, Any]] = []
    for p in persons:
        if p.aliases:
            raw.append(p.model_dump())
        else:
            raw.append(build_person_entry(p.name, p.role, p.research_unit, p.profile_url))

    validated = validate_people(raw)

    with _write_lock:
        save_json(PEOPLE_PATH, validated)
        _people = validated

    logger.info("Persons list replaced: %d entries", len(validated))
    return JSONResponse({"updated": len(validated)})


@app.post("/api/persons")
def add_person(person: PersonIn) -> JSONResponse:
    """Add or update a single person (matched by name).

    If a person with the same name already exists, they are replaced.
    """
    global _people
    entry: dict[str, Any] = (
        {**person.model_dump(), "aliases": person.aliases}
        if person.aliases
        else build_person_entry(person.name, person.role, person.research_unit, person.profile_url)
    )

    with _write_lock:
        updated = [p for p in _people if p["name"] != person.name]
        updated.append(entry)
        save_json(PEOPLE_PATH, updated)
        _people = updated

    logger.info("Person added/updated: %s", person.name)
    return JSONResponse({"name": person.name, "aliases": entry.get("aliases", [])})


@app.delete("/api/persons/{name}")
def delete_person(name: str) -> JSONResponse:
    """Remove a person by exact name."""
    global _people
    with _write_lock:
        before = len(_people)
        updated = [p for p in _people if p["name"] != name]
        if len(updated) == before:
            raise HTTPException(status_code=404, detail=f"Person {name!r} not found")
        save_json(PEOPLE_PATH, updated)
        _people = updated

    logger.info("Person removed: %s", name)
    return JSONResponse({"removed": name})


@app.put("/api/keywords")
def replace_keywords(keywords: KeywordsIn) -> JSONResponse:
    """Replace the keyword configuration.

    Takes effect on the next collection run (no restart needed).
    Persists to data/config.json.
    """
    global _app_config
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    with _write_lock:
        raw = load_json(CONFIG_PATH, default={})
        raw["keywords"] = keywords.model_dump()
        save_json(CONFIG_PATH, raw)
        _app_config = load_config()

    logger.info("Keywords updated")
    return JSONResponse(_app_config.keywords.model_dump())


@app.put("/api/sources")
def replace_sources(sources: list[SourceIn]) -> JSONResponse:
    """Replace the RSS sources list.

    Takes effect on the next collection run (no restart needed).
    Persists to data/config.json.
    """
    global _app_config
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    with _write_lock:
        raw = load_json(CONFIG_PATH, default={})
        raw["sources"] = [s.model_dump() for s in sources]
        save_json(CONFIG_PATH, raw)
        _app_config = load_config()

    logger.info("Sources updated: %d feeds", len(sources))
    return JSONResponse({"updated": len(sources)})


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


@app.post("/api/import")
def import_article(body: ImportUrlIn) -> JSONResponse:
    """Import a single article by URL (scrapes title, date, snippet automatically).

    Useful for adding historical articles not in RSS feeds.
    Always persists the article regardless of relevance filter;
    returns whether it matched configured keywords.

    Example:
        POST /api/import
        {"url": "https://monitorulcj.ro/educatie/132640-...", "source_name": "Monitorul de Cluj"}
    """
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Infer source name from domain if not provided
    source_name = body.source_name
    if not source_name:
        import urllib.parse
        domain = urllib.parse.urlparse(body.url).netloc.lstrip("www.")
        # Match against configured sources
        for src in _app_config.sources:
            if domain in src.homepage:
                source_name = src.name
                break
        if not source_name:
            source_name = domain

    result = collector.import_url(
        url=body.url,
        source_name=source_name,
        config=_app_config,
        people=_people,
        articles_path=ARTICLES_PATH,
        graph_path=GRAPH_PATH,
        graph_builder=graph_builder,
    )
    if result["status"] == "fetch_error":
        raise HTTPException(status_code=502, detail=f"Could not fetch {body.url}")
    return JSONResponse(result)


@app.post("/api/collect")
def trigger_collection() -> JSONResponse:
    """Manually trigger a collection run."""
    if _app_config is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        n = collector.collect_once(
            _app_config, _people, ARTICLES_PATH, GRAPH_PATH, graph_builder
        )
        return JSONResponse({"new_articles": n})
    except Exception as exc:
        logger.exception("Manual collection failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Static files  (must be mounted last — catches all unmatched routes)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
