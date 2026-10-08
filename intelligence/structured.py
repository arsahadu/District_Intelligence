"""Structured records are copied from the fields they hold. No model is asked."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from intelligence.context import RecordContext, build_context
from intelligence.contract import (
    Claim,
    ContextFact,
    ContextType,
    Generation,
    Incident,
    RecordKind,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.geography import source_geography
from intelligence.models.enums import ExtractionMethod, Modality
from intelligence.models.evidence import Evidence
from intelligence.records import CONTENT_PATH
from intelligence.spans import SourceField

DATA_PREFIX = "data."
TITLE_PATH = "title"

#: Stated on every structured result so no reader takes this for a model's answer.
PROVIDER = "intelligence.structured"
MODEL = "none"
MAPPING_VERSION = "structured-1"
NO_MODEL = "no language model was called: every value here was copied from the record's own fields"

VALIDATOR_NAME = "intelligence.structured"

#: What the feed's own ``record_type`` says the record is.
KIND_BY_RECORD_TYPE: dict[str, tuple[RecordKind, ContextType]] = {
    "forecast": (RecordKind.FORECAST, ContextType.WEATHER_FORECAST),
    "market_price": (RecordKind.CONTEXT, ContextType.MARKET_PRICE),
}

#: For a structured feed whose record_type this table has no name for. A weather page states what
#: the weather is likely to do; nothing else may be assumed, so an unknown shape is just context.
KIND_BY_SOURCE_TYPE: dict[str, tuple[RecordKind, ContextType]] = {
    "weather": (RecordKind.FORECAST, ContextType.WEATHER_FORECAST),
}
UNKNOWN_SHAPE = (RecordKind.CONTEXT, ContextType.OTHER)


def _classify(record_type: str, source_type: str) -> tuple[RecordKind, Optional[ContextType]]:
    found = KIND_BY_RECORD_TYPE.get(str(record_type).strip().casefold())
    if found is not None:
        return found
    return KIND_BY_SOURCE_TYPE.get(str(source_type).strip().casefold(), UNKNOWN_SHAPE)


def process_record(
    record: Any,
    *,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Incident:
    """One structured CommonRecord, read with no provider and no prompt."""
    return process_context(build_context(record, text_keys=text_keys, extra_keys=extra_keys))


def process_context(context: RecordContext) -> Incident:
    """Every value the record carries becomes a fact pointing at the field that holds it.

    Nothing outside those fields is filled in, and the record's own geography is handed to the GIS
    boundary beside them, under the same paths and the same words.
    """
    record = context.record
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    facts: list[ContextFact] = []

    def cite(field: SourceField) -> list[str]:
        item = field.metadata_evidence(modality=Modality.STRUCTURED_DATA)
        if not any(existing.evidence_id == item.evidence_id for existing in evidence):
            evidence.append(item)
        return [item.evidence_id]

    title = context.field(TITLE_PATH)
    if title is not None:
        claims.append(
            Claim(
                field=TITLE_PATH,
                value=title.text,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=cite(title),
                review=ReviewState.ACCEPTED,
                notes="the record carries this title itself",
            )
        )
    if record.event_time is not None:
        claims.append(
            Claim(
                field="event_time",
                value=record.event_time.isoformat(),
                method=ExtractionMethod.SOURCE_METADATA,
                review=ReviewState.ACCEPTED,
                notes="copied from the record's own event_time",
            )
        )

    for index, field in enumerate(
        (item for item in context.fields if item.field.startswith(DATA_PREFIX)), start=1
    ):
        if not field.text.strip():
            continue
        facts.append(
            ContextFact(
                fact_id=f"fact-{index}",
                field=field.field,
                value=field.text,
                method=ExtractionMethod.SOURCE_METADATA,
                review=ReviewState.ACCEPTED,
                evidence_ids=cite(field),
                notes="the record holds this value in this field",
            )
        )

    grounded = bool(claims or facts)
    record_kind, context_type = _classify(record.record_type, record.source_type)
    geography, geography_evidence = source_geography(context.geography)
    evidence.extend(geography_evidence)

    return Incident(
        schema_version=SCHEMA_VERSION,
        incident_id=f"INC-{record.record_id}",
        provenance=context.provenance().model_copy(update={"modality": Modality.STRUCTURED_DATA}),
        record_kind=record_kind,
        context_type=context_type,
        title=title.text if title is not None else None,
        event_time=record.event_time,
        context_facts=facts,
        source_geography=geography,
        evidence=evidence,
        claims=claims,
        validation=Validation(
            state=ReviewState.ACCEPTED if grounded else ReviewState.UNRESOLVED,
            validator=VALIDATOR_NAME,
            checked_at=datetime.now(),
        ),
        generation=Generation(
            provider=PROVIDER,
            model=MODEL,
            prompt_version=MAPPING_VERSION,
            input_hash=context.context_hash(),
            generated_at=datetime.now(),
            warnings=[NO_MODEL],
        ),
    )
