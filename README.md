# AIRI@UTCN Press Review

Automated daily press review platform for the Artificial Intelligence Research
Institute at the Technical University of Cluj-Napoca (UTCN).

Tracks Romanian press for mentions of the institute, affiliated researchers,
and research units.

---

## Quick start

```bash
# 1. Build and start
docker compose up --build

# 2. Open the press review
open http://localhost:8000/

# 3. Open the social graph
open http://localhost:8000/graph.html
```

A collection run fires immediately at startup, then every 30 minutes
(configurable in `data/config.json`).

---

## Project layout

```
press-review/
├── app/           Python service (FastAPI + scheduler)
├── data/          JSON files — config, articles, graph, people
├── static/        Vanilla HTML frontend
├── tests/         pytest test suite
├── scripts/       Utility scripts
└── reference/     Original manual press review (docx)
```

---

## Adding or removing RSS sources

Edit `data/config.json`, section `"sources"`:

```json
{
  "name": "My New Source",
  "rss_url": "https://example.com/feed.rss",
  "homepage": "https://example.com"
}
```

Restart the service — no code changes needed.

> **Note on sources without RSS:** Mediafax's current RSS endpoint
> (`/rss/`) was included but may require registration.  Agerpres has
> topic-specific feeds (`/rss/educatie.rss`, `/rss/stiinta.rss`).
> Sources without a public RSS feed must be omitted in this MVP.

---

## Adding or removing keywords

Edit `data/config.json`, section `"keywords"`:

```json
{
  "keywords": {
    "institute": ["AIRI", "my new keyword"],
    "university": ["UTCN"],
    "research_units": ["My New Lab"]
  }
}
```

Restart the service.

---

## Adding people

### Option A — run the scraper

```bash
python scripts/import_people.py
# writes data/people.json from https://airi.utcluj.ro/people
```

This requires the AIRI website to be reachable.  Re-run whenever the
people page changes.

### Option B — edit manually

Add entries to `data/people.json` (or `data/people.sample.json` for
testing).  Each entry must have:

```json
{
  "name": "Firstname Lastname",
  "role": "Researcher",
  "research_unit": "Software & Hardware 4 AI",
  "profile_url": "https://airi.utcluj.ro/people/staff/Firstname-Lastname",
  "aliases": [
    "Firstname Lastname",
    "F. Lastname",
    "LASTNAME, Firstname",
    "Prof. Dr. Ing. Firstname Lastname"
  ]
}
```

The `people_importer.generate_aliases()` helper generates the alias list
automatically — you can also call it directly:

```python
from app.people_importer import generate_aliases
print(generate_aliases("Doina Liana Pisla"))
```

---

## How matching works

1. **Normalization** — both the article text and every search term are
   NFD-decomposed and stripped of combining marks, then lower-cased.
   This means "Călin" and "Calin" are equivalent.

2. **Word-boundary check** — each term is matched with a `\b…\b` regex
   so "UTCN" does not match "BUTCNA".

3. **Relevance rule:**
   - Any `institute` keyword → article is included.
   - Any `research_unit` keyword → article is included.
   - Any `university` keyword **and** at least one tracked person → included.
   - Rationale: a lone "UTCN" mention (e.g., a sports article) is not
     relevant; a "UTCN" + researcher name combination is.

All matching is deterministic and auditable — inspect `matched_keywords`
and `matched_persons` fields in `data/articles.json` to verify.

---

## Running tests

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```

All tests are offline — no network calls.

---

## API reference

### Read

| Endpoint | Description |
|---|---|
| `GET /api/articles` | Paginated articles. Params: `date`, `source`, `person`, `page`, `page_size` |
| `GET /api/persons` | Current tracked persons list |
| `GET /api/keywords` | Current keyword configuration |
| `GET /api/sources` | Current RSS sources list |
| `GET /api/graph` | Graph JSON `{nodes, edges}` for Cytoscape.js or any renderer |
| `GET /api/digest?date=YYYY-MM-DD&period=day\|week\|month` | Digest grouped by source for the given day / week / month |
| `POST /api/import` | `{url, source_name?}` | Scrape and import a single article by URL |

### Write — called by Strapi/Next.js or any admin client

| Endpoint | Body | Description |
|---|---|---|
| `PUT /api/persons` | `[{name, role, research_unit, profile_url, aliases?}, ...]` | Replace entire persons list. Aliases auto-generated if omitted. |
| `POST /api/persons` | `{name, role, research_unit, profile_url, aliases?}` | Add or update a single person. |
| `DELETE /api/persons/{name}` | — | Remove a person by exact name. |
| `PUT /api/keywords` | `{institute:[...], university:[...], research_units:[...]}` | Replace keyword config. Takes effect on next collection run. |
| `PUT /api/sources` | `[{name, rss_url, homepage}, ...]` | Replace RSS sources list. Takes effect on next collection run. |
| `POST /api/collect` | — | Trigger a manual collection run immediately. |

**Example — Strapi pushing an updated people list:**

```bash
curl -X PUT http://localhost:8000/api/persons \
  -H "Content-Type: application/json" \
  -d '[{"name":"Ioan Alfred Letia","role":"Researcher","research_unit":"Software & Hardware 4 AI","profile_url":"https://airi.utcluj.ro/people/staff/Ioan-Alfred-Letia"}]'
