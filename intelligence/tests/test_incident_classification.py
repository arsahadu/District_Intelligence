"""Stage 7 through the seam it is installed at: one record in, an honest relevance and type out."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

import pytest

from intelligence.config import CUE_LEXICON_VERSION, TAXONOMY_VERSION
from intelligence.config.vocabularies import EVENT_TYPE_FAMILY
from intelligence.extraction.spans import SpanCheck
from intelligence.mapping import feed_policies
from intelligence.mapping.assembly import MappingPolicy, map_record
from intelligence.mapping.classifier import (
    RECORD_TYPE_PATH,
    SOURCE_TYPE_PATH,
    STAGE_VERSION,
    classify_incident,
    read_cues,
)
from intelligence.mapping.enrichment import enrich_incident
from intelligence.mapping.record_input import CONTENT_PATH, UnreadableRecord
from intelligence.models.enums import (
    DistrictHintAuthority,
    EventType,
    ExtractionMethod,
    IncidentStatus,
    ResolutionState,
    RelevanceState,
    ReviewReason,
    SeverityLevel,
    SpanValidation,
    ScriptType,
)
from intelligence.models.spatial import SpatialHint
from intelligence.tests import record_fixtures as F

NEWS_LINE = F.temple_closure()
ENGLISH_LINE = F.english_article()
MIXED_LINE = F.mixed_language_article()
TIE_LINE = F.two_hazard_article()
BAIT_LINE = F.keyword_bait_article()
AD_LINE = F.advertisement()
FORECAST_LINE = F.weather_forecast()
CYCLONE_LINE = F.weather_warning(text=F.CYCLONE_WARNING_TEXT)
RAIN_LINE = F.weather_warning()
ANNA_NAGAR = F.market_price()
SELLUR = F.market_price(market="Sellur", market_id="126", commodity="Brinjal")
DISTRESS_LINE = F.crop_distress_report()

EVERY_LINE = [
    NEWS_LINE,
    ENGLISH_LINE,
    MIXED_LINE,
    TIE_LINE,
    BAIT_LINE,
    AD_LINE,
    F.tamil_article(),
    F.power_outage(),
    F.events_listing(),
    FORECAST_LINE,
    CYCLONE_LINE,
    RAIN_LINE,
    ANNA_NAGAR,
    SELLUR,
    DISTRESS_LINE,
]


def policy_for(payload: dict) -> MappingPolicy:
    return feed_policies.policy_for(payload["source_type"])


def classify(payload: dict):
    policy = policy_for(payload)
    return classify_incident(enrich_incident(map_record(payload, policy), policy), policy)


def synth(content: str, *, title: str = "", district: Optional[str] = "Madurai", **fields: Any):
    return F.base_record(
        record_id=str(fields.pop("record_id", "SYNTH-CLASSIFY-0001")),
        title=title or content,
        event_time=None,
        location={"raw_text": district, "district": district, "state": "Tamil Nadu" if district else None},
        data={"content": content, "language": "ta"},
        source_url=None,
        raw_reference=None,
        **fields,
    )


def evidence_for(draft, evidence_ids) -> list:
    index = draft.incident.evidence_by_id()
    return [index[evidence_id] for evidence_id in evidence_ids]


def fields_of(entries) -> set[str]:
    return {entry.field for entry in entries}


def test_a_tamil_news_item_the_text_states_is_relevant_and_named():
    draft = classify(NEWS_LINE)
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.relevance.is_incident is True
    assert incident.relevance.method is ExtractionMethod.DICTIONARY
    assert "போராட்ட" not in incident.relevance.reason
    assert incident.relevance.reason.endswith("the record states the district it happened in")
    assert incident.classification.event_type is EventType.PROTEST_OR_STRIKE
    assert incident.classification.family == EVENT_TYPE_FAMILY[EventType.PROTEST_OR_STRIKE]
    assert incident.classification.method is ExtractionMethod.DICTIONARY
    assert incident.classification.confidence == pytest.approx(0.64)
    assert [hint.department.value for hint in incident.classification.departments] == ["police"]


def test_an_english_news_item_is_read_the_same_way():
    incident = classify(ENGLISH_LINE).incident

    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.classification.event_type is EventType.FLOOD
    assert incident.classification.confidence == pytest.approx(0.76)
    assert incident.language.primary_language == "en"


def test_a_record_in_both_scripts_is_decided_from_both():
    incident = classify(MIXED_LINE).incident

    assert incident.language.primary_script is ScriptType.MIXED
    assert incident.language.primary_language == "ta"
    assert incident.classification.event_type is EventType.POWER_SUPPLY_DISRUPTION
    assert incident.classification.confidence == pytest.approx(0.81)
    assert len(incident.classification.evidence_ids) == 4


def test_paid_space_is_called_a_non_event_and_says_so():
    draft = classify(AD_LINE)
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.NOT_INCIDENT
    assert incident.relevance.is_incident is False
    assert incident.relevance.method is ExtractionMethod.RULE
    assert incident.relevance.confidence == pytest.approx(0.85)
    assert "விளம்பரம்" in incident.relevance.reason
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert incident.classification.category_scores == {}
    assert ReviewReason.UNRESOLVED_RELEVANCE not in incident.review.reasons


def test_a_diary_listing_with_one_topic_word_is_not_believed():
    incident = classify(F.events_listing()).incident

    assert incident.relevance.state is RelevanceState.UNSURE
    assert incident.relevance.is_incident is None
    assert incident.relevance.confidence is None
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert incident.classification.category_scores == {"ceremonial_or_award_event": 0.285}
    assert ReviewReason.UNRESOLVED_RELEVANCE in incident.review.reasons


def test_a_record_with_nothing_event_shaped_in_it_stays_unresolved_without_evidence():
    draft = classify(synth("மதுரை மாநகராட்சி அலுவலகம் கூட்டம்.", record_id="SYNTH-BLANK-0001"))
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.UNRESOLVED
    assert incident.relevance.method is ExtractionMethod.UNRESOLVED
    assert incident.relevance.evidence_ids == []
    assert "was not decided from nothing" in incident.relevance.reason
    assert incident.classification.evidence_ids == []


def test_a_record_with_no_text_at_all_never_reaches_a_decision():
    with pytest.raises(UnreadableRecord):
        classify(F.no_text())


def test_a_weather_observation_is_relevant_because_the_feed_says_what_it_is():
    draft = classify(FORECAST_LINE)
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.relevance.method is ExtractionMethod.SOURCE_METADATA
    assert incident.relevance.confidence == pytest.approx(0.8)
    assert fields_of(evidence_for(draft, incident.relevance.evidence_ids)) >= {
        RECORD_TYPE_PATH,
        SOURCE_TYPE_PATH,
        "location.district",
    }
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert incident.classification.category_scores == {"flood": 0.27}


def test_a_warning_that_names_a_hazard_earns_the_hazard_type():
    named = classify(CYCLONE_LINE).incident
    plain = classify(RAIN_LINE).incident

    assert named.classification.event_type is EventType.CYCLONE_STORM_DAMAGE
    assert named.classification.confidence == pytest.approx(0.76)
    assert plain.classification.event_type is EventType.UNRESOLVED
    assert plain.relevance.state is RelevanceState.INCIDENT


def test_the_weather_policy_reads_the_fields_the_weather_feed_writes():
    draft = classify(RAIN_LINE)

    assert "data.warning" in draft.record.text_paths
    assert "data.warning" not in feed_policies.NEWS.text_keys
    match = read_cues(draft).matches[0]
    assert match.field == "data.warning"
    assert match.evidence.quote == "rain"


def test_a_price_line_is_an_observation_this_district_tracks():
    draft = classify(ANNA_NAGAR)
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.relevance.method is ExtractionMethod.SOURCE_METADATA
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert incident.classification.secondary_event_types == [EventType.MARKET_PRICE_DISTRESS]
    assert incident.classification.category_scores == {"market_price_distress": 0.45}
    assert incident.classification.confidence is None
    assert incident.classification.departments == []


def test_two_markets_are_two_observations_decided_the_same_way():
    first, second = classify(ANNA_NAGAR), classify(SELLUR)

    assert first.incident.incident_id != second.incident.incident_id
    assert (
        first.incident.relevance.model_dump(exclude={"evidence_ids"})
        == second.incident.relevance.model_dump(exclude={"evidence_ids"})
    )
    assert (
        first.incident.classification.model_dump()
        == second.incident.classification.model_dump()
    )
    assert [record.title for record in (first.record, second.record)] == [
        "Tomato price - Anna nagar Uzhavar Santhai",
        "Brinjal price - Sellur Uzhavar Santhai",
    ]


def test_a_price_crash_the_prose_states_is_named_as_what_happened():
    incident = classify(DISTRESS_LINE).incident

    assert incident.classification.event_type is EventType.CROP_DAMAGE
    assert incident.classification.confidence == pytest.approx(0.86)
    assert incident.classification.family == "agriculture"
    assert [hint.department.value for hint in incident.classification.departments] == ["agriculture"]


def test_words_that_only_name_a_thing_do_not_open_a_case():
    draft = classify(BAIT_LINE)
    incident = draft.incident

    assert incident.relevance.state is RelevanceState.UNSURE
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert set(incident.classification.category_scores) == {
        "hospital_service_gap",
        "disease_outbreak",
    }
    assert all(score < 0.55 for score in incident.classification.category_scores.values())
    assert any("refused" in warning for warning in incident.processing.warnings)


def test_two_plausible_types_are_left_unresolved_between_them():
    incident = classify(TIE_LINE).incident

    assert incident.relevance.state is RelevanceState.INCIDENT
    assert incident.classification.event_type is EventType.UNRESOLVED
    assert {
        EventType(item) for item in incident.classification.category_scores
    } == {EventType.FLOOD, EventType.POWER_SUPPLY_DISRUPTION}
    assert incident.classification.secondary_event_types == [
        EventType.FLOOD,
        EventType.POWER_SUPPLY_DISRUPTION,
    ]
    assert "margin" in incident.classification.notes
    assert ReviewReason.UNRESOLVED_EVENT_TYPE in incident.review.reasons


def test_a_district_the_text_disagrees_with_is_a_question_not_an_answer():
    draft = enrich_incident(map_record(NEWS_LINE, policy_for(NEWS_LINE)), policy_for(NEWS_LINE))
    contested = draft.incident.model_copy(
        deep=True,
        update={
            "spatial": SpatialHint(
                district_hint="Madurai",
                district_hint_authority=DistrictHintAuthority.SOURCE_CONFIGURATION,
                competing_districts=["Sivaganga"],
                resolution_state=ResolutionState.PENDING_GIS,
            )
        },
    )
    incident = classify_incident(replace(draft, incident=contested), policy_for(NEWS_LINE)).incident

    assert incident.relevance.state is RelevanceState.UNSURE
    assert incident.relevance.is_incident is None
    assert "Sivaganga" in incident.relevance.reason
    assert ReviewReason.DISTRICT_CONFLICT in incident.review.reasons


def test_a_district_the_feed_never_asserted_anchors_nothing():
    draft = classify(synth("மதுரையில் மின்தடை தொடர்ந்தது.", district=None))
    incident = draft.incident

    assert incident.spatial.district_hint is None
    assert incident.spatial.mentions
    assert incident.relevance.state is RelevanceState.UNSURE
    assert incident.relevance.is_incident is None
    assert "nothing in the record ties it to a district this platform covers" in (
        incident.relevance.reason
    )
    assert incident.classification.event_type is EventType.POWER_SUPPLY_DISRUPTION
    assert ReviewReason.UNRESOLVED_RELEVANCE in incident.review.reasons


@pytest.mark.parametrize("payload", EVERY_LINE, ids=lambda payload: payload["record_id"])
def test_a_type_is_never_claimed_without_the_evidence_for_it(payload):
    draft = classify(payload)
    incident = draft.incident
    resolved = incident.classification.event_type is not EventType.UNRESOLVED

    if resolved:
        assert incident.classification.evidence_ids
        assert incident.classification.confidence is not None
        for entry in evidence_for(draft, incident.classification.evidence_ids):
            assert entry.method is ExtractionMethod.DICTIONARY
            assert entry.quote
    else:
        assert incident.classification.confidence is None or (
            incident.classification.confidence <= 0.5
        )
        assert incident.classification.departments == []
    if incident.relevance.state in (RelevanceState.INCIDENT, RelevanceState.NOT_INCIDENT):
        assert incident.relevance.evidence_ids
        assert incident.relevance.confidence is not None
    else:
        assert incident.relevance.is_incident is None
        assert incident.relevance.confidence is None


@pytest.mark.parametrize("payload", EVERY_LINE, ids=lambda payload: payload["record_id"])
def test_every_span_stage_7_quoted_still_replays(payload):
    draft = classify(payload)

    assert draft.all_spans_verify
    assert all(isinstance(check, SpanCheck) for check in draft.checks)
    assert {check.validation for check in draft.checks} <= {
        SpanValidation.VALIDATED,
        SpanValidation.NOT_APPLICABLE,
    }
    for entry in draft.incident.evidence:
        assert entry.field in draft.fields, entry.describe()


@pytest.mark.parametrize("payload", EVERY_LINE, ids=lambda payload: payload["record_id"])
def test_the_contract_holds_for_every_line_stage_7_touched(payload):
    draft = classify(payload)
    incident = draft.incident
    known = [entry.evidence_id for entry in incident.evidence]

    assert incident.status is IncidentStatus.CANDIDATE
    assert len(known) == len(set(known))
    assert set(incident.relevance.evidence_ids) <= set(known)
    assert set(incident.classification.evidence_ids) <= set(known)
    assert draft.referenced_evidence_ids <= set(known)
    assert incident.review.needed is bool(incident.review.reasons)


def test_stage_7_settled_nothing_stage_5_had_left_alone():
    for payload in EVERY_LINE:
        policy = policy_for(payload)
        stage5 = map_record(payload, policy)
        incident = classify(payload).incident
        before = stage5.incident

        assert incident.incident_id == before.incident_id
        assert incident.title == before.title
        assert incident.language == before.language
        assert incident.event_time == before.event_time
        assert incident.severity == before.severity
        assert incident.dedup == before.dedup
        assert incident.supporting_record_ids == before.supporting_record_ids
        assert incident.processing.input_record_hash == before.processing.input_record_hash
        assert incident.processing.config_hash == before.processing.config_hash
        assert incident.processing.modality is before.processing.modality


def test_stage_7_kept_every_mention_and_party_stage_6_read():
    for payload in EVERY_LINE:
        policy = policy_for(payload)
        stage6 = enrich_incident(map_record(payload, policy), policy)
        incident = classify(payload).incident

        assert incident.spatial.mentions == stage6.incident.spatial.mentions
        assert incident.actors == stage6.incident.actors
        assert incident.spatial.resolution_state is stage6.incident.spatial.resolution_state
        assert incident.spatial.gis is None
        assert incident.severity.level is SeverityLevel.UNRESOLVED
        assert set(
            entry.evidence_id for entry in stage6.incident.evidence
        ) <= set(entry.evidence_id for entry in incident.evidence)


def test_citing_the_district_again_does_not_duplicate_the_evidence():
    policy = policy_for(NEWS_LINE)
    stage5 = map_record(NEWS_LINE, policy)
    draft = classify(NEWS_LINE)
    stage5_district = [
        entry for entry in stage5.incident.evidence if entry.field == "location.district"
    ]
    cited = [entry for entry in draft.incident.evidence if entry.field == "location.district"]

    assert len(stage5_district) == 1 and len(cited) == 1
    assert cited[0].evidence_id == stage5_district[0].evidence_id
    assert stage5_district[0].evidence_id in draft.incident.relevance.evidence_ids


def test_stage_7_added_its_own_versions_and_used_no_model():
    incident = classify(NEWS_LINE).incident

    assert incident.processing.stage_versions["classification"] == STAGE_VERSION
    assert incident.processing.stage_versions["relevance"] == STAGE_VERSION
    assert incident.processing.stage_versions["cue_lexicon"] == CUE_LEXICON_VERSION
    assert incident.processing.stage_versions["taxonomy"] == TAXONOMY_VERSION
    assert incident.processing.llm_used is False
    assert incident.processing.llm_tasks == []
    assert incident.processing.llm_models == []


def test_an_unresolved_section_is_counted_and_never_scored():
    resolved = classify(NEWS_LINE).incident
    unresolved = classify(TIE_LINE).incident

    assert "classification" in resolved.confidence.components
    assert "classification" not in unresolved.confidence.components
    assert unresolved.confidence.overall == min(unresolved.confidence.components.values())
    assert unresolved.confidence.unresolved_field_count > resolved.confidence.unresolved_field_count
    assert unresolved.confidence.rule == resolved.confidence.rule


def test_the_feed_policies_differ_only_in_what_the_feed_cannot_declare():
    assert feed_policies.policy_for("news") is feed_policies.NEWS
    assert feed_policies.policy_for("agriculture") is feed_policies.AGRICULTURE
    assert feed_policies.policy_for("a-feed-nobody-tuned") is feed_policies.DEFAULT
    assert feed_policies.NEWS.text_keys == (CONTENT_PATH,)
    assert "data.warning" in feed_policies.WEATHER.text_keys
    assert "data.market" in feed_policies.AGRICULTURE.extra_keys
    hashes = {
        policy.config_hash()
        for policy in (
            feed_policies.DEFAULT,
            feed_policies.NEWS,
            feed_policies.WEATHER,
            feed_policies.AGRICULTURE,
        )
    }

    assert len(hashes) == 4


def test_deciding_twice_decides_the_same_thing():
    for payload in EVERY_LINE:
        assert classify(payload).incident.model_dump() == classify(payload).incident.model_dump()


def test_stage_7_needs_no_enrichment_to_be_honest():
    policy = policy_for(NEWS_LINE)
    draft = classify_incident(map_record(NEWS_LINE, policy), policy)

    assert draft.incident.relevance.state is RelevanceState.INCIDENT
    assert draft.incident.classification.event_type is EventType.PROTEST_OR_STRIKE
    assert draft.all_spans_verify
    assert draft.incident.spatial.district_hint == "Madurai"
