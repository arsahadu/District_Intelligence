"""Stage 4: temporal surfaces in Tamil, English and mixed text, before anyone decides what they mean."""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

import pytest

from intelligence.extraction import boilerplate
from intelligence.extraction.time_expressions import (
    ABSOLUTE_DATE,
    AMBIGUOUS_ABBREVIATION,
    ANCHORED,
    CLOCK_TIME,
    DATE_RANGE,
    DAY_ONLY,
    Expression,
    INTERVAL,
    IMPOSSIBLE,
    KINDS,
    MONTH_DAY,
    MONTH_YEAR,
    MONTHS,
    NO_ANCHOR,
    NO_DATE,
    NO_MONTH,
    NO_REFERENCE,
    NO_YEAR,
    PRECISION_ORDER,
    RELATIVE_CLOCK,
    RELATIVE_DAY,
    RELATIVE_OFFSET,
    RELATIVE_WINDOW,
    REPORTING_CUES,
    Resolution,
    TIMEZONE_DECLARATION,
    WEEKDAY,
    YEAR_ONLY,
    coarser,
    finer,
    find,
    reporting_cue,
    resolve,
    timezone_cue,
)
from intelligence.models.enums import TimePrecision, TimeQualifier, TimeSemantics

#: A Friday morning. Relative surfaces are only ever measured against a supplied moment.
REFERENCE = datetime(2026, 10, 2, 9, 0)


def surfaces(text: str) -> list[str]:
    return [expression.surface for expression in find(text)]


def kinds(text: str) -> list[str]:
    return [expression.kind for expression in find(text)]


def one(text: str) -> Expression:
    found = find(text)
    assert len(found) == 1, found
    return found[0]


def read(text: str, *, reference=REFERENCE) -> Resolution:
    return resolve(one(text), reference=reference)


def state(resolution: Resolution) -> tuple[str, str, str]:
    value = resolution.value.strftime("%Y-%m-%dT%H:%M") if resolution.value else "none"
    return value, resolution.precision.value, resolution.qualifier.value


def test_an_iso_date_is_read_whole():
    expression = one("2026-10-02 அன்று மழை")
    assert (expression.kind, expression.rule) == (ABSOLUTE_DATE, "iso_date")
    assert expression.surface == "2026-10-02"
    assert state(read("2026-10-02 அன்று மழை")) == ("2026-10-02T00:00", "day", "exact")


@pytest.mark.parametrize(
    "text, printed, expected",
    [
        ("அக்டோபர் 2, 2026 கூட்டம்", "அக்டோபர் 2, 2026", "2026-10-02T00:00"),
        ("2 அக்டோபர் 2026 அன்று", "2 அக்டோபர் 2026", "2026-10-02T00:00"),
        ("October 2, 2026", "October 2, 2026", "2026-10-02T00:00"),
        ("2 October 2026", "2 October 2026", "2026-10-02T00:00"),
        ("Oct 2nd, 2026", "Oct 2nd, 2026", "2026-10-02T00:00"),
        ("செப்டம்பர் 28, 2026", "செப்டம்பர் 28, 2026", "2026-09-28T00:00"),
        ("28 செப்டம்பர் 2026", "28 செப்டம்பர் 2026", "2026-09-28T00:00"),
    ],
)
def test_named_dates_are_read_in_both_orders_and_both_scripts(text, printed, expected):
    expression = one(text)
    assert expression.kind == ABSOLUTE_DATE
    assert expression.surface == printed
    assert state(read(text)) == (expected, "day", "exact")


def test_a_named_date_is_whole_even_when_both_orders_could_read_it():
    expression = one("2 October 2026")
    assert expression.rule == "day_month_year"
    assert (expression.parts["day"], expression.parts["month"]) == (2, 10)
    assert expression.ambiguous is False


@pytest.mark.parametrize("text", ["02.10.2026", "02/10/2026", "2-10-2026"])
def test_numeric_dates_are_read_day_first_and_flagged(text):
    expression = one(text)
    assert (expression.kind, expression.rule) == (ABSOLUTE_DATE, "numeric_date")
    assert (expression.parts["day"], expression.parts["month"]) == (2, 10)
    assert expression.ambiguous is True
    assert resolve(expression, reference=REFERENCE).ambiguous is True


def test_a_numeric_date_that_cannot_be_day_first_is_read_the_other_way():
    expression = one("10.12.2026")
    assert (expression.parts["day"], expression.parts["month"]) == (10, 12)
    assert expression.ambiguous is True
    assert state(read("10.12.2026")) == ("2026-12-10T00:00", "day", "exact")


