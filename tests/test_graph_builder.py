"""Tests for app/graph_builder.py.

All tests are offline — no network calls.
"""
from __future__ import annotations

import pytest

from app.graph_builder import build_graph


PEOPLE = [
    {
        "name": "Alice Pop",
        "role": "Researcher",
        "research_unit": "Software & Hardware 4 AI",
        "profile_url": "https://airi.utcluj.ro/people/staff/Alice-Pop",
        "aliases": ["Alice Pop", "A. Pop"],
    },
    {
        "name": "Bob Ionescu",
        "role": "Researcher",
        "research_unit": "Cybersecurity & Space",
        "profile_url": "https://airi.utcluj.ro/people/staff/Bob-Ionescu",
        "aliases": ["Bob Ionescu", "B. Ionescu"],
    },
    {
        "name": "Carol Micu",
        "role": "Staff",
        "research_unit": None,
        "profile_url": "",
        "aliases": ["Carol Micu", "C. Micu"],
    },
]

ARTICLES: list[dict] = [
    {
        "id": "art001",
        "title": "AIRI Research Highlights 2026",
        "url": "https://example.com/art001",
        "source": "G4Media",
        "published_date": "2026-05-14T10:00:00+00:00",
        "snippet": "Short summary.",
        "matched_keywords": ["AIRI"],
        "matched_persons": ["Alice Pop", "Bob Ionescu"],
    },
    {
        "id": "art002",
        "title": "UTCN Innovation Award",
        "url": "https://example.com/art002",
        "source": "Edupedu",
        "published_date": "2026-05-13T09:00:00+00:00",
        "snippet": "Another summary.",
        "matched_keywords": ["UTCN"],
        "matched_persons": ["Alice Pop"],
    },
    {
        "id": "art003",
        "title": "No persons article",
        "url": "https://example.com/art003",
        "source": "G4Media",
        "published_date": "2026-05-12T08:00:00+00:00",
        "snippet": "No person mentioned.",
        "matched_keywords": ["AIRI"],
        "matched_persons": [],
    },
]


class TestBuildGraph:
    def setup_method(self) -> None:
        self.graph = build_graph(ARTICLES, PEOPLE)

    def test_returns_required_keys(self) -> None:
        assert "nodes" in self.graph
        assert "edges" in self.graph
        assert "generated_at" in self.graph

    def test_article_nodes_created(self) -> None:
        node_ids = {n["id"] for n in self.graph["nodes"]}
        assert "article:art001" in node_ids
        assert "article:art002" in node_ids
        assert "article:art003" in node_ids

    def test_source_nodes_created(self) -> None:
        node_ids = {n["id"] for n in self.graph["nodes"]}
        assert "source:G4Media" in node_ids
        assert "source:Edupedu" in node_ids

    def test_person_nodes_created_for_matched_persons(self) -> None:
        node_ids = {n["id"] for n in self.graph["nodes"]}
        assert "person:Alice Pop" in node_ids
        assert "person:Bob Ionescu" in node_ids

    def test_unmentioned_person_not_in_graph(self) -> None:
        node_ids = {n["id"] for n in self.graph["nodes"]}
        assert "person:Carol Micu" not in node_ids

    def test_research_unit_nodes_created(self) -> None:
        node_ids = {n["id"] for n in self.graph["nodes"]}
        assert "unit:Software & Hardware 4 AI" in node_ids
        assert "unit:Cybersecurity & Space" in node_ids

    def test_person_with_null_unit_has_no_unit_node(self) -> None:
        # Carol Micu has research_unit=None; no affiliated_with edge expected
        edges = self.graph["edges"]
        affiliated = [e for e in edges if e["type"] == "AFFILIATED_WITH" and "carol" in e["source"].lower()]
        assert affiliated == []

    def test_mentioned_in_edges(self) -> None:
        edges = self.graph["edges"]
        mentioned = [e for e in edges if e["type"] == "MENTIONED_IN"]
        # art001 has 2 persons, art002 has 1 person
        sources = [(e["source"], e["target"]) for e in mentioned]
        assert ("person:Alice Pop", "article:art001") in sources
        assert ("person:Bob Ionescu", "article:art001") in sources
        assert ("person:Alice Pop", "article:art002") in sources

    def test_published_by_edges(self) -> None:
        edges = self.graph["edges"]
        pub_edges = {(e["source"], e["target"]) for e in edges if e["type"] == "PUBLISHED_BY"}
        assert ("article:art001", "source:G4Media") in pub_edges
        assert ("article:art002", "source:Edupedu") in pub_edges

    def test_co_mentioned_with_edge(self) -> None:
        edges = self.graph["edges"]
        co = [e for e in edges if e["type"] == "CO_MENTIONED_WITH"]
        assert len(co) == 1
        e = co[0]
        pair = {e["source"], e["target"]}
        assert pair == {"person:Alice Pop", "person:Bob Ionescu"}
        assert e["weight"] == 1

    def test_co_mention_weight_accumulates(self) -> None:
        """If two persons co-appear in two articles, weight should be 2."""
        articles_doubled = ARTICLES + [
            {
                "id": "art004",
                "title": "Second co-mention",
                "url": "https://example.com/art004",
                "source": "G4Media",
                "published_date": "2026-05-11T08:00:00+00:00",
                "snippet": "",
                "matched_keywords": ["AIRI"],
                "matched_persons": ["Alice Pop", "Bob Ionescu"],
            }
        ]
        graph2 = build_graph(articles_doubled, PEOPLE)
        co = [e for e in graph2["edges"] if e["type"] == "CO_MENTIONED_WITH"]
        alice_bob = [
            e
            for e in co
            if {e["source"], e["target"]} == {"person:Alice Pop", "person:Bob Ionescu"}
        ]
        assert len(alice_bob) == 1
        assert alice_bob[0]["weight"] == 2

    def test_person_total_mentions(self) -> None:
        nodes_by_id = {n["id"]: n for n in self.graph["nodes"]}
        alice = nodes_by_id.get("person:Alice Pop")
        assert alice is not None
        assert alice["total_mentions"] == 2  # art001 and art002

    def test_affiliated_with_edges(self) -> None:
        edges = self.graph["edges"]
        affiliated = [e for e in edges if e["type"] == "AFFILIATED_WITH"]
        sources = {e["source"] for e in affiliated}
        assert "person:Alice Pop" in sources
        assert "person:Bob Ionescu" in sources

    def test_empty_articles(self) -> None:
        graph = build_graph([], PEOPLE)
        assert graph["nodes"] == []
        assert graph["edges"] == []

    def test_generated_at_is_set(self) -> None:
        assert self.graph["generated_at"] is not None
        assert "T" in self.graph["generated_at"]  # ISO format
