"""The GIS resolution boundary: place values kept exactly as the record's own fields hold them.

Collected only from slots the CommonRecord declares, so no feed's field names are recognised here.
Resolves nothing - no canonical id, coordinate or resolution state. ``Location.resolution_state`` stays
the only place a resolution is recorded, and a resolver joins later by field path.
"""

from __future__ import annotations

from typing import Sequence

from intelligence.contract import ReviewState, SourceGeography
from intelligence.models.enums import ExtractionMethod, Modality
from intelligence.models.evidence import Evidence
from intelligence.spans import SourceField

GEOGRAPHY_ID_PREFIX = "geo"

#: Stated on every entry so a reader takes the value as the source's, not as a resolved place.
NOT_RESOLVED = "the record stated this value under this path; only GIS may set a canonical location"


def source_geography(
    fields: Sequence[SourceField],
) -> tuple[list[SourceGeography], list[Evidence]]:
    """One entry per geography field, each citing the field that holds it."""
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
