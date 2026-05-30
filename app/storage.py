"""Atomic JSON persistence helpers."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: Any) -> None:
    """Write *data* as JSON to *path* atomically via a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)
        logger.debug("Wrote %s", path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON from *path*; return *default* if the file doesn't exist."""
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    """Atomically save *data* as JSON to *path*."""
    _atomic_write(path, data)


def load_articles(articles_path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load_json(articles_path, default=[])
    return result


def save_articles(articles_path: Path, articles: list[dict[str, Any]]) -> None:
    save_json(articles_path, articles)


def load_graph(graph_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = load_json(
        graph_path,
        default={"nodes": [], "edges": [], "generated_at": None},
    )
    return result


def save_graph(graph_path: Path, graph: dict[str, Any]) -> None:
    save_json(graph_path, graph)
