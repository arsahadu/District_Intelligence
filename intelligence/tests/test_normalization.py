"""Stage 3: derived text never replaces the source and always maps back to it."""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

import pytest

from intelligence.extraction.language import language_info, representation_id
from intelligence.extraction.normalize import (
    MappedText,
    MappingError,
    build_mapped_text,
    cluster_end,
    normalize,
    normalize_field,
)
from intelligence.extraction.spans import (
    AmbiguousQuoteError,
    SourceField,
    Span,
    verify_evidence,
)
from intelligence.models.enums import ExtractionMethod, ScriptType, SpanValidation, TextRole
from intelligence.models.language import LanguageInfo
from intelligence.tests.builders import CONTENT_TA, RECORD_ID, SOURCE_ID, SOURCE_TYPE, SOURCE_URL

RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RULE = ExtractionMethod.RULE
STAMP_PREFIX = "UPDATED : அக் 02, 2026 12:00 AM ADDED : அக் 01, 2026 09:46 PM "


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


def test_original_text_is_never_replaced():
    source = field()
    derived = normalize(source.text)
    assert derived.original == CONTENT_TA
    assert source.text == CONTENT_TA
    assert CONTENT_TA.startswith("UPDATED :")
    assert not derived.text.startswith("UPDATED :")


def test_normalization_records_what_it_did():
    derived = normalize("Madurai  City", lowercase=True, collapse_whitespace=True)
    assert derived.steps == ("unicode:NFC", "lowercase", "collapse_whitespace")
    assert derived.text == "madurai city"


def test_whitespace_runs_collapse_without_being_stripped():
    derived = normalize("  மதுரை   அலுவலகம்.  ")
    assert derived.text == " மதுரை அலுவலகம். "
    assert derived.project(1, 3).quote == "மது"
    assert derived.project(1, 3).char_start == 2


def test_newlines_are_positions_too():
    derived = normalize("first line\n\nsecond line")
    assert derived.text == "first line second line"
    span = derived.span_for("second")
    assert span.as_dict() == {"quote": "second", "char_start": 12, "char_end": 18}
    assert span.slice_from("first line\n\nsecond line") == "second"


def test_a_term_found_in_derived_text_maps_back_onto_the_original():
    source = field()
    body = normalize(source.text, drop_ranges=[(0, len(STAMP_PREFIX))])
    span = body.span_for("மதுரையில்")
    assert span.char_start == CONTENT_TA.index("மதுரையில்")
    assert span.char_end == span.char_start + len("மதுரையில்")
    assert span.slice_from(CONTENT_TA) == "மதுரையில்"
    assert span.char_start != body.text.index("மதுரையில்")


def test_evidence_from_derived_text_validates_against_the_original():
    source = field()
    evidence = normalize_field(source).evidence(
        source, "மதுரையில்", method=ExtractionMethod.DICTIONARY
    )
    assert evidence.quote == "மதுரையில்"
    assert evidence.char_start == CONTENT_TA.index("மதுரையில்")
    check = verify_evidence(evidence, CONTENT_TA)
    assert check.validation is SpanValidation.VALIDATED


def test_derived_offsets_cannot_be_cited_against_a_different_field():
    with pytest.raises(MappingError, match="never from a derived copy"):
        normalize_field(field()).evidence(
            field("மதுரை வேலை நிறுத்தம்"), "அவர்", method=RULE
        )


def test_ambiguity_survives_derivation_and_selecting_one_is_explicit():
    text = "மதுரை மதுரை"
    derived = normalize(text, lowercase=False, unicode_form=None, collapse_whitespace=False)
    assert [span.as_dict() for span in derived.spans_for("மதுரை")] == [
        {"quote": "மதுரை", "char_start": 0, "char_end": 5},
        {"quote": "மதுரை", "char_start": 6, "char_end": 11},
    ]
    with pytest.raises(AmbiguousQuoteError):
        derived.span_for("மதுரை")
    assert derived.span_for("மதுரை", occurrence=-1).char_start == 6


def test_offsets_align_with_source_is_only_true_for_unchanged_text():
    assert normalize("நீதிமன்றம்").offsets_align_with_source is True
    assert normalize("Madurai").offsets_align_with_source is False
    assert normalize("மதுரை  ஊர்").offsets_align_with_source is False


