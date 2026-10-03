"""Stage 2: evidence spans are derived from the source, verified against it, never typed."""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone

import pytest

from intelligence.extraction.spans import (
    AmbiguousQuoteError,
    EmptyQuoteError,
    OccurrenceOutOfBoundsError,
    QuoteNotFoundError,
    Span,
    SpanError,
    SourceField,
    build_evidence,
    build_evidence_at,
    compute_field_hash,
    derive_evidence_id,
    find_spans,
    locate,
    revalidate,
    verify_evidence,
)
from intelligence.models.enums import ExtractionMethod, IncidentStatus, Modality, SpanValidation
from intelligence.models.evidence import Evidence
from intelligence.models.classification import RelevanceInfo
from intelligence.models.incident import Incident
from intelligence.tests.builders import (
    BENCH_CONTENT_TA,
    CONTENT_TA,
    RECORD_ID,
    SOURCE_ID,
    SOURCE_TYPE,
    SOURCE_URL,
    field_hash,
    span_evidence,
)

RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RULE = ExtractionMethod.RULE
LLM = ExtractionMethod.LLM


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


def test_english_exact_span_has_exact_offsets():
    text = "Madurai district collector office received the complaint"
    evidence = build_evidence(field(text), "district", method=RULE)

    assert evidence.char_start == 8
    assert evidence.char_end == 16
    assert text[evidence.char_start : evidence.char_end] == "district"


def test_english_quote_at_the_end_of_the_field():
    text = "Madurai district collector office received the complaint"
    evidence = build_evidence(field(text), "complaint", method=RULE)

    assert (evidence.char_start, evidence.char_end) == (47, 56)
    assert evidence.char_end == len(text)


def test_tamil_exact_span_reproduces_the_quote():
    evidence = build_evidence(field(), "4800 மனுக்களை", method=RULE)

    assert CONTENT_TA[evidence.char_start : evidence.char_end] == "4800 மனுக்களை"
    assert evidence.char_end - evidence.char_start == len("4800 மனுக்களை")


def test_tamil_quote_at_the_very_start_of_the_field():
    text = "UPDATED : அக் 02, 2026 12:00 AM"
    evidence = build_evidence(field(text), "UPDATED", method=RULE)

    assert evidence.char_start == 0
    assert evidence.char_end == len("UPDATED")


def test_tamil_quote_at_the_very_end_of_the_field():
    text = "துாய்மைப் பணியாளர்கள் மாநகராட்சி அலுவலகத்தில் மனு அளித்த ஆர்ப்பாட்டம்"
    evidence = build_evidence(field(text), "ஆர்ப்பாட்டம்", method=RULE)

    assert evidence.char_end == len(text) == 69
    assert text[evidence.char_start : evidence.char_end] == "ஆர்ப்பாட்டம்"


def test_mixed_tamil_english_span():
    text = "மதுரை city road 4800 complaint"
    evidence = build_evidence(field(text), "city road", method=RULE)

    assert (evidence.char_start, evidence.char_end) == (6, 15)
    assert text[evidence.char_start : evidence.char_end] == "city road"


def test_offsets_are_code_points_not_bytes():
    quote = "நீதிமன்ற"
    text = BENCH_CONTENT_TA
    evidence = build_evidence(field(text), quote, method=RULE)

    assert any(unicodedata.combining(character) for character in quote)
    assert evidence.char_end - evidence.char_start == len(quote)
    # Tamil is three bytes per character; byte arithmetic would overshoot.
    assert len(quote.encode("utf-8")) > evidence.char_end - evidence.char_start
    assert text[evidence.char_start : evidence.char_end] == quote


def test_span_survives_a_newline_inside_the_quote():
    text = "first line\nsecond line\nthird"
    evidence = build_evidence(field(text), "line\nsecond", method=RULE)

    assert (evidence.char_start, evidence.char_end) == (6, 17)
    assert "\n" in evidence.quote
    assert text[evidence.char_start : evidence.char_end] == "line\nsecond"


def test_surrounding_whitespace_is_preserved_not_stripped():
    text = "  மதுரை  அலுவலகம்  "
    evidence = build_evidence(field(text), " மதுரை ", method=RULE)

    assert (evidence.char_start, evidence.char_end) == (1, 8)
    assert evidence.quote.startswith(" ") and evidence.quote.endswith(" ")
    assert evidence.quote == text[1:8]


def test_source_text_is_never_altered_by_the_builder():
    source = field()
    before = source.text
    build_evidence(source, "மதுரையில்", method=RULE)

    assert source.text == before
    assert CONTENT_TA == before


