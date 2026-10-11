from __future__ import annotations

from typing import Sequence

from intelligence.contract import Location, ReviewState, SourceGeography
from intelligence.models.enums import (
    ExtractionMethod,
    MentionRole,
    MentionType,
    Modality,
    ResolutionState,
)
from intelligence.models.evidence import Evidence
from intelligence.records import DISTRICT_PATH, RAW_TEXT_PATH
from intelligence.spans import SourceField

GEOGRAPHY_ID_PREFIX = "geo"

NOT_RESOLVED = "the record stated this value under this path; only GIS may set a canonical location"

LOCATION_ID_PREFIX = "loc"

LOCATION_SLOTS: dict[str, tuple[MentionType, str]] = {
    RAW_TEXT_PATH: (
        MentionType.UNKNOWN,
        "the record wrote this place as free text and never said what kind of place it is, so the "
        "kind is left unknown rather than guessed from how the name reads",
    ),
    DISTRICT_PATH: (
        MentionType.DISTRICT,
        "the record named this as its own district; only GIS may set a canonical id, coordinates "
        "or boundary",
    ),
}


def source_geography(
    fields: Sequence[SourceField],
) -> tuple[list[SourceGeography], list[Evidence]]:
    entries: list[SourceGeography] = []
    evidence: list[Evidence] = []
    for index, field in enumerate(fields, start=1):
        item = field.metadata_evidence(modality=Modality.TEXT, notes=NOT_RESOLVED)
        evidence.append(item)
        entries.append(
            SourceGeography(
                geography_id=f"{GEOGRAPHY_ID_PREFIX}-{index}",
                field=field.field,
                value=field.text,
                method=ExtractionMethod.SOURCE_METADATA,
                review=ReviewState.ACCEPTED,
                evidence_ids=[item.evidence_id],
                notes=NOT_RESOLVED,
            )
        )
    return entries, evidence


def source_locations(entries: Sequence[SourceGeography]) -> list[Location]:
    locations: list[Location] = []
    for index, entry in enumerate(entries, start=1):
        slot = LOCATION_SLOTS.get(entry.field)
        if slot is None:
            continue
        kind, reason = slot
        locations.append(
            Location(
                location_id=f"{LOCATION_ID_PREFIX}-{index}",
                text=entry.value,
                location_type=kind,
                role=MentionRole.EVENT_LOCATION,
                district=entry.value if entry.field == DISTRICT_PATH else None,
                resolution_state=ResolutionState.PENDING_GIS,
                method=entry.method,
                review=entry.review,
                evidence_ids=list(entry.evidence_ids),
                notes=reason,
            )
        )
    return locations
