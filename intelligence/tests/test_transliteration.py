"""Stage 3: Latin candidates that keep the printed Tamil recoverable."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from intelligence.extraction.language import (
    language_info,
    representation_id,
    source_representation,
)
from intelligence.extraction.normalize import MappingError, normalize_field
from intelligence.extraction.spans import (
    QuoteNotFoundError,
    SourceField,
    find_spans,
    locate,
)
from intelligence.extraction.transliteration import (
    PROVIDER,
    VOCABULARY,
    TransliterationCandidate,
    VocabularyTerm,
    candidates,
    candidates_for_text,
    render_cluster,
    representation,
    transliterate,
    transliterated_field,
    vocabulary_term,
)
from intelligence.models.enums import ExtractionMethod, ScriptType, SpanValidation, TextRole
from intelligence.models.language import LanguageInfo, TextRepresentation
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


def forms(word: str) -> list[str]:
    return [candidate.latin for candidate in candidates(word)]


def kinds(word: str) -> list[tuple[str, str, str]]:
    return [
        (candidate.latin, candidate.basis, candidate.method.value) for candidate in candidates(word)
    ]


def test_one_cluster_renders_one_syllable():
    assert render_cluster("க") == "ka"
    assert render_cluster("கி") == "ki"
    assert render_cluster("லே") == "lee"
    assert render_cluster("ழ") == "zha"
    assert render_cluster("அ") == "a"
    assert transliterate("மது").text == "mathu"


def test_a_pulli_kills_the_inherent_vowel():
    assert render_cluster("க்") == "k"
    assert render_cluster("ன்") == "nn"
    assert render_cluster("க்") + render_cluster("க") == "kka"


def test_text_outside_the_table_passes_through():
    assert render_cluster("A") == "A"
    assert render_cluster("4") == "4"
    assert render_cluster("") == ""
    assert transliterate("Madurai 4800").text == "Madurai 4800"


def test_transliteration_never_touches_the_original():
    source = "மதுரையில் உள்ள மாநகராட்சி"
    mapped = transliterate(source)
    assert mapped.original == source
    assert mapped.text == "mathuraiyil ulla maanakaraatsi"
    assert mapped.steps == ("tamil-transliteration",)
    assert mapped.offsets_align_with_source is False


def test_every_latin_character_knows_which_tamil_it_came_from():
    mapped = transliterate("லேண்ட்")
    assert len(mapped.positions) == len(mapped.text)
    assert mapped.project(0, len(mapped.text)).as_dict() == {
        "quote": "லேண்ட்",
        "char_start": 0,
        "char_end": 6,
    }
    assert mapped.project(0, 2).quote == "லே"
    assert mapped.project(3, 5).quote == "ண்ட்"


def test_a_field_transliterates_without_losing_a_character():
    source = field()
    mapped = transliterated_field(source)
    assert mapped.original is source.text
    assert mapped.positions[-1] == (len(source.text) - 1, len(source.text))
    assert mapped.project(0, len(mapped.text)).char_start == 0


def test_transliteration_is_idempotent_on_latin_text():
    assert transliterate("Madurai").text == "Madurai"


def test_a_loanword_keeps_the_latin_spelling_it_came_from():
    assert kinds("லேண்ட்")[0] == ("Land", "vocabulary", "dictionary")
    assert kinds("கமிஷனர்")[0] == ("Commissioner", "vocabulary", "dictionary")
    assert candidates("கமிஷனர்")[0].note == "loanword spelling"
    assert candidates("லேண்ட்")[0].is_from_vocabulary is True


def test_the_table_rendering_is_offered_alongside_the_vocabulary():
    assert forms("கமிஷனர்") == ["Commissioner", "kamishannar"]
    assert kinds("கமிஷனர்")[1] == ("kamishannar", "table", "rule")
    assert candidates("கமிஷனர்")[1].is_from_vocabulary is False


def test_a_suffix_keeps_the_stem_spelling_visible():
    commissioner = candidates("கமிஷனரிடம்")[0]
    assert commissioner.latin == "Commissioner"
    assert commissioner.basis == "vocabulary of stem"
    assert commissioner.stem == "கமிஷனர்"
    assert candidates("மதுரையில்")[0].stem == "மதுரை"
    assert forms("மதுரையில்")[0] == "Madurai"


def test_place_names_from_the_real_fixtures():
    assert forms("மாநகராட்சி")[0] == "Managaram"
    assert forms("கும்பகோணத்தில்")[0] == "Kumbakonam"
    assert vocabulary_term("சென்னை").latins == ("Chennai",)
    assert vocabulary_term("தஞ்சாவூர்").latins == ("Thanjavur",)
    assert vocabulary_term("விதித்தது") is None


def test_a_word_with_no_vocabulary_entry_still_gets_the_table():
    assert kinds("ஆர்ப்பாட்டத்தில்") == [("aarppaattaththil", "table", "rule")]
    assert forms("அலுவலகத்தில்") == ["aluvalakaththil"]


def test_text_that_is_already_latin_produces_no_candidate():
    assert candidates("Madurai") == []
    assert candidates("4800") == []
    assert candidates("UPDATED") == []


def test_the_printed_tamil_form_is_carried_by_every_candidate():
    for candidate in candidates("மதுரையில்"):
        assert candidate.form == "மதுரையில்"
        assert locate(CONTENT_TA, candidate.form).quote == "மதுரையில்"
    for candidate in candidates("லேண்ட்"):
        assert candidate.as_dict()["form"] == "லேண்ட்"


def test_candidates_are_unique_and_strongest_first():
    lowered = [latin.lower() for latin in forms("மதுரையில்")]
    assert len(set(lowered)) == len(lowered)
    with pytest.raises(ValueError, match="empty word"):
        candidates("")


def test_the_fixture_vocabulary_maps_spellings_not_meanings():
    assert VOCABULARY
    for term in VOCABULARY.values():
        assert term.latins
        assert term.note
        for latin in term.latins:
            assert latin.isascii()
            assert latin.strip() == latin
            assert " " not in latin
            assert latin != term.source
        assert vocabulary_term(term.source) is term


def test_candidates_for_text_covers_the_fixture_and_stays_locatable():
    analysed = candidates_for_text(CONTENT_TA)
    assert "லேண்ட்" in analysed
    assert "கமிஷனரிடம்" in analysed
    assert "மதுரையில்" in analysed
    assert analysed["நிறுவன"]
    for word, produced in analysed.items():
        assert find_spans(CONTENT_TA, word), word
        for candidate in produced:
            assert candidate.form == word


def test_a_bench_fixture_word_and_a_title_word_both_read():
    analysed = candidates_for_text(BENCH_CONTENT_TA)
    assert analysed["கும்பகோணத்தில்"][0].latin == "Kumbakonam"
    assert candidates_for_text(TITLE_TA)["அலுவலகத்தில்"][0].latin == "aluvalakaththil"


def test_a_latin_hit_becomes_evidence_on_the_tamil_it_was_cut_from():
    source = field()
    latin = transliterated_field(source)
    evidence = latin.evidence(source, "leent", method=ExtractionMethod.RULE)
    assert evidence.quote == "லேண்ட்"
    assert evidence.char_start == CONTENT_TA.index("லேண்ட்")
    assert evidence.char_end == evidence.char_start + len("லேண்ட்")
    assert evidence.span_validation is SpanValidation.VALIDATED


def test_the_tamil_word_is_not_found_in_the_latin_representation():
    latin = transliterated_field(field())
    with pytest.raises(QuoteNotFoundError):
        latin.span_for("லேண்ட்")


def test_evidence_refuses_a_word_mapped_from_a_slice_instead_of_the_field():
    latin = transliterate("லேண்ட்")
    with pytest.raises(MappingError, match="never from a derived copy"):
        latin.evidence(field(), "leent", method=ExtractionMethod.RULE)


def test_transliterated_representation_is_labelled_as_derived():
    representation_item = representation(
        transliterated_field(field()),
        representation_id="rep-latin",
        derived_from=representation_id(field()),
        language="ta",
    )
    assert representation_item.role is TextRole.TRANSLITERATED
    assert representation_item.script is ScriptType.LATIN
    assert representation_item.language == "ta"
    assert representation_item.method is ExtractionMethod.RULE
    assert representation_item.provider == PROVIDER
    assert representation_item.offsets_align_with_source is False
    assert representation_item.text == transliterate(CONTENT_TA).text


def test_a_transliterated_representation_must_name_its_parent():
    with pytest.raises(ValueError, match="derived_from"):
        TextRepresentation(
            representation_id="rep-latin",
            text="mathurai",
            role=TextRole.TRANSLITERATED,
            language="ta",
            script=ScriptType.LATIN,
            method=ExtractionMethod.RULE,
        )


def test_the_original_tamil_survives_alongside_the_latin_reading():
    source = field()
    parent = representation_id(source)
    assembled = language_info(
        [source],
        derived_representations=[
            normalize_field(source).to_representation(
                representation_id="rep-normalized",
                derived_from=parent,
                language="ta",
                script=ScriptType.TAMIL,
            ),
            representation(
                transliterated_field(source),
                representation_id="rep-latin",
                derived_from=parent,
                language="ta",
            ),
        ],
    )
    reloaded = LanguageInfo.model_validate(json.loads(assembled.model_dump_json()))
    assert [rep.role for rep in reloaded.text_representations] == [
        TextRole.SOURCE,
        TextRole.NORMALIZED,
        TextRole.TRANSLITERATED,
    ]
    assert reloaded.text_representations[0].text == CONTENT_TA
    assert reloaded.text_representations[2].derived_from == parent
    assert reloaded.text_representations[2].language == "ta"
    assert reloaded.text_representations[2].script is ScriptType.LATIN


def test_language_info_refuses_a_source_representation_it_would_duplicate():
    source = field()
    with pytest.raises(ValueError, match="SOURCE representations"):
        language_info(
            [source],
            derived_representations=[
                source_representation(source, representation_id_override="rep-source")
            ],
        )


def test_a_transliterated_candidate_is_a_spelling_not_a_translation():
    candidate = TransliterationCandidate(
        form="லேண்ட்", latin="Land", basis="vocabulary", method=ExtractionMethod.DICTIONARY
    )
    assert candidate.as_dict() == {
        "form": "லேண்ட்",
        "latin": "Land",
        "basis": "vocabulary",
        "method": "dictionary",
        "note": None,
        "stem": None,
    }
    assert VocabularyTerm("கமிஷனர்", ("Commissioner",), "loanword spelling").latins == (
        "Commissioner",
    )