def test_only_one_surface_is_cut_from_a_numeric_date():
    assert len(find("02.10.2026")) == 1


def test_a_month_and_year_keeps_the_month_as_the_smallest_true_unit():
    expression = one("அக் 2026 முதல்")
    assert (expression.kind, expression.rule) == (MONTH_YEAR, "month_year")
    assert state(read("அக் 2026 முதல்")) == ("2026-10-01T00:00", "month", "approximate")
    assert expression.notes == ("no day in the surface",)


def test_a_year_alone_never_becomes_a_day():
    expression = one("2026 ஆம் ஆண்டு")
    assert (expression.kind, expression.rule) == (YEAR_ONLY, "year_only")
    assert state(read("2026 ஆம் ஆண்டு")) == ("2026-01-01T00:00", "year", "approximate")


@pytest.mark.parametrize("text", ["2026", "எண் 2026", "1857 ஆம் ஆண்டு"])
def test_a_bare_or_out_of_range_year_is_not_a_date(text):
    assert find(text) == []


def test_a_day_of_the_month_is_only_a_day_when_the_text_marks_it():
    expression = one("2 ஆம் தேதி கூட்டம்")
    assert (expression.kind, expression.rule) == (MONTH_DAY, DAY_ONLY)
    resolution = read("2 ஆம் தேதி கூட்டம்")
    assert resolution.resolved is False
    assert resolution.reason == NO_MONTH
    assert find("எண் 2 பெரியது") == []


def test_a_day_and_month_without_a_year_stays_unresolved():
    expression = one("அக் 2 அன்று")
    assert expression.kind == MONTH_DAY
    assert read("அக் 2 அன்று").reason == NO_YEAR


def test_an_abbreviation_that_names_two_months_claims_neither():
    expression = one("ஜூ 2026")
    assert expression.ambiguous is True
    resolution = read("ஜூ 2026")
    assert resolution.resolved is False
    assert resolution.reason == AMBIGUOUS_ABBREVIATION


def test_an_impossible_calendar_date_is_refused_not_repaired():
    assert read("31.02.2026").reason == IMPOSSIBLE
    assert read("2026-02-30").reason == IMPOSSIBLE


@pytest.mark.parametrize(
    "text",
    ["2.5 கோடி ரூபாய்", "4800 மனுக்களை", "108 மீ", "20 கிலோ", "மதுரையில்"],
)
def test_numbers_that_are_not_dates_produce_nothing(text):
    assert find(text) == []


def test_an_iso_zone_offset_is_not_a_clock_reading():
    assert find("2026-10-02T09:46:00+05:30") == []
    assert [expression.surface for expression in find("05:30")] == ["05:30"]


def test_a_four_digit_year_inside_a_number_is_not_a_clock():
    assert find("2026 மணி") == []


def test_a_clock_reading_is_kept_from_the_calendar():
    expression = one("10:30 மணிக்கு")
    assert (expression.kind, expression.rule) == (CLOCK_TIME, "clock_tamil")
    assert expression.precision is TimePrecision.MINUTE
    resolution = read("10:30 மணிக்கு")
    assert resolution.resolved is False
    assert resolution.reason == NO_DATE


def test_an_hour_only_clock_is_less_precise_than_a_minute_clock():
    assert one("6 மணி").precision is TimePrecision.HOUR
    assert one("06:45").precision is TimePrecision.MINUTE
    assert one("14:30:15").precision is TimePrecision.SECOND


def test_a_meridiem_supplies_the_hour_that_the_clock_lacks():
    expression = one("4:30 PM")
    assert expression.parts["meridiem"] == "pm"
    assert expression.qualifier is TimeQualifier.EXACT
    midnight = one("2026-10-02 12:15 AM")
    assert resolve(midnight).value == datetime(2026, 10, 2, 0, 15)


def test_a_clock_without_a_meridiem_says_so():
    expression = one("10:30")
    assert expression.qualifier is TimeQualifier.APPROXIMATE
    assert expression.meridiem is None
    assert expression.notes == ("no meridiem stated; the reading is 12-hour ambiguous",)


def test_a_day_part_and_a_clock_are_one_phrase_not_two():
    expression = one("28.09.2026 அன்று இரவு 10 மணிக்கு")
    assert (expression.kind, expression.rule) == (ABSOLUTE_DATE, RELATIVE_CLOCK) or (
        expression.rule == "date_clock"
    )
    assert expression.surface == "28.09.2026 அன்று இரவு 10 மணிக்கு"
    assert state(read("28.09.2026 அன்று இரவு 10 மணிக்கு")) == (
        "2026-09-28T22:00",
        "hour",
        "exact",
    )


