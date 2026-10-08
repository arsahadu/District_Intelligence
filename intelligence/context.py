"""A CommonRecord-shaped input becomes an LLM-ready context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from intelligence.contract import Provenance
from intelligence.records import CONTENT_PATH, DISTRICT_PATH, RAW_TEXT_PATH, STATE_PATH
from intelligence.records import RecordInput, input_hash, read_record
from intelligence.spans import SourceField, compute_field_hash

FIELD_HEADING = "field"
DATA_PREFIX = "data."

#: The geography the record declared about itself, in the platform's own slot names. These are the
#: CommonRecord contract's fields, not a feed's, so no source field name is ever looked for here.
GEOGRAPHY_SLOTS: tuple[tuple[str, str], ...] = (
    (RAW_TEXT_PATH, "location_raw_text"),
    (DISTRICT_PATH, "district"),
    (STATE_PATH, "state"),
)

#: A language tag labels the record; quoting it as a fact about the world means nothing.
INHERITED_KEYS = frozenset({"language"})


def data_field_paths(obj: Any) -> tuple[str, ...]:
    """Every ``data`` key this record actually carries, in a stable order."""
    data = getattr(obj, "data", None)
    if data is None and isinstance(obj, Mapping):
        data = obj.get("data")
    if not isinstance(data, Mapping):
        return ()
    return tuple(
        f"{DATA_PREFIX}{key}"
        for key in sorted(str(name) for name in data)
        if key not in INHERITED_KEYS
    )


@dataclass(frozen=True)
class RecordContext:
    """Everything one record offers a model, plus the text a span may be cut from."""

    record: RecordInput
    fields: tuple[SourceField, ...]

    #: Citable like any other field, and never sent to the model: this is what the record said
    #: about place, which the GIS boundary reads rather than the prompt.
    geography: tuple[SourceField, ...] = ()

    def field(self, path: str) -> Optional[SourceField]:
        return next((item for item in self.fields if item.field == path), None)

    def text_of(self, path: str) -> Optional[str]:
        found = self.field(path)
        return None if found is None else found.text

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.field for item in self.fields)

    def prompt_block(self) -> str:
        """The labelled text the model reads. Offsets count from the start of each field."""
        parts = [f"{FIELD_HEADING} {item.field}\n{item.text}" for item in self.fields]
        return "\n\n".join(parts)

    def context_hash(self) -> str:
        """Over the fields as sent, so a re-run can prove the model saw the same text."""
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
            language_hint=record.language_hint,
            district_hint=record.district,
            state_hint=record.state,
            declared_severity=record.severity,
            declared_status=record.status,
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
            "context_hash": self.context_hash(),
        }


def build_context(
    obj: Any,
    *,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> RecordContext:
    """Read a record structurally and keep every field it offers citable and whole, so spans replay.

    The record's own ``location`` slot is citable but stays out of the fields the model reads: it is
    what the record declared about place, so the GIS boundary takes it, not a prompt.
    """
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
    return RecordContext(record=record, fields=fields, geography=tuple(slots))
