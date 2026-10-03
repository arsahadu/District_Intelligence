"""Location, severity and time: unresolved and ambiguous are valid answers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intelligence.models.enums import (
    DistrictHintAuthority,
    ExtractionMethod,
    GranularityLevel,
    MentionRole,
    MentionType,
    ResolutionState,
    SeverityCueCategory,
    SeverityLevel,
    TimePrecision,
    TimeQualifier,
    TimeSemantics,
)
from intelligence.models.severity import Severity, SeveritySignal
from intelligence.models.spatial import GisResolution, LocationMention, SpatialHint
from intelligence.models.temporal import TimeValue


def mention(**overrides) -> LocationMention:
    payload = {
        "mention_id": "men-1",
        "text": "மதுரையில்",
        "text_normalized": "மதுரை",
        "transliterated_latin": "madurai",
        "mention_type": MentionType.DISTRICT,
        "granularity": GranularityLevel.DISTRICT,
        "role": MentionRole.EVENT_LOCATION,
        "is_event_location_candidate": True,
        "method": ExtractionMethod.DICTIONARY,
        "confidence": 0.8,
        "evidence_ids": ["ev-madurai"],
    }
    payload.update(overrides)
    return LocationMention(**payload)


def test_ambiguous_location_is_representable_without_choosing_one(ambiguous_incident):
    spatial = ambiguous_incident.spatial

    assert spatial.best_event_location_mention_id is None
    assert spatial.resolution_state is ResolutionState.AMBIGUOUS
    assert spatial.competing_districts == ["Thanjavur"]
    assert spatial.district_hint == "Madurai"
    assert spatial.district_hint_authority is DistrictHintAuthority.SOURCE_CONFIGURATION

    by_id = {m.mention_id: m for m in spatial.mentions}
    assert by_id["men-kumbakonam"].is_event_location_candidate is True
    assert by_id["men-madurai-bench"].role is MentionRole.INSTITUTION_NAME
    assert by_id["men-madurai-bench"].is_event_location_candidate is False
    assert "உயர்நீதிமன்ற" in by_id["men-madurai-bench"].context_window


def test_an_institution_name_cannot_be_promoted_to_event_location():
    with pytest.raises(ValidationError, match="role is an event location"):
        SpatialHint(
            mentions=[
                mention(
                    role=MentionRole.INSTITUTION_NAME,
                    is_event_location_candidate=False,
                )
            ],
            best_event_location_mention_id="men-1",
        )


def test_best_mention_must_reference_a_real_mention():
    with pytest.raises(ValidationError, match="does not reference a known mention"):
        SpatialHint(mentions=[mention()], best_event_location_mention_id="men-nowhere")


def test_duplicate_mention_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate mention ids"):
        SpatialHint(mentions=[mention(), mention()])


def test_event_location_candidate_must_cite_evidence():
    with pytest.raises(ValidationError, match="must cite the evidence"):
        mention(evidence_ids=[])


def test_candidate_flag_and_role_must_agree():
    with pytest.raises(ValidationError, match="event-location role"):
        mention(role=MentionRole.MENTIONED_ONLY, is_event_location_candidate=True)


def test_coordinates_cannot_exist_without_an_attributed_gis_source():
    """No field on LocationMention accepts a lat/lon at all; only GisResolution does."""
    assert not any(
        f in LocationMention.model_fields for f in ("latitude", "longitude", "geometry")
    )
    with pytest.raises(ValidationError):
        mention(transliterated_latin="madurai", latitude=9.92)


def test_half_a_coordinate_pair_is_rejected():
    with pytest.raises(ValidationError, match="both be present or both absent"):
        GisResolution(resolved_by="gis-module-v1", latitude=9.92)


def test_resolved_coordinates_require_confidence():
    with pytest.raises(ValidationError, match="require a resolution confidence"):
        GisResolution(resolved_by="gis-module-v1", latitude=9.92, longitude=78.12)


def test_gis_output_only_valid_after_gis_reports_a_state():
    resolution = GisResolution(
        resolved_by="gis-module-v1",
        canonical_place_id="TN-MDU",
        canonical_district="Madurai",
        latitude=9.9252,
        longitude=78.1198,
        confidence=0.99,
    )

    with pytest.raises(ValidationError, match="only valid once GIS has reported"):
        SpatialHint(gis=resolution, resolution_state=ResolutionState.PENDING_GIS)

    with pytest.raises(ValidationError, match="requires a GisResolution"):
        SpatialHint(resolution_state=ResolutionState.RESOLVED)

    resolved = SpatialHint(gis=resolution, resolution_state=ResolutionState.RESOLVED)
    assert resolved.has_gis_output is True
    assert resolved.gis.resolved_by == "gis-module-v1"


def test_district_hint_must_declare_its_authority():
    with pytest.raises(ValidationError, match="must declare where it came from"):
        SpatialHint(district_hint="Madurai")

    with pytest.raises(ValidationError, match="without a district hint"):
        SpatialHint(district_hint_authority=DistrictHintAuthority.TEXT_EVIDENCE)


def test_severity_can_stay_explicitly_unresolved():
    severity = Severity.unresolved("no casualty or damage cue in the source")

    assert severity.level is SeverityLevel.UNRESOLVED
    assert severity.signals == []
    assert severity.score is None
    assert severity.is_authoritative is False
    assert severity.notes.startswith("no casualty")


def test_a_severity_level_without_evidence_is_rejected():
    with pytest.raises(ValidationError, match="requires at least one cited cue"):
        Severity(level=SeverityLevel.HIGH, rationale="looks serious")


def test_llm_asserted_authoritative_severity_is_rejected():
    with pytest.raises(ValidationError, match="unresolved severity cannot be authoritative"):
        Severity(level=SeverityLevel.UNRESOLVED, is_authoritative=True)


def test_unresolved_severity_cannot_carry_a_score():
    with pytest.raises(ValidationError, match="cannot carry a score"):
        Severity(level=SeverityLevel.UNRESOLVED, score=0.9)


def test_resolved_severity_needs_signal_method_rationale_and_confidence():
    signal = SeveritySignal(
        signal_id="sig-fatal",
        category=SeverityCueCategory.FATALITY,
        cue_text="மூவர் பலி",
        weight=1.0,
        method=ExtractionMethod.DICTIONARY,
        confidence=0.85,
        evidence_ids=["ev-fatal"],
    )

    with pytest.raises(ValidationError, match="requires a rationale"):
        Severity(level=SeverityLevel.CRITICAL, signals=[signal], confidence=0.8)
    with pytest.raises(ValidationError, match="requires confidence"):
        Severity(level=SeverityLevel.CRITICAL, signals=[signal], rationale="three deaths")
    with pytest.raises(ValidationError, match="requires a method"):
        Severity(
            level=SeverityLevel.CRITICAL,
            signals=[signal],
            rationale="three deaths",
            confidence=0.8,
        )

    severity = Severity(
        level=SeverityLevel.CRITICAL,
        signals=[signal],
        rationale="three reported deaths is the top band",
        confidence=0.8,
        method=ExtractionMethod.RULE,
    )
    assert severity.level is SeverityLevel.CRITICAL
    assert severity.is_authoritative is False


def test_severity_signal_must_be_grounded():
    with pytest.raises(ValidationError, match="must cite the evidence"):
        SeveritySignal(
            signal_id="sig",
            category=SeverityCueCategory.INJURY,
            cue_text="காயமடைந்தனர்",
            confidence=0.7,
        )
    with pytest.raises(ValidationError, match="unresolved cue"):
        SeveritySignal(
            signal_id="sig",
            category=SeverityCueCategory.UNRESOLVED,
            cue_text="எதோ",
            confidence=0.7,
            evidence_ids=["ev-1"],
        )


def test_time_may_be_entirely_unknown():
    value = TimeValue()

    assert value.value is None
    assert value.precision is TimePrecision.UNKNOWN
    assert value.semantics is TimeSemantics.UNKNOWN
    assert value.as_display_string() == "time unresolved"


def test_a_datetime_without_provenance_is_rejected():
    from datetime import datetime

    with pytest.raises(ValidationError, match="missing its provenance"):
        TimeValue(value=datetime(2026, 10, 1))


def test_publication_time_is_kept_distinct_from_event_time():
    value = TimeValue(
        value="2026-10-01T21:46:00",
        precision=TimePrecision.MINUTE,
        qualifier=TimeQualifier.EXACT,
        semantics=TimeSemantics.PUBLICATION_TIME,
        raw_text="அக் 01, 2026 09:46 PM",
        timezone="Asia/Kolkata",
        method=ExtractionMethod.REGEX,
        confidence=0.95,
    )

    assert value.is_publication_time_only is True
    assert value.as_display_string().startswith("2026-10-01T21:46")


def test_extracted_time_must_keep_the_raw_expression():
    from datetime import datetime

    with pytest.raises(ValidationError, match="raw expression"):
        TimeValue(
            value=datetime(2026, 10, 1, 21, 46),
            precision=TimePrecision.MINUTE,
            semantics=TimeSemantics.EVENT_TIME,
            method=ExtractionMethod.RULE,
            confidence=0.6,
        )


def test_timezone_is_declared_rather_than_assumed():
    from datetime import datetime

    value = TimeValue(
        value=datetime(2026, 10, 1, 21, 46),
        precision=TimePrecision.MINUTE,
        semantics=TimeSemantics.EVENT_TIME,
        raw_text="நேற்று இரவு 9:46 மணி",
        method=ExtractionMethod.RULE,
        confidence=0.5,
    )

    assert value.timezone is None
    assert value.value.tzinfo is None