def test_repeated_quote_raises_instead_of_choosing_silently():
    text = "மதுரை ... மதுரை ..."
    with pytest.raises(AmbiguousQuoteError) as excinfo:
        locate(text, "மதுரை")

    occurrences = excinfo.value.occurrences
    assert len(occurrences) == 2
    assert [(s.char_start, s.char_end) for s in occurrences] == [(0, 5), (10, 15)]
    assert "occurrence=" in str(excinfo.value)


def test_occurrence_can_be_selected_explicitly():
    text = "மதுரை ... மதுரை ..."
    first = locate(text, "மதுரை", occurrence=0)
    second = locate(text, "மதுரை", occurrence=1)

    assert (first.char_start, second.char_start) == (0, 10)


def test_occurrence_accepts_negative_indexes():
    text = "மதுரை ... மதுரை ..."
    assert locate(text, "மதுரை", occurrence=-1).char_start == 10


def test_occurrence_out_of_range_is_rejected():
    text = "மதுரை ... மதுரை ..."
    with pytest.raises(OccurrenceOutOfBoundsError):
        locate(text, "மதுரை", occurrence=5)


def test_an_ambiguous_quote_builds_once_the_occurrence_is_chosen():
    text = "மதுரை ... மதுரை ..."
    evidence = build_evidence(field(text), "மதுரை", method=RULE, occurrence=1)

    assert evidence.char_start == 10


def test_overlapping_occurrences_are_both_reported():
    spans = find_spans("aaa", "aa")

    assert [(s.char_start, s.char_end) for s in spans] == [(0, 2), (1, 3)]


def test_missing_quote_is_rejected():
    with pytest.raises(QuoteNotFoundError):
        build_evidence(field(), "மதுரை மாவட்ட ஆட்சியர்", method=RULE)


def test_a_quote_that_only_matches_after_normalisation_is_rejected_with_a_hint():
    """Reporting the near-miss is fine; silently normalising the source is not."""
    text = "மதுரை cafe\u0301 office"
    with pytest.raises(QuoteNotFoundError) as excinfo:
        locate(text, "café")

    assert "normalisation" in str(excinfo.value)


def test_matching_is_case_sensitive():
    with pytest.raises(QuoteNotFoundError):
        build_evidence(field("Madurai district"), "madurai", method=RULE)


def test_empty_quote_is_rejected():
    with pytest.raises(EmptyQuoteError):
        locate(CONTENT_TA, "")


def test_whitespace_only_quote_is_rejected():
    with pytest.raises(EmptyQuoteError):
        locate(CONTENT_TA, "   \n ")


def test_an_unresolved_method_cannot_produce_evidence():
    with pytest.raises(ValueError, match="must state how it was produced"):
        build_evidence(field(), "மதுரையில்", method=ExtractionMethod.UNRESOLVED)


@pytest.mark.parametrize("method", [LLM, ExtractionMethod.STATISTICAL])
def test_probabilistic_methods_must_quote_a_confidence(method):
    with pytest.raises(ValueError, match="requires confidence"):
        build_evidence(field(), "மதுரையில்", method=method)

    assert build_evidence(field(), "மதுரையில்", method=method, confidence=0.4).method is method


def test_exact_match_methods_may_omit_confidence():
    evidence = build_evidence(field(), "மதுரையில", method=ExtractionMethod.REGEX)

    assert evidence.confidence is None


def test_field_hash_is_sha256_of_the_whole_original_field():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)

    assert evidence.field_text_hash == compute_field_hash(CONTENT_TA)
    assert evidence.field_text_hash == field_hash(CONTENT_TA)
    assert len(evidence.field_text_hash) == 64
    assert evidence.field_text_hash == evidence.field_text_hash.lower()


def test_hash_covers_the_field_not_the_quote():
    first = build_evidence(field(), "மதுரையில்", method=RULE)
    second = build_evidence(field(), "கமிஷனரிடம்", method=RULE)

    assert first.field_text_hash == second.field_text_hash
    assert first.char_start != second.char_start


def test_a_different_source_text_yields_a_different_hash():
    assert compute_field_hash(CONTENT_TA) != compute_field_hash(CONTENT_TA + " ")


def test_evidence_carries_the_record_and_source_provenance():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)

    assert evidence.record_id == RECORD_ID
    assert evidence.source_id == SOURCE_ID
    assert evidence.source_type == SOURCE_TYPE
    assert evidence.field == "data.content"
    assert evidence.source_url == SOURCE_URL
    assert evidence.raw_reference == SOURCE_URL
    assert evidence.retrieved_at == RETRIEVED_AT
    assert evidence.span_validation is SpanValidation.VALIDATED


