---
title: AIRI UTCN Press Review
emoji: 📰
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# AIRI@UTCN Press Review

Automated press review platform for the Artificial Intelligence Research
Institute at the Technical University of Cluj-Napoca (UTCN).

Monitors Romanian press for mentions of the institute, affiliated researchers,
and research units via RSS feeds and Google Search.

---

## Quick start

```bash
docker compose up --build
```

- Press review: `http://localhost:8000/`
- Social graph: `http://localhost:8000/graph.html`

A collection run fires at startup, then every 30 minutes (configurable in `data/config.json`).

---

## Project layout

```
press-review/
├── app/           FastAPI service + scheduler + matching logic
├── data/          JSON files — config, articles, graph, people
├── static/        HTML frontend (no build step)
├── tests/         pytest test suite
└── reference/     Original manual press review (docx)
```

---

## Configuration

### RSS sources — `data/config.json` → `sources`

```json
{ "name": "My Source", "rss_url": "https://example.com/feed.rss", "homepage": "https://example.com" }
```

### Keywords — `data/config.json` → `keywords`

```json
{
  "institute": ["AIRI", "AIRi@UTCN"],
  "university": ["UTCN", "Universitatea Tehnică din Cluj-Napoca"],
  "research_units": ["Trusted AI", "Robotics and IoT"]
}
```

Changes take effect on the next collection run; no restart needed.

### People — `data/people.json`

Each entry:

```json
{
  "name": "Firstname Lastname",
  "role": "researcher",
  "research_unit": "Trusted AI",
  "profile_url": "https://airi.utcluj.ro/people/slug",
  "aliases": ["Firstname Lastname", "F. Lastname", "LASTNAME, Firstname"]
}
```

Aliases can be generated automatically:

```python
from app.people_importer import generate_aliases
generate_aliases("Doina Liana Pisla")
```

---

## How matching works

1. **Normalization** — text and terms are NFD-decomposed and lower-cased, making matching diacritics-insensitive ("Călin" = "Calin").
2. **Word boundaries** — terms are matched with `\b…\b` so "UTCN" does not match "BUTCNA".
3. **Relevance rules:**
   - Any `institute` keyword → relevant.
   - Any `research_units` keyword → relevant.
   - Any `university` keyword **and** at least one tracked person → relevant.

`matched_keywords` and `matched_persons` on each article in `data/articles.json` show exactly what triggered inclusion.

---

## Graph

The social graph (`/graph.html`) shows:

| Node | Color | Description |
|---|---|---|
| Person | Blue | Researcher mentioned in an article |
| Article | Orange | Press article |
| Source | Green | Publication |
| ResearchUnit | Purple | Lab/unit the researcher belongs to |
| Keyword | Amber | Keyword that triggered the match |

Edges: `MENTIONED_IN`, `PUBLISHED_BY`, `AFFILIATED_WITH`, `MATCHED_BY`, `CO_MENTIONED_WITH`.

The date filter bar (Total / Lunar / Săptămânal / Zilnic) limits visible nodes to the selected period.

---

## API

### Read

| Endpoint | Description |
|---|---|
| `GET /api/articles` | Paginated articles. Params: `date`, `source`, `person`, `page`, `page_size` |
| `GET /api/digest?date=YYYY-MM-DD&period=day\|week\|month` | Articles grouped by source for a period |
| `GET /api/graph` | Graph JSON `{nodes, edges}` |
| `GET /api/persons` | Tracked persons list |
| `GET /api/keywords` | Keyword configuration |
| `GET /api/sources` | RSS sources list |

### Write

| Endpoint | Body | Description |
|---|---|---|
| `PUT /api/persons` | `[{name, role, research_unit, profile_url, aliases?}, ...]` | Replace persons list |
| `POST /api/persons` | `{name, role, ...}` | Add or update a person |
| `DELETE /api/persons/{name}` | — | Remove a person |
| `PUT /api/keywords` | `{institute:[...], university:[...], research_units:[...]}` | Replace keywords |
| `PUT /api/sources` | `[{name, rss_url, homepage}, ...]` | Replace sources |
| `POST /api/collect` | — | Trigger RSS collection immediately |
| `POST /api/search` | — | Search Google and import new articles |
| `POST /api/import` | `{url, source_name?}` | Import a single article by URL |

All write operations persist to `data/` and take effect immediately without restart.

---

## Running tests

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```

---

## Collection interval

| Key | Default | Description |
|---|---|---|
| `collection.poll_interval_minutes` | 30 | RSS poll interval |
| `collection.max_article_age_days` | 7 | Ignore articles older than this |
| `collection.request_delay_seconds` | 1.0 | Delay between requests per domain |
| `collection.user_agent` | `AIRI-PressReview/1.0` | HTTP User-Agent |
