"""Stage 6's substrate: a printed Tamil word, every stem it can be read as, and the row it points at."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from intelligence.config import mention_words as V
from intelligence.extraction import boilerplate, normalize
from intelligence.extraction.mention_text import (
    ACTOR_LEXICON,
    PLACE_LEXICON,
    Entry,
    Lexicon,
    MIN_STEM,
    MentionTextError,
    build_prose,
    contiguous,
    fold,
    not_an_entity,
    blocked,
    phrase,
    quote_of,
    read_word,
    stem_candidates,
)
from intelligence.extraction.spans import SourceField

RECORD_ID = "MENTION-0001"
SOURCE_ID = "dinamalar"
SOURCE_TYPE = "news"
FIELD = "data.content"
URL = "https://example.com/mention"
RETRIEVED_AT = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)

STAMPED = "UPDATED : அக் 03, 2026 12:00 AM\nமதுரையில் பேட்டி நடந்தது."


def field(text: str = "மதுரையில் பேட்டி நடந்தது.", **overrides) -> SourceField:
    payload = {
        "record_id": RECORD_ID,
        "source_id": SOURCE_ID,
        "source_type": SOURCE_TYPE,
        "field": FIELD,
        "text": text,
        "source_url": URL,
        "raw_reference": URL,
        "retrieved_at": RETRIEVED_AT,
    }
    payload.update(overrides)
    return SourceField(**payload)


def prose(text: str = "மதுரையில் பேட்டி நடந்தது.", *, stamps: bool = False):
    source = field(text)
    split = boilerplate.split(source, lowercase=True) if stamps else None
    body = split.body if split else normalize(text, lowercase=True)
    return build_prose(source, body, split=split)


def lexicon(rows: tuple[Entry, ...], name: str = "test") -> Lexicon:
    return Lexicon(name, (("GROUP", rows),), lambda surface: False)


def entry(surface: str, claim: tuple[str, ...], latin: tuple[str, ...] = ()) -> Entry:
    return Entry(
        surface=surface,
        lexicon="GROUP",
        kind="known-name",
        claim=claim,
        latin=latin,
        note="test row",
        row=surface,
    )


def test_fold_drops_a_final_am_and_a_final_pulli_but_nothing_else():
    assert fold("நிர்வாகம்") == "நிர்வாக"
    assert fold("ரயில்") == "ரயில"
    assert fold("மதுரை") == "மதுரை"
    assert fold("Madurai") == "madurai"


def test_a_plain_noun_can_be_an_entity_and_still_be_no_place():
    assert blocked("நிர்வாகம்") is True
    assert not_an_entity("நிர்வாகம்") is False
    assert blocked("மதுரை") is False
    assert not_an_entity("மதுரை") is False


@pytest.mark.parametrize(
    "surface", [next(iter(V.CALENDAR_TOKENS)), next(iter(V.LATIN_STOP_WORDS))]
)
def test_a_word_no_mention_can_be_made_from_is_refused_by_both_families(surface: str):
    assert blocked(surface) is True
    assert not_an_entity(surface) is True


def test_the_printed_form_is_read_before_anything_is_done_to_it():
    match = read_word("மதுரை", PLACE_LEXICON)

    assert match is not None
    assert (match.rule, match.label, match.stem) == ("as-printed", "none", "மதுரை")
    assert match.entry.surface == "மதுரை"
    assert match.entry.claim == ("district", "district")
    assert match.inflected is False


def test_a_locative_form_reaches_the_name_it_was_made_from():
    match = read_word("மதுரையில்", PLACE_LEXICON)

    assert match is not None
    assert match.entry.surface == "மதுரை"
    assert (match.stem, match.label, match.rule) == ("மதுரை", "locative", "strip-suffix")
    assert match.is_locative is True
    assert match.inflected is True


def test_a_blocklisted_stem_ends_the_place_reading_even_when_inflected():
    assert blocked("ரயில்") is True
    assert read_word("ரயில்", PLACE_LEXICON) is None
    assert read_word("ரயிலில்", PLACE_LEXICON) is None


def test_an_emphasis_clitic_still_reaches_the_body_it_names():
    match = read_word("நிர்வாகமும்", ACTOR_LEXICON)

    assert match is not None
    assert match.entry.surface == "நிர்வாகம்"
    assert (match.stem, match.label, match.rule) == ("நிர்வாக", "emphasis", "strip-tail")


def test_a_refused_stem_on_the_way_down_does_not_condemn_the_word():
    assert not_an_entity("பயணம்") is True

    match = read_word("பயணிகளும்", ACTOR_LEXICON)
    assert match is not None
    assert match.entry.surface == "பயணி"
    assert match.label == "nominative plural"


@pytest.mark.parametrize("surface", ["Madurai", "MADURAI", "madurai"])
def test_a_latin_spelling_reaches_the_row_that_lists_it(surface: str):
    match = read_word(surface, PLACE_LEXICON)

    assert match is not None
    assert match.entry.surface == "மதுரை"
    assert (match.via, match.rule) == ("latin", "latin-spelling")


def test_a_latin_type_word_is_read_from_its_own_surface():
    match = read_word("district", PLACE_LEXICON)

    assert match is not None
    assert match.entry.kind == "latin-type-word"
    assert match.entry.claim == ("district", "district")


def test_two_rows_on_one_key_are_refused_rather_than_guessed():
    contested = lexicon(
        (entry("கோட்டை", ("institution", "landmark")), entry("கோட்டை", ("town", "town")))
    )

    assert "கோட்டை" in contested.contested
    assert read_word("கோட்டை", contested) is None


def test_stems_are_offered_printed_first_and_never_shorter_than_two_characters():
    forms = stem_candidates("கோயிலுக்குள்")

    assert forms[0].rule == "as-printed"
    assert all(len(candidate.form) >= MIN_STEM for candidate in forms)


@pytest.mark.parametrize("surface", ["", "   "])
def test_a_word_that_holds_no_stem_is_refused_outright(surface: str):
    with pytest.raises(MentionTextError):
        stem_candidates(surface)


def test_a_bare_joining_mark_offers_no_stem_a_lexicon_can_read():
    forms = stem_candidates("்")

    assert [candidate.form for candidate in forms] == ["்"]
    assert all(len(candidate.form) < MIN_STEM for candidate in forms)


@pytest.mark.parametrize("lexicon", [PLACE_LEXICON, ACTOR_LEXICON], ids=["place", "actor"])
def test_every_row_is_reachable_from_the_surface_it_prints(lexicon: Lexicon):
    for row in lexicon.entries:
        match = read_word(row.surface, lexicon)
        assert match is not None, f"{lexicon.name} row {row.surface!r} is unreachable"
        assert match.entry.claim == row.claim, row.surface


def test_two_spellings_of_one_colony_word_fold_onto_one_claim():
    assert fold("நகர்") == fold("நகரம்")
    assert read_word("நகர்", PLACE_LEXICON).entry.claim == (
        read_word("நகரம்", PLACE_LEXICON).entry.claim
    )


def test_each_token_is_cut_from_the_untouched_field():
    text = "மதுரையில் பேட்டி நடந்தது."
    words = prose(text).words

    assert [word.text for word in words] == ["மதுரையில்", "பேட்டி", "நடந்தது"]
    for word in words:
        assert text[word.char_start : word.char_end] == word.text
        assert word.derived in text.lower()


def test_a_publish_stamp_is_never_prose():
    reading = prose(STAMPED, stamps=True)

    assert reading.dropped
    gate = max(end for _, end in reading.dropped)
    assert [word.text for word in reading.words] == ["மதுரையில்", "பேட்டி", "நடந்தது"]
    assert all(word.char_start >= gate for word in reading.words)


def test_numbers_are_not_words_unless_a_reader_asks_for_them():
    text = "100 மதுரை"

    assert [word.text for word in prose(text).words] == ["மதுரை"]
    kept = build_prose(field(text), normalize(text, lowercase=True), skip_numbers=False)
    assert [word.text for word in kept.words] == ["100", "மதுரை"]


def test_a_field_of_punctuation_produces_no_words():
    reading = prose("...   。")

    assert reading.words == ()
    assert reading.is_empty is True


def test_the_context_window_stays_inside_the_sentence_that_printed_the_word():
    text = "மதுரையில் பேட்டி நடந்தது. சென்னையில் கூட்டம் கூடியது."
    reading = prose(text)
    second = next(word for word in reading.words if word.text == "கூட்டம்")

    window = reading.context_window(second.index, limit=120)

    assert "சென்னையில்" in window
    assert "மதுரையில்" not in window
    assert len(window) <= 120


def test_a_phrase_run_stops_at_the_first_word_it_will_not_accept():
    text = "மதுரை மாநகராட்சி அறிவித்தது"
    reading = prose(text)
    head = next(word.index for word in reading.words if word.text == "மாநகராட்சி")

    accepted = phrase(reading, head, accept=lambda word: word.text == "மதுரை")
    rejected = phrase(reading, head, accept=lambda word: False)

    assert accepted == (0, 1)
    assert rejected == (head,)


def test_only_spaces_may_separate_the_words_of_one_phrase():
    reading = prose("மதுரை, மாநகராட்சி அறிவித்தது")
    words = reading.words

    assert contiguous(words, 0, 2, source_text=reading.text) is False
    assert contiguous(words, 1, 3, source_text=reading.text) is True


def test_a_quoted_run_carries_the_source_offsets_it_was_cut_at():
    text = "மதுரை மாநகராட்சி அறிவித்தது"
    words = prose(text).words

    span = quote_of(words, 0, 2, text)

    assert (span.char_start, span.char_end) == (words[0].char_start, words[1].char_end)
    assert text[span.char_start : span.char_end] == "மதுரை மாநகராட்சி"


def test_a_lexicon_carries_the_rejection_its_own_family_uses():
    assert PLACE_LEXICON.rejects is blocked
    assert ACTOR_LEXICON.rejects is not_an_entity


def test_the_stage_6_vocabulary_is_internally_consistent():
    assert V.mention_lexicon_integrity_errors() == []
