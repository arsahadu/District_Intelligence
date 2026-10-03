"""Stage 7: what this record is about, written into the incident Stage 6 left alone."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional

from intelligence.config import CUE_LEXICON_VERSION, TAXONOMY_VERSION
from intelligence.extraction import event_classification, cues
from intelligence.extraction.cues import CueExtraction
from intelligence.extraction.spans import SourceField
from intelligence.mapping.assembly import (
    IncidentDraft,
    MappingPolicy,
    unresolved_field_count,
    verify_draft,
)
from intelligence.mapping.enrichment import ACTOR_SECTION, SPATIAL_SECTION, read_prose
from intelligence.mapping.record_input import DISTRICT_PATH
from intelligence.models.enums import ExtractionMethod, EventType, RelevanceState
from intelligence.models.evidence import Evidence
from intelligence.models.incident import Incident
from intelligence.models.metadata import ConfidenceSummary, ReviewInfo

PROVIDER = "intelligence.mapping.classifier"
STAGE_VERSION = "7"
RELEVANCE_SECTION = "relevance"
CLASSIFICATION_SECTION = "classification"
RECORD_TYPE_PATH = "record_type"
SOURCE_TYPE_PATH = "source_type"
CONFIDENCE_VERBATIM = 1.0
RULE_WITH_CLASSIFICATION = (
    "min over the components the pipeline established; sections left unresolved are counted in "
    "unresolved_field_count, never scored"
)


def read_cues(draft: IncidentDraft) -> CueExtraction:
    """Every event word the record's own readable fields printed."""
    readings = [(draft.fields[path], read_prose(draft, path)) for path in draft.record.text_paths]
    if not readings:
        return CueExtraction(record_id=draft.record.record_id)
    return cues.extract(readings)


def _identity_fields(draft: IncidentDraft) -> dict[str, SourceField]:
    """The record's own kind, held as a field so a decision can cite it without inventing a span."""
    record = draft.record
    stated = {RECORD_TYPE_PATH: record.record_type, SOURCE_TYPE_PATH: record.source_type}
    fields: dict[str, SourceField] = {}
    for path, value in stated.items():
        existing = draft.fields.get(path)
        if existing is not None:
            if existing.text != value:
                raise ValueError(
                    f"field {path!r} was read twice with different text: {existing.text!r} and "
                    f"{value!r}; one record holds one version of a field"
                )
            fields[path] = existing
            continue
        fields[path] = SourceField(
            record_id=record.record_id,
            source_id=record.source_id,
            source_type=record.source_type,
            field=path,
            text=value,
            source_url=record.source_url,
            raw_reference=record.raw_reference,
            retrieved_at=record.retrieved_at,
        )
    return fields


def _citations(
    fields: Mapping[str, SourceField],
) -> tuple[list[Evidence], tuple[str, ...], tuple[str, ...]]:
    """Field-level provenance for the record's kind, and the district Stage 5 already quoted."""
    evidence: list[Evidence] = []
    kind_ids: list[str] = []
    for path, note in (
        (
            RECORD_TYPE_PATH,
            "record_type as the source named it, which states what kind of record this is",
        ),
        (
            SOURCE_TYPE_PATH,
            "source_type as the feed declared it, which states which feed this came from",
        ),
    ):
        source = fields.get(path)
        if source is None:
            continue
        entry = source.metadata_evidence(confidence=CONFIDENCE_VERBATIM, notes=note)
        evidence.append(entry)
        kind_ids.append(entry.evidence_id)

    district = fields.get(DISTRICT_PATH)
    district_ids: list[str] = []
    if district is not None:
        entry = district.evidence(
            district.text,
            method=ExtractionMethod.SOURCE_METADATA,
            confidence=CONFIDENCE_VERBATIM,
            notes=(
                "the district this feed asserts it covers, quoted from the record's own field"
            ),
        )
        evidence.append(entry)
        district_ids.append(entry.evidence_id)
    return evidence, tuple(kind_ids), tuple(district_ids)


