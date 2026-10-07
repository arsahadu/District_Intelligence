"""Stage 8b through the seam it is installed at: whether the situation was still running, and how serious the source said it was."""

from __future__ import annotations

from typing import Any

from intelligence.config import OPERATIONS_LEXICON_VERSION, operations_integrity_errors
from intelligence.config import operations_cues as vocabulary
from intelligence.extraction.status import NO_SIGNAL
from intelligence.mapping import feed_policies
from intelligence.mapping.assembly import map_record
from intelligence.mapping.classifier import classify_incident
from intelligence.mapping.enrichment import enrich_incident
from intelligence.mapping.operations import STAGE_VERSION, assess_incident
from intelligence.models.enums import (
    EventType,
    ExtractionMethod,
    ObservationKind,
    OperationalState,
    RelevanceState,
    ReviewReason,
    SeverityCueCategory,
    SeverityLevel,
    SignalTier,
    SpanValidation,
)
from intelligence.tests import record_fixtures as F

CLEAN = (SpanValidation.VALIDATED, SpanValidation.NOT_APPLICABLE)

STILL_RUNNING = (
    "Rescue operations in Madurai district continue and officials said the search for the "
    "missing driver is still underway."
)
WATER_CLEARED = (
    "The blocked culvert near Madurai has been cleared and bus services on the highway are "
    "back to normal."
)
COMPLAINT_CLOSED = (
    "The corporation drain complaint in Madurai is officially closed after the works "
    "completed last week."
)
MEETING_NOTED = "A ward committee meeting was held in Madurai on Friday afternoon."
BOTH_STATES = (
    "The drain in Madurai has been cleared, but the flooding continues in three streets of "
    "the ward."
)
MINOR_INCONVENIENCE = (
    "Water stayed in one low-lying street in Madurai and residents faced inconvenience for "
    "an hour."
)
DISRUPTION_HELD = "Power supply was disrupted for several streets in Madurai after the rain."
WALL_INJURIES = "Three persons were injured when a compound wall collapsed in Madurai."
BUS_FATALITIES = "Two persons died after a bus fell into a canal in Madurai district."
EVACUATION_CALLED = (
    "More than 200 people were evacuated from villages near Madurai as the river rose."
)
POPULATION_STATED = (
    "About 5000 people were affected by the flooding in Madurai district on Saturday."
)
EVENT_TYPE_ONLY = "Urban waterlogging was reported in Madurai corporation limits on Friday."


def synth(
    content: str,
    *,
    title: str = "District report",
    language: str = "en",
    **fields: Any,
) -> dict[str, Any]:
    record_id = str(fields.pop("record_id", "SYNTH-OPS-0001"))
    return F.base_record(
        record_id=record_id,
        title=title,
        event_time=None,
        location={"raw_text": "Madurai", "district": "Madurai", "state": "Tamil Nadu"},
        data={"content": content, "language": language},
        source_url=None,
        raw_reference=None,
        **fields,
    )


def assess(payload: dict[str, Any]):
    policy = feed_policies.policy_for(payload["source_type"])
    classified = classify_incident(
        enrich_incident(map_record(payload, policy), policy), policy
    )
    return classified, assess_incident(classified, policy)


def decided(content: str, **fields: Any):
    return assess(synth(content, **fields))[1].incident


def cues_of(incident) -> list[str]:
    return [signal.cue_text for signal in incident.severity.signals]


def test_english_ongoing_wording_makes_the_status_ongoing():
    status = decided(STILL_RUNNING).operational_status
    assert status.state is OperationalState.ONGOING
    assert status.method is ExtractionMethod.DICTIONARY
    assert status.confidence == 0.8
    stated = [signal for signal in status.signals if signal.tier is SignalTier.STATED]
    assert stated and {signal.state for signal in stated} == {OperationalState.ONGOING}
    assert any("continue" in signal.cue_text for signal in stated)


def test_english_resolved_wording_makes_the_status_resolved():
    status = decided(WATER_CLEARED).operational_status
    assert status.state is OperationalState.RESOLVED
    assert {signal.state for signal in status.signals} == {OperationalState.RESOLVED}
    assert any(signal.cue_text == "back to normal" for signal in status.signals)


def test_english_closed_wording_makes_the_status_closed():
    status = decided(COMPLAINT_CLOSED).operational_status
    assert status.state is OperationalState.CLOSED
    assert status.reason is not None and "closed" in status.reason


def test_a_record_that_never_said_whether_it_was_running_stays_unknown():
    status = decided(MEETING_NOTED).operational_status
    assert status.state is OperationalState.UNKNOWN
    assert status.confidence is None
    assert status.signals == []
    assert status.evidence_ids == []
    assert NO_SIGNAL in (status.notes or "")


def test_tamil_monitoring_wording_makes_the_status_ongoing():
    incident = assess(F.waterlogging())[1].incident
    status = incident.operational_status
    assert status.state is OperationalState.ONGOING
    assert [signal.cue_text for signal in status.signals] == ["கண்காணித்து வருகின்றனர்"]
    assert status.signals[0].tier is SignalTier.STATED
    assert status.confidence == 0.8


def test_two_states_stated_alike_leave_the_status_unknown_and_under_review():
    incident = decided(BOTH_STATES)
    status = incident.operational_status
    assert status.state is OperationalState.UNKNOWN
    assert status.confidence is None
    assert sorted(state.value for state in status.conflicting_states) == [
        "ongoing",
        "resolved",
    ]
    assert status.is_contested
    assert {signal.state for signal in status.signals} == {
        OperationalState.ONGOING,
        OperationalState.RESOLVED,
    }
    assert ReviewReason.STATUS_CONFLICT in incident.review.reasons
    assert status.evidence_ids


def test_the_event_type_alone_decides_no_status():
    incident = decided(EVENT_TYPE_ONLY)
    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.classification.event_type is EventType.URBAN_WATERLOGGING
    assert incident.operational_status.state is OperationalState.UNKNOWN


def test_a_wording_free_record_has_no_severity_level():
    incident = decided(MEETING_NOTED)
    severity = incident.severity
    assert severity.level is SeverityLevel.UNRESOLVED
    assert severity.score is None
    assert severity.confidence is None
    assert severity.signals == []
    assert severity.is_authoritative is False
    assert ReviewReason.UNRESOLVED_SEVERITY in incident.review.reasons


def test_localized_inconvenience_reads_as_low():
    incident = decided(MINOR_INCONVENIENCE)
    assert incident.severity.level is SeverityLevel.LOW
    assert "inconvenience" in cues_of(incident)


def test_a_disruption_the_source_called_moderate_reads_as_moderate():
    incident = decided(DISRUPTION_HELD)
    assert incident.severity.level is SeverityLevel.MODERATE
    assert "disrupted" in cues_of(incident)


def test_injuries_read_as_high():
    incident = decided(WALL_INJURIES)
    severity = incident.severity
    assert severity.level is SeverityLevel.HIGH
    categories = {signal.category for signal in severity.signals}
    assert SeverityCueCategory.INJURY in categories
    assert severity.is_authoritative is False


def test_an_explicit_fatality_reads_as_critical():
    incident = decided(BUS_FATALITIES)
    severity = incident.severity
    assert severity.level is SeverityLevel.CRITICAL
    assert SeverityCueCategory.FATALITY in {signal.category for signal in severity.signals}


def test_an_evacuation_is_worth_a_moderate_and_a_count():
    incident = decided(EVACUATION_CALLED)
    severity = incident.severity
    assert severity.level is SeverityLevel.MODERATE
    assert any(signal.cue_text == "evacuated" for signal in severity.signals)
    assert [(o.kind, o.value_numeric) for o in incident.observations] == [
        (ObservationKind.DISPLACED_PERSONS, 200.0)
    ]


def test_a_stated_affected_population_lifts_the_level_by_itself():
    incident = decided(POPULATION_STATED)
    assert incident.severity.level is SeverityLevel.HIGH
    assert [(o.kind, o.value_numeric) for o in incident.observations] == [
        (ObservationKind.AFFECTED_PERSONS, 5000.0)
    ]
    assert any(signal.magnitude_observed == "5000 people" for signal in incident.severity.signals)


def test_the_event_type_alone_decides_no_severity():
    incident = decided(EVENT_TYPE_ONLY)
    assert incident.classification.event_type is EventType.URBAN_WATERLOGGING
    assert incident.severity.level is SeverityLevel.UNRESOLVED
    assert incident.severity.score is None


