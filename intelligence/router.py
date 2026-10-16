from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

NEWS_SOURCE_TYPES = frozenset({"news"})
NEWS_SOURCE_IDS = frozenset({"dinamalar"})
NON_NEWS_SOURCE_TYPES = frozenset({"agriculture", "weather"})
NON_NEWS_RECORD_TYPES = frozenset({"market_price", "forecast"})


class Mode(str, Enum):
    STRUCTURED = "structured"
    SEMANTIC = "semantic"


def _value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def route(record: Any) -> Mode:
    source_type = str(_value(record, "source_type") or "").strip().casefold()
    record_type = str(_value(record, "record_type") or "").strip().casefold()
    source_id = str(_value(record, "source_id") or "").strip().casefold()
    if source_type in NON_NEWS_SOURCE_TYPES or record_type in NON_NEWS_RECORD_TYPES:
        return Mode.STRUCTURED
    if source_type in NEWS_SOURCE_TYPES or (
        not source_type
        and source_id in NEWS_SOURCE_IDS
        and record_type == "article"
    ):
        return Mode.SEMANTIC
    return Mode.STRUCTURED
