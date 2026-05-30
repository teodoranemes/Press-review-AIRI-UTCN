"""Social graph construction from matched articles.

Nodes: Person, Article, Source (optionally ResearchUnit).
Edges: MENTIONED_IN, PUBLISHED_BY, CO_MENTIONED_WITH, AFFILIATED_WITH.
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


def build_graph(
    articles: list[dict[str, Any]],
    people: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build graph JSON from articles and people lists.

    Returns the standard graph dict with 'nodes', 'edges', 'generated_at'.
    """
    person_by_name: dict[str, dict[str, Any]] = {p["name"]: p for p in people}

    # Track per-person stats
    person_mention_count: Counter[str] = Counter()
    person_last_seen: dict[str, str] = {}

    # Collect used sources
    source_names: set[str] = set()
    # co-mention weights: frozenset -> count
    co_mention_weights: Counter[frozenset[str]] = Counter()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    article_nodes_added: set[str] = set()
    source_nodes_added: set[str] = set()
    unit_nodes_added: set[str] = set()
    person_nodes_added: set[str] = set()

    for article in articles:
        matched_persons: list[str] = article.get("matched_persons", [])
        if not matched_persons and not article.get("matched_keywords"):
            continue

        a_node_id = _article_id_node(article["id"])
        if a_node_id not in article_nodes_added:
            nodes.append(
                {
                    "id": a_node_id,
                    "type": "Article",
                    "label": article.get("title", "")[:80],
                    "url": article.get("url", ""),
                    "source": article.get("source", ""),
                    "published_date": article.get("published_date", ""),
                    "snippet": article.get("snippet", ""),
                    "matched_keywords": article.get("matched_keywords", []),
                }
            )
            article_nodes_added.add(a_node_id)

        # Source node
        src = article.get("source", "")
        if src:
            source_names.add(src)
            s_node_id = _source_id(src)
            if s_node_id not in source_nodes_added:
                nodes.append({"id": s_node_id, "type": "Source", "label": src})
                source_nodes_added.add(s_node_id)
            edges.append(
                {
                    "source": a_node_id,
                    "target": s_node_id,
                    "type": "PUBLISHED_BY",
                }
            )

        # Person nodes and MENTIONED_IN edges
        pub_date = article.get("published_date", "")
        for pname in matched_persons:
            person_mention_count[pname] += 1
            if pname not in person_last_seen or pub_date > person_last_seen[pname]:
                person_last_seen[pname] = pub_date

            p_node_id = _person_id(pname)
            if p_node_id not in person_nodes_added:
                pdata = person_by_name.get(pname, {})
                nodes.append(
                    {
                        "id": p_node_id,
                        "type": "Person",
                        "label": pname,
                        "role": pdata.get("role", ""),
                        "research_unit": pdata.get("research_unit"),
                        "profile_url": pdata.get("profile_url", ""),
                        "total_mentions": 0,  # will be updated below
                        "last_seen": "",
                    }
                )
                person_nodes_added.add(p_node_id)

                # AFFILIATED_WITH edge
                unit = pdata.get("research_unit")
                if unit:
                    u_node_id = _unit_id(unit)
                    if u_node_id not in unit_nodes_added:
                        nodes.append(
                            {"id": u_node_id, "type": "ResearchUnit", "label": unit}
                        )
                        unit_nodes_added.add(u_node_id)
                    edges.append(
                        {
                            "source": p_node_id,
                            "target": u_node_id,
                            "type": "AFFILIATED_WITH",
                        }
                    )

            edges.append(
                {
                    "source": p_node_id,
                    "target": a_node_id,
                    "type": "MENTIONED_IN",
                }
            )

        # CO_MENTIONED_WITH — accumulate weights
        if len(matched_persons) >= 2:
            for pair in combinations(sorted(set(matched_persons)), 2):
                co_mention_weights[frozenset(pair)] += 1

    # Patch person node stats
    for node in nodes:
        if node["type"] == "Person":
            pname = node["label"]
            node["total_mentions"] = person_mention_count[pname]
            node["last_seen"] = person_last_seen.get(pname, "")

    # CO_MENTIONED_WITH edges
    for pair_set, weight in co_mention_weights.items():
        pair = sorted(pair_set)
        edges.append(
            {
                "source": _person_id(pair[0]),
                "target": _person_id(pair[1]),
                "type": "CO_MENTIONED_WITH",
                "weight": weight,
            }
        )

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    logger.info(
        "Graph built: %d nodes, %d edges", len(nodes), len(edges)
    )
    return {
        "nodes": nodes,
        "edges": edges,
        "generated_at": generated_at,
    }
