"""Which of the two processing paths one CommonRecord takes."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

#: Feeds whose records are field values rather than prose.
STRUCTURED_SOURCE_TYPES = frozenset({"agriculture", "weather"})

#: The CommonRecord body slot. A record whose body says something is read for its meaning,
#: whatever feed carried it.
NARRATIVE_KEY = "content"

#: ``data.language`` names the record instead of carrying a value.
INHERITED_KEY = "language"

_SCALARS = (bool, int, float, str, date, datetime)


class Mode(str, Enum):
    """How one record gets read."""

    STRUCTURED = "structured"
    SEMANTIC = "semantic"


def _value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _is_field_value(value: Any) -> bool:
    """One field value: a scalar, nothing nested, nothing running over several lines."""
    if value is None:
        return True
    if not isinstance(value, _SCALARS):
        return False
    return not (isinstance(value, str) and "\n" in value)


def route(record: Any) -> Mode:
    """STRUCTURED only when the feed's own metadata and the shape of its data both say so.

    Anything that cannot read as a table of values - prose, nested data, an unknown feed - is SEMANTIC,
    so an unfamiliar record is never flattened into facts it does not hold.
    """
    source_type = str(_value(record, "source_type") or "").strip().casefold()
    if source_type not in STRUCTURED_SOURCE_TYPES:
        return Mode.SEMANTIC

    data = _value(record, "data")
    if not isinstance(data, Mapping) or not data:
        return Mode.SEMANTIC

    body = data.get(NARRATIVE_KEY)
    if isinstance(body, str) and body.strip():
        return Mode.SEMANTIC

    if not all(_is_field_value(value) for value in data.values()):
        return Mode.SEMANTIC

    if all(key == INHERITED_KEY or value is None for key, value in data.items()):
        return Mode.SEMANTIC

    return Mode.STRUCTURED
