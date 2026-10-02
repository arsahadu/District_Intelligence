"""Stage 3: suffix candidates, token spans and publish-stamp isolation."""

from __future__ import annotations

import dataclasses
import unicodedata
from datetime import datetime, timezone

import pytest

from intelligence.extraction.boilerplate import (
    BOILERPLATE_SEMANTICS,
    Boilerplate,
    body_without_stamps,
    drop_ranges,
    find_boilerplate,
    split,
)
from intelligence.extraction.language import language_info, representation_id
from intelligence.extraction.morphology import (
    MIN_STEM_LENGTH,
    SUFFIX_RULES,
    MorphologyError,
    analyze_tokens,
    matches_surface,
    suffix_candidates,
    surface_forms,
    tokenize,
)
from intelligence.extraction.spans import SourceField, SpanError, locate
from intelligence.models.enums import ExtractionMethod, ScriptType, SpanValidation
from intelligence.models.enums import TextRole, TimeSemantics
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
ADDED_STAMP = "அக் 01, 2026 09:46 PM"
UPDATED_STAMP = "அக் 02, 2026 12:00 AM"
BODY_START = CONTENT_TA.index("அவர்")
BODY = CONTENT_TA[BODY_START:]


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


def stems(word: str) -> list[tuple[str, str, str]]:
    return [(c.stem, c.suffix, c.label) for c in suffix_candidates(word)]


def test_locative_suffix_resolves_to_the_dictionary_stem():
    assert ("மதுரை", "யில்", "locative") in stems("மதுரையில்")


def test_plural_and_accusative_suffixes():
    assert ("மனு", "க்களை", "accusative plural") in stems("மனுக்களை")
    assert ("ஊழியர்", "கள்", "nominative plural") in stems("ஊழியர்கள்")


def test_a_dropped_ampul_is_offered_back_as_the_stem():
    restored = [c for c in suffix_candidates("அலுவலகத்தில்") if c.suffix == "த்தில்"]
    assert [(c.stem, c.rule) for c in restored] == [
        ("அலுவலக", "strip-suffix"),
        ("அலுவலகம்", "strip-suffix+restore-am"),
    ]
    assert all(c.form == "அலுவலகத்தில்" for c in restored)


def test_a_dropped_pulli_is_offered_back_as_the_stem():
    assert ("கமிஷனர்", "ிடம்", "dative locative") in stems("கமிஷனரிடம்")


def test_suffix_arithmetic_uses_code_points_not_bytes_or_glyphs():
    assert len("யில்") == 4
    assert len("த்தில்") == 6
    candidate = suffix_candidates("ஆர்ப்பாட்டத்தில்")[0]
    assert (candidate.stem, candidate.suffix) == ("ஆர்ப்பாட்ட", "த்தில்")
    assert len(candidate.stem) + len(candidate.suffix) == len("ஆர்ப்பாட்டத்தில்")
    assert candidate.stem.encode("utf-8") != candidate.stem.encode("utf-16")


def test_suffix_surfaces_are_spelled_with_vowel_signs():
    assert "கிளையில்" != "கிளைஇல்"
    assert ("கிளை", "யில்", "locative") in stems("கிளையில்")
    assert all(c.stem != "கிளைஇ" for c in suffix_candidates("கிளையில்"))


def test_every_candidate_carries_the_word_exactly_as_printed():
    for candidate in suffix_candidates("மதுரையில்"):
        assert candidate.form == "மதுரையில்"
        assert len(candidate.stem) <= len(candidate.form)
    for candidate in suffix_candidates("மதுரையில்"):
        if candidate.rule == "strip-suffix":
            assert "மதுரையில்".startswith(candidate.stem)
    assert suffix_candidates("மதுரையில்")[-1].rule == "as-printed"


def test_a_candidate_is_only_citable_through_its_printed_form():
    for candidate in suffix_candidates("ஆர்ப்பாட்டத்தில்"):
        assert locate(CONTENT_TA, candidate.form).quote == candidate.form
    with pytest.raises(SpanError, match="does not occur"):
        locate(CONTENT_TA, "ஆர்ப்பாட்டம்")


def test_the_longest_suffix_is_offered_first():
    candidates = suffix_candidates("அலுவலகத்திலிருந்து")
    lengths = [len(c.suffix) for c in candidates]
    assert lengths == sorted(lengths, reverse=True)
    assert candidates[0].suffix == "த்திலிருந்து"
    assert candidates[0].stem == "அலுவலக"
    assert ("அலுவலகம்", "த்திலிருந்து", "ablative") in stems("அலுவலகத்திலிருந்து")
    assert ("அலுவலகத்த", "ிலிருந்து", "ablative") in stems("அலுவலகத்திலிருந்து")


