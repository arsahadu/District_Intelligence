from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from intelligence.contract import Provenance
from intelligence.documents import Document, Page
from intelligence.models.enums import Modality, TimePrecision
from intelligence.records import CONTENT_PATH, DISTRICT_PATH, RAW_TEXT_PATH, STATE_PATH
from intelligence.records import RecordInput, RecordShapeError, input_hash, read_record
from intelligence.spans import SourceField, compute_field_hash

FIELD_HEADING = "field"
DATA_PREFIX = "data."

GEOGRAPHY_SLOTS: tuple[tuple[str, str], ...] = (
    (RAW_TEXT_PATH, "location_raw_text"),
    (DISTRICT_PATH, "district"),
    (STATE_PATH, "state"),
)

INHERITED_KEYS = frozenset({"language"})


def data_field_paths(obj: Any) -> tuple[str, ...]:
    data = _data_of(obj)
    if not isinstance(data, Mapping):
        return ()
    return tuple(
        f"{DATA_PREFIX}{key}"
        for key in sorted(str(name) for name in data)
        if key not in INHERITED_KEYS
    )


def _data_of(obj: Any) -> Any:
    data = getattr(obj, "data", None)
    if data is None and isinstance(obj, Mapping):
        data = obj.get("data")
    return data


def data_paths_without_value(obj: Any) -> tuple[str, ...]:
    data = _data_of(obj)
    if not isinstance(data, Mapping):
        return ()
    return tuple(
        f"{DATA_PREFIX}{key}"
        for key in sorted(str(name) for name in data)
        if key not in INHERITED_KEYS and data[key] in (None, "")
    )


@dataclass(frozen=True)
class RecordContext:
    record: RecordInput
    fields: tuple[SourceField, ...]

    geography: tuple[SourceField, ...] = ()

    without_value: tuple[str, ...] = ()

    document: Optional[Document] = None

    def field(self, path: str) -> Optional[SourceField]:
        return next((item for item in self.fields if item.field == path), None)

    def reading(self, path: str) -> Optional[Page]:
        return None if self.document is None else self.document.page(path)

    def text_of(self, path: str) -> Optional[str]:
        found = self.field(path)
        return None if found is None else found.text

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.field for item in self.fields)

    def prompt_block(self) -> str:
        parts = [f"{FIELD_HEADING} {item.field}\n{item.text}" for item in self.fields]
        return "\n\n".join(parts)

    def context_hash(self) -> str:
        joined = "\x1e".join(f"{item.field}\x1f{item.text_hash}" for item in self.fields)
        return compute_field_hash(joined)

    def provenance(self) -> Provenance:
        record = self.record
        return Provenance(
            record_id=record.record_id,
            source_id=record.source_id,
            source_type=record.source_type,
            record_type=record.record_type,
            source_url=record.source_url,
            raw_reference=record.raw_reference,
            retrieved_at=record.retrieved_at,
            modality=Modality.TEXT if self.document is None else self.document.modality,
            language_hint=record.language_hint,
            district_hint=record.district,
            state_hint=record.state,
            declared_severity=record.severity,
            declared_status=record.status,
            declared_event_time=record.event_time,
            declared_event_time_precision=(
                None
                if record.event_time is None
                else record.event_time_precision or TimePrecision.UNKNOWN
            ),
            fields_without_value=list(self.without_value),
            input_hash=input_hash(record),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.as_dict(),
            "fields": [
                {"field": item.field, "text": item.text, "text_hash": item.text_hash}
                for item in self.fields
            ],
            "geography": [
                {"field": item.field, "text": item.text, "text_hash": item.text_hash}
                for item in self.geography
            ],
            "without_value": list(self.without_value),
            "context_hash": self.context_hash(),
        }


def build_context(
    obj: Any,
    *,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> RecordContext:
    declared = tuple(text_keys)
    discovered = tuple(key for key in data_field_paths(obj) if key not in declared)
    record = read_record(obj, text_keys=declared + discovered, extra_keys=extra_keys)
    identity = {
        "record_id": record.record_id,
        "source_id": record.source_id,
        "source_type": record.source_type,
        "source_url": record.source_url,
        "raw_reference": record.raw_reference,
        "retrieved_at": record.retrieved_at,
    }
    citable = list(record.texts) + [
        pair for pair in record.scalars if pair[0].startswith(DATA_PREFIX)
    ]
    fields = tuple(SourceField(field=path, text=text, **identity) for path, text in citable)
    slots: list[SourceField] = []
    for path, attribute in GEOGRAPHY_SLOTS:
        value = getattr(record, attribute, None)
        if value:
            slots.append(SourceField(field=path, text=value, **identity))
    document = None if record.document is None else Document.from_block(record.document)
    for page in () if document is None else document.pages:
        held = next((item for item in fields if item.field == page.field), None)
        if held is not None and any(item.char_end > len(held.text) for item in page.uncertain):
            raise RecordShapeError(
                f"{page.field} marks an uncertain range past the end of its own text"
            )
    return RecordContext(
        record=record,
        fields=fields,
        geography=tuple(slots),
        without_value=data_paths_without_value(obj),
        document=document,
    )
