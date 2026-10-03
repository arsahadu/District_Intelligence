"""The only place in Intelligence that knows how a CommonRecord is spelled."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from intelligence.extraction.spans import compute_field_hash

TITLE_PATH = "title"
CONTENT_PATH = "data.content"
LANGUAGE_PATH = "data.language"
DISTRICT_PATH = "location.district"
RETRIEVED_AT_PATH = "retrieved_at"
EVENT_TIME_PATH = "event_time"

IDENTITY_PATHS = ("record_id", "source_id", "source_type", "record_type")
OPTIONAL_PATHS = ("source_url", "raw_reference", "severity", "status")
_REQUIRED_PATHS = IDENTITY_PATHS + (RETRIEVED_AT_PATH,)

_MISSING = object()


class MappingError(ValueError):
    """Base for the record seam's failures."""


class RecordShapeError(MappingError):
    """The object is not record-shaped: a caller or configuration mistake."""


class UnreadableRecord(MappingError):
    """Well-shaped, but holding no text this module is allowed to read."""


@runtime_checkable
class CommonRecordLike(Protocol):
    """The structural contract `read_record` relies on. Never imported from ingestion."""

    record_id: str
    source_id: str
    source_type: str
    record_type: str
    title: str
    event_time: Optional[datetime]
    location: Any
    data: dict[str, Any]
    severity: Optional[str]
    status: Optional[str]
    source_url: Optional[str]
    retrieved_at: datetime
    raw_reference: Optional[str]


@dataclass(frozen=True)
class RecordInput:
    """One record read structurally: text kept verbatim, scalars kept as text."""

    record_id: str
    source_id: str
    source_type: str
    record_type: str
    retrieved_at: datetime
    source_url: Optional[str] = None
    raw_reference: Optional[str] = None
    event_time: Optional[datetime] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    location_raw_text: Optional[str] = None
    language_hint: Optional[str] = None
    texts: tuple[tuple[str, str], ...] = ()
    scalars: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    unused_data_keys: tuple[str, ...] = ()

    @property
    def text_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.texts)

    @property
    def title(self) -> Optional[str]:
        return self.texts_by_path().get(TITLE_PATH)

    def texts_by_path(self) -> dict[str, str]:
        return dict(self.texts)

    def scalar(self, path: str) -> Optional[str]:
        return dict(self.scalars).get(path)

    def has_text(self) -> bool:
        return bool(self.texts)

    def canonical(self) -> str:
        return json.dumps(
            _jsonable(self.as_dict()), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "record_id": self.record_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "record_type": self.record_type,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_url": self.source_url,
            "raw_reference": self.raw_reference,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "severity": self.severity,
            "status": self.status,
            "district": self.district,
            "state": self.state,
            "location_raw_text": self.location_raw_text,
            "language_hint": self.language_hint,
            "texts": list(self.texts),
            "scalars": list(self.scalars),
            "notes": list(self.notes),
            "unused_data_keys": list(self.unused_data_keys),
        }
        return payload


def input_hash(record: RecordInput) -> str:
    """One hash over everything the mapper read, so a re-run can prove it read the same input."""
    return compute_field_hash(record.canonical())


