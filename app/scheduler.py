"""Background scheduler for periodic RSS collection."""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start(
    config: Any,
    people: list[dict[str, Any]],
    articles_path: Any,
    graph_path: Any,
    graph_builder: Any,
    collector: Any,
) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running; not starting again")
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    def _job() -> None:
        try:
            n = collector.collect_once(config, people, articles_path, graph_path, graph_builder)
            logger.info("Scheduled collection: %d new articles", n)
        except Exception as exc:
            logger.exception("Error during scheduled collection: %s", exc)

    _scheduler.add_job(
        _job,
        trigger=IntervalTrigger(minutes=config.collection.poll_interval_minutes),
        id="rss_collector",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started; interval: %d min", config.collection.poll_interval_minutes)
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
