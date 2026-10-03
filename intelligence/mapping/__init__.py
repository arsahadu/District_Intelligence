"""The record seam: CommonRecord-shaped input to Stage 1 Incident output."""

from __future__ import annotations

from intelligence.mapping.assembly import (
    DEFAULT_TIMEZONE,
    PROVIDER,
    STAGE_VERSIONS,
    IncidentDraft,
    MappingPolicy,
    derive_incident_id,
    map_record,
    time_precision_of,
    unresolved_field_count,
    verify_draft,
)
from intelligence.mapping.enrichment import (
    enrich_incident,
    find_actors,
    find_places,
    read_prose,
)
from intelligence.mapping.record_input import (
    CONTENT_PATH,
    DISTRICT_PATH,
    EVENT_TIME_PATH,
    IDENTITY_PATHS,
    LANGUAGE_PATH,
    RETRIEVED_AT_PATH,
    TITLE_PATH,
    CommonRecordLike,
    MappingError,
    RecordInput,
    RecordShapeError,
    UnreadableRecord,
    input_hash,
    read_record,
)

__all__ = [
    "CONTENT_PATH",
    "CommonRecordLike",
    "DEFAULT_TIMEZONE",
    "DISTRICT_PATH",
    "EVENT_TIME_PATH",
    "IDENTITY_PATHS",
    "LANGUAGE_PATH",
    "MappingError",
    "MappingPolicy",
    "PROVIDER",
    "RETRIEVED_AT_PATH",
    "RecordInput",
    "RecordShapeError",
    "STAGE_VERSIONS",
    "TITLE_PATH",
    "IncidentDraft",
    "UnreadableRecord",
    "derive_incident_id",
    "enrich_incident",
    "find_actors",
    "find_places",
    "input_hash",
    "map_record",
    "read_prose",
    "read_record",
    "time_precision_of",
    "unresolved_field_count",
    "verify_draft",
]
