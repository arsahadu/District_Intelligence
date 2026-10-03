"""Stage 4: which moment a field establishes, which one it merely printed, and which it never gives."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from intelligence.extraction import temporal
from intelligence.extraction.boilerplate import split as split_boilerplate
from intelligence.extraction.spans import SourceField, SpanValidation, locate, verify_evidence
from intelligence.extraction.temporal import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_DATE,
    CONFIDENCE_DATE_CLOCK,
    CONFIDENCE_METADATA,
    CONFIDENCE_RELATIVE,
    NO_EVENT_TIME,
    PROVIDER,
    STAMP_ONLY,
    TemporalError,
    TemporalExtraction,
    TemporalMention,
    extract,
    unresolved_time,
)
from intelligence.extraction.time_expressions import (
    ABSOLUTE_DATE,
    DATE_RANGE,
    INTERVAL,
    NO_DATE,
    NO_REFERENCE,
    RELATIVE_DAY,
    RELATIVE_WINDOW,
)
from intelligence.models.enums import ExtractionMethod, TimePrecision, TimeQualifier, TimeSemantics
from intelligence.models.temporal import TimeValue
from intelligence.tests.builders import CONTENT_TA, RECORD_ID, SOURCE_ID, SOURCE_TYPE, SOURCE_URL

RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
REFERENCE = datetime(2026, 10, 2, 9, 0)

ADDED_STAMP = "அக் 01, 2026 09:46 PM"
UPDATED_STAMP = "அக் 02, 2026 12:00 AM"

ARTICLE_TA = (
    f"UPDATED : {UPDATED_STAMP} ADDED : {ADDED_STAMP} "
    "மதுரையில் செப் 28, 2026 அன்று இரவு 10 மணிக்கு மழை பெய்தது. "
    "நேற்று மாலை கமிஷனரிடம் புகார் அளித்ததாக தகவல் தெரிவித்துள்ளனர். "
    "ஜூலை 20 முதல் 25 வரை நீதிமன்றம் விடுமுறை."
)

MIXED_TA_EN = (
    "மதுரையில் 28.09.2026 அன்று சந்திப்பு நடைபெற்றது. "
    "The collector inspected the site on October 2, 2026 at 4:30 PM."
)

RELATIVE_ONLY_TA = "நேற்று மதுரையில் கூட்டம் நடந்தது. கடந்த இரண்டு நாட்களாக மின்சாரம் இல்லை."

REPORTED_TA = "மதுரையில் நேற்று மாலை புகார் அளித்ததாக தகவல் தெரிவித்துள்ளனர்."

CLOCK_ONLY_TA = "நிகழ்வு நேற்று இரவு 10:30 மணிக்கு தொடங்கியது."

ISO_STAMP_TA = "ADDED : 2026-10-02 09:46\nமதுரையில் செப் 28, 2026 அன்று மழை."

SLASH_STAMP_TA = "UPDATED : 02/10/2026 09:46 AM\nமழை பெய்தது."

STAMP_LINE_BOUNDARY_TA = (
    "ADDED : 2026-10-02 09:46\nசெப் 28, 2026 அன்று மழை.\nடிச 1, 2026 கூட்டம்."
)


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


def read(text: str, **options) -> TemporalExtraction:
    return extract(field(text), reference=REFERENCE, **options)


def bodies(extraction: TemporalExtraction) -> list[TemporalMention]:
    return list(extraction.mentions)


def raw_texts(extraction: TemporalExtraction) -> list[str]:
    return [mention.raw_text for mention in extraction.mentions]


def test_the_stamp_fixture_carries_no_event_time():
    extraction = extract(field(), reference=REFERENCE)
    assert raw_texts(extraction) == []
    event = extraction.event_time()
    assert event.value is None
    assert event.method is ExtractionMethod.UNRESOLVED
    assert event.semantics is TimeSemantics.UNKNOWN
    assert STAMP_ONLY in event.notes
    assert extraction.has_only_publication_time is True


def test_stamps_are_publication_time_and_never_anything_else():
    extraction = extract(field(), reference=REFERENCE)
    assert [mention.raw_text for mention in extraction.publication] == [
        UPDATED_STAMP,
        ADDED_STAMP,
    ]
    for mention in extraction.publication:
        assert mention.semantics is TimeSemantics.PUBLICATION_TIME
        assert "never event time" in mention.evidence.notes
        assert mention.evidence.method is ExtractionMethod.REGEX
    assert {mention.semantics for mention in extraction.mentions} != {
        TimeSemantics.PUBLICATION_TIME
    }


def test_the_latest_stamp_is_the_publication_time():
    publication = extract(field(), reference=REFERENCE).publication_time()
    assert publication.value == datetime(2026, 10, 2, 0, 0)
    assert publication.semantics is TimeSemantics.PUBLICATION_TIME
    assert publication.raw_text == UPDATED_STAMP
    assert publication.method is ExtractionMethod.REGEX
    assert publication.confidence == CONFIDENCE_DATE_CLOCK


def test_a_field_without_stamps_says_so_instead_of_inventing_one():
    extraction = read("மதுரையில் செப் 28, 2026 மழை பெய்தது.")
    publication = extraction.publication_time()
    assert publication.value is None
    assert "no publish stamp" in publication.notes
    assert extraction.has_only_publication_time is False


def test_the_retrieval_clock_is_its_own_role():
    extraction = extract(field(), reference=REFERENCE)
    retrieval = extraction.retrieval
    assert retrieval is not None
    assert retrieval.semantics is TimeSemantics.RETRIEVAL_TIME
    assert retrieval.method is ExtractionMethod.SOURCE_METADATA
    assert retrieval.confidence == CONFIDENCE_METADATA
    assert retrieval.timezone == "UTC"
    assert retrieval.raw_text is None
    evidence = extraction.retrieval_evidence
    assert evidence.span_validation is SpanValidation.NOT_APPLICABLE
    assert evidence.has_span is False
    assert retrieval.evidence_ids == [evidence.evidence_id]


def test_a_field_without_a_retrieval_clock_has_no_retrieval_time():
    extraction = extract(field(retrieved_at=None))
    assert extraction.retrieval is None
    assert extraction.retrieval_evidence is None


def test_a_body_date_becomes_event_time_with_its_printing_kept():
    extraction = read(ARTICLE_TA)
    event = extraction.event_time()
    assert event.value == datetime(2026, 9, 28, 22, 0)
    assert event.semantics is TimeSemantics.EVENT_TIME
    assert event.method is ExtractionMethod.REGEX
    assert event.precision is TimePrecision.HOUR
    assert event.qualifier is TimeQualifier.EXACT
    assert event.raw_text == "செப் 28, 2026 அன்று இரவு 10 மணிக்கு"
    assert event.raw_text in ARTICLE_TA
    assert event.confidence == CONFIDENCE_DATE_CLOCK


def test_stamp_text_never_reaches_the_body_mentions():
    extraction = read(ARTICLE_TA)
    stamp_ranges = [(item.char_start, item.char_end) for item in extraction.split.items]
    for mention in extraction.mentions:
        assert not any(
            mention.char_start < end and start < mention.char_end
            for start, end in stamp_ranges
        )
        assert "UPDATED" not in mention.raw_text


def test_a_timestamp_the_stamp_grammar_misses_is_still_not_event_time():
    extraction = read(ISO_STAMP_TA)
    assert raw_texts(extraction) == ["செப் 28, 2026"]
    assert extraction.event_time().value == datetime(2026, 9, 28)
    stamp = extraction.publication
    assert [mention.raw_text for mention in stamp] == ["2026-10-02 09:46"]
    assert stamp[0].semantics is TimeSemantics.PUBLICATION_TIME
    assert "never event time" in stamp[0].evidence.notes
    assert extraction.publication_time().value == datetime(2026, 10, 2, 9, 46)


def test_a_stamp_timestamp_that_could_be_read_two_ways_keeps_the_publication_role():
    extraction = read(SLASH_STAMP_TA)
    assert extraction.event_time().value is None
    assert extraction.has_only_publication_time is True
    assert NO_EVENT_TIME in extraction.event_time().notes
    assert STAMP_ONLY in extraction.event_time().notes
    assert extraction.publication_time().value == datetime(2026, 10, 2, 9, 46)


def test_a_stamp_line_owns_only_its_own_line():
    extraction = read(STAMP_LINE_BOUNDARY_TA)
    assert raw_texts(extraction) == ["செப் 28, 2026", "டிச 1, 2026"]
    assert [mention.raw_text for mention in extraction.publication] == ["2026-10-02 09:46"]
    assert extraction.event_time().value == datetime(2026, 9, 28)


def test_a_demoted_stamp_line_is_still_cited_in_the_untouched_field():
    extraction = read(ISO_STAMP_TA)
    mention = extraction.publication[0]
    printed = extraction.source.text[mention.char_start : mention.char_end]
    assert printed == mention.raw_text == "2026-10-02 09:46"
    check = verify_evidence(mention.evidence, extraction.source.text)
    assert check.ok is True
    assert mention.evidence.field_text_hash == extraction.source.text_hash
    assert mention.evidence.method is ExtractionMethod.REGEX
    assert mention.evidence in extraction.evidence


def test_a_reused_split_still_demotes_its_stamp_lines():
    source = field(STAMP_LINE_BOUNDARY_TA)
    prepared = split_boilerplate(source, lowercase=True)
    extraction = extract(source, reference=REFERENCE, split=prepared)
    assert raw_texts(extraction) == ["செப் 28, 2026", "டிச 1, 2026"]
    assert [mention.raw_text for mention in extraction.publication] == ["2026-10-02 09:46"]


def test_a_statement_attributed_to_a_reporting_verb_is_not_the_event():
    extraction = read(REPORTED_TA)
    assert [mention.semantics for mention in extraction.mentions] == [
        TimeSemantics.REPORTED_TIME
    ]
    reported = extraction.reported_time()
    assert reported.value == datetime(2026, 10, 1)
    assert reported.semantics is TimeSemantics.REPORTED_TIME
    assert extraction.event_time().value is None
    assert NO_EVENT_TIME in extraction.event_time().notes


def test_event_and_reported_times_are_kept_apart_in_one_field():
    extraction = read(ARTICLE_TA)
    assert [mention.kind for mention in extraction.event_time_mentions] == [
        ABSOLUTE_DATE,
        DATE_RANGE,
    ]
    assert [mention.raw_text for mention in extraction.reported_time_mentions] == [
        "நேற்று மாலை"
    ]


def test_the_finest_true_reading_wins_the_event_slot():
    extraction = read(MIXED_TA_EN)
    assert raw_texts(extraction) == ["28.09.2026", "October 2, 2026 at 4:30 PM"]
    event = extraction.event_time()
    assert event.value == datetime(2026, 10, 2, 16, 30)
    assert event.precision is TimePrecision.MINUTE


def test_a_numeric_date_with_only_one_possible_reading_is_not_flagged():
    extraction = read(MIXED_TA_EN)
    assert [mention.ambiguous for mention in extraction.mentions] == [False, False]


def test_a_numeric_date_that_could_be_read_both_ways_is_flagged():
    extraction = read("மதுரையில் 02.10.2026 அன்று கூட்டம்.")
    mention = extraction.mentions[0]
    assert mention.ambiguous is True
    assert mention.raw_text == "02.10.2026"
    value = mention.time_value()
    assert value.confidence == CONFIDENCE_AMBIGUOUS
    assert value.value == datetime(2026, 10, 2)
    assert mention.precision is TimePrecision.DAY


def test_a_relative_expression_stays_relative_without_a_reference():
    extraction = extract(field(RELATIVE_ONLY_TA))
    assert [mention.needs_reference for mention in extraction.mentions] == [True, True]
    assert extraction.event_time().value is None
    assert all(NO_REFERENCE in (mention.reason or "") for mention in extraction.mentions)
    assert NO_REFERENCE in extraction.event_time().notes


def test_the_same_relative_expression_resolves_once_a_reference_is_given():
    extraction = read(RELATIVE_ONLY_TA)
    assert [mention.value for mention in extraction.mentions] == [
        datetime(2026, 10, 1),
        datetime(2026, 9, 30),
    ]
    event = extraction.event_time()
    assert event.method is ExtractionMethod.RULE
    assert event.confidence == CONFIDENCE_RELATIVE
    assert event.raw_text == "நேற்று"


def test_a_day_part_narrows_the_day_without_inventing_an_hour():
    extraction = read(REPORTED_TA)
    mention = extraction.mentions[0]
    assert mention.precision is TimePrecision.DAY
    assert mention.value == datetime(2026, 10, 1)
    assert mention.qualifier is TimeQualifier.APPROXIMATE
    assert mention.expression.day_part == "evening"


def test_a_clock_inside_a_day_part_is_read_as_pm():
    extraction = read("நேற்றிரவு 2 மணிக்கு சமையல் எரிந்தது.")
    event = extraction.event_time()
    assert event.value == datetime(2026, 10, 1, 14, 0)
    assert event.qualifier is TimeQualifier.EXACT


def test_a_clock_without_a_date_is_left_unplaced():
    extraction = read("காலை 10:30 மணிக்கு கூட்டம்.")
    assert extraction.event_time().value is None
    assert NO_DATE in extraction.event_time().notes


def test_a_clock_held_by_a_named_day_is_placed():
    extraction = read(CLOCK_ONLY_TA)
    event = extraction.event_time()
    assert event.raw_text == "நேற்று இரவு 10:30 மணிக்கு"
    assert event.value == datetime(2026, 10, 1, 22, 30)
    assert event.precision is TimePrecision.MINUTE


def test_a_range_is_an_interval_not_an_instant():
    extraction = read(ARTICLE_TA)
    ranged = [mention for mention in extraction.mentions if mention.kind == DATE_RANGE]
    assert len(ranged) == 1
    mention = ranged[0]
    assert mention.resolved is False
    assert mention.reason == INTERVAL
    assert mention.time_value().value is None
    assert [endpoint.raw_text for endpoint in mention.endpoints] == ["ஜூலை 20", "25 வரை"]
    assert mention.time_value().evidence_ids == mention.evidence_ids
    assert extraction.intervals


def test_an_ongoing_window_is_recorded_as_the_start_of_the_span():
    extraction = read(RELATIVE_ONLY_TA)
    window = [m for m in extraction.mentions if m.kind == RELATIVE_WINDOW][0]
    value = window.time_value()
    assert value.value == datetime(2026, 9, 30)
    assert value.qualifier is TimeQualifier.ONGOING
    assert "start of the interval" in value.notes


def test_every_mention_quotes_the_untouched_field_at_original_offsets():
    extraction = read(ARTICLE_TA)
    source = extraction.source
    for evidence in extraction.evidence:
        assert evidence.field_text_hash == source.text_hash
        check = verify_evidence(evidence, source.text)
        if evidence.has_span:
            assert source.text[evidence.char_start : evidence.char_end] == evidence.quote
            assert check.ok is True
        else:
            assert check.validation is SpanValidation.NOT_APPLICABLE
    for mention in extraction.mentions:
        assert locate(source.text, mention.raw_text).char_start == mention.char_start


def test_a_body_match_is_cited_in_original_coordinates_after_normalization():
    text = "On 02.10.2026   the ROAD was closed. மதுரையில் நேற்று மழை."
    extraction = read(text)
    assert extraction.body.text.startswith("on 02.10.2026 the road was closed")
    assert extraction.body.offsets_align_with_source is False
    first = extraction.mentions[0]
    assert first.raw_text == "02.10.2026"
    assert first.char_start == text.index("02.10.2026")
    assert extraction.mentions[-1].raw_text == "நேற்று"
    assert extraction.mentions[-1].char_start == text.index("நேற்று")


def test_tamil_printing_survives_the_whole_extraction():
    extraction = read(ARTICLE_TA)
    assert extraction.source.text == ARTICLE_TA
    assert any("செப்" in mention.raw_text for mention in extraction.mentions)
    assert all("October" not in mention.raw_text for mention in extraction.mentions[:2])
    assert extraction.body.original == ARTICLE_TA


def test_the_original_field_is_never_replaced_by_the_body():
    extraction = read(ARTICLE_TA)
    assert extraction.source.text.startswith("UPDATED :")
    assert "UPDATED" not in extraction.body.text
    assert extraction.body.text in extraction.source.text.replace(UPDATED_STAMP, "")


def test_evidence_is_one_per_surface_and_unique():
    extraction = read(ARTICLE_TA)
    collected = extraction.evidence
    assert len(collected) == len({evidence.evidence_id for evidence in collected})
    surfaces = len(extraction.mentions) + sum(
        len(mention.endpoints) for mention in extraction.mentions
    )
    assert len(collected) == surfaces + len(extraction.publication) + 1


def test_every_time_value_points_at_evidence_that_exists():
    extraction = read(ARTICLE_TA)
    known = {evidence.evidence_id for evidence in extraction.evidence}
    values = [
        extraction.event_time(),
        extraction.reported_time(),
        extraction.publication_time(),
        extraction.retrieval,
        *[mention.time_value() for mention in extraction.mentions],
    ]
    for value in values:
        if value is None:
            continue
        assert set(value.evidence_ids) <= known, value
        assert TimeValue.model_validate(value.model_dump()) == value


def test_an_unresolved_value_never_claims_a_method_or_confidence():
    extraction = extract(field(RELATIVE_ONLY_TA))
    for mention in extraction.mentions:
        value = mention.time_value()
        assert value.value is None
        assert value.method is ExtractionMethod.UNRESOLVED
        assert value.confidence is None
        assert value.semantics is TimeSemantics.UNKNOWN
        assert value.raw_text == mention.raw_text
        assert NO_REFERENCE in value.notes


def test_unresolved_time_is_the_honest_default():
    value = unresolved_time()
    assert value.value is None
    assert value.precision is TimePrecision.UNKNOWN
    assert value.qualifier is TimeQualifier.UNKNOWN
    assert value.semantics is TimeSemantics.UNKNOWN
    assert value.method is ExtractionMethod.UNRESOLVED
    assert value.notes == NO_EVENT_TIME
    assert value.evidence_ids == []
    with_evidence = unresolved_time(
        raw_text="நேற்று", evidence_ids=["ev-1"], notes="a reason", precision=TimePrecision.DAY
    )
    assert with_evidence.raw_text == "நேற்று"
    assert with_evidence.evidence_ids == ["ev-1"]
    assert with_evidence.precision is TimePrecision.DAY


def test_a_declared_zone_is_attached_without_touching_the_naive_value():
    extraction = read("மதுரையில் செப் 28, 2026 மழை.", timezone="Asia/Kolkata")
    event = extraction.event_time()
    assert event.timezone == "Asia/Kolkata"
    assert event.value.utcoffset() is None


def test_a_zone_named_in_the_text_is_read_when_the_caller_does_not_declare_one():
    extraction = read("செப் 28, 2026 அன்று இந்திய நேரப்படி 10 மணிக்கு மழை.")
    assert extraction.timezone == "Asia/Kolkata"
    assert extraction.event_time().timezone == "Asia/Kolkata"


def test_a_zone_the_caller_declares_beats_the_one_the_text_names():
    extraction = read(
        "செப் 28, 2026 அன்று இந்திய நேரப்படி 10 மணிக்கு மழை.",
        timezone="Asia/Colombo",
    )
    assert extraction.timezone == "Asia/Colombo"


def test_a_time_zone_nobody_declared_stays_undeclared():
    extraction = read("மதுரையில் செப் 28, 2026 மழை.")
    assert extraction.timezone is None
    assert extraction.event_time().timezone is None


def test_a_split_from_another_field_is_refused():
    source = field(ARTICLE_TA)
    other = field("மதுரையில் நேற்று மழை.")
    with pytest.raises(TemporalError, match="derived from"):
        extract(source, split=split_boilerplate(other))


def test_an_already_split_body_is_reused_rather_than_rebuilt():
    source = field(ARTICLE_TA)
    prepared = split_boilerplate(source, lowercase=True)
    extraction = extract(source, reference=REFERENCE, split=prepared)
    assert extraction.split is prepared
    assert extraction.event_time().value == datetime(2026, 9, 28, 22, 0)


def test_extraction_is_deterministic():
    first = read(ARTICLE_TA).as_dict()
    second = read(ARTICLE_TA).as_dict()
    assert first == second
    assert json.loads(json.dumps(first, ensure_ascii=False)) == first


def test_the_serialised_shape_keeps_roles_and_provenance_apart():
    payload = read(ARTICLE_TA).as_dict()
    assert payload["record_id"] == RECORD_ID
    assert payload["field"] == "data.content"
    assert payload["reference"] == REFERENCE.isoformat()
    assert payload["event_time"]["semantics"] == "event_time"
    assert payload["publication_time"]["semantics"] == "publication_time"
    assert payload["retrieval_time"]["semantics"] == "retrieval_time"
    assert payload["stamps"][0]["semantics"] == "publication_time"
    assert payload["unresolved"] == ["ஜூலை 20 முதல் 25 வரை"]
    assert payload["mentions"][0]["evidence_id"].startswith("ev-NEWS-MDU-TEST-0001-")


def test_a_mention_describes_itself_for_a_log_line():
    extraction = read(ARTICLE_TA)
    mention = extraction.mentions[0]
    printed = "செப் 28, 2026 அன்று இரவு 10 மணிக்கு"
    start = ARTICLE_TA.index(printed)
    assert mention.describe() == (
        f"absolute_date {printed!r} [{start}:{start + len(printed)}] -> 2026-09-28T22:00:00"
    )
    assert mention.span.slice_from(ARTICLE_TA) == mention.raw_text
    assert mention.expression.kind == ABSOLUTE_DATE
    assert mention.resolution.resolved is True
    assert mention.interval() == (None, None)


def test_an_unresolved_mention_describes_why():
    extraction = read(ARTICLE_TA)
    ranged = [mention for mention in extraction.mentions if mention.kind == DATE_RANGE][0]
    assert "unresolved" in ranged.describe()
    assert INTERVAL in ranged.describe()
    assert ranged.confidence is None
    assert ranged.method is ExtractionMethod.REGEX


def test_a_field_that_says_nothing_about_time_is_recorded_as_silent():
    extraction = read("மதுரையில் மாநகராட்சி அலுவலகம் உள்ளது.")
    assert extraction.mentions == ()
    assert extraction.publication == ()
    assert extraction.evidence == [extraction.retrieval_evidence]
    assert extraction.event_time().value is None
    assert extraction.reported_time().value is None
    assert NO_EVENT_TIME in extraction.event_time().notes


def test_the_provider_is_stated_on_the_module_that_owns_the_roles():
    assert PROVIDER == "intelligence.extraction.temporal"
    assert temporal.PROVIDER == PROVIDER
    assert isinstance(bodies(read(ARTICLE_TA))[0], TemporalMention)


def test_relative_arithmetic_uses_the_reference_it_was_given():
    for offset, expected in [(0, datetime(2026, 10, 2)), (5, datetime(2026, 10, 7))]:
        extraction = extract(
            field("நேற்று மழை."), reference=datetime(2026, 10, 2) + timedelta(days=offset)
        )
        assert extraction.event_time().value == expected - timedelta(days=1)