def test_plural_then_case_is_only_read_in_one_step():
    """One suffix is cut at a time, so the residue stays a fragment, not a compound rule."""
    candidates = suffix_candidates("அலுவலகங்களில்")
    assert candidates[0].suffix == "களில்"
    assert candidates[0].stem == "அலுவலகங்"
    assert not any(c.stem == "அலுவலகம்" for c in candidates)


def test_words_that_cannot_be_analysed_keep_their_printed_form():
    for word in ["4800", "கிளை", "விதித்தது"]:
        assert [c.rule for c in suffix_candidates(word)] == ["as-printed"]


def test_a_fragment_is_not_reported_as_a_stem():
    assert [c.rule for c in suffix_candidates("ில்")] == ["as-printed"]
    assert MIN_STEM_LENGTH == 2
    with pytest.raises(MorphologyError, match="empty word"):
        suffix_candidates("")


def test_surface_forms_round_trip_into_the_printed_word():
    cases = {
        "மதுரை": "மதுரையில்",
        "அலுவலகம்": "அலுவலகத்தில்",
        "ஆர்ப்பாட்டம்": "ஆர்ப்பாட்டத்தில்",
        "மனு": "மனுக்களை",
        "கமிஷனர்": "கமிஷனரிடம்",
        "ஊழியர்": "ஊழியர்கள்",
        "கிளை": "கிளையில்",
        "கும்பகோணம்": "கும்பகோணத்தில்",
    }
    for stem, printed in cases.items():
        candidate = matches_surface(printed, stem)
        assert candidate is not None, (stem, printed)
        assert candidate.stem == stem
        assert candidate.rule.startswith("concatenate")


def test_an_unmodelled_junction_is_a_miss_not_a_guess():
    """The ``வழக்கு`` -> ``வழக்கில்`` u-drop is unmodelled, so it reports no match."""
    assert matches_surface("வழக்கில்", "வழக்கு") is None
    assert [c.stem for c in suffix_candidates("வழக்கில்") if c.suffix == "ில்"] == [
        "வழக்க",
        "வழக்க்",
    ]


def test_surface_forms_refuse_illegal_junctions():
    madurai = {f.form for f in surface_forms("மதுரை")}
    assert "மதுரையில்" in madurai
    assert "மதுரைஇல்" not in madurai

    office = {f.form for f in surface_forms("அலுவலகம்")}
    assert "அலுவலகத்தில்" in office
    assert "அலுவலகம்தில்" not in office
    assert [f for f in office if f.startswith("அலுவலகம்")] == ["அலுவலகம்"]

    branch = {f.form for f in surface_forms("கிளை")}
    assert "கிளையில்" in branch
    assert "கிளைில்" not in branch


def test_an_unrelated_stem_does_not_match():
    assert matches_surface("மதுரையில்", "சென்னை") is None


def test_the_suffix_table_is_ordered_and_labelled():
    lengths = [len(suffix) for suffix, _ in SUFFIX_RULES]
    assert lengths == sorted(lengths, reverse=True)
    assert all(label for _, label in SUFFIX_RULES)
    assert len({suffix for suffix, _ in SUFFIX_RULES}) == len(SUFFIX_RULES)


def test_tokenize_keeps_tamil_syllables_whole_and_located():
    tokens = tokenize(CONTENT_TA)
    assert tokens[0].quote == "UPDATED"
    for token in tokens:
        assert token.slice_from(CONTENT_TA) == token.quote
        assert token.char_end - token.char_start == len(token.quote)
        assert unicodedata.category(token.quote[0]) not in ("Mn", "Mc", "Me")


def test_punctuation_splits_tokens_without_eating_offsets():
    text = "தடை; வழக்கு\nமதுரை"
    tokens = tokenize(text)
    assert [t.quote for t in tokens] == ["தடை", "வழக்கு", "மதுரை"]
    assert [t.char_start for t in tokens] == [0, 5, 12]
    assert [len(t.quote) for t in tokens] == [3, 6, 5]


def test_analyse_tokens_covers_a_real_fixture():
    analysed = analyze_tokens(BENCH_CONTENT_TA)
    assert "கும்பகோணத்தில்" in analysed
    assert any(c.stem == "கும்பகோணம்" for c in analysed["கும்பகோணத்தில்"])
    assert "விதித்தது" in analysed


def test_both_stamps_are_found_in_the_real_fixture():
    items = find_boilerplate(CONTENT_TA)
    assert [item.label for item in items] == ["UPDATED", "ADDED"]
    assert [item.raw_text for item in items] == [
        f"UPDATED : {UPDATED_STAMP}",
        f"ADDED : {ADDED_STAMP}",
    ]
    for item in items:
        assert item.span().slice_from(CONTENT_TA) == item.raw_text


