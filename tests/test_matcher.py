"""Tests for app/matcher.py — all offline, no network calls."""
from __future__ import annotations

from app.matcher import article_is_relevant, match_keywords, match_persons, normalize, term_matches


class TestNormalize:
    def test_strips_diacritics(self) -> None:
        assert normalize("Călin") == "calin"
        assert normalize("Pișla") == "pisla"

    def test_lowercases(self) -> None:
        assert normalize("UTCN") == "utcn"

    def test_ascii_unchanged(self) -> None:
        assert normalize("hello world") == "hello world"


class TestTermMatches:
    def test_simple_match(self) -> None:
        assert term_matches("UTCN", "Universitatea Tehnică UTCN a câștigat un grant.")

    def test_case_insensitive(self) -> None:
        assert term_matches("airi", "Institutul AIRI a publicat rezultate.")

    def test_diacritic_normalization(self) -> None:
        assert term_matches("Călin", "Profesorul Calin Iclanzan a susținut o conferință.")
        assert term_matches("Calin", "Profesorul Călin Iclănzan a susținut o conferință.")

    def test_no_substring_match(self) -> None:
        assert not term_matches("UTCN", "Institutul BUTCNA a publicat.")

    def test_word_boundary_at_start(self) -> None:
        assert term_matches("UTCN", "UTCN este o universitate.")

    def test_word_boundary_at_end(self) -> None:
        assert term_matches("UTCN", "Universitatea din Cluj este UTCN")

    def test_no_match(self) -> None:
        assert not term_matches("AIRI", "Un articol despre sport.")

    def test_hyphenated_name(self) -> None:
        assert term_matches("Radu-Emil Precup", "Prof. Radu-Emil Precup a câștigat un premiu.")


class TestMatchKeywords:
    def test_returns_matching_keywords(self) -> None:
        result = match_keywords(["AIRI", "UTCN", "G4Media"], "Cercetătorii de la UTCN", "au primit finanțare")
        assert result == ["UTCN"]

    def test_multiple_matches(self) -> None:
        result = match_keywords(["AIRI", "UTCN"], "AIRI@UTCN inauguration", "AIRI și UTCN sunt parteneri")
        assert "UTCN" in result

    def test_no_match(self) -> None:
        assert match_keywords(["AIRI", "UTCN"], "Fotbal la Cluj", "Echipa a câștigat") == []

    def test_matches_in_body(self) -> None:
        kws = ["Artificial Intelligence Research Institute"]
        result = match_keywords(kws, "Conferință la Cluj", "Organizat de Artificial Intelligence Research Institute.")
        assert kws[0] in result


SAMPLE_PEOPLE: list[dict] = [
    {
        "name": "Călin Iclănzan",
        "aliases": [
            "Călin Iclănzan", "Calin Iclanzan", "C. Iclănzan",
            "ICLĂNZAN, Călin", "Prof. Dr. Ing. Călin Iclănzan",
        ],
    },
    {
        "name": "Radu-Emil Precup",
        "aliases": [
            "Radu-Emil Precup", "Radu Emil Precup", "R. Precup",
            "PRECUP, Radu-Emil", "Prof. Dr. Ing. Radu-Emil Precup",
        ],
    },
]


class TestMatchPersons:
    def test_match_by_full_name(self) -> None:
        result = match_persons(SAMPLE_PEOPLE, "Conf. univ. dr. Calin Iclanzan a primit premiul", "")
        assert "Călin Iclănzan" in result

    def test_match_diacritics_normalized(self) -> None:
        result = match_persons(SAMPLE_PEOPLE, "Profesorul Călin Iclănzan de la UTCN", "")
        assert "Călin Iclănzan" in result

    def test_match_by_alias_with_title(self) -> None:
        result = match_persons(SAMPLE_PEOPLE, "", "Discuție cu Prof. Dr. Ing. Radu-Emil Precup despre AI.")
        assert "Radu-Emil Precup" in result

    def test_no_match(self) -> None:
        assert match_persons(SAMPLE_PEOPLE, "Sport la Cluj", "Rezultate fotbal") == []

    def test_no_partial_match(self) -> None:
        result = match_persons(SAMPLE_PEOPLE, "Domnul Precupescu a câștigat", "")
        assert "Radu-Emil Precup" not in result


KEYWORDS_CONFIG = {
    "institute": ["AIRI", "Artificial Intelligence Research Institute"],
    "university": ["UTCN", "Universitatea Tehnică din Cluj-Napoca"],
    "research_units": ["Infineon Semiconductor AI Lab"],
}


class TestArticleIsRelevant:
    def test_institute_keyword_alone_is_sufficient(self) -> None:
        relevant, kws, _ = article_is_relevant("Conferință organizată de AIRI la Cluj", "", KEYWORDS_CONFIG, [])
        assert relevant is True
        assert "AIRI" in kws

    def test_research_unit_alone_is_sufficient(self) -> None:
        relevant, _, _ = article_is_relevant("Infineon Semiconductor AI Lab lansează proiect", "", KEYWORDS_CONFIG, [])
        assert relevant is True

    def test_university_alone_is_not_sufficient(self) -> None:
        relevant, _, _ = article_is_relevant("UTCN a câștigat un meci de fotbal", "", KEYWORDS_CONFIG, [])
        assert relevant is False

    def test_university_plus_person_is_sufficient(self) -> None:
        relevant, kws, persons = article_is_relevant(
            "Prof. Dr. Ing. Călin Iclănzan de la UTCN primește grant european",
            "Cercetătorul de la Universitatea Tehnică din Cluj-Napoca",
            KEYWORDS_CONFIG,
            SAMPLE_PEOPLE,
        )
        assert relevant is True
        assert "Călin Iclănzan" in persons

    def test_no_keywords_no_persons_is_not_relevant(self) -> None:
        relevant, _, _ = article_is_relevant("Fotbal la Cluj", "Un meci spectaculos.", KEYWORDS_CONFIG, SAMPLE_PEOPLE)
        assert relevant is False

    def test_returns_matched_persons(self) -> None:
        _, _, persons = article_is_relevant("AIRI researcher Radu-Emil Precup wins award", "", KEYWORDS_CONFIG, SAMPLE_PEOPLE)
        assert "Radu-Emil Precup" in persons