def test_a_day_part_touching_a_date_still_leaves_the_clock_alone():
    expression = one("28 செப்டம்பர் 2026 இரவு 10 மணிக்கு")
    assert expression.surface == "28 செப்டம்பர் 2026 இரவு 10 மணிக்கு"
    assert expression.day_part == "night"
    assert resolve(expression).value == datetime(2026, 9, 28, 22, 0)


def test_an_english_date_and_time_is_one_surface():
    expression = one("October 2, 2026 at 4:30 PM")
    assert expression.rule == "date_clock"
    assert state(read("October 2, 2026 at 4:30 PM")) == (
        "2026-10-02T16:30",
        "minute",
        "exact",
    )


def test_a_period_does_not_join_a_date_and_a_clock_across_a_sentence():
    found = find("செப் 28, 2026 அன்று. 10 மணிக்கு மழை")
    assert [expression.kind for expression in found] == [ABSOLUTE_DATE, CLOCK_TIME]
    assert found[0].rule != "date_clock"


@pytest.mark.parametrize(
    "text, offset, printed",
    [
        ("நேற்று மழை", -1, "நேற்று"),
        ("இன்று கூட்டம்", 0, "இன்று"),
        ("நாளை விடுமுறை", 1, "நாளை"),
        ("நேற்று முன்தினம்", -2, "நேற்று முன்தினம்"),
        ("நாளை மறுநாள்", 2, "நாளை மறுநாள்"),
        ("yesterday it rained", -1, "yesterday"),
        ("the day before yesterday", -2, "the day before yesterday"),
        ("the day after tomorrow is a holiday", 2, "the day after tomorrow"),
    ],
)
def test_relative_days_are_counted_from_the_reference(text, offset, printed):
    expression = one(text)
    assert (expression.kind, expression.offset_days) == (RELATIVE_DAY, offset)
    assert expression.surface == printed
    expected = datetime(2026, 10, 2) + timedelta(days=offset)
    assert resolve(expression, reference=REFERENCE).value == expected


@pytest.mark.parametrize(
    "text, value",
    [
        ("last night", "2026-10-01T00:00"),
        ("tonight", "2026-10-02T00:00"),
        ("this morning", "2026-10-02T00:00"),
        ("yesterday evening", "2026-10-01T00:00"),
        ("நேற்றிரவு", "2026-10-01T00:00"),
        ("நாளைக்காலை", "2026-10-03T00:00"),
    ],
)
def test_a_fused_day_part_keeps_the_day_approximate(text, value):
    expression = one(text)
    assert expression.kind == RELATIVE_DAY
    resolution = resolve(expression, reference=REFERENCE)
    assert state(resolution) == (value, "day", "approximate")
    assert expression.day_part in {
        "night",
        "morning",
        "evening",
    }


def test_a_relative_day_and_a_clock_are_one_phrase():
    expression = one("நாளை காலை 10 மணிக்கு")
    assert expression.rule == RELATIVE_CLOCK
    assert state(read("நாளை காலை 10 மணிக்கு")) == ("2026-10-03T10:00", "hour", "exact")


def test_a_day_word_without_a_date_still_needs_a_reference():
    for text in ["நேற்று", "tomorrow", "கடந்த இரண்டு நாட்களாக"]:
        resolution = resolve(one(text))
        assert resolution.resolved is False, text
        assert resolution.reason == NO_REFERENCE, text
        assert resolution.needs_reference is True, text


@pytest.mark.parametrize("text", ["மறுநாள்", "next day", "the same day"])
def test_an_anchored_day_word_is_not_a_day_of_its_own(text):
    resolution = read(text)
    assert resolution.resolved is False
    assert resolution.reason == ANCHORED


def test_now_is_today_and_carries_the_clock_of_the_reference():
    expression = one("தற்போது")
    assert resolve(expression, reference=REFERENCE).value == datetime(2026, 10, 2, 0, 0)
    assert expression.offset_days == 0


def test_a_bare_day_part_is_a_noun_not_a_time():
    assert find("மாலை") == []
    assert find("காலை") == []


@pytest.mark.parametrize(
    "text, hours_back",
    [("3 மணி நேரத்திற்கு முன்", -3), ("2 hours ago", -2)],
)
def test_hour_offsets_move_the_clock_of_the_reference(text, hours_back):
    expression = one(text)
    assert (expression.kind, expression.unit) == (RELATIVE_OFFSET, "hour")
    expected = datetime(2026, 10, 2, 9, 0) + timedelta(hours=hours_back)
    assert resolve(expression, reference=REFERENCE).value == expected


