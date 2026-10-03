"""Stage 5: a CommonRecord-shaped record in, an honest candidate Incident out."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from intelligence.extraction.spans import SpanValidation, verify_evidence
from intelligence.mapping import (
    CONTENT_PATH,
    LANGUAGE_PATH,
    TITLE_PATH,
    IncidentDraft,
    MappingError,
    MappingPolicy,
    RecordInput,
    RecordShapeError,
    UnreadableRecord,
    derive_incident_id,
    input_hash,
    map_record,
    read_record,
    verify_draft,
)
from intelligence.mapping.record_input import DISTRICT_PATH, EVENT_TIME_PATH, RETRIEVED_AT_PATH
from intelligence.models.enums import (
    DedupDecision,
    DistrictHintAuthority,
    EventType,
    ExtractionMethod,
    IncidentStatus,
    Modality,
    ResolutionState,
    RelevanceState,
    ReviewReason,
    ScriptType,
    SeverityLevel,
    TimePrecision,
    TimeSemantics,
)
from intelligence.models.language import UNKNOWN_LANGUAGE
from intelligence.tests import record_fixtures as F

REPO_ROOT = Path(__file__).resolve().parents[2]

WEATHER_POLICY = MappingPolicy(
    text_keys=(CONTENT_PATH, "data.forecast"),
    extra_keys=("data.min_temp_c", "data.max_temp_c", "data.warning", "data.station_id"),
    time_trusted_record_types=frozenset({"forecast"}),
)


def evidence_for(draft: IncidentDraft, field: str):
    matches = [e for e in draft.incident.evidence if e.field == field]
    assert matches, f"nothing in the ledger was read from {field!r}"
    return matches[0]


def test_a_valid_record_maps_to_a_candidate_incident():
    draft = map_record(F.tamil_article())
    incident = draft.incident

    assert isinstance(draft, IncidentDraft)
    assert incident.incident_id == "INC-NEWS-MDU-0001"
    assert incident.status is IncidentStatus.CANDIDATE
    assert incident.supporting_record_ids == ["NEWS-MDU-0001"]
    assert incident.contributing_record_ids() == ["NEWS-MDU-0001"]
    assert incident.title.text.source == F.ARTICLE_TITLE
    assert incident.title.text.language == "ta"
    assert incident.processing.modality is Modality.TEXT
    assert incident.processing.source_record_count == 1
    assert incident.processing.stage_versions["mapping"] == "5"
    assert incident.processing.processed_at is None
    assert incident.processing.llm_used is False
    assert draft.all_spans_verify


def test_the_pydantic_record_shape_reads_like_the_dict():
    payload = F.tamil_article()

    from_dict = map_record(payload)
    from_model = map_record(F.as_record_model(payload))

    assert from_model.incident.to_storage_document() == from_dict.incident.to_storage_document()
    assert from_model.incident.processing.input_record_hash == (
        from_dict.incident.processing.input_record_hash
    )


def test_a_record_can_be_read_once_and_mapped_twice():
    record = read_record(F.tamil_article())
    assert isinstance(record, RecordInput)
    assert record.text_paths == (TITLE_PATH, CONTENT_PATH)
    assert record.title == F.ARTICLE_TITLE
    assert record.has_text()

    first = map_record(record)
    second = map_record(record)
    assert first.incident.to_storage_document() == second.incident.to_storage_document()


@pytest.mark.parametrize(
    "path",
    ["record_id", "source_id", "source_type", "record_type", RETRIEVED_AT_PATH],
)
def test_a_record_missing_an_identity_field_is_rejected(path: str):
    payload = F.tamil_article()
    del payload[path]

    with pytest.raises(RecordShapeError) as error:
        read_record(payload)

    assert path in str(error.value)


def test_a_record_field_holding_a_structure_is_rejected_by_name():
    with pytest.raises(RecordShapeError, match="record_id"):
        read_record(F.tamil_article(record_id=["NEWS-MDU-0001"]))

    with pytest.raises(RecordShapeError, match="retrieved_at"):
        read_record(F.tamil_article(retrieved_at="yesterday evening"))


def test_a_record_with_a_title_but_no_body_still_maps():
    draft = map_record(F.title_only())
    incident = draft.incident

    assert "data.content is empty" in incident.processing.warnings
    assert [r.role.value for r in incident.language.text_representations] == ["source"]
    assert incident.language.primary_language == "ta"
    assert draft.split is None
    assert draft.temporal is None
    assert incident.event_time.value is None
    assert incident.event_time.semantics is TimeSemantics.UNKNOWN
    assert incident.event_time.method is ExtractionMethod.UNRESOLVED
    assert "no time established at all" in incident.inconsistencies()


def test_a_record_with_no_readable_text_is_rejected():
    with pytest.raises(UnreadableRecord, match="no readable text"):
        read_record(F.no_text())

    with pytest.raises(MappingError):
        map_record(F.no_text())


def test_dotted_paths_read_nested_values_and_leave_the_rest_as_a_warning():
    record = read_record(
        F.weather_forecast(),
        text_keys=(CONTENT_PATH, "data.forecast"),
        extra_keys=("data.min_temp_c", "data.station_id"),
    )

    assert record.text_paths == (TITLE_PATH, "data.forecast")
    assert record.scalar("data.min_temp_c") == "24.5"
    assert record.scalar("data.station_id") == "MDU"
    assert record.language_hint is None
    assert record.district == "Madurai"
    assert record.unused_data_keys == ("max_temp_c", "relative_humidity_0830", "relative_humidity_1730", "warning")


def test_a_value_outside_the_text_keys_can_never_be_read_as_body_text():
    record = read_record(F.weather_forecast(), extra_keys=("data.station_id",))

    assert record.text_paths == (TITLE_PATH,)
    assert "MDU" not in [text for _, text in record.texts]


def test_every_evidence_carries_the_record_it_came_from():
    payload = F.tamil_article()
    draft = map_record(payload)

    assert draft.incident.evidence
    for evidence in draft.incident.evidence:
        assert evidence.record_id == payload["record_id"]
        assert evidence.source_id == payload["source_id"]
        assert evidence.source_type == payload["source_type"]
        assert evidence.source_url == payload["source_url"]
        assert evidence.raw_reference == payload["raw_reference"]
        assert evidence.retrieved_at == datetime.fromisoformat(payload["retrieved_at"])
        assert evidence.field in draft.fields


def test_tamil_is_decided_from_the_text_not_the_feed_label():
    incident = map_record(F.tamil_article()).incident

    assert incident.language.primary_language == "ta"
    assert incident.language.primary_script is ScriptType.TAMIL
    assert incident.language.detection is not None
    assert incident.language.detection.primary_language == "ta"
    assert incident.language.detection.method is ExtractionMethod.RULE
    assert incident.language.is_multilingual is False


def test_english_is_decided_from_the_text():
    incident = map_record(F.english_article()).incident

    assert incident.language.primary_language == "en"
    assert incident.language.primary_script is ScriptType.LATIN
    assert incident.title.text.language == "en"


def test_the_inherited_language_hint_is_recorded_and_never_load_bearing():
    labelled = map_record(F.tamil_article(language_hint="ta")).incident
    unlabelled = map_record(F.tamil_article(language_hint=None)).incident

    assert labelled.language.inherited_language_hint == "ta"
    assert unlabelled.language.inherited_language_hint is None
    assert labelled.language.primary_language == unlabelled.language.primary_language
    assert labelled.language.detection.confidence == unlabelled.language.detection.confidence


def test_the_language_label_is_cited_as_a_hint_and_never_as_body_text():
    draft = map_record(F.tamil_article())
    hint = evidence_for(draft, LANGUAGE_PATH)

    assert hint.quote == "ta"
    assert hint.method is ExtractionMethod.SOURCE_METADATA
    assert LANGUAGE_PATH not in draft.record.text_paths
    assert LANGUAGE_PATH in draft.fields[LANGUAGE_PATH].field
    assert any(
        "inherited hint only" in warning and LANGUAGE_PATH in warning
        for warning in draft.warnings
    )


def test_a_wrong_language_hint_does_not_override_the_detection():
    incident = map_record(F.tamil_article(language_hint="en")).incident

    assert incident.language.primary_language == "ta"
    assert incident.language.inherited_language_hint == "en"
    assert any(
        "inherited language hint 'en' does not match" in warning
        for warning in incident.processing.warnings
    )


def test_an_articles_own_event_time_is_not_trusted_as_event_time():
    payload = F.tamil_article()
    draft = map_record(payload)
    event_time = draft.incident.event_time

    assert datetime.fromisoformat(payload["event_time"]) == datetime(2026, 10, 2, 17, 45)
    assert event_time.value != datetime.fromisoformat(payload["event_time"])
    assert EVENT_TIME_PATH not in draft.fields
    assert any(
        "publish stamp captured from the page" in warning for warning in draft.warnings
    )


def test_a_publish_stamp_stands_in_with_publication_semantics():
    draft = map_record(F.tamil_article())
    event_time = draft.incident.event_time

    assert event_time.value == datetime(2026, 10, 3, 0, 0)
    assert event_time.semantics is TimeSemantics.PUBLICATION_TIME
    assert event_time.method is ExtractionMethod.REGEX
    assert event_time.precision is TimePrecision.MINUTE
    assert event_time.raw_text == "அக் 03, 2026 12:00 AM"
    assert event_time.confidence == 0.95
    assert ReviewReason.PUBLICATION_TIME_ONLY in draft.incident.review.reasons
    assert draft.time_roles()["event_time"] is None
    assert draft.time_roles()["publication_time"] == "2026-10-03T00:00:00"
    assert (
        "best available time is the publication stamp, not when the incident happened"
        in draft.incident.inconsistencies()
    )


def test_a_relative_tamil_date_is_resolved_against_the_records_own_retrieval_time():
    payload = F.temple_closure()
    draft = map_record(payload)
    event_time = draft.incident.event_time

    assert event_time.value == datetime(2026, 10, 1, 19, 45)
    assert event_time.semantics is TimeSemantics.EVENT_TIME
    assert event_time.method is ExtractionMethod.RULE
    assert "நேற்று முன்தினம்" in event_time.raw_text
    assert ReviewReason.PUBLICATION_TIME_ONLY not in draft.incident.review.reasons

    moved = map_record({**payload, "retrieved_at": "2026-10-05T09:00:00"})
    assert moved.incident.event_time.value == datetime(2026, 10, 3, 19, 45)
    assert moved.incident.event_time.value != event_time.value


def test_a_bare_weekday_is_never_promoted_to_a_date():
    draft = map_record(F.events_listing())
    weekdays = [mention for mention in draft.temporal.mentions if mention.kind == "weekday"]

    assert weekdays
    assert all(mention.expression.surface == "சனி" for mention in weekdays)
    assert all(mention.resolution.resolved is False for mention in weekdays)
    assert all(mention.resolution.value is None for mention in weekdays)
    assert all("weekday" in mention.resolution.reason for mention in weekdays)
    assert draft.time_roles()["event_time"] is None
    assert "சனி" not in (draft.incident.event_time.raw_text or "")


def test_a_forecast_valid_date_is_trusted_only_when_the_policy_says_so():
    draft = map_record(F.weather_forecast(), WEATHER_POLICY)
    event_time = draft.incident.event_time

    assert event_time.value == datetime(2026, 10, 3, 0, 0)
    assert event_time.semantics is TimeSemantics.EVENT_TIME
    assert event_time.method is ExtractionMethod.SOURCE_METADATA
    assert event_time.precision is TimePrecision.DAY
    assert event_time.confidence == 1.0
    assert event_time.raw_text is None
    assert evidence_for(draft, EVENT_TIME_PATH).quote == "2026-10-03T00:00:00"
    assert draft.incident.review.reasons
    assert ReviewReason.PUBLICATION_TIME_ONLY not in draft.incident.review.reasons


def test_the_same_forecast_is_honest_without_the_trust_policy():
    draft = map_record(F.weather_forecast())
    event_time = draft.incident.event_time

    assert event_time.value is None
    assert event_time.semantics is TimeSemantics.UNKNOWN
    assert EVENT_TIME_PATH not in draft.fields
    assert "no time established at all" in draft.incident.inconsistencies()


def test_too_little_text_yields_an_unknown_language_rather_than_a_guess():
    incident = map_record(F.weather_forecast()).incident

    assert incident.language.primary_language == UNKNOWN_LANGUAGE
    assert incident.language.detection.confidence is None
    assert incident.title.text.language == UNKNOWN_LANGUAGE
    assert incident.language.inherited_language_hint is None


def test_scalar_record_values_are_cited_rather_than_read_as_body_text():
    draft = map_record(F.weather_forecast(), WEATHER_POLICY)

    assert evidence_for(draft, "data.min_temp_c").quote == "24.5"
    assert evidence_for(draft, "data.max_temp_c").quote == "33.1"
    assert "data.warning is null" in draft.warnings
    representations = [r.text for r in draft.incident.language.text_representations]
    for path, rendered in draft.record.scalars:
        assert path not in draft.record.text_paths
        assert all(rendered != text for text in representations)


def test_the_feed_district_is_kept_as_an_authority_tagged_hint():
    draft = map_record(F.tamil_article())
    spatial = draft.incident.spatial

    assert spatial.district_hint == "Madurai"
    assert spatial.district_hint_authority is DistrictHintAuthority.SOURCE_CONFIGURATION
    assert spatial.district_hint_from_text is None
    assert spatial.district_hint_confidence is None
    assert spatial.resolution_state is ResolutionState.PENDING_GIS
    assert spatial.notes is not None and "feed configuration" in spatial.notes
    assert evidence_for(draft, DISTRICT_PATH).quote == "Madurai"
    assert (
        "district came from feed configuration, not from the article text"
        in draft.incident.inconsistencies()
    )


def test_no_location_mention_is_read_before_stage_six():
    draft = map_record(F.tamil_article())

    assert draft.incident.spatial.mentions == []
    assert draft.incident.spatial.best_event_location_mention_id is None
    assert draft.incident.spatial.competing_districts == []
    assert ReviewReason.NO_EVENT_LOCATION_CANDIDATE in draft.incident.review.reasons
    assert ReviewReason.AMBIGUOUS_LOCATION in draft.incident.review.reasons


def test_no_coordinates_or_gis_identity_are_invented():
    document = map_record(F.tamil_article()).incident.to_storage_document()
    serialized = json.dumps(document, ensure_ascii=False)

    assert document["spatial"]["gis"] is None
    assert document["spatial"]["mentions"] == []
    for forbidden in ("latitude", "longitude", "canonical_place_id", "geocoder_version"):
        assert forbidden not in serialized


def test_a_record_that_asserts_no_district_asserts_nothing():
    draft = map_record(F.tamil_article(district=None))
    spatial = draft.incident.spatial

    assert spatial.district_hint is None
    assert spatial.district_hint_authority is DistrictHintAuthority.NONE
    assert spatial.resolution_state is ResolutionState.NOT_ATTEMPTED
    assert spatial.gis is None
    assert DISTRICT_PATH not in draft.fields
    assert ReviewReason.AMBIGUOUS_LOCATION not in draft.incident.review.reasons


def test_a_source_severity_string_is_cited_without_becoming_a_level():
    draft = map_record(F.orange_alert())
    severity = draft.incident.severity

    assert severity.level is SeverityLevel.UNRESOLVED
    assert severity.score is None
    assert severity.is_authoritative is False
    assert severity.signals == []
    assert severity.evidence_ids
    cited = draft.incident.evidence_by_id()[severity.evidence_ids[0]]
    assert cited.field == "severity"
    assert cited.quote == "Orange Alert"
    assert cited.method is ExtractionMethod.SOURCE_METADATA
    assert ReviewReason.UNRESOLVED_SEVERITY in draft.incident.review.reasons


def test_a_record_with_no_severity_says_so_without_a_citation():
    draft = map_record(F.tamil_article())
    severity = draft.incident.severity

    assert severity.level is SeverityLevel.UNRESOLVED
    assert severity.evidence_ids == []
    assert "Stage 5 extracts no severity" in severity.notes


def test_status_is_held_as_provenance_and_never_promoted():
    draft = map_record(F.orange_alert())

    assert draft.incident.status is IncidentStatus.CANDIDATE
    assert evidence_for(draft, "status").quote == "UPDATED"
    assert any(
        "held as provenance only" in warning for warning in draft.incident.processing.warnings
    )


def test_relevance_is_left_unresolved_and_asserts_nothing():
    incident = map_record(F.tamil_article()).incident

    assert incident.relevance.state is RelevanceState.UNRESOLVED
    assert incident.relevance.is_incident is None
    assert incident.relevance.confidence is None
    assert incident.relevance.evidence_ids == []


def test_classification_is_left_unresolved_with_no_department_invented():
    incident = map_record(F.tamil_article()).incident

    assert incident.event_type is EventType.UNRESOLVED
    assert incident.classification.departments == []
    assert incident.classification.category_scores == {}
    assert incident.classification.confidence is None
    assert incident.classification.taxonomy_version is None
    assert incident.actors == []
    assert incident.observations == []


def test_every_span_bearing_evidence_reproduces_the_field_it_was_cut_from():
    draft = map_record(F.temple_closure())
    spanned = [e for e in draft.incident.evidence if e.has_span]

    assert spanned
    assert len(draft.checks) == len(draft.incident.evidence)
    assert all(
        check.validation in (SpanValidation.VALIDATED, SpanValidation.NOT_APPLICABLE)
        for check in draft.checks
    )
    for evidence in spanned:
        field_text = draft.fields[evidence.field].text
        assert field_text[evidence.char_start : evidence.char_end] == evidence.quote
        assert verify_evidence(evidence, field_text).validation is SpanValidation.VALIDATED

    tampered = verify_evidence(spanned[0], spanned[0].quote[:-1] + "x")
    assert tampered.validation is SpanValidation.MISMATCH


def test_verification_replays_the_whole_ledger_not_only_the_cited_evidence():
    draft = map_record(F.tamil_article())

    assert len(draft.incident.evidence) > len(draft.incident.source_quotes())
    assert draft.unreferenced_evidence_ids
    assert len(verify_draft(draft)) == len(draft.incident.evidence)
    index = draft.incident.evidence_by_id()
    assert all(
        index[evidence_id].record_id == draft.record.record_id
        for evidence_id in draft.unreferenced_evidence_ids
    )


def test_mapping_the_same_record_twice_gives_the_same_incident():
    first = map_record(F.tamil_article())
    second = map_record(F.tamil_article())

    assert first.incident.to_storage_document() == second.incident.to_storage_document()
    assert first.incident.processing.input_record_hash == (
        second.incident.processing.input_record_hash
    )
    assert first.incident.processing.config_hash == second.incident.processing.config_hash


def test_the_input_hash_follows_the_record_and_the_config_hash_follows_the_policy():
    baseline = read_record(F.tamil_article())
    retitled = read_record(F.tamil_article(title=F.CLOSURE_TITLE))
    districtless = read_record(F.tamil_article(district=None))

    assert input_hash(baseline) != input_hash(retitled)
    assert input_hash(baseline) != input_hash(districtless)
    assert MappingPolicy().config_hash() != MappingPolicy(timezone=None).config_hash()
    assert MappingPolicy().config_hash() != WEATHER_POLICY.config_hash()
    assert derive_incident_id(baseline) == "INC-NEWS-MDU-0001"
    assert derive_incident_id(baseline, prefix="NEWS") == "NEWS-NEWS-MDU-0001"


@pytest.mark.parametrize("payload", F.capture(), ids=lambda p: p["record_id"])
def test_the_capture_maps_to_an_honest_candidate(payload: dict):
    draft = map_record(payload)
    incident = draft.incident

    assert incident.incident_id == f"INC-{payload['record_id']}"
    assert incident.status is IncidentStatus.CANDIDATE
    assert incident.origin.value == "pipeline"
    assert incident.fingerprint is None
    assert incident.dedup.decision is DedupDecision.UNRESOLVED
    assert incident.dedup.cluster_id is None
    assert incident.created_at is None
    assert incident.updated_at is None
    assert incident.reported_at is None
    assert incident.contradicts_record_ids == []
    assert incident.supporting_record_ids == [payload["record_id"]]
    assert incident.language.primary_language in ("ta", "en", UNKNOWN_LANGUAGE)
    assert incident.review.needed is True
    assert incident.inconsistencies()
    assert draft.all_spans_verify
    assert [e.record_id for e in incident.evidence] == [payload["record_id"]] * len(incident.evidence)


def test_the_duplicate_article_pair_stays_two_candidate_records():
    original = map_record(F.tamil_article())
    twin = map_record(F.twin_article())

    assert original.record.texts == twin.record.texts
    assert original.incident.incident_id != twin.incident.incident_id
    assert original.incident.supporting_record_ids == ["NEWS-MDU-0001"]
    assert twin.incident.supporting_record_ids == ["NEWS-MDU-0002"]
    assert evidence_for(original, CONTENT_PATH).source_url != evidence_for(twin, CONTENT_PATH).source_url
    assert {e.evidence_id for e in original.incident.evidence}.isdisjoint(
        {e.evidence_id for e in twin.incident.evidence}
    )
    assert original.incident.fingerprint is None
    assert twin.incident.fingerprint is None
    assert original.incident.dedup.duplicate_of is None
    assert twin.incident.dedup.duplicate_of is None


def test_the_scrape_position_instability_is_recorded_when_the_policy_declares_it():
    warned = map_record(F.tamil_article(), MappingPolicy(positional_record_ids=True))

    assert any(
        "scrape position" in warning and "NEWS-MDU-0001" in warning
        for warning in warned.incident.processing.warnings
    )
    silent = map_record(F.tamil_article())
    assert not any("scrape position" in warning for warning in silent.warnings)


def test_a_naive_retrieved_at_is_declared_never_assumed():
    naive = map_record(F.tamil_article())

    assert naive.incident.event_time.timezone == "Asia/Kolkata"
    assert any(
        "carries no UTC offset" in warning and "Asia/Kolkata" in warning
        for warning in naive.warnings
    )

    aware = map_record(F.weather_forecast(), WEATHER_POLICY)
    assert not any("carries no UTC offset" in warning for warning in aware.warnings)
    assert aware.time_roles()["retrieval_time"].endswith("+00:00")


def test_a_policy_without_a_declared_timezone_says_so():
    draft = map_record(F.tamil_article(), MappingPolicy(timezone=None))

    assert draft.incident.event_time.timezone is None
    assert any("undeclared" in warning for warning in draft.warnings)


def test_mapping_never_imports_ingestion():
    code = (
        "import sys\n"
        "import intelligence.mapping\n"
        "print(sorted({name.split('.')[0] for name in sys.modules"
        " if name.split('.')[0] in {'ingestion', 'app'}}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    assert result.stdout.strip() == "[]"

    offenders = [
        path.name
        for path in (REPO_ROOT / "intelligence").rglob("*.py")
        if re.search(r"^(?:from|import)\s+(?:ingestion|app)\b", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []
