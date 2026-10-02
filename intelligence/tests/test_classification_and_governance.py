"""Classification, relevance, dedup and processing-state invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intelligence.models.classification import (
    ClassificationInfo,
    DepartmentHint,
    RelevanceInfo,
)
from intelligence.models.enums import (
    DataOrigin,
    DedupDecision,
    Department,
    DepartmentHintBasis,
    EventType,
    ExtractionMethod,
    IncidentStatus,
    Modality,
    RelevanceState,
    ReviewReason,
)
from intelligence.models.incident import Incident
from intelligence.models.metadata import (
    ConfidenceSummary,
    DedupMetadata,
    ProcessingMetadata,
    ReviewInfo,
)
from intelligence.tests.builders import RECORD_ID, protest_evidence, protest_incident


# --------------------------------------------------------------------------- #
# relevance
# --------------------------------------------------------------------------- #


def test_unresolved_relevance_does_not_assert_a_verdict():
    relevance = RelevanceInfo()

    assert relevance.state is RelevanceState.UNRESOLVED
    assert relevance.is_incident is None


@pytest.mark.parametrize(
    "state, expected",
    [
        (RelevanceState.INCIDENT, True),
        (RelevanceState.NOT_INCIDENT, False),
    ],
)
def test_not_incident_is_distinct_from_unresolved(state, expected):
    relevance = RelevanceInfo(
        state=state,
        is_incident=expected,
        reason="award ceremony coverage",
        method=ExtractionMethod.RULE,
        confidence=0.9,
        evidence_ids=["ev-protest"],
    )
    assert relevance.is_incident is expected


def test_relevance_state_and_flag_must_agree():
    with pytest.raises(ValidationError, match="cannot assert is_incident"):
        RelevanceInfo(state=RelevanceState.UNRESOLVED, is_incident=False)

    with pytest.raises(ValidationError, match="requires is_incident=True"):
        RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=False,
            method=ExtractionMethod.RULE,
            confidence=0.5,
            evidence_ids=["ev-1"],
        )


def test_resolved_relevance_must_cite_evidence():
    with pytest.raises(ValidationError, match="must cite its evidence"):
        RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=True,
            method=ExtractionMethod.RULE,
            confidence=0.9,
        )


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def test_unresolved_event_type_is_a_valid_classification():
    classification = ClassificationInfo()

    assert classification.event_type is EventType.UNRESOLVED
    assert classification.is_low_confidence() is True


def test_unresolved_event_type_cannot_carry_department_hints():
    with pytest.raises(ValidationError, match="cannot carry department hints"):
        ClassificationInfo(
            event_type=EventType.UNRESOLVED,
            departments=[
                DepartmentHint(
                    department=Department.POLICE,
                    basis=DepartmentHintBasis.TAXONOMY,
                    confidence=0.5,
                )
            ],
        )


def test_unresolved_event_type_cannot_claim_high_confidence():
    with pytest.raises(ValidationError, match="cannot claim high confidence"):
        ClassificationInfo(event_type=EventType.UNRESOLVED, confidence=0.95)


def test_category_scores_keys_must_be_real_event_types():
    with pytest.raises(ValidationError, match="must be EventType values"):
        ClassificationInfo(
            event_type=EventType.PROTEST_OR_STRIKE,
            category_scores={"probtest_or_strik": 0.9},
            method=ExtractionMethod.RULE,
            confidence=0.9,
            evidence_ids=["ev-1"],
        )


def test_automatic_classification_must_pick_the_top_scoring_type():
    """A rule classifier that ignores its own scores is a bug, not a judgement."""
    with pytest.raises(ValidationError, match="not the highest-scoring category"):
        ClassificationInfo(
            event_type=EventType.PROTEST_OR_STRIKE,
            category_scores={"protest_or_strike": 0.3, "public_disturbance": 0.8},
            method=ExtractionMethod.RULE,
            confidence=0.7,
            evidence_ids=["ev-1"],
        )

    overridden = ClassificationInfo(
        event_type=EventType.PROTEST_OR_STRIKE,
        category_scores={"protest_or_strike": 0.3, "public_disturbance": 0.8},
        method=ExtractionMethod.HUMAN,
        confidence=0.7,
        evidence_ids=["ev-1"],
    )
    assert overridden.event_type is EventType.PROTEST_OR_STRIKE


def test_primary_type_must_not_repeat_as_secondary():
    with pytest.raises(ValidationError, match="must not repeat as secondary"):
        ClassificationInfo(
            event_type=EventType.PROTEST_OR_STRIKE,
            secondary_event_types=[EventType.PROTEST_OR_STRIKE],
            method=ExtractionMethod.RULE,
            confidence=0.8,
            evidence_ids=["ev-1"],
        )

    with pytest.raises(ValidationError, match="not a valid secondary"):
        ClassificationInfo(
            event_type=EventType.PROTEST_OR_STRIKE,
            secondary_event_types=[EventType.UNRESOLVED],
            method=ExtractionMethod.RULE,
            confidence=0.8,
            evidence_ids=["ev-1"],
        )


def test_department_hint_carries_its_basis_and_strength():
    hint = DepartmentHint(
        department=Department.LABOUR_EMPLOYMENT,
        basis=DepartmentHintBasis.TEXT_MENTION,
        confidence=0.7,
        evidence_ids=["ev-commissioner"],
    )
    assert hint.basis is DepartmentHintBasis.TEXT_MENTION

    with pytest.raises(ValidationError, match="must cite the text"):
        DepartmentHint(
            department=Department.LABOUR_EMPLOYMENT,
            basis=DepartmentHintBasis.TEXT_MENTION,
            confidence=0.7,
        )

    with pytest.raises(ValidationError, match="unresolved department"):
        DepartmentHint(
            department=Department.UNRESOLVED,
            basis=DepartmentHintBasis.TAXONOMY,
            confidence=0.1,
        )


# --------------------------------------------------------------------------- #
# dedup
# --------------------------------------------------------------------------- #


def test_fingerprint_is_absent_until_computed_never_placeholdered():
    incident = Incident(incident_id="INC-1")

    assert incident.fingerprint is None
    assert incident.dedup.decision is DedupDecision.UNRESOLVED
    assert incident.dedup.similarity_features == {}


def test_duplicate_decision_requires_a_target_and_similarity():
    with pytest.raises(ValidationError, match="requires duplicate_of or merged_from"):
        DedupMetadata(decision=DedupDecision.DUPLICATE, similarity_features={"jaccard": 0.9})

    with pytest.raises(ValidationError, match="requires similarity evidence"):
        DedupMetadata(decision=DedupDecision.DUPLICATE, duplicate_of="INC-2")

    dedup = DedupMetadata(
        decision=DedupDecision.DUPLICATE,
        duplicate_of="INC-2",
        similarity_features={"fingerprint": 1.0, "title_jaccard": 0.86},
        max_similarity=1.0,
        confidence=0.93,
    )
    assert dedup.duplicate_of == "INC-2"


def test_unique_decision_contradicts_a_duplicate_reference():
    with pytest.raises(ValidationError, match="decision=unique contradicts"):
        DedupMetadata(
            decision=DedupDecision.UNIQUE,
            duplicate_of="INC-9",
            similarity_features={"fingerprint": 1.0},
        )


def test_similarity_features_are_bounded():
    with pytest.raises(ValidationError):
        DedupMetadata(
            decision=DedupDecision.MERGE_CANDIDATE,
            duplicate_of="INC-2",
            similarity_features={"title_jaccard": 12.5},
            confidence=0.5,
        )


# --------------------------------------------------------------------------- #
# processing / LLM disclosure
# --------------------------------------------------------------------------- #


def test_processing_metadata_records_that_no_llm_was_used():
    processing = ProcessingMetadata(pipeline_version="0.1.0")

    assert processing.llm_used is False
    assert processing.llm_tasks == []
    assert processing.modality is Modality.TEXT


def test_llm_usage_must_be_disclosed_with_tasks_and_models():
    with pytest.raises(ValidationError, match="requires the tasks"):
        ProcessingMetadata(llm_used=True)

    with pytest.raises(ValidationError, match="requires the model names"):
        ProcessingMetadata(llm_used=True, llm_tasks=["event_type_fallback"])

    with pytest.raises(ValidationError, match="set llm_used=True"):
        ProcessingMetadata(llm_used=False, llm_tasks=["summary"])

    processing = ProcessingMetadata(
        llm_used=True,
        llm_tasks=["event_type_fallback"],
        llm_models=["gpt-placeholder"],
        llm_prompt_versions={"event_type_fallback": "v1"},
    )
    assert processing.llm_used is True


def test_llm_assistance_survives_to_the_incident_and_asks_for_review():
    protest = protest_incident()
    protest.processing = protest.processing.model_copy(
        update={
            "llm_used": True,
            "llm_tasks": ["summary"],
            "llm_models": ["gpt-placeholder"],
        }
    )
    flagged = protest.apply_review_flags()

    assert flagged.processing.llm_used is True
    assert ReviewReason.LLM_ASSISTED in flagged.review.reasons


def test_input_record_hash_makes_a_run_reproducible():
    protest = protest_incident()
    protest.processing = protest.processing.model_copy(
        update={"input_record_hash": "a" * 64, "config_hash": "b" * 64}
    )

    assert protest.processing.input_record_hash == "a" * 64


# --------------------------------------------------------------------------- #
# review and confidence aggregation
# --------------------------------------------------------------------------- #


def test_review_reasons_require_the_flag_and_cannot_be_duplicated():
    assert ReviewInfo().needed is False

    with pytest.raises(ValidationError, match="requires at least one reason"):
        ReviewInfo(needed=True)

    with pytest.raises(ValidationError, match="reasons but needed=False"):
        ReviewInfo(needed=False, reasons=[ReviewReason.LOW_CONFIDENCE])

    with pytest.raises(ValidationError, match="duplicate review reasons"):
        ReviewInfo(
            needed=True,
            reasons=[ReviewReason.LOW_CONFIDENCE, ReviewReason.LOW_CONFIDENCE],
        )


def test_overall_confidence_requires_its_inputs():
    assert ConfidenceSummary().overall is None

    with pytest.raises(ValidationError, match="requires the components"):
        ConfidenceSummary(overall=0.8)

    with pytest.raises(ValidationError, match="requires the combining rule"):
        ConfidenceSummary(overall=0.8, components={"event_type": 0.8})

    summary = ConfidenceSummary(
        overall=0.8, components={"event_type": 0.8, "spatial": 0.9}, rule="min()"
    )
    assert summary.overall == 0.8


def test_incident_defaults_are_a_legal_empty_state():
    incident = Incident(incident_id="INC-DEFAULT", origin=DataOrigin.PIPELINE)

    assert incident.status is IncidentStatus.CANDIDATE
    assert incident.evidence == []
    assert incident.supporting_record_ids == []
    assert incident.inconsistencies()


def test_evidence_rollup_is_queryable_from_the_incident(protest):
    assert protest.contributing_record_ids() == [RECORD_ID]
    assert set(protest.evidence_by_id()) >= {"ev-protest", "ev-petitions"}

    resolved = protest.resolve_evidence(["ev-petitions"])
    assert resolved[0].quote == "4800 மனுக்களை"
    assert protest.source_quotes()
