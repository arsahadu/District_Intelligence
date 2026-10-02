"""Evidence is the traceability primitive: it validates, and it cannot lie."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from intelligence.models.enums import ExtractionMethod, SpanValidation
from intelligence.models.evidence import Evidence
from intelligence.tests.builders import CONTENT_TA, RECORD_ID, field_hash, span_evidence


def test_evidence_validates_against_real_tamil_text():
    quote = "4800 மனுக்களை"
    evidence = span_evidence("ev-1", quote=quote)

    assert isinstance(evidence, Evidence)
    assert evidence.record_id == RECORD_ID
    assert evidence.field == "data.content"
    assert evidence.quote == quote
    assert CONTENT_TA[evidence.char_start : evidence.char_end] == quote
    assert evidence.span_validation is SpanValidation.VALIDATED


def test_evidence_offsets_are_derived_from_the_source_text():
    quote = "கமிஷனரிடம்"
    evidence = span_evidence("ev-1", quote=quote)
    start = CONTENT_TA.index(quote)

    assert evidence.char_start == start
    assert evidence.char_end == start + len(quote)
    assert evidence.char_end - evidence.char_start == len(quote)


def test_field_text_hash_matches_sha256_of_original_field():
    evidence = span_evidence("ev-1", quote="மதுரையில்")
    expected = hashlib.sha256(CONTENT_TA.encode("utf-8")).hexdigest()

    assert evidence.field_text_hash == expected == field_hash(CONTENT_TA)


def test_quote_length_must_match_declared_offsets():
    """A hand-written span - the way an LLM would fake one - is rejected."""
    with pytest.raises(ValidationError, match="quote length must equal"):
        Evidence(
            evidence_id="ev-bad",
            record_id=RECORD_ID,
            source_id="dinamalar",
            source_type="news",
            field="data.content",
            method=ExtractionMethod.RULE,
            quote="மதுரையில்",
            char_start=100,
            char_end=104,  # deliberately wrong
        )


def test_non_metadata_evidence_must_pin_a_span():
    with pytest.raises(ValidationError, match="must pin a span"):
        Evidence(
            evidence_id="ev-bad",
            record_id=RECORD_ID,
            source_id="dinamalar",
            source_type="news",
            field="data.content",
            method=ExtractionMethod.LLM,
            confidence=0.9,
        )


def test_metadata_evidence_needs_no_offsets():
    evidence = Evidence(
        evidence_id="ev-meta",
        record_id=RECORD_ID,
        source_id="dinamalar",
        source_type="news",
        field="retrieved_at",
        method=ExtractionMethod.SOURCE_METADATA,
        quote="2026-10-02T06:00:00",
        span_validation=SpanValidation.NOT_APPLICABLE,
    )

    assert evidence.is_source_fact is True
    assert evidence.has_span is False


def test_validated_span_requires_a_hash():
    quote = "மதுரையில்"
    with pytest.raises(ValidationError, match="require field_text_hash"):
        Evidence(
            evidence_id="ev-bad",
            record_id=RECORD_ID,
            source_id="dinamalar",
            source_type="news",
            field="data.content",
            method=ExtractionMethod.RULE,
            quote=quote,
            char_start=0,
            char_end=len(quote),
            span_validation=SpanValidation.VALIDATED,
        )


def test_negative_offsets_rejected():
    with pytest.raises(ValidationError):
        span_evidence("ev-1", quote="மதுரையில்", char_start=-1, char_end=4)


def test_evidence_rejects_invented_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        Evidence(
            evidence_id="ev-1",
            record_id=RECORD_ID,
            source_id="dinamalar",
            source_type="news",
            field="data.content",
            quote="மதுரையில்",
            char_start=0,
            char_end=9,
            hallucinated_field="நல்லது",
        )


def test_describe_points_a_reviewer_at_the_exact_place():
    quote = "மதுரையில்"
    evidence = span_evidence("ev-1", quote=quote)
    start = CONTENT_TA.index(quote)

    assert evidence.describe() == f"{RECORD_ID}:data.content[{start}:{start + len(quote)}]"


def test_confidence_is_constrained_to_the_unit_interval():
    base = {
        "evidence_id": "ev-1",
        "record_id": RECORD_ID,
        "source_id": "dinamalar",
        "source_type": "news",
        "field": "data.content",
        "method": ExtractionMethod.RULE,
        "quote": "மதுரையில்",
        "char_start": 0,
        "char_end": len("மதுரையில்"),
    }

    for legal in (0.0, 1.0, 0.5):
        assert Evidence(**base, confidence=legal).confidence == legal

    for illegal in (-0.01, 1.01, 2, 100, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            Evidence(**base, confidence=illegal)


def test_confidence_rejects_strings_and_booleans():
    """Coercing "0.9" or true into a probability hides bad upstream output."""
    base = {
        "evidence_id": "ev-1",
        "record_id": RECORD_ID,
        "source_id": "dinamalar",
        "source_type": "news",
        "field": "data.content",
        "method": ExtractionMethod.RULE,
        "quote": "மதுரையில்",
        "char_start": 0,
        "char_end": len("மதுரையில்"),
    }

    for illegal in ("0.9", "high", True, False, [0.5]):
        with pytest.raises(ValidationError):
            Evidence(**base, confidence=illegal)