def test_counted_days_are_measured_to_the_day_not_the_hour():
    expression = one("2 days ago")
    assert expression.precision is TimePrecision.DAY
    assert state(read("2 days ago")) == ("2026-09-30T00:00", "day", "exact")


def test_a_forward_count_is_measured_from_the_reference():
    assert state(read("in two days")) == ("2026-10-04T00:00", "day", "exact")
    assert state(read("within 3 hours")) == ("2026-10-02T12:00", "hour", "exact")


def test_a_duration_without_a_marker_is_not_a_point_in_time():
    assert find("இரண்டு நாட்கள்") == []
    assert find("two hours") == []


def test_a_tamil_forward_offset_is_read_through_its_euphonic_consonant():
    for text, unit, days in [
        ("இரண்டு நாட்களுக்குப் பிறகு மழை", "day", 2),
        ("மூன்று நாட்களுக்குப் பின்னர் கூட்டம்", "day", 3),
        ("ஒரு வாரத்திற்கு அடுத்து", "week", 7),
    ]:
        expression = one(text)
        assert (expression.kind, expression.unit, expression.direction) == (
            RELATIVE_OFFSET,
            unit,
            "future",
        ), text
        expected = datetime(2026, 10, 2) + timedelta(days=days)
        assert resolve(expression, reference=REFERENCE).value == expected


def test_a_direction_word_alone_is_not_an_offset():
    assert find("பிறகு அவர் பேசினார்") == []
    assert find("அடுத்து") == []


def test_an_ongoing_window_is_reported_as_its_start():
    expression = one("கடந்த இரண்டு நாட்களாக மழை")
    assert (expression.kind, expression.unit, expression.direction) == (
        RELATIVE_WINDOW,
        "day",
        "past",
    )
    resolution = resolve(expression, reference=REFERENCE)
    assert state(resolution) == ("2026-09-30T00:00", "day", "ongoing")
    assert "value is the start of the interval" in resolution.notes


def test_a_counted_month_window_lands_on_the_first_of_a_month():
    expression = one("மூன்று மாதங்களாக")
    assert (expression.count, expression.unit) == (3, "month")
    assert state(read("மூன்று மாதங்களாக")) == ("2026-07-01T00:00", "month", "ongoing")


def test_a_range_is_an_interval_and_claims_no_instant():
    expression = one("ஜூலை 20 முதல் 25 வரை")
    assert (expression.kind, expression.rule) == (DATE_RANGE, "range_tamil")
    assert expression.surface == "ஜூலை 20 முதல் 25 வரை"
    resolution = resolve(expression, reference=REFERENCE)
    assert resolution.resolved is False
    assert resolution.reason == INTERVAL
    assert [endpoint.surface for endpoint in expression.endpoints] == ["ஜூலை 20", "25 வரை"]


def test_weekdays_also_make_a_range():
    expression = one("திங்கட்கிழமை முதல் வியாழக்கிழமை வரை")
    assert expression.kind == DATE_RANGE
    assert [endpoint.kind for endpoint in expression.endpoints] == [WEEKDAY, WEEKDAY]


def test_an_english_range_is_cut_as_one_phrase():
    expression = one("January 5, 2026 to January 10, 2026")
    assert expression.kind == DATE_RANGE
    assert expression.surface == "January 5, 2026 to January 10, 2026"


def test_a_range_gives_its_endpoints_their_own_spans():
    text = "ஜூலை 20 முதல் 25 வரை"
    expression = one(text)
    for endpoint in expression.endpoints:
        span = endpoint.span()
        assert span.slice_from(text) == endpoint.surface
    assert expression.span().slice_from(text) == expression.surface


def test_a_weekday_name_is_never_one_specific_day():
    for text in ["வெள்ளிக்கிழமை", "next Friday", "Monday"]:
        resolution = read(text)
        assert resolution.resolved is False, text
        assert resolution.reason == NO_ANCHOR, text


def test_a_full_tamil_weekday_is_not_treated_as_ambiguous():
    assert one("வெள்ளிக்கிழமை").ambiguous is False
    assert one("வெள்ளி").ambiguous is True


def test_a_fused_tamil_weekday_adverb_is_still_a_weekday():
    for text in ["வெள்ளியன்று", "சனியன்று"]:
        expression = one(text)
        assert (expression.kind, expression.rule) == (WEEKDAY, "weekday_tamil"), text
        assert read(text).reason == NO_ANCHOR, text


def test_an_english_weekday_is_not_read_by_the_tamil_rule():
    assert one("Friday").rule == "weekday_english"
    assert one("வியாழக்கிழமை").rule == "weekday_tamil"


