"""Stage 6 enrichment: what Stage 5 settled stays, what the text states is added, spans replayed."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Optional

import pytest

from intelligence.config import LEXICON_VERSION
from intelligence.mapping.assembly import MappingPolicy, map_record, verify_draft
from intelligence.mapping.enrichment import STAGE_VERSION, enrich_incident
from intelligence.models.enums import (
    DataOrigin,
    DistrictHintAuthority,
    IncidentStatus,
    ResolutionState,
    ReviewReason,
    SpanValidation,
    TextRole,
)
from intelligence.tests import record_fixtures as F

FORBIDDEN_KEYS = {
    "latitude",
    "longitude",
    "lat",
    "lng",
    "canonical_place_id",
    "geocoder_version",
    "gazetteer_id",
    "polygon",
    "coordinates",
}
CAPTURE = F.capture()


def record(content: str, *, title: str = "", district: Optional[str] = "Madurai", **fields: Any):
    return F.base_record(
        record_id=fields.pop("record_id", "SYNTH-0001"),
        title=title,
        event_time=None,
        location={
            "raw_text": district,
            "district": district,
            "state": "Tamil Nadu" if district else None,
        },
        data={"content": content, "language": "ta", **fields.pop("data", {})},
        **fields,
    )


def both(payload):
    stage5 = map_record(payload)
    return stage5, enrich_incident(stage5)


def walk_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_keys(value)


def dig(payload: dict, path: str):
    node: Any = payload
    for part in path.split("."):
        node = node[part]
    return node


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_record_text_comes_back_exactly_as_it_was_sent(payload):
    stage5, stage6 = both(payload)

    assert stage6.fields is stage5.fields
    for path, source in stage6.fields.items():
        assert source.text == dig(payload, path)

    stored = stage6.incident.to_storage_document()
    originals = {
        r["text"]
        for r in stored["language"]["text_representations"]
        if r["role"] == TextRole.SOURCE.value
    }
    assert payload["title"] in originals
    assert stage6.incident.title.text.source == payload["title"]
    if "data.content" in stage6.fields and payload["data"]["content"].strip():
        assert payload["data"]["content"] in originals


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_everything_stage_5_settled_survives_enrichment(payload):
    stage5, stage6 = both(payload)
    before, after = stage5.incident, stage6.incident

    assert after.incident_id == before.incident_id
    assert after.status is IncidentStatus.CANDIDATE is before.status
    assert after.origin is before.origin is DataOrigin.PIPELINE
    assert after.title == before.title
    assert after.event_time == before.event_time
    assert after.language == before.language
    assert after.severity == before.severity
    assert after.relevance == before.relevance
    assert after.classification == before.classification
    assert after.supporting_record_ids == before.supporting_record_ids
    assert after.contributing_record_ids() == before.contributing_record_ids()
    assert after.processing.input_record_hash == before.processing.input_record_hash
    assert after.processing.config_hash == before.processing.config_hash
    assert after.processing.pipeline_version == before.processing.pipeline_version
    assert after.processing.modality is before.processing.modality
    assert after.processing.source_record_count == before.processing.source_record_count
    assert after.processing.processed_at == before.processing.processed_at
    assert after.confidence.overall == before.confidence.overall
    assert after.confidence.components == before.confidence.components
    assert after.confidence.rule == before.confidence.rule


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_a_district_the_text_never_printed_stays_a_configured_hint(payload):
    stage5, stage6 = both(payload)
    before, after = stage5.incident.spatial, stage6.incident.spatial

    assert after.district_hint == before.district_hint == "Madurai"
    if after.district_hint_authority is DistrictHintAuthority.SOURCE_CONFIGURATION:
        assert after.district_hint_from_text == before.district_hint_from_text is None
        assert after.district_hint_confidence == before.district_hint_confidence
    else:
        assert after.district_hint_authority is DistrictHintAuthority.TEXT_EVIDENCE
        assert after.district_hint_from_text == stage6.places.district_text.surface


def test_a_district_the_record_itself_prints_is_earned_as_text_evidence():
    stage5, stage6 = both(F.events_listing())
    before, after = stage5.incident.spatial, stage6.incident.spatial

    assert before.district_hint_authority is DistrictHintAuthority.SOURCE_CONFIGURATION
    assert before.district_hint_from_text is None
    assert after.district_hint_authority is DistrictHintAuthority.TEXT_EVIDENCE
    assert after.district_hint_from_text == "மதுரை மாவட்டம்"
    assert after.competing_districts == before.competing_districts


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_holes_stage_6_left_open_are_the_only_confidence_change(payload):
    stage5, stage6 = both(payload)
    before, after = stage5.incident, stage6.incident

    filled = int(bool(after.spatial.mentions)) + int(bool(after.actors))
    assert after.confidence.unresolved_field_count == (
        before.confidence.unresolved_field_count - filled
    )
    assert after.confidence.unresolved_field_count in (6, 7)


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_review_is_recomputed_rather_than_accumulated(payload):
    stage5, stage6 = both(payload)
    before, after = stage5.incident, stage6.incident

    assert set(after.review.reasons) <= set(before.review.reasons)
    assert after.review.needed is True
    assert (
        ReviewReason.NO_EVENT_LOCATION_CANDIDATE in after.review.reasons
    ) is (after.spatial.best_event_location_mention_id is None)
    assert (ReviewReason.AMBIGUOUS_LOCATION in after.review.reasons) == (
        after.spatial.district_hint_authority is DistrictHintAuthority.SOURCE_CONFIGURATION
        and after.spatial.district_hint_from_text is None
    )


def test_the_stage_versions_stage_5_wrote_are_untouched_by_stage_6():
    stage5, stage6 = both(F.tamil_article())
    versions = stage6.incident.processing.stage_versions

    assert stage5.incident.processing.stage_versions["mapping"] == "5"
    assert "places" not in stage5.incident.processing.stage_versions
    assert versions["mapping"] == stage5.incident.processing.stage_versions["mapping"]
    assert versions["places"] == versions["actors"] == STAGE_VERSION
    assert versions["mention_lexicon"] == LEXICON_VERSION
    assert set(versions) - set(stage5.incident.processing.stage_versions) == {
        "places",
        "actors",
        "mention_lexicon",
    }


def test_the_lexicon_version_rides_with_the_incident_not_with_the_policy():
    policy = MappingPolicy()

    assert LEXICON_VERSION not in policy.config_hash()
    assert policy.config_hash() == MappingPolicy().config_hash()
    assert policy.config_hash() != MappingPolicy(pipeline_version="0.2.0").config_hash()
    assert policy.config_hash() != MappingPolicy(text_keys=("data.title",)).config_hash()


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_evidence_ledger_grows_and_never_repeats_an_identifier(payload):
    stage5, stage6 = both(payload)
    ids = [e.evidence_id for e in stage6.incident.evidence]

    assert len(ids) == len(set(ids))
    assert len(ids) >= len(stage5.incident.evidence)
    assert {e.evidence_id for e in stage5.incident.evidence} <= set(ids)
    assert all(
        e.span_validation in (SpanValidation.VALIDATED, SpanValidation.NOT_APPLICABLE)
        for e in stage6.incident.evidence
    )


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_every_span_the_enriched_incident_carries_still_cuts_from_its_own_field(payload):
    stage6 = enrich_incident(map_record(payload))
    validations = {check.validation for check in stage6.checks}

    assert stage6.all_spans_verify
    assert len(stage6.checks) == len(stage6.incident.evidence)
    assert validations <= {SpanValidation.VALIDATED, SpanValidation.NOT_APPLICABLE}
    assert SpanValidation.VALIDATED in validations
    for evidence in stage6.incident.evidence:
        if evidence.has_span:
            assert evidence.span_validation is SpanValidation.VALIDATED, evidence.describe()
        else:
            assert evidence.span_validation is SpanValidation.NOT_APPLICABLE


def test_a_span_that_no_longer_cuts_is_refused_rather_than_reported():
    stage6 = enrich_incident(map_record(F.tamil_article()))
    tampered = [
        evidence.model_copy(update={"char_start": evidence.char_start + 1})
        if index == 9
        else evidence
        for index, evidence in enumerate(stage6.incident.evidence)
    ]

    with pytest.raises(Exception) as error:
        verify_draft(replace(stage6, incident=stage6.incident.model_copy(update={"evidence": tampered})))

    assert "a span no longer reproduces" in str(error.value)


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_everything_stage_6_declined_to_read_is_written_into_the_warnings(payload):
    stage5, stage6 = both(payload)
    places, actors = stage6.places, stage6.actors

    assert stage6.warnings[: len(stage5.warnings)] == stage5.warnings
    assert set(stage6.warnings) == set(stage5.warnings) | set(places.warnings()) | set(
        actors.warnings()
    )
    assert len(stage6.warnings) == len(set(stage6.warnings))
    assert stage6.incident.processing.warnings == list(stage6.warnings)
    for refusal in (*places.rejections, *actors.refusals):
        assert any(refusal.surface in warning for warning in stage6.warnings), refusal.describe()


def test_the_report_shows_the_mentions_and_the_parties_behind_what_it_stored():
    stage5, stage6 = both(F.temple_closure())

    assert stage5.report()["places"] is None
    assert stage5.report()["actors"] is None
    places = stage6.report()["places"]
    actors = stage6.report()["actors"]

    assert places["record_id"] == actors["record_id"] == "NEWS-MDU-0004"
    assert len(places["mentions"]) == len(stage6.places.mentions) == 7
    assert len(places["rejections"]) == 18
    assert len(actors["actors"]) == len(stage6.actors.actors) == 4
    assert len(actors["refusals"]) == 1
    assert places["best_event_location_mention_id"] == "LOC-NEWS-MDU-0004-4"
    assert stage6.report()["district_hint"]["state"] == ResolutionState.PENDING_GIS.value


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_no_geography_of_any_kind_enters_the_stored_document(payload):
    stage6 = enrich_incident(map_record(payload))
    stored = stage6.incident.to_storage_document()
    keys = set(walk_keys(json.loads(json.dumps(stored, default=str))))

    assert keys & FORBIDDEN_KEYS == set()
    assert stage6.incident.spatial.gis is None
    assert stage6.incident.spatial.resolution_state is ResolutionState.PENDING_GIS
    assert all(not mention.gazetteer_matched for mention in stage6.incident.spatial.mentions)


def test_two_records_of_identical_text_leave_as_two_incidents_with_no_shared_evidence():
    first, twin = enrich_incident(map_record(F.tamil_article())), enrich_incident(
        map_record(F.twin_article())
    )

    assert first.incident.incident_id != twin.incident.incident_id
    assert first.incident.supporting_record_ids == ["NEWS-MDU-0001"]
    assert twin.incident.supporting_record_ids == ["NEWS-MDU-0002"]
    assert not {e.evidence_id for e in first.incident.evidence} & {
        e.evidence_id for e in twin.incident.evidence
    }
    assert not {m.mention_id for m in first.incident.spatial.mentions} & {
        m.mention_id for m in twin.incident.spatial.mentions
    }
    assert not {a.actor_id for a in first.incident.actors} & {
        a.actor_id for a in twin.incident.actors
    }
    assert [m.text for m in first.incident.spatial.mentions] == [
        m.text for m in twin.incident.spatial.mentions
    ]


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_same_record_enriches_to_the_same_document_twice(payload):
    first = enrich_incident(map_record(payload)).incident.to_storage_document()
    second = enrich_incident(map_record(payload)).incident.to_storage_document()

    assert first == second


@pytest.mark.parametrize("payload", CAPTURE, ids=lambda p: p["record_id"])
def test_the_incident_carries_the_very_mentions_and_parties_the_draft_kept(payload):
    stage6 = enrich_incident(map_record(payload))

    assert [m.mention_id for m in stage6.places.location_mentions()] == [
        m.mention_id for m in stage6.incident.spatial.mentions
    ]
    assert [a.actor_id for a in stage6.actors.actors] == [
        a.actor_id for a in stage6.incident.actors
    ]
    assert stage6.places.best_event_location_mention_id == (
        stage6.incident.spatial.best_event_location_mention_id
    )
    for actor in stage6.incident.actors:
        if actor.affiliation_mention_id is not None:
            assert actor.affiliation_mention_id in {
                m.mention_id for m in stage6.incident.spatial.mentions
            }


@pytest.mark.parametrize(
    "content, title",
    [
        ("நேரம் போனது.", "செய்தி"),
        ("    \n   ", "மதுரை"),
        ("மதுரையில் கூட்டம் நடந்தது", ""),
        ("", "மதுரை"),
        ("...", ""),
    ],
)
def test_thin_or_empty_text_is_answered_without_inventing_anything(content: str, title: str):
    stage5, stage6 = both(record(content, title=title))

    assert stage6.incident.status is IncidentStatus.CANDIDATE
    assert stage6.all_spans_verify
    for mention in stage6.places.mentions:
        assert mention.surface in stage6.fields[mention.field].text
    assert stage6.incident.confidence.unresolved_field_count <= (
        stage5.incident.confidence.unresolved_field_count
    )


def test_a_record_that_prints_no_place_leaves_the_slot_empty_rather_than_guessing():
    stage5, stage6 = both(record("நேரம் போனது.", title="செய்தி"))

    assert stage6.incident.spatial.mentions == []
    assert stage6.incident.actors == []
    assert stage6.incident.spatial.best_event_location_mention_id is None
    assert ReviewReason.NO_EVENT_LOCATION_CANDIDATE in stage6.incident.review.reasons
    assert stage6.incident.confidence.unresolved_field_count == (
        stage5.incident.confidence.unresolved_field_count
    )
    assert "Stage 6 read 0 place mention(s)" in stage6.incident.spatial.notes


def test_a_record_with_no_text_at_all_never_reaches_stage_6():
    with pytest.raises(Exception) as error:
        map_record(F.no_text())

    assert "no readable text" in str(error.value)


def test_enriching_a_draft_twice_adds_no_second_copy_of_anything():
    once = enrich_incident(map_record(F.tamil_article()))
    twice = enrich_incident(once)

    assert [e.evidence_id for e in twice.incident.evidence] == [
        e.evidence_id for e in once.incident.evidence
    ]
    assert [m.mention_id for m in twice.incident.spatial.mentions] == [
        m.mention_id for m in once.incident.spatial.mentions
    ]
    assert [a.actor_id for a in twice.incident.actors] == [
        a.actor_id for a in once.incident.actors
    ]
    assert twice.warnings == once.warnings
    assert twice.incident.confidence.unresolved_field_count == (
        once.incident.confidence.unresolved_field_count
    )
