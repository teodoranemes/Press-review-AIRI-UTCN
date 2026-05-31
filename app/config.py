"""Configuration loading and validation."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
PEOPLE_PATH = DATA_DIR / "people.json"
PEOPLE_SAMPLE_PATH = DATA_DIR / "people.sample.json"
ARTICLES_PATH = DATA_DIR / "articles.json"
GRAPH_PATH = DATA_DIR / "graph.json"
EXTRACTED_PERSONS_PATH = DATA_DIR / "extracted_persons.json"


class CollectionConfig(BaseModel):
    poll_interval_minutes: int = 30
    max_article_age_days: int = 7
    request_delay_seconds: float = 1.0
    user_agent: str = "AIRI-PressReview/1.0 (+https://airi.utcluj.ro)"


class SourceConfig(BaseModel):
    name: str
    rss_url: str
    homepage: str = ""


class KeywordsConfig(BaseModel):
    institute: list[str] = Field(default_factory=list)
    university: list[str] = Field(default_factory=list)
    research_units: list[str] = Field(default_factory=list)


class GoogleCSEConfig(BaseModel):
    api_key: str = ""
    cx: str = ""
    serpapi_key: str = ""


class AppConfig(BaseModel):
    keywords: KeywordsConfig
    sources: list[SourceConfig] = Field(default_factory=list)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    google_cse: GoogleCSEConfig = Field(default_factory=GoogleCSEConfig)


def load_config() -> AppConfig:
    """Load application configuration from data/config.json."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)
    config = AppConfig(**raw)
    logger.info(
        "Config loaded: %d sources, %d institute keywords",
        len(config.sources),
        len(config.keywords.institute),
    )
    return config


def resolve_people_path() -> Path:
    """Return people.json if it exists, else fall back to people.sample.json."""
    if PEOPLE_PATH.exists():
        return PEOPLE_PATH
    if PEOPLE_SAMPLE_PATH.exists():
        logger.warning(
            "people.json not found; falling back to people.sample.json. "
            "Run scripts/import_people.py to generate the full dataset."
        )
        return PEOPLE_SAMPLE_PATH
    raise FileNotFoundError(
        f"Neither {PEOPLE_PATH} nor {PEOPLE_SAMPLE_PATH} exist. "
        "Run scripts/import_people.py first."
    )
