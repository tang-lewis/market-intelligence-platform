"""
quality_log.py — Structured data quality event logger.
Every data quality anomaly is written here with full context.
Do not silently drop records — log and decide.
"""

import csv
import logging
import os
from datetime import datetime, timezone

QUALITY_LOG_PATH = "data/quality_log.csv"
FIELDNAMES = ["timestamp_utc", "source", "event_type", "detail", "disposition"]

logger = logging.getLogger(__name__)


def log_quality_event(
    source: str,
    event_type: str,
    detail: str,
    disposition: str
) -> None:
    """
    Write a quality event to the quality log CSV.

    Args:
        source:      Data source identifier (e.g. 'yahoo_finance', 'rba')
        event_type:  Short code for the event (e.g. 'NULL_VALUES', 'API_TIMEOUT')
        detail:      Human-readable description of the issue
        disposition: What was done (e.g. 'RETAINED_FOR_REVIEW', 'PIPELINE_HALTED')
    """
    os.makedirs(os.path.dirname(QUALITY_LOG_PATH), exist_ok=True)

    write_header = not os.path.exists(QUALITY_LOG_PATH)
    with open(QUALITY_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
            "source": source,
            "event_type": event_type,
            "detail": detail,
            "disposition": disposition,
        })

    logger.info("Quality event logged: [%s] %s — %s", source, event_type, disposition)