"""Stage 3: language is detected from code points and the claim stays auditable."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from intelligence.extraction.language import (
    ENGLISH_FUNCTION_WORDS,
    MULTILINGUAL,
    ScriptProfile,
    UNKNOWN_LANGUAGE,
    assess,
    detection,
    hint_conflict,
    language_info,
    normalise_hint,
    profile,
    representation_id,
    script_bucket,
    source_representation,
)
from intelligence.extraction.spans import SourceField
from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole
from intelligence.models.language import LanguageInfo
from intelligence.tests.builders import (
    BENCH_CONTENT_TA,
    CONTENT_TA,
    RECORD_ID,
    SOURCE_ID,
    SOURCE_TYPE,
    SOURCE_URL,
    TITLE_TA,
)

RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

MIXED_TA_EN = (
    "தொழிலாளர் நல வாரிய ஊழியர்கள் ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்; the court"
)
CODE_SWITCHED = (
    "மதுரை மாநகராட்சி அலுவலகத்தில் the complaint was filed against the agreement"
)
ENGLISH_PROSE = (
    "The collector directed the corporation to restore the water supply within a week"
)
TELUGU = "మద్రాసు నగర పాలక సంస్థ"


def field(text: str = CONTENT_TA, **overrides) -> SourceField:
    payload = {
        "record_id": RECORD_ID,
        "source_id": SOURCE_ID,
        "source_type": SOURCE_TYPE,
        "field": "data.content",
        "text": text,
        "source_url": SOURCE_URL,
        "raw_reference": SOURCE_URL,
        "retrieved_at": RETRIEVED_AT,
    }
    payload.update(overrides)
    return SourceField(**payload)


def test_tamil_body_is_detected_as_tamil():
    assessment = assess(CONTENT_TA)
    assert assessment.primary_language == "ta"
    assert assessment.confidence == assessment.profile.tamil_ratio
    assert assessment.confidence > 0.8


def test_tamil_detection_carries_a_method_and_script_ratios():
    info = language_info([field()])
    assert info.detection is not None
    assert info.detection.method is ExtractionMethod.RULE
    assert info.detection.confidence == pytest.approx(profile(CONTENT_TA).tamil_ratio)
    assert set(info.detection.script_ratios) == {"tamil", "latin"}
    assert sum(info.detection.script_ratios.values()) == pytest.approx(1.0)
    assert info.detection.tamil_script_ratio == info.detection.script_ratios["tamil"]


def test_english_prose_is_detected_as_english():
    assessment = assess(ENGLISH_PROSE)
    assert assessment.primary_language == "en"
    assert assessment.profile.script_type is ScriptType.LATIN


def test_latin_script_alone_does_not_claim_english():
    assessment = assess("Madurai Corporation protest 4800 petitions")
    assert assessment.primary_language == UNKNOWN_LANGUAGE
    assert assessment.confidence is None
    assert "function words" in assessment.reason


def test_tamil_dominant_text_with_english_words_names_both_languages():
    assessment = assess(MIXED_TA_EN)
    assert assessment.primary_language == "ta"
    assert assessment.secondary_language == "en"
    assert assessment.is_multilingual is True


def test_code_switched_text_with_no_dominant_script_is_multilingual():
    assessment = assess(CODE_SWITCHED)
    assert assessment.primary_language == MULTILINGUAL
    assert assessment.is_multilingual is True
    assert assessment.confidence is not None and assessment.confidence < 1.0

    info = language_info([field(CODE_SWITCHED)])
    assert info.primary_language == MULTILINGUAL
    assert info.is_multilingual is True
    assert info.primary_script is ScriptType.MIXED


def test_text_without_letters_is_unknown():
    assessment = assess("4800 2026 !!! ...")
    assert assessment.primary_language == UNKNOWN_LANGUAGE
    assert assessment.profile.letters == 0
    assert assessment.profile.script_type is ScriptType.UNKNOWN
    assert language_info([field("4800 2026 !!!")]).detection.tamil_script_ratio is None


def test_other_scripts_are_neither_tamil_nor_english():
    assessment = assess(TELUGU)
    assert assessment.primary_language == UNKNOWN_LANGUAGE
    assert assessment.profile.script_type is ScriptType.OTHER
    assert assessment.profile.other_ratio == 1.0


def test_only_letters_count_as_script_evidence():
    assert profile("நீதிமன்ற") == ScriptProfile(tamil=5, latin=0, other=0)
    assert profile("நீதிமன்ற 2026,") == profile("நீதிமன்ற")
    assert script_bucket("ி") is None
    assert script_bucket("4") is None


def test_inherited_hint_is_recorded_but_never_decides_the_language():
    info = language_info([field()], inherited_hint="en")
    assert info.inherited_language_hint == "en"
    assert info.primary_language == "ta"
    conflict = hint_conflict(info)
    assert conflict is not None
    assert "'en'" in conflict and "'ta'" in conflict


def test_agreeing_hint_produces_no_conflict():
    info = language_info([field()], inherited_hint="Tamil")
    assert info.primary_language == "ta"
    assert hint_conflict(info) is None


def test_hint_without_detection_is_still_reported_as_a_gap():
    info = LanguageInfo(inherited_language_hint="ta")
    assert info.primary_language == UNKNOWN_LANGUAGE
    assert "does not match" in hint_conflict(info)


def test_hint_labels_are_normalised_to_codes():
    assert normalise_hint("Tamil") == "ta"
    assert normalise_hint("en-IN") == "en"
    assert normalise_hint("  ") is None
    assert normalise_hint(None) is None


def test_every_source_field_becomes_its_own_representation():
    info = language_info(
        [field(TITLE_TA, field="title"), field(CONTENT_TA, field="data.content")]
    )
    assert [r.role for r in info.text_representations] == [TextRole.SOURCE, TextRole.SOURCE]
    assert {r.representation_id for r in info.text_representations} == {
        "rep-NEWS-MDU-TEST-0001-title",
        "rep-NEWS-MDU-TEST-0001-data-content",
    }
    assert info.detection.detected_by_record == {RECORD_ID: "ta"}


def test_two_records_are_detected_separately_and_together():
    info = language_info(
        [
            field(ENGLISH_PROSE, record_id="NEWS-MDU-TEST-0002", field="title"),
            field(CONTENT_TA, field="data.content"),
        ]
    )
    assert info.detection.detected_by_record == {
        "NEWS-MDU-TEST-0002": "en",
        RECORD_ID: "ta",
    }
    assert info.primary_language == MULTILINGUAL
    assert [r.language for r in info.text_representations] == ["en", "ta"]


def test_source_representation_is_verbatim_and_declares_its_method():
    source = field()
    representation = source_representation(source)
    assert representation.role is TextRole.SOURCE
    assert representation.method is ExtractionMethod.SOURCE_METADATA
    assert representation.derived_from is None
    assert representation.text == CONTENT_TA
    assert representation.script is ScriptType.TAMIL
    assert representation.representation_id == representation_id(source)


def test_detection_accepts_evidence_ids_for_provenance():
    info = language_info([field()], detection_evidence_ids=["ev-content"])
    assert info.detection.evidence_ids == ["ev-content"]
    assert info.text_representations[0].evidence_ids == []

    both = language_info([field()], source_evidence_ids=["ev-content"])
    assert both.text_representations[0].evidence_ids == ["ev-content"]


def test_detection_needs_something_to_read():
    with pytest.raises(ValueError, match="at least one source field"):
        detection([])
    with pytest.raises(ValueError, match="at least one source field"):
        language_info([])


def test_output_validates_through_the_stage_one_contract():
    info = language_info([field(BENCH_CONTENT_TA, field="data.content")])
    assert LanguageInfo.model_validate(info.model_dump()) == info
    restored = LanguageInfo.model_validate_json(info.model_dump_json())
    assert restored == info
    assert restored.text_representations[0].text == BENCH_CONTENT_TA


def test_contracts_refuse_invented_fields():
    payload = language_info([field()]).model_dump()
    payload["translator"] = "google"
    with pytest.raises(ValidationError, match="Extra inputs"):
        LanguageInfo.model_validate(payload)


def test_english_vocabulary_signal_uses_function_words_only():
    assert assess("protest in madurai") .primary_language == "en"
    assert not assess("4800 மனுக்களை UPDATED").english_vocabulary_found
    assert "the" in ENGLISH_FUNCTION_WORDS