def classify_incident(
    draft: IncidentDraft, policy: Optional[MappingPolicy] = None
) -> IncidentDraft:
    """Decide relevance and event type from the record's own evidence, and change nothing else."""
    policy = MappingPolicy() if policy is None else policy
    incident = draft.incident
    record = draft.record

    found = read_cues(draft)
    fields = {**draft.fields, **_identity_fields(draft)}
    cited, kind_ids, district_ids = _citations(fields)
    anchor = event_classification.anchor_of(incident.spatial, district_ids)
    scores = event_classification.score_types(found, record.record_type)
    relevance = event_classification.decide_relevance(
        source_type=record.source_type,
        record_type=record.record_type,
        cues=found,
        anchor=anchor,
        kind_ids=kind_ids,
    )
    classification = event_classification.decide_event_type(
        scores=scores, relevance=relevance, actors=incident.actors
    )

    settled: set[str] = set()
    if incident.spatial.mentions:
        settled.add(SPATIAL_SECTION)
    if incident.actors:
        settled.add(ACTOR_SECTION)
    if relevance.state in (RelevanceState.INCIDENT, RelevanceState.NOT_INCIDENT):
        settled.add(RELEVANCE_SECTION)
    if classification.event_type is not EventType.UNRESOLVED:
        settled.add(CLASSIFICATION_SECTION)

    unresolved = ()
    if classification.event_type is EventType.UNRESOLVED and classification.notes:
        unresolved = (f"event type left unresolved: {classification.notes}",)
    warnings = tuple(dict.fromkeys((*draft.warnings, *found.warnings(), *unresolved)))

    classified = incident.model_copy(deep=True)
    classified.evidence = _evidence(incident.evidence, found, cited)
    classified.relevance = relevance
    classified.classification = classification
    classified.confidence = _confidence(incident.confidence, classification, settled, incident)
    classified.processing = incident.processing.model_copy(
        update={
            "stage_versions": {
                **incident.processing.stage_versions,
                "classification": STAGE_VERSION,
                "relevance": STAGE_VERSION,
                "cue_lexicon": CUE_LEXICON_VERSION,
                "taxonomy": TAXONOMY_VERSION,
            },
            "warnings": list(warnings),
        }
    )
    classified.review = ReviewInfo()
    classified.apply_review_flags(policy.low_confidence_threshold)

    result = replace(
        draft, incident=classified, fields=fields, warnings=warnings
    )
    return replace(result, checks=verify_draft(result))


def _confidence(
    previous: ConfidenceSummary,
    classification,
    settled: set[str],
    incident: Incident,
) -> ConfidenceSummary:
    """Keep the components earlier stages established; add this stage's only when it decided."""
    components = dict(previous.components)
    if classification.confidence is not None:
        components["classification"] = classification.confidence
    count = unresolved_field_count(
        incident.severity, incident.spatial, settled=frozenset(settled)
    )
    if not components:
        return ConfidenceSummary(unresolved_field_count=count)
    return ConfidenceSummary(
        overall=min(components.values()),
        components=components,
        rule=RULE_WITH_CLASSIFICATION,
        unresolved_field_count=count,
    )


def _evidence(
    existing: list[Evidence], found: CueExtraction, cited: list[Evidence]
) -> list[Evidence]:
    collected: dict[str, Evidence] = {evidence.evidence_id: evidence for evidence in existing}
    for evidence in (*cited, *found.evidence()):
        collected.setdefault(evidence.evidence_id, evidence)
    return list(collected.values())


__all__ = [
    "CLASSIFICATION_SECTION",
    "PROVIDER",
    "RECORD_TYPE_PATH",
    "RELEVANCE_SECTION",
    "SOURCE_TYPE_PATH",
    "STAGE_VERSION",
    "classify_incident",
    "read_cues",
]