def test_stamp_timestamps_are_pinned_separately_from_the_label():
    added = find_boilerplate(CONTENT_TA)[1]
    span = added.timestamp_span()
    assert span.quote == ADDED_STAMP
    assert span.char_start == CONTENT_TA.index(ADDED_STAMP)
    assert span.slice_from(CONTENT_TA) == ADDED_STAMP
    assert added.raw_text.index(ADDED_STAMP) == len("ADDED : ")


def test_stamps_are_publication_metadata_and_never_event_time():
    items = find_boilerplate(CONTENT_TA)
    assert {item.semantics for item in items} == {TimeSemantics.PUBLICATION_TIME}
    assert BOILERPLATE_SEMANTICS is TimeSemantics.PUBLICATION_TIME
    for item in items:
        for field_name in dataclasses.fields(item):
            assert not isinstance(getattr(item, field_name.name), datetime)


def test_stamp_evidence_validates_and_says_what_it_is_not():
    produced = split(field()).timestamp_evidence()
    assert [e.quote for e in produced] == [UPDATED_STAMP, ADDED_STAMP]
    for evidence in produced:
        assert "not event time" in evidence.notes
        assert evidence.span_validation is SpanValidation.VALIDATED
        assert locate(CONTENT_TA, evidence.quote).char_start == evidence.char_start


def test_the_body_is_a_new_representation_not_a_replacement():
    source = field()
    result = split(source)
    assert result.has_boilerplate
    assert result.body.original == CONTENT_TA
    assert result.body.text == BODY
    assert "UPDATED" not in result.body.text
    assert source.text.startswith("UPDATED")


def test_body_offsets_point_at_the_untouched_original():
    body = split(field()).body
    span = body.span_for("மதுரையில்")
    assert span.char_start == CONTENT_TA.index("மதுரையில்")
    assert span.slice_from(CONTENT_TA) == "மதுரையில்"
    assert body.offsets_align_with_source is False


def test_evidence_built_from_the_body_validates_against_the_original_field():
    source = field()
    evidence = split(source).body.evidence(source, "4800", method=ExtractionMethod.REGEX)
    assert evidence.char_start == CONTENT_TA.index("4800")
    assert evidence.span_validation is SpanValidation.VALIDATED


def test_a_field_without_stamps_is_returned_unchanged():
    result = split(field(BENCH_CONTENT_TA))
    assert result.items == ()
    assert result.body.text == BENCH_CONTENT_TA
    assert result.body.offsets_align_with_source is True
    assert result.has_boilerplate is False


def test_drop_ranges_extend_over_the_space_after_each_stamp():
    items = find_boilerplate(CONTENT_TA)
    assert drop_ranges(CONTENT_TA, items)[0] == (0, CONTENT_TA.index("ADDED"))
    assert body_without_stamps(field(), items).text == BODY


def test_full_month_without_a_time_is_still_a_stamp():
    text = "ADDED : October 2, 2026 The collector reviewed the file."
    items = find_boilerplate(text)
    assert [(i.label, i.timestamp_text) for i in items] == [("ADDED", "October 2, 2026")]
    assert items[0].span().slice_from(text) == "ADDED : October 2, 2026"


def test_a_label_without_a_timestamp_is_still_isolated():
    text = "Updated : மதுரை நகரம்"
    items = find_boilerplate(text)
    assert [(i.label, i.timestamp_text) for i in items] == [("UPDATED", None)]
    assert items[0].timestamp_span() is None
    assert body_without_stamps(field(text), items).text == "மதுரை நகரம்"


def test_a_stamp_label_never_reaches_the_body_representation():
    text = "UPDATED : அக் 02, 2026 12:00 AM ADDED : மதுரை"
    items = find_boilerplate(text)
    assert [i.label for i in items] == ["UPDATED", "ADDED"]
    assert items[1].timestamp_text is None
    assert body_without_stamps(field(text), items).text == "மதுரை"


def test_body_representation_plugs_into_the_language_contract():
    source = field()
    result = split(source)
    parent = representation_id(source)
    representation = result.representation(
        representation_id="rep-body",
        derived_from=parent,
        language="ta",
        script=ScriptType.TAMIL,
    )
    info = language_info([source], derived_representations=[representation])
    LanguageInfo.model_validate(info.model_dump())
    assert [r.role for r in info.text_representations] == [TextRole.SOURCE, TextRole.NORMALIZED]
    assert info.text_representations[1].provider == "intelligence.extraction.boilerplate"
    assert info.source_texts()[0].text == CONTENT_TA


def test_a_boilerplate_record_holds_printing_not_interpretation():
    item = Boilerplate(label="ADDED", raw_text="ADDED :", char_start=0, char_end=7)
    assert item.as_dict() == {
        "label": "ADDED",
        "raw_text": "ADDED :",
        "char_start": 0,
        "char_end": 7,
        "timestamp_text": None,
        "semantics": "publication_time",
    }
    assert TITLE_TA.count("UPDATED") == 0