def test_evidence_id_is_derived_from_the_record_by_default():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)

    assert evidence.evidence_id.startswith(f"ev-{RECORD_ID}-")


def test_same_fact_from_same_field_reuses_one_evidence_id():
    a = build_evidence(field(), "மதுரையில்", method=RULE)
    b = build_evidence(field(), "மதுரையில்", method=RULE)

    assert a.evidence_id == b.evidence_id
    assert derive_evidence_id(
        field(), locate(CONTENT_TA, "மதுரையில்"), RULE
    ) == a.evidence_id


def test_different_spans_or_methods_get_different_ids():
    span = locate(CONTENT_TA, "மதுரையில்")
    other_method = derive_evidence_id(field(), span, ExtractionMethod.REGEX)
    other_span = derive_evidence_id(field(), locate(CONTENT_TA, "கமிஷனரிடம்"), RULE)
    base = derive_evidence_id(field(), span, RULE)

    assert base != other_method != other_span


def test_an_explicit_evidence_id_is_respected():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE, evidence_id="ev-loc-1")

    assert evidence.evidence_id == "ev-loc-1"


def test_build_evidence_at_accepts_a_span_the_caller_located():
    text = "Madurai district collector office"
    span = Span(quote="collector", char_start=17, char_end=26)
    evidence = build_evidence_at(field(text), span, method=ExtractionMethod.REGEX)

    assert (evidence.char_start, evidence.char_end) == (17, 26)
    assert evidence.quote == "collector"


def test_a_supplied_span_is_still_verified_against_the_text():
    """A regex stage may say where, but never what: a lying range is rejected."""
    with pytest.raises(SpanError, match="does not reproduce"):
        build_evidence_at(
            field(), Span(quote="மதுரை", char_start=0, char_end=6), method=ExtractionMethod.REGEX
        )


def test_a_supplied_span_cannot_point_outside_the_field():
    text = "short field"
    with pytest.raises(SpanError):
        build_evidence_at(
            field(text), Span(quote="short", char_start=9, char_end=20), method=RULE
        )


def test_a_span_running_past_the_end_of_the_field_is_rejected():
    """A truncated slice could coincidentally equal the quote; bounds are checked."""
    with pytest.raises(SpanError, match="runs past the end"):
        build_evidence_at(
            field("abc"), Span(quote="abc", char_start=0, char_end=99), method=RULE
        )


def test_stage_two_output_matches_the_handbuilt_stage_one_fixture():
    """The producer must reproduce what Stage 1 pinned by hand, offset for offset."""
    quote = "4800 மனுக்களை"
    built = build_evidence(field(), quote, method=RULE, confidence=0.95)
    fixture = span_evidence("ev-quantity", quote=quote)

    assert built.char_start == fixture.char_start
    assert built.char_end == fixture.char_end
    assert built.quote == fixture.quote
    assert built.field_text_hash == fixture.field_text_hash


def test_source_field_rejects_a_field_path_with_whitespace():
    with pytest.raises(ValueError, match="dotted path"):
        SourceField(
            record_id=RECORD_ID,
            source_id=SOURCE_ID,
            source_type=SOURCE_TYPE,
            field="data content",
            text=CONTENT_TA,
        )


def test_source_field_refuses_an_empty_text():
    with pytest.raises(ValueError):
        field("")


def test_source_field_is_immutable():
    source = field()
    with pytest.raises(ValueError):
        source.text = CONTENT_TA + " edited"

    assert source.text == CONTENT_TA


def test_modality_defaults_to_text_and_can_be_declared():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    transcript = build_evidence(
        field(), "மதுரையில்", method=RULE, modality=Modality.AUDIO_TRANSCRIPT
    )

    assert evidence.modality is Modality.TEXT
    assert transcript.modality is Modality.AUDIO_TRANSCRIPT


def test_verification_passes_against_the_unchanged_field():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    check = verify_evidence(evidence, CONTENT_TA)

    assert check.ok
    assert check.validation is SpanValidation.VALIDATED
    assert check.expected_hash == check.actual_hash


def test_verification_detects_a_changed_source_field():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    edited = CONTENT_TA.replace("4800 மனுக்களை", "5200 மனுக்களை")
    check = verify_evidence(evidence, edited)

    assert check.validation is SpanValidation.MISMATCH
    assert "changed" in check.reason
    assert check.expected_hash != check.actual_hash


