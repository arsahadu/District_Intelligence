"""Stage 8b: whether the situation is still running and how serious it was, from what the source said."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from intelligence.config import OPERATIONS_LEXICON_VERSION
from intelligence.config import operations_cues as vocabulary
from intelligence.extraction import severity as severity_reading
from intelligence.extraction import status as status_reading
from intelligence.extraction.severity import DeclaredSeverity
from intelligence.extraction.spans import SourceField
from intelligence.extraction.status import DeclaredStatus
from intelligence.mapping.assembly import (
    SEVERITY_PATH,
    STATUS_PATH,
    IncidentDraft,
    MappingPolicy,
    unresolved_field_count,
    verify_draft,
)
from intelligence.mapping.enrichment import ACTOR_SECTION, SPATIAL_SECTION, read_prose
from intelligence.models.enums import ExtractionMethod, RelevanceState
from intelligence.models.evidence import Evidence
from intelligence.models.metadata import ConfidenceSummary, ReviewInfo
from intelligence.models.severity import Severity
from intelligence.models.spatial import SpatialHint
from intelligence.models.status import OperationalStatusInfo

PROVIDER = "intelligence.mapping.operations"
STAGE_VERSION = "8b"
STATUS_SECTION = "operational_status"
OBSERVATION_SECTION = "observations"
CONFIDENCE_VERBATIM = 1.0
RULE_WITH_OPERATIONS = (
    "min over the components the pipeline established; an unresolved status or severity is "
    "recorded as a gap in unresolved_field_count, never scored"
)

NOT_AN_EVENT = (
    "this record states no event, so nothing here has an operational status to report"
)


def _field(draft: IncidentDraft, path: str) -> Optional[SourceField]:
    """The record's own slot, already registered by Stage 5 when the record carried one."""
    return draft.fields.get(path)


def _cited(source: SourceField) -> Evidence:
    """Re-quote a field's own value; the deterministic id makes this the evidence Stage 5 kept."""
    return source.evidence(
        source.text,
        method=ExtractionMethod.SOURCE_METADATA,
        confidence=CONFIDENCE_VERBATIM,
        notes=(
            f"{source.field} as the source recorded it, read as a statement about the case "
            "rather than about this pipeline"
        ),
    )


def _declared_status(draft: IncidentDraft) -> tuple[Optional[DeclaredStatus], list[Evidence], list[str]]:
    """What the record's own status field says, when it says a condition at all."""
    source = _field(draft, STATUS_PATH)
    if source is None:
        return None, [], []
    state = vocabulary.declared_status(source.text)
    evidence = _cited(source)
    if state is None:
        return None, [evidence], [
            f"status {source.text!r} names no operational condition, so the wording in the "
            "record's own text decided the status"
        ]
    return (
        DeclaredStatus(
            state=state, value=source.text, field=STATUS_PATH, evidence_id=evidence.evidence_id
        ),
        [evidence],
        [],
    )


def _declared_severity(
    draft: IncidentDraft,
) -> tuple[Optional[DeclaredSeverity], list[Evidence], list[str]]:
    """What the record's own severity field says, on the only scales this module trusts."""
    source = _field(draft, SEVERITY_PATH)
    if source is None:
        return None, [], []
    level = vocabulary.declared_severity(source.text)
    evidence = _cited(source)
    if level is None:
        return None, [evidence], []
    return (
        DeclaredSeverity(
            level=level,
            value=source.text,
            field=SEVERITY_PATH,
            evidence_id=evidence.evidence_id,
            source_id=draft.record.source_id,
        ),
        [evidence],
        [],
    )