def test_status_and_severity_evidence_ids_are_held_and_reproduce_their_source():
    _, draft = assess(F.waterlogging())
    incident = draft.incident
    cited = set(incident.severity.evidence_ids) | set(incident.operational_status.evidence_ids)
    assert cited
    held = {evidence.evidence_id: evidence for evidence in incident.evidence}
    assert cited <= set(held)
    for evidence_id in cited:
        evidence = held[evidence_id]
        assert evidence.has_span
        source = draft.fields[evidence.field]
        assert source.text[evidence.char_start : evidence.char_end] == evidence.quote
    assert draft.all_spans_verify
    assert all(check.validation in CLEAN for check in draft.checks)


def test_the_record_own_status_field_is_a_declared_statement():
    incident = decided(MEETING_NOTED, status="closed")
    status = incident.operational_status
    assert status.state is OperationalState.CLOSED
    assert status.method is ExtractionMethod.SOURCE_METADATA
    assert status.confidence == 0.9
    assert {signal.tier for signal in status.signals} == {SignalTier.DECLARED}


def test_a_feed_colour_scale_is_the_only_authoritative_severity():
    incident = decided(
        "IMD has issued an orange alert for heavy rain in Madurai district.", severity="Orange Alert"
    )
    severity = incident.severity
    assert severity.level is SeverityLevel.HIGH
    assert severity.is_authoritative is True
    assert severity.method is ExtractionMethod.SOURCE_METADATA
    assert severity.confidence == 0.9
    assert "orange alert" in (severity.confirmed_by or "").lower()


def test_a_record_still_carries_no_authoritative_severity_without_its_own_scale():
    incident = decided(BUS_FATALITIES)
    assert incident.severity.is_authoritative is False
    assert incident.severity.confidence <= 0.9


def test_full_chain_leaves_the_capture_evidence_verifiable():
    for payload in (F.tamil_article(), F.waterlogging(), F.power_outage(), F.temple_closure()):
        classified, draft = assess(payload)
        incident = draft.incident
        assert draft.all_spans_verify
        assert incident.processing.stage_versions["operational_status"] == STAGE_VERSION
        assert incident.processing.stage_versions["operations_lexicon"] == OPERATIONS_LEXICON_VERSION
        referenced = set(incident.severity.evidence_ids) | set(
            incident.operational_status.evidence_ids
        )
        assert referenced <= {evidence.evidence_id for evidence in incident.evidence}


def test_stage_8b_writes_nothing_but_severity_status_and_observations():
    classified, draft = assess(F.waterlogging())
    before, after = classified.incident, draft.incident
    for section in (
        "relevance",
        "classification",
        "spatial",
        "actors",
        "language",
        "title",
        "summary",
        "event_time",
        "reported_at",
        "origin",
        "dedup",
    ):
        assert getattr(before, section) == getattr(after, section)
    assert before.evidence == after.evidence[: len(before.evidence)]
    assert after.severity is not before.severity
    assert after.operational_status is not before.operational_status


def test_the_example_record_behaves_as_the_policy_requires():
    incident = assess(F.waterlogging())[1].incident
    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.classification.event_type is EventType.URBAN_WATERLOGGING
    assert incident.operational_status.state is OperationalState.ONGOING
    assert incident.severity.level not in {SeverityLevel.HIGH, SeverityLevel.CRITICAL}
    assert incident.severity.level in {SeverityLevel.LOW, SeverityLevel.UNRESOLVED}
    assert incident.severity.confidence is None or incident.severity.confidence <= 0.85
    assert incident.severity.is_authoritative is False


def test_wording_that_only_contained_the_event_states_no_level():
    incident = decided(
        "The corporation said the pothole filling was minor and any delay on the road temporary."
    )
    severity = incident.severity
    assert severity.level is SeverityLevel.UNRESOLVED
    assert severity.score is None
    assert severity.confidence is None
    assert severity.signals and all(
        signal.direction == vocabulary.MITIGATING for signal in severity.signals
    )
    assert "argued that the event was serious" in (severity.notes or "")


def test_the_operations_lexicon_passes_its_own_integrity_check():
    assert operations_integrity_errors() == []
    assert vocabulary.ACCEPTANCE_FLOOR > 0.0
    assert vocabulary.declared_status("updated") is None
    assert vocabulary.declared_status("closed") is OperationalState.CLOSED
    assert vocabulary.declared_severity("high rainfall") is None