def test_a_mismatch_is_never_repaired():
    """Silently re-finding the quote would make an edited source look honest."""
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    edited = CONTENT_TA.replace("மதுரையில்", "கோவை") + " மதுரையில்"
    revalidated, check = revalidate(evidence, edited)

    assert check.validation is SpanValidation.MISMATCH
    assert revalidated.char_start == evidence.char_start
    assert revalidated.char_end == evidence.char_end
    assert revalidated.quote == evidence.quote
    assert revalidated.span_validation is SpanValidation.MISMATCH


def test_revalidate_records_the_reason_without_rewriting_the_span():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    revalidated, check = revalidate(evidence, CONTENT_TA + " appended")

    assert check.reason in revalidated.notes
    assert revalidated.evidence_id == evidence.evidence_id


def test_an_unchanged_verdict_leaves_the_evidence_untouched():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    revalidated, check = revalidate(evidence, CONTENT_TA)

    assert revalidated is evidence
    assert revalidated.notes is None
    assert check.ok


def test_offsets_are_verified_even_when_the_hash_matches():
    """A hand-typed span over the right field must still fail on its quote."""
    quote = "மதுரை"
    span = locate(CONTENT_TA, quote)
    liar = build_evidence(field(), quote, method=RULE).model_copy(
        update={"char_start": span.char_end, "char_end": span.char_end + len(quote)}
    )
    check = verify_evidence(liar, CONTENT_TA)

    assert check.validation is SpanValidation.MISMATCH
    assert "now hold" in check.reason


def test_evidence_without_a_hash_is_honest_about_being_unproven():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE).model_copy(
        update={"field_text_hash": None}
    )
    check = verify_evidence(evidence, CONTENT_TA)

    assert check.validation is SpanValidation.UNVALIDATED
    assert "unproven" in check.reason


def test_field_level_metadata_evidence_is_not_applicable_rather_than_failed():
    evidence = Evidence(
        evidence_id="ev-meta",
        record_id=RECORD_ID,
        source_id=SOURCE_ID,
        source_type=SOURCE_TYPE,
        field="data.language",
        method=ExtractionMethod.SOURCE_METADATA,
    )
    check = verify_evidence(evidence, CONTENT_TA)

    assert check.validation is SpanValidation.NOT_APPLICABLE
    assert not check.ok


def test_verification_works_through_serialisation():
    evidence = build_evidence(field(), "மதுரையில்", method=RULE)
    restored = Evidence.model_validate_json(evidence.model_dump_json())
    check = verify_evidence(restored, CONTENT_TA)

    assert check.validation is SpanValidation.VALIDATED
    assert restored.field_text_hash == evidence.field_text_hash


def test_tamil_evidence_serialises_without_escaping():
    evidence = build_evidence(field(), "ஆர்ப்பாட்டத்தில்", method=RULE)
    encoded = json.dumps(json.loads(evidence.model_dump_json()), ensure_ascii=False)

    assert "ஆர்ப்பாட்டத்தில்" in encoded
    assert "\\u0b85" not in encoded.lower()


def test_tamil_quote_survives_a_json_round_trip():
    quote = "துாய்மைப் பணியாளர்கள்"
    evidence = build_evidence(field(), quote, method=RULE, confidence=0.9)
    restored = Evidence.model_validate_json(evidence.model_dump_json())

    assert restored == evidence
    assert restored.quote == quote
    assert CONTENT_TA[restored.char_start : restored.char_end] == quote


def test_produced_evidence_plugs_into_an_incident_reference():
    evidence = build_evidence(field(), "ஆர்ப்பாட்டத்தில்", method=RULE, confidence=0.9)
    incident = Incident(
        incident_id="INC-STAGE2-0001",
        status=IncidentStatus.CANDIDATE,
        evidence=[evidence],
        relevance=RelevanceInfo(
            state="incident",
            is_incident=True,
            method=RULE,
            confidence=0.9,
            evidence_ids=[evidence.evidence_id],
        ),
    )

    assert incident.resolve_evidence(evidence.evidence_id) is not None
    assert incident.contributing_record_ids() == [RECORD_ID]


def test_provenance_of_the_title_field_is_addressable():
    source = field("துாய்மைப் பணியாளர்கள் ஆர்ப்பாட்டம்", field="title")
    evidence = build_evidence(
        source, "ஆர்ப்பாட்டம்", method=ExtractionMethod.SOURCE_METADATA
    )

    assert evidence.field == "title"
    assert evidence.is_source_fact
    assert evidence.describe() == f"{RECORD_ID}:title[{evidence.char_start}:{evidence.char_end}]"