def test_tamil_combining_marks_are_never_split():
    text = "உயர்நீதிமன்ற மதுரைக் கிளை"
    assert cluster_end(text, 0) == 1
    assert cluster_end(text, 4) == 6
    assert cluster_end("நீ", 0) == 2
    assert cluster_end("்", 0) == 1

    derived = normalize(text)
    assert derived.text == text
    assert derived.positions[:4] == ((0, 1), (1, 2), (2, 4), (2, 4))
    for start, end in set(derived.positions):
        assert unicodedata.category(text[start]) not in ("Mn", "Mc", "Me")
        assert text[start:end]


def test_a_decomposed_accent_composes_without_losing_source_width():
    text = "கிளைa\u0302"
    derived = normalize(text)
    assert derived.text == "கிளைâ"
    span = derived.span_for("â")
    assert span.as_dict() == {"quote": "a\u0302", "char_start": 4, "char_end": 6}
    assert span.slice_from(text) == "a\u0302"


def test_punctuation_is_kept_because_the_source_is_authoritative():
    derived = normalize("தடை; விதித்தது.")
    assert derived.text == "தடை; விதித்தது."


def test_drop_ranges_are_expressed_in_original_coordinates():
    cut = normalize(CONTENT_TA, drop_ranges=[(0, CONTENT_TA.index("அவர்"))])
    assert cut.text.startswith("அவர்")
    assert cut.span_for("அவர்").char_start == CONTENT_TA.index("அவர்")


def test_bad_drop_ranges_are_refused():
    with pytest.raises(MappingError, match="outside a"):
        normalize("மதுரை", drop_ranges=[(0, 99)])
    with pytest.raises(MappingError, match="overlap"):
        normalize("மதுரை", drop_ranges=[(0, 3), (2, 4)])


def test_pieces_that_reorder_the_source_are_refused():
    with pytest.raises(MappingError, match="overlaps or runs past"):
        build_mapped_text("மதுரை", [("a", 3, 5), ("b", 0, 3)])


def test_projection_refuses_an_empty_or_inverted_range():
    derived = normalize("மதுரை ஊர்")
    with pytest.raises(MappingError, match="is outside"):
        derived.project(4, 4)
    with pytest.raises(MappingError, match="is outside"):
        derived.project(5, 2)
    with pytest.raises(MappingError, match="derived index"):
        derived.source_range(99)


def test_a_total_derivation_produces_no_representation():
    derived = normalize("மதுரை", drop_ranges=[(0, len("மதுரை"))])
    assert derived.text == ""
    with pytest.raises(MappingError, match="must hold something"):
        derived.to_representation(
            representation_id="rep-x", derived_from="rep-source", language="ta",
            script=ScriptType.TAMIL,
        )


def test_normalized_representation_names_its_parent():
    source = field()
    parent = representation_id(source)
    derived = normalize_field(source)
    representation = derived.to_representation(
        representation_id="rep-normalized",
        derived_from=parent,
        language="ta",
        script=ScriptType.TAMIL,
    )
    assert representation.role is TextRole.NORMALIZED
    assert representation.derived_from == parent
    assert representation.provider == "intelligence.extraction.normalize"
    assert representation.offsets_align_with_source is False
    assert representation.text != source.text


def test_normalized_representation_plugs_into_the_language_contract():
    source = field()
    parent = representation_id(source)
    representation = normalize_field(source).to_representation(
        representation_id="rep-normalized",
        derived_from=parent,
        language="ta",
        script=ScriptType.TAMIL,
    )
    info = language_info([source], derived_representations=[representation])
    LanguageInfo.model_validate(info.model_dump())
    assert [r.role for r in info.text_representations] == [TextRole.SOURCE, TextRole.NORMALIZED]
    assert info.source_texts()[0].text == CONTENT_TA
    assert info.translation_texts() == []


def test_a_derived_representation_without_a_parent_is_refused():
    with pytest.raises(ValueError, match="must declare derived_from"):
        normalize(CONTENT_TA).to_representation(
            representation_id="rep-orphan", derived_from=None, language="ta",
            script=ScriptType.TAMIL,
        )


def test_mapped_text_is_a_value_not_a_mutating_object():
    derived = normalize("மதுரை  ஊர்")
    assert isinstance(derived, MappedText)
    with pytest.raises(AttributeError):
        derived.text = "மாறு"