def test_direction_words_are_kept_on_the_surface():
    assert one("அடுத்த வாரம்").direction == "future"
    assert one("கடந்த வாரம்").direction == "past"
    assert one("this week").direction == "present"


def test_every_phrase_becomes_exactly_one_surface():
    text = (
        "செப் 28, 2026 அன்று இரவு 10 மணிக்கு மழை. நேற்று மாலை புகார். "
        "ஜூலை 20 முதல் 25 வரை விடுமுறை. October 2, 2026 at 4:30 PM meeting."
    )
    found = find(text)
    assert kinds(text) == [
        ABSOLUTE_DATE,
        RELATIVE_DAY,
        DATE_RANGE,
        ABSOLUTE_DATE,
    ]
    covered = [(expression.char_start, expression.char_end) for expression in found]
    assert covered == sorted(covered)
    assert all(a < b for a, b in zip(covered, covered[1:])) or len(covered) == 1


def test_surfaces_keep_their_position_in_the_text():
    text = "மதுரையில் நேற்று மழை"
    expression = one(text)
    assert expression.char_start == text.index("நேற்று")
    assert expression.char_end == expression.char_start + len("நேற்று")
    assert expression.span().slice_from(text) == "நேற்று"


def test_an_expression_prints_itself_for_a_log_line():
    expression = one("நேற்று மழை")
    assert expression.describe().startswith("relative_day(relative_day) 'நேற்று'")
    assert expression.as_dict()["endpoints"] == []
    assert set(KINDS) >= {ABSOLUTE_DATE, CLOCK_TIME, WEEKDAY, DATE_RANGE}


def test_as_dict_is_plain_data():
    payload = one("28.09.2026 அன்று இரவு 10 மணிக்கு").as_dict()
    assert payload["precision"] == "hour"
    assert payload["parts"]["hour"] == 10
    assert payload["parts"]["meridiem"] == "pm"
    assert isinstance(resolve(one("நேற்று"), reference=REFERENCE).as_dict()["value"], str)


def test_precision_helpers_order_the_calendar_units():
    assert finer(TimePrecision.DAY, TimePrecision.HOUR) is TimePrecision.HOUR
    assert coarser(TimePrecision.DAY, TimePrecision.HOUR) is TimePrecision.DAY
    assert PRECISION_ORDER[0] is TimePrecision.SECOND
    assert PRECISION_ORDER[-1] is TimePrecision.UNKNOWN


def test_a_declared_time_zone_is_read_from_the_text_only_when_it_is_named():
    assert timezone_cue("இந்திய நேரப்படி 10 மணி") == TIMEZONE_DECLARATION
    assert timezone_cue("at 10 AM Indian Standard Time") == TIMEZONE_DECLARATION
    assert timezone_cue("10 மணி") is None


def test_a_reporting_verb_in_the_same_sentence_is_found_next_to_a_surface():
    text = "நேற்று மாலை மதுரையில் புகார் அளித்ததாக தகவல் தெரிவித்துள்ளனர்"
    start = text.index("நேற்று")
    assert reporting_cue(text, char_start=start, char_end=start + 6) in REPORTING_CUES


def test_a_cue_in_the_next_sentence_does_not_reach_back():
    two_sentences = "நேற்று மழை. தகவல் தெரிவித்துள்ளனர்."
    start = two_sentences.index("நேற்று")
    assert reporting_cue(two_sentences, char_start=start, char_end=start + 6) is None


def test_a_cue_farther_than_the_window_is_not_read_into_the_sentence():
    text = "தகவல் வந்தது " + "x " * 60 + "நேற்று"
    start = text.index("நேற்று")
    assert reporting_cue(text, char_start=start, char_end=start + 6) is None


def test_the_boilerplate_month_names_are_read_by_the_time_rules_too():
    for month in (*boilerplate.TAMIL_MONTHS, *boilerplate.LATIN_MONTHS):
        if len(month) < 4:
            continue
        key = unicodedata.normalize("NFC", month).lower().rstrip(".")
        assert key in MONTHS, month


def test_a_stamp_timestamp_is_readable_on_its_own():
    text = "அக் 01, 2026 09:46 PM"
    expression = one(text)
    assert resolve(expression).value == datetime(2026, 10, 1, 21, 46)
    assert expression.precision is TimePrecision.MINUTE


def test_semantics_is_never_claimed_by_the_pattern_layer():
    expression = one("28.09.2026")
    assert not any(isinstance(value, TimeSemantics) for value in vars(expression).values())
    assert "semantics" not in expression.as_dict()


def test_an_empty_or_plain_text_field_yields_nothing():
    assert find("") == []
    assert find("அவர் மதுரையில் உள்ளார்") == []