# → {"updated": 1}
```

All write operations persist immediately to `data/` and take effect in-memory
without a restart.

---

## Configuration reference (`data/config.json`)

| Key | Default | Description |
|---|---|---|
| `collection.poll_interval_minutes` | 30 | RSS poll interval |
| `collection.max_article_age_days` | 7 | Ignore articles older than this |
| `collection.request_delay_seconds` | 1.0 | Minimum delay between requests per domain |
| `collection.user_agent` | `AIRI-PressReview/1.0` | HTTP User-Agent header |

---

## Răspunsuri la clarificările primite

### 1. Arhitectură: microserviciu separat ✓

Platforma este construită ca microserviciu standalone cu API REST complet.
Site-ul Strapi/Next.js **nu este atins** — va comunica prin HTTP cu acest serviciu.

Fluxul recomandat de integrare ulterioară:
```
Strapi (people CMS) ──PUT /api/persons──► Press Review microservice
Strapi (config CMS) ──PUT /api/keywords─►      │
                                               ↓
                                    colectare RSS + matching
                                               ↓
Next.js frontend ◄──GET /api/digest────────────┘
```

### 2. String matching cu variații (regex) ✓

Implementat în `app/matcher.py`:
- Normalizare Unicode NFD (diacritice insensitive: „Călin" = „Calin")
- Word-boundary via `\b...\b` (nu face match pe subșiruri: „UTCN" ≠ „BUTCNA")
- Case-insensitive
- Alias-uri generate automat per persoană (inițiale, titluri academice, inversarea numelui)

Extensibil fără modificări de arhitectură: adăugați termeni în `data/config.json`
sau trimiteți `PUT /api/keywords`.

### 3. Tehnologie knowledge graph: NetworkX (open source) + opțiuni

**MVP actual:** `networkx` (BSD license) pentru construcția grafului; serializat în
`data/graph.json`; vizualizat cu `cytoscape.js` (MIT license). Zero dependențe externe,
zero licențe comerciale.

**Upgrade recomandat pentru producție** (când volumul de date crește):

| Opțiune | Licență | Note |
|---|---|---|
| **Neo4j Community Edition** | GPL-3 (gratuit) | State of the art; Cypher query language; self-hosted |
| **Memgraph** | BSL / Community gratuit | Compatibil Cypher; mai rapid decât Neo4j pentru grafuri în memorie |
| **Apache AGE** | Apache 2.0 | Extensie PostgreSQL; Cypher pe PostgreSQL existent |
| **Kuzu** | MIT | Embedded, fără server; ideal pentru microserviciu fără infra suplimentară |

Pentru tranziție, schimbați doar `app/graph_builder.py` (interfața `build_graph()` rămâne
aceeași) și `app/storage.py` (înlocuiți `save_graph`/`load_graph` cu driver-ul ales).

---

## Future Strapi integration (conceptual note)

This MVP stores articles in flat JSON files under `data/`.  When
integrating with the AIRI Next.js + Strapi site
(https://github.com/airi-utcn/ai-institute-site):

1. **Replace `storage.py`** with a Strapi REST/GraphQL client.  The
   `Article` content-type in Strapi would mirror the current article
   dict schema (title, url, source, published\_date, snippet,
   matched\_keywords, matched\_persons).

2. **People** would be read from Strapi's `Person` content-type instead
   of `data/people.json`, eliminating the need for `import_people.py`.

3. **The FastAPI service** can run as a sidecar that writes to Strapi
   and exposes the `/api/graph` endpoint which Strapi cannot serve
   natively, or it can be replaced entirely with a Strapi plugin.

4. **The graph** (`graph.json`) has no direct Strapi equivalent — either
   keep the FastAPI sidecar for the graph endpoint, or store the graph
   as a JSON field in a Strapi single-type content type.

The matching logic (`matcher.py`) is completely independent of the
storage layer and can be reused without change in either context.
