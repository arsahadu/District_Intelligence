"""Stage 6: the places and the parties the record's own text states, written into its incident."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional

from intelligence.config import LEXICON_VERSION
from intelligence.extraction import actors, boilerplate, place_expressions, places
from intelligence.extraction.mention_text import Prose, build_prose
from intelligence.mapping.assembly import (
    IncidentDraft,
    MappingPolicy,
    unresolved_field_count,
    verify_draft,
)
from intelligence.mapping.record_input import TITLE_PATH
from intelligence.models.evidence import Evidence
from intelligence.models.metadata import ReviewInfo

PROVIDER = "intelligence.mapping.enrichment"
STAGE_VERSION = "6"
SPATIAL_SECTION = "spatial.mentions"
ACTOR_SECTION = "actors"


def read_prose(draft: IncidentDraft, path: str, *, skip_numbers: bool = True) -> Prose:
    """One field as running text, in the coordinates of the untouched field."""
    source = draft.fields[path]
    if draft.split is not None and draft.split.source.field == path:
        return build_prose(source, draft.split.body, split=draft.split, skip_numbers=skip_numbers)
    fresh = boilerplate.split(source, lowercase=True)
    return build_prose(source, fresh.body, split=fresh, skip_numbers=skip_numbers)


def find_places(draft: IncidentDraft, prose: Mapping[str, Prose]) -> places.PlaceExtraction:
    readings = [(draft.fields[path], place_expressions.find(prose[path])) for path in prose]
    body = next((path for path in draft.record.text_paths if path != TITLE_PATH), None)
    return places.extract(
        readings,
        title_field=TITLE_PATH if TITLE_PATH in draft.fields else None,
        body_field=body,
        district_hint=draft.record.district,
    )


def find_actors(
    draft: IncidentDraft, prose: Mapping[str, Prose], found: places.PlaceExtraction
) -> actors.ActorExtraction:
    readings = [(draft.fields[path], prose[path]) for path in prose]
    return actors.extract(readings, place_mentions=found.mentions)


def enrich_incident(
    draft: IncidentDraft, policy: Optional[MappingPolicy] = None
) -> IncidentDraft:
    """Add what the text mentions and change nothing Stage 5 settled."""
    policy = MappingPolicy() if policy is None else policy
    incident = draft.incident
    prose = {path: read_prose(draft, path) for path in draft.record.text_paths}

    found = find_places(draft, prose)
    picked = find_actors(draft, prose, found)
    stated = found.with_roles(picked.affiliation_ids())

    settled: set[str] = set()
    if stated.mentions:
        settled.add(SPATIAL_SECTION)
    if picked.actors:
        settled.add(ACTOR_SECTION)
    warnings = tuple(dict.fromkeys((*draft.warnings, *stated.warnings(), *picked.warnings())))

    enriched = incident.model_copy(deep=True)
    enriched.evidence = _evidence(incident.evidence, stated, picked)
    enriched.spatial = places.apply(stated, incident.spatial)
    enriched.actors = picked.model_actors()
    enriched.confidence = incident.confidence.model_copy(
        update={
            "unresolved_field_count": unresolved_field_count(
                incident.severity, enriched.spatial, settled=frozenset(settled)
            )
        }
    )
    enriched.processing = incident.processing.model_copy(
        update={
            "stage_versions": {
                **incident.processing.stage_versions,
                "places": STAGE_VERSION,
                "actors": STAGE_VERSION,
                "mention_lexicon": LEXICON_VERSION,
            },
            "warnings": list(warnings),
        }
    )
    enriched.review = ReviewInfo()
    enriched.apply_review_flags(policy.low_confidence_threshold)

    result = replace(draft, incident=enriched, places=stated, actors=picked, warnings=warnings)
    return replace(result, checks=verify_draft(result))


def _evidence(
    existing: list[Evidence],
    stated: places.PlaceExtraction,
    picked: actors.ActorExtraction,
) -> list[Evidence]:
    collected: dict[str, Evidence] = {evidence.evidence_id: evidence for evidence in existing}
    for evidence in (*stated.evidence(), *picked.evidence()):
        collected.setdefault(evidence.evidence_id, evidence)
    return list(collected.values())


__all__ = [
    "PROVIDER",
    "STAGE_VERSION",
    "enrich_incident",
    "find_actors",
    "find_places",
    "read_prose",
]