def read_record(
    obj: Any,
    *,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> RecordInput:
    """Read a CommonRecord-shaped object; only the title and declared text keys become body text."""
    if isinstance(obj, RecordInput):
        return obj

    notes: list[str] = []
    for path in _REQUIRED_PATHS:
        if _resolve(obj, path) is _MISSING:
            raise RecordShapeError(f"{_describe(obj)} has no {path!r}")

    identity = {path: _text(_resolve(obj, path), path, obj) for path in IDENTITY_PATHS}
    retrieved_at = _moment(_resolve(obj, RETRIEVED_AT_PATH), RETRIEVED_AT_PATH, obj)

    optionals: dict[str, Optional[str]] = {}
    for path in OPTIONAL_PATHS:
        value = _resolve(obj, path)
        if value is _MISSING or value is None or value == "":
            optionals[path] = None
        else:
            optionals[path] = _text(value, path, obj)

    event_time_value = _resolve(obj, EVENT_TIME_PATH)
    event_time = (
        None if event_time_value is _MISSING or event_time_value is None
        else _moment(event_time_value, EVENT_TIME_PATH, obj)
    )

    location = _resolve(obj, "location")
    district = _location_text(location, "district")
    state = _location_text(location, "state")
    location_raw_text = _location_text(location, "raw_text")

    data = _resolve(obj, "data")
    if data is _MISSING:
        data = {}
    elif not isinstance(data, Mapping):
        raise RecordShapeError(f"{_describe(obj)} data must be a mapping, got {type(data).__name__}")

    texts: list[tuple[str, str]] = []
    scalars: list[tuple[str, str]] = []
    seen: set[str] = set()
    language_hint: Optional[str] = None
    text_slots = {TITLE_PATH, *text_keys}

    for path in (TITLE_PATH, *text_keys, *extra_keys):
        if path in seen:
            continue
        seen.add(path)
        if path == LANGUAGE_PATH:
            notes.append("data.language is read as the inherited hint, never as text")
            continue
        value = _resolve(obj, path)
        if value is _MISSING:
            if path != TITLE_PATH:
                notes.append(f"{path} is not present on this record")
            continue
        if isinstance(value, str):
            if value == "":
                notes.append(f"{path} is empty")
            elif path in text_slots:
                texts.append((path, value))
            else:
                scalars.append((path, value))
        elif value is None:
            notes.append(f"{path} is null")
        elif _is_scalar(value):
            scalars.append((path, _render(value)))
        else:
            notes.append(f"{path} holds {type(value).__name__}, which is neither text nor a scalar")

    consumed = {path.split(".", 1)[1] for path in (*text_keys, *extra_keys) if path.startswith("data.")}
    unused = sorted(str(key) for key in data if str(key) not in consumed)

    hint = data.get("language")
    if hint is not None:
        language_hint = _text(hint, LANGUAGE_PATH, obj)

    if not texts:
        raise UnreadableRecord(
            f"{_describe(obj)} offers no readable text: {'; '.join(notes) or 'no text fields declared'}"
        )

    return RecordInput(
        record_id=identity["record_id"],
        source_id=identity["source_id"],
        source_type=identity["source_type"],
        record_type=identity["record_type"],
        retrieved_at=retrieved_at,
        source_url=optionals["source_url"],
        raw_reference=optionals["raw_reference"],
        event_time=event_time,
        severity=optionals["severity"],
        status=optionals["status"],
        district=district,
        state=state,
        location_raw_text=location_raw_text,
        language_hint=language_hint,
        texts=tuple(texts),
        scalars=tuple(scalars),
        notes=tuple(notes),
        unused_data_keys=tuple(k for k in unused if k != "language"),
    )


def _location_text(location: Any, name: str) -> Optional[str]:
    if location is _MISSING or location is None:
        return None
    value = getattr(location, name, _MISSING)
    if value is _MISSING and isinstance(location, Mapping):
        value = location.get(name, _MISSING)
    if value is _MISSING or value is None or value == "":
        return None
    return _text(value, f"location.{name}", location)


def _resolve(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        current = getattr(current, part, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def _text(value: Any, path: str, obj: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, date, datetime)) and not isinstance(value, bool):
        return _render(value)
    raise RecordShapeError(
        f"{_describe(obj)} field {path!r} must be text, got {type(value).__name__}"
    )


def _moment(value: Any, path: str, obj: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise RecordShapeError(
                f"{_describe(obj)} field {path!r} is not an ISO timestamp: {error}"
            ) from error
    raise RecordShapeError(
        f"{_describe(obj)} field {path!r} must be a datetime, got {type(value).__name__}"
    )


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (bool, int, float, date, datetime))


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _describe(obj: Any) -> str:
    for path in ("record_id", "source_id", "title"):
        value = _resolve(obj, path)
        if isinstance(value, str) and value:
            return f"record {value!r}"
    return f"object of type {type(obj).__name__}"
