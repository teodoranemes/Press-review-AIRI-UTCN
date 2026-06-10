"""Social graph construction from matched articles.

Nodes: Person, Article, Source, ResearchUnit, Keyword.
Edges: MENTIONED_IN, PUBLISHED_BY, CO_MENTIONED_WITH, AFFILIATED_WITH, MATCHED_BY.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

logger = logging.getLogger(__name__)


def _person_id(name: str) -> str:
    return f"person:{name}"

def _article_id_node(article_id: str) -> str:
    return f"article:{article_id}"

def _source_id(source: str) -> str:
    return f"source:{source}"

def _unit_id(unit: str) -> str:
    return f"unit:{unit}"

def _keyword_id(kw: str) -> str:
    return f"keyword:{kw}"


def build_graph(
    articles: list[dict[str, Any]],
    people: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build graph JSON from articles and people lists.

    Only people from people.json appear as Person nodes.
    CO_MENTIONED_WITH edges are weighted by co-occurrence count.
    """
    person_by_name: dict[str, dict[str, Any]] = {p["name"]: p for p in people}

    person_mention_count: Counter[str] = Counter()
    person_last_seen: dict[str, str] = {}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    article_nodes_added: set[str] = set()
    source_nodes_added: set[str] = set()
    unit_nodes_added: set[str] = set()
    person_nodes_added: set[str] = set()
    keyword_nodes_added: set[str] = set()
    # Track how many articles each keyword matched
    keyword_article_count: Counter[str] = Counter()
    co_mention_weights: Counter[frozenset[str]] = Counter()

    for article in articles:
        matched_persons: list[str] = article.get("matched_persons", [])
        if not matched_persons and not article.get("matched_keywords"):
            continue

        a_node_id = _article_id_node(article["id"])
        if a_node_id not in article_nodes_added:
            nodes.append({
                "id": a_node_id,
                "type": "Article",
                "label": article.get("title", "")[:80],
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "published_date": article.get("published_date", ""),
                "snippet": article.get("snippet", ""),
                "matched_keywords": article.get("matched_keywords", []),
            })
            article_nodes_added.add(a_node_id)

        src = article.get("source", "")
        if src:
            s_node_id = _source_id(src)
            if s_node_id not in source_nodes_added:
                nodes.append({"id": s_node_id, "type": "Source", "label": src})
                source_nodes_added.add(s_node_id)
            edges.append({"source": a_node_id, "target": s_node_id, "type": "PUBLISHED_BY"})

        # Keyword nodes and MATCHED_BY edges
        for kw in article.get("matched_keywords", []):
            kw_node_id = _keyword_id(kw)
            keyword_article_count[kw] += 1
            if kw_node_id not in keyword_nodes_added:
                nodes.append({"id": kw_node_id, "type": "Keyword", "label": kw})
                keyword_nodes_added.add(kw_node_id)
            edges.append({"source": kw_node_id, "target": a_node_id, "type": "MATCHED_BY"})

        pub_date = article.get("published_date", "")
        for pname in matched_persons:
            person_mention_count[pname] += 1
            if pname not in person_last_seen or pub_date > person_last_seen[pname]:
                person_last_seen[pname] = pub_date

            p_node_id = _person_id(pname)
            if p_node_id not in person_nodes_added:
                pdata = person_by_name.get(pname, {})
                nodes.append({
                    "id": p_node_id,
                    "type": "Person",
                    "label": pname,
                    "role": pdata.get("role", ""),
                    "research_unit": pdata.get("research_unit"),
                    "profile_url": pdata.get("profile_url", ""),
                    "total_mentions": 0,
                    "last_seen": "",
                })
                person_nodes_added.add(p_node_id)

                unit = pdata.get("research_unit")
                if unit:
                    u_node_id = _unit_id(unit)
                    if u_node_id not in unit_nodes_added:
                        nodes.append({"id": u_node_id, "type": "ResearchUnit", "label": unit})
                        unit_nodes_added.add(u_node_id)
                    edges.append({"source": p_node_id, "target": u_node_id, "type": "AFFILIATED_WITH"})

            edges.append({"source": p_node_id, "target": a_node_id, "type": "MENTIONED_IN"})

        if len(matched_persons) >= 2:
            for pair in combinations(sorted(set(matched_persons)), 2):
                co_mention_weights[frozenset(pair)] += 1

    for node in nodes:
        if node["type"] == "Keyword":
            node["article_count"] = keyword_article_count[node["label"]]
        if node["type"] == "Person":
            pname = node["label"]
            node["total_mentions"] = person_mention_count[pname]
            node["last_seen"] = person_last_seen.get(pname, "")

    for pair_set, weight in co_mention_weights.items():
        pair = sorted(pair_set)
        edges.append({
            "source": _person_id(pair[0]),
            "target": _person_id(pair[1]),
            "type": "CO_MENTIONED_WITH",
            "weight": weight,
        })

    logger.info("Graph built: %d nodes, %d edges", len(nodes), len(edges))
    return {
        "nodes": nodes,
        "edges": edges,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
