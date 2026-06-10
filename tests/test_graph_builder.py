"""Tests for app/graph_builder.py — all offline, no network calls."""
from __future__ import annotations

from app.graph_builder import build_graph


PEOPLE = [
    {"name": "Alice Pop",    "role": "Researcher", "research_unit": "Software & Hardware 4 AI", "profile_url": "", "aliases": ["Alice Pop", "A. Pop"]},
    {"name": "Bob Ionescu",  "role": "Researcher", "research_unit": "Cybersecurity & Space",    "profile_url": "", "aliases": ["Bob Ionescu", "B. Ionescu"]},
    {"name": "Carol Micu",   "role": "Staff",       "research_unit": None,                       "profile_url": "", "aliases": ["Carol Micu", "C. Micu"]},
]

ARTICLES: list[dict] = [
    {
        "id": "art001", "title": "AIRI Research Highlights 2026",
        "url": "https://example.com/art001", "source": "G4Media",
        "published_date": "2026-05-14T10:00:00+00:00", "snippet": "",
        "matched_keywords": ["AIRI"], "matched_persons": ["Alice Pop", "Bob Ionescu"],
    },
    {
        "id": "art002", "title": "UTCN Innovation Award",
        "url": "https://example.com/art002", "source": "Edupedu",
        "published_date": "2026-05-13T09:00:00+00:00", "snippet": "",
        "matched_keywords": ["UTCN"], "matched_persons": ["Alice Pop"],
    },
    {
        "id": "art003", "title": "No persons article",
        "url": "https://example.com/art003", "source": "G4Media",
        "published_date": "2026-05-12T08:00:00+00:00", "snippet": "",
        "matched_keywords": ["AIRI"], "matched_persons": [],
    },
]


class TestBuildGraph:
    def setup_method(self) -> None:
        self.graph = build_graph(ARTICLES, PEOPLE)

    def test_returns_required_keys(self) -> None:
        assert {"nodes", "edges", "generated_at"} <= self.graph.keys()

    def test_article_nodes_created(self) -> None:
        ids = {n["id"] for n in self.graph["nodes"]}
        assert {"article:art001", "article:art002", "article:art003"} <= ids

    def test_source_nodes_created(self) -> None:
        ids = {n["id"] for n in self.graph["nodes"]}
        assert {"source:G4Media", "source:Edupedu"} <= ids

    def test_person_nodes_created_for_matched_persons(self) -> None:
        ids = {n["id"] for n in self.graph["nodes"]}
        assert "person:Alice Pop" in ids
        assert "person:Bob Ionescu" in ids

    def test_unmentioned_person_not_in_graph(self) -> None:
        assert "person:Carol Micu" not in {n["id"] for n in self.graph["nodes"]}

    def test_research_unit_nodes_created(self) -> None:
        ids = {n["id"] for n in self.graph["nodes"]}
        assert "unit:Software & Hardware 4 AI" in ids
        assert "unit:Cybersecurity & Space" in ids

    def test_keyword_nodes_created(self) -> None:
        ids = {n["id"] for n in self.graph["nodes"]}
        assert "keyword:AIRI" in ids
        assert "keyword:UTCN" in ids

    def test_mentioned_in_edges(self) -> None:
        pairs = {(e["source"], e["target"]) for e in self.graph["edges"] if e["type"] == "MENTIONED_IN"}
        assert ("person:Alice Pop", "article:art001") in pairs
        assert ("person:Bob Ionescu", "article:art001") in pairs
        assert ("person:Alice Pop", "article:art002") in pairs

    def test_published_by_edges(self) -> None:
        pairs = {(e["source"], e["target"]) for e in self.graph["edges"] if e["type"] == "PUBLISHED_BY"}
        assert ("article:art001", "source:G4Media") in pairs
        assert ("article:art002", "source:Edupedu") in pairs

    def test_matched_by_edges(self) -> None:
        pairs = {(e["source"], e["target"]) for e in self.graph["edges"] if e["type"] == "MATCHED_BY"}
        assert ("keyword:AIRI", "article:art001") in pairs
        assert ("keyword:UTCN", "article:art002") in pairs

    def test_co_mentioned_with_edge(self) -> None:
        co = [e for e in self.graph["edges"] if e["type"] == "CO_MENTIONED_WITH"]
        assert len(co) == 1
        assert {co[0]["source"], co[0]["target"]} == {"person:Alice Pop", "person:Bob Ionescu"}
        assert co[0]["weight"] == 1

    def test_co_mention_weight_accumulates(self) -> None:
        extra = {**ARTICLES[0], "id": "art004", "url": "https://example.com/art004"}
        graph2 = build_graph(ARTICLES + [extra], PEOPLE)
        co = [e for e in graph2["edges"] if e["type"] == "CO_MENTIONED_WITH"
              and {e["source"], e["target"]} == {"person:Alice Pop", "person:Bob Ionescu"}]
        assert co[0]["weight"] == 2

    def test_person_total_mentions(self) -> None:
        nodes = {n["id"]: n for n in self.graph["nodes"]}
        assert nodes["person:Alice Pop"]["total_mentions"] == 2

    def test_affiliated_with_edges(self) -> None:
        sources = {e["source"] for e in self.graph["edges"] if e["type"] == "AFFILIATED_WITH"}
        assert "person:Alice Pop" in sources
        assert "person:Bob Ionescu" in sources

    def test_empty_articles(self) -> None:
        graph = build_graph([], PEOPLE)
        assert graph["nodes"] == [] and graph["edges"] == []

    def test_generated_at_is_set(self) -> None:
        assert "T" in self.graph["generated_at"]
