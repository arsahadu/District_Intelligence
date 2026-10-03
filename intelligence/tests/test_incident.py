"""Incident assembly: nested validation and evidence reference integrity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intelligence.models.actors import Actor
from intelligence.models.classification import ClassificationInfo
from intelligence.models.enums import ActorRole, ActorType, EventType, ExtractionMethod
from intelligence.models.enums import IncidentStatus, ReviewReason, ScriptType
from intelligence.models.incident import SCHEMA_VERSION, Incident
from intelligence.tests.builders import RECORD_ID, protest_evidence, protest_incident, unresolved_incident


def test_fully_populated_incident_validates(protest):
    assert isinstance(protest, Incident)
    assert protest.schema_version == SCHEMA_VERSION
    assert protest.event_type is EventType.PROTEST_OR_STRIKE
    assert protest.primary_language == "ta"


def test_nested_models_are_reachable_and_typed(protest):
    assert protest.relevance.is_incident is True
    assert protest.classification.category_scores["protest_or_strike"] == 0.82
    assert {d.department.value for d in protest.classification.departments} == {
        "police",
        "urban_local_bodies",
    }

    actor = protest.actors[0]
    assert actor.role is ActorRole.RESPONDING_AUTHORITY
    assert actor.transliterated_latin == "commissioner"

    observation = protest.observations[0]
    assert observation.value_numeric == 4800
    assert observation.display_value == "4800"

    assert protest.event_time.is_publication_time_only is True
    assert len(protest.spatial.mentions) == 2
    assert protest.spatial.event_location_candidates
    assert protest.confidence.overall == 0.82


def test_every_referenced_evidence_id_must_exist():
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        Incident(
            incident_id="INC-BAD-1",
            evidence=protest_evidence(),
            supporting_record_ids=[RECORD_ID],
            classification=ClassificationInfo(
                event_type=EventType.PROTEST_OR_STRIKE,
                method=ExtractionMethod.RULE,
                confidence=0.8,
                evidence_ids=["ev-never-recorded"],
            ),
        )


def test_dangling_reference_inside_a_nested_list_is_also_caught():
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        Incident(
            incident_id="INC-BAD-2",
            evidence=protest_evidence(),
            supporting_record_ids=[RECORD_ID],
            actors=[
                Actor(
                    actor_id="act-bad",
                    name_text="கலெக்டர்",
                    actor_type=ActorType.GOVERNMENT_OFFICIAL,
                    role=ActorRole.DECISION_MAKER,
                    script=ScriptType.TAMIL,
                    method=ExtractionMethod.DICTIONARY,
                    confidence=0.7,
                    evidence_ids=["ev-missing"],
                )
            ],
        )


def test_duplicate_evidence_ids_rejected():
    evidence = protest_evidence()
    duplicate = evidence[0].model_copy(update={"record_id": evidence[0].record_id})

    with pytest.raises(ValidationError, match="duplicate evidence_id"):
        Incident(incident_id="INC-BAD-3", evidence=[*evidence, duplicate])


def test_supporting_record_must_have_evidence_behind_it():
    with pytest.raises(ValidationError, match="without any evidence"):
        Incident(
            incident_id="INC-BAD-4",
            evidence=protest_evidence(),
            supporting_record_ids=["REC-NOBODY-NO-QUOTE-FROM-THIS"],
        )


def test_records_cannot_both_support_and_contradict():
    with pytest.raises(ValidationError, match="support and contradict"):
        Incident(
            incident_id="INC-BAD-5",
            evidence=protest_evidence(),
            supporting_record_ids=[RECORD_ID],
            contradicts_record_ids=[RECORD_ID],
        )


def test_actor_cannot_point_at_a_mention_that_does_not_exist():
    protest = protest_incident()
    orphan_mention = protest.spatial.mentions[0].model_copy(
        update={"mention_id": "men-ghost", "is_event_location_candidate": False}
    )
    bad_actor = protest.actors[0].model_copy(update={"affiliation_mention_id": "men-absent"})

    with pytest.raises(ValidationError, match="unknown mention"):
        Incident(
            incident_id="INC-BAD-6",
            evidence=protest.evidence,
            supporting_record_ids=[RECORD_ID],
            language=protest.language,
            spatial=protest.spatial.model_copy(
                update={"mentions": [*protest.spatial.mentions, orphan_mention]}
            ),
            actors=[bad_actor],
        )


def test_an_incident_cannot_be_a_duplicate_of_itself():
    protest = protest_incident()
    with pytest.raises(ValidationError, match="duplicate of itself"):
        protest.dedup = protest.dedup.model_copy(
            update={
                "duplicate_of": protest.incident_id,
                "decision": "duplicate",
                "similarity_features": {"fingerprint": 1.0},
                "confidence": 0.9,
            }
        )


def test_merged_status_requires_a_duplicate_reference():
    protest = protest_incident()
    with pytest.raises(ValidationError, match="requires dedup.duplicate_of"):
        protest.status = IncidentStatus.MERGED


def test_invented_top_level_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        Incident(incident_id="INC-BAD-7", made_up_coordinate=9.92)


def test_nothing_in_an_unresolved_incident_is_claimed_as_authoritative():
    empty = unresolved_incident()

    assert empty.evidence == []
    assert empty.classification.event_type is EventType.UNRESOLVED
    assert empty.severity.is_authoritative is False
    assert empty.fingerprint is None
    assert empty.dedup.duplicate_of is None


def test_review_flags_are_derivable_from_content():
    reasons = unresolved_incident().derive_review_reasons()

    assert ReviewReason.NO_SUPPORTING_EVIDENCE in reasons
    assert ReviewReason.UNRESOLVED_EVENT_TYPE in reasons
    assert ReviewReason.UNRESOLVED_SEVERITY in reasons
    assert ReviewReason.NO_EVENT_LOCATION_CANDIDATE in reasons


def test_apply_review_flags_keeps_review_consistent():
    flagged = unresolved_incident().apply_review_flags()

    assert flagged.review.needed is True
    assert flagged.review.reasons
    assert set(flagged.review.reasons) <= set(ReviewReason)
    assert all(r.value for r in flagged.review.reasons)


def test_inconsistencies_describe_weakness_in_plain_language():
    notes = unresolved_incident().inconsistencies()

    assert any("traceable" in note for note in notes)
    assert any("event_type is unresolved" in note for note in notes)
    assert any("no time established" in note.lower() for note in notes)