def assess_incident(
    draft: IncidentDraft, policy: Optional[MappingPolicy] = None
) -> IncidentDraft:
    """Write the status and severity the record's own evidence supports, and decide nothing else."""
    policy = MappingPolicy() if policy is None else policy
    incident = draft.incident
    record = draft.record

    readings = [
        (draft.fields[path], read_prose(draft, path, skip_numbers=False))
        for path in record.text_paths
        if path in draft.fields
    ]
    extraction, counts = severity_reading.extract(readings)
    statements = status_reading.extract(readings)

    declared_status, status_cited, status_notes = _declared_status(draft)
    declared_severity, severity_cited, _ = _declared_severity(draft)

    if incident.relevance.state is RelevanceState.NOT_INCIDENT:
        decided = OperationalStatusInfo.unknown(NOT_AN_EVENT)
    else:
        decided = status_reading.decide_status(
            record.record_id, statements.matches, declared_status
        )
    severity, observations = severity_reading.assess_severity(
        record.record_id,
        extraction,
        counts,
        declared=declared_severity,
        source_value=record.severity,
        carried_evidence=incident.severity.evidence_ids,
    )

    settled: set[str] = set()
    if incident.spatial.mentions:
        settled.add(SPATIAL_SECTION)
    if incident.actors:
        settled.add(ACTOR_SECTION)
    if observations:
        settled.add(OBSERVATION_SECTION)

    warnings = tuple(
        dict.fromkeys(
            (
                *draft.warnings,
                *extraction.warnings(),
                *statements.warnings(),
                *status_notes,
            )
        )
    )

    assessed = incident.model_copy(deep=True)
    assessed.evidence = _evidence(
        incident.evidence, extraction, statements, counts, status_cited, severity_cited
    )
    assessed.severity = severity
    assessed.operational_status = decided
    assessed.observations = observations
    assessed.confidence = _confidence(
        incident.confidence, severity, decided, incident.spatial, settled
    )
    assessed.processing = incident.processing.model_copy(
        update={
            "stage_versions": {
                **incident.processing.stage_versions,
                STATUS_SECTION: STAGE_VERSION,
                "severity": STAGE_VERSION,
                "observations": STAGE_VERSION,
                "operations_lexicon": OPERATIONS_LEXICON_VERSION,
            },
            "warnings": list(warnings),
        }
    )
    assessed.review = ReviewInfo()
    assessed.apply_review_flags(policy.low_confidence_threshold)

    result = replace(
        draft, incident=assessed, warnings=warnings
    )
    return replace(result, checks=verify_draft(result))


def _confidence(
    previous: ConfidenceSummary,
    severity: Severity,
    decided: OperationalStatusInfo,
    spatial: SpatialHint,
    settled: set[str],
) -> ConfidenceSummary:
    """Keep what earlier stages established; add this stage's only where it reached a state."""
    components = dict(previous.components)
    if severity.confidence is not None:
        components["severity"] = severity.confidence
    if decided.confidence is not None:
        components[STATUS_SECTION] = decided.confidence
    count = unresolved_field_count(severity, spatial, settled=frozenset(settled))
    if not components:
        return ConfidenceSummary(unresolved_field_count=count)
    return ConfidenceSummary(
        overall=min(components.values()),
        components=components,
        rule=RULE_WITH_OPERATIONS,
        unresolved_field_count=count,
    )


def _evidence(
    existing: list[Evidence],
    extraction: severity_reading.SeverityExtraction,
    statements: status_reading.StatusExtraction,
    counts: Sequence[severity_reading.QuantityMatch],
    status_cited: list[Evidence],
    severity_cited: list[Evidence],
) -> list[Evidence]:
    collected: dict[str, Evidence] = {evidence.evidence_id: evidence for evidence in existing}
    for evidence in (
        *status_cited,
        *severity_cited,
        *statements.evidence(),
        *extraction.evidence(),
        *(count.evidence for count in counts if count.evidence is not None),
    ):
        collected.setdefault(evidence.evidence_id, evidence)
    return list(collected.values())


__all__ = [
    "NOT_AN_EVENT",
    "OBSERVATION_SECTION",
    "PROVIDER",
    "STAGE_VERSION",
    "STATUS_SECTION",
    "assess_incident",
]
