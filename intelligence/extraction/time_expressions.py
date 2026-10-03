"""Temporal surface patterns: what the text says about time, before anyone decides what it means."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from intelligence.extraction.spans import Span
from intelligence.models.enums import TimePrecision, TimeQualifier

ABSOLUTE_DATE = "absolute_date"
MONTH_DAY = "month_day"
MONTH_YEAR = "month_year"
YEAR_ONLY = "year"
CLOCK_TIME = "clock_time"
RELATIVE_DAY = "relative_day"
RELATIVE_OFFSET = "relative_offset"
RELATIVE_WINDOW = "relative_window"
WEEKDAY = "weekday"
DATE_RANGE = "date_range"

KINDS = frozenset(
    {
        ABSOLUTE_DATE,
        MONTH_DAY,
        MONTH_YEAR,
        YEAR_ONLY,
        CLOCK_TIME,
        RELATIVE_DAY,
        RELATIVE_OFFSET,
        RELATIVE_WINDOW,
        WEEKDAY,
        DATE_RANGE,
    }
)

EARLY_MORNING = "early_morning"
MORNING = "morning"
NOON = "noon"
MIDDAY = "midday"
AFTERNOON = "afternoon"
EVENING = "evening"
NIGHT = "night"
MIDNIGHT = "midnight"

PAST = "past"
FUTURE = "future"
PRESENT = "present"

ISO_DATE = "iso_date"
NUMERIC_DATE = "numeric_date"
MONTH_DAY_YEAR = "month_day_year"
DAY_MONTH_YEAR = "day_month_year"
MONTH_DAY_ONLY = "month_day_only"
DAY_MONTH_ONLY = "day_month_only"
DAY_ONLY = "day_only"
NUMERIC_DAY_MONTH_ONLY = "numeric_day_month_only"
MONTH_YEAR = "month_year"
YEAR_ONLY_RULE = "year_only"
CLOCK_COLON = "clock_colon"
CLOCK_TAMIL = "clock_tamil"
CLOCK_MERIDIEM = "clock_meridiem"
RELATIVE_DAY_RULE = "relative_day"
RELATIVE_OFFSET_RULE = "relative_offset"
RELATIVE_WINDOW_RULE = "relative_window"
WEEKDAY_TAMIL = "weekday_tamil"
WEEKDAY_ENGLISH = "weekday_english"
RANGE_TAMIL = "range_tamil"
RANGE_ENGLISH = "range_english"
DATE_CLOCK = "date_clock"
RELATIVE_CLOCK = "relative_clock"

MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "ஜனவரி": 1,
    "ஜன": 1,
    "february": 2,
    "feb": 2,
    "பிப்ரவரி": 2,
    "பிப்": 2,
    "march": 3,
    "mar": 3,
    "மார்ச்": 3,
    "மார்": 3,
    "april": 4,
    "apr": 4,
    "ஏப்ரல்": 4,
    "ஏப்": 4,
    "may": 5,
    "மே": 5,
    "june": 6,
    "jun": 6,
    "ஜூன்": 6,
    "july": 7,
    "jul": 7,
    "ஜூலை": 7,
    "august": 8,
    "aug": 8,
    "ஆகஸ்ட்": 8,
    "செப்டம்பர்": 9,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "செப்": 9,
    "october": 10,
    "oct": 10,
    "அக்டோபர்": 10,
    "அக்": 10,
    "november": 11,
    "nov": 11,
    "நவம்பர்": 11,
    "நவ": 11,
    "december": 12,
    "dec": 12,
    "டிசம்பர்": 12,
    "டிச": 12,
}

#: Abbreviations whose expansion is not unique, so the number is never guessed.
AMBIGUOUS_MONTHS: dict[str, tuple[int, ...]] = {"ஜூ": (6, 7)}

DAY_PARTS: dict[str, tuple[str, str]] = {
    "அதிகாலை": (EARLY_MORNING, "am"),
    "காலை": (MORNING, "am"),
    "காலையில்": (MORNING, "am"),
    "காலை நேரத்தில்": (MORNING, "am"),
    "நண்பகல்": (NOON, "pm"),
    "நண்பகலில்": (NOON, "pm"),
    "மதியம்": (MIDDAY, "pm"),
    "மதியத்தில்": (MIDDAY, "pm"),
    "பிற்பகல்": (AFTERNOON, "pm"),
    "பிற்பகலில்": (AFTERNOON, "pm"),
    "மாலை": (EVENING, "pm"),
    "மாலையில்": (EVENING, "pm"),
    "மாலை நேரத்தில்": (EVENING, "pm"),
    "இரவு": (NIGHT, "pm"),
    "இரவில்": (NIGHT, "pm"),
    "நள்ளிரவு": (MIDNIGHT, "am"),
    "dawn": (EARLY_MORNING, "am"),
    "early morning": (EARLY_MORNING, "am"),
    "morning": (MORNING, "am"),
    "noon": (NOON, "pm"),
    "midday": (MIDDAY, "pm"),
    "afternoon": (AFTERNOON, "pm"),
    "evening": (EVENING, "pm"),
    "night": (NIGHT, "pm"),
    "midnight": (MIDNIGHT, "am"),
}

#: A day part is a 12-hour clue, so a clock inside it reads as pm or am.
DAY_PART_MERIDIEM: dict[str, str] = {label: meridiem for label, meridiem in DAY_PARTS.values()}

RELATIVE_DAYS: dict[str, int] = {
    "நேற்று": -1,
    "இன்று": 0,
    "நாளை": 1,
    "நாளைக்கு": 1,
    "நேற்று முன்தினம்": -2,
    "முன்னேற்று": -2,
    "நாளை மறுநாள்": 2,
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
    "the day before yesterday": -2,
    "the day after tomorrow": 2,
}

#: A day part fused into the day word, so it carries both at once.
FUSED_DAY_PARTS: dict[str, tuple[int, str]] = {
    "நேற்றிரவு": (-1, NIGHT),
    "இன்றிரவு": (0, NIGHT),
    "நாளைக்காலை": (1, MORNING),
    "tonight": (0, NIGHT),
    "last night": (-1, NIGHT),
    "this morning": (0, MORNING),
    "this afternoon": (0, AFTERNOON),
    "this evening": (0, EVENING),
    "yesterday morning": (-1, MORNING),
    "yesterday afternoon": (-1, AFTERNOON),
    "yesterday evening": (-1, EVENING),
    "today morning": (0, MORNING),
    "today evening": (0, EVENING),
    "tomorrow morning": (1, MORNING),
    "tomorrow evening": (1, EVENING),
}

#: These only make sense against another date, which the article has to supply.
ANCHORED_RELATIVE_DAYS: dict[str, None] = {
    "மறுநாள்": None,
    "next day": None,
    "previous day": None,
    "the same day": None,
}

PRESENT_WORDS: dict[str, int] = {
    "தற்போது": 0,
    "இப்போது": 0,
    "now": 0,
    "currently": 0,
}

DAY_PART_ANCHORS = (
    ("நேற்று", -1),
    ("இன்று", 0),
    ("நாளை", 1),
    ("yesterday", -1),
    ("today", 0),
    ("tomorrow", 1),
)


def _relative_table() -> dict[str, tuple[Optional[int], Optional[str]]]:
    table: dict[str, tuple[Optional[int], Optional[str]]] = {}
    for word, offset in {**RELATIVE_DAYS, **PRESENT_WORDS}.items():
        table[word] = (offset, None)
    for word, (offset, label) in FUSED_DAY_PARTS.items():
        table[word] = (offset, label)
    for word in ANCHORED_RELATIVE_DAYS:
        table[word] = (None, None)
    for base, offset in DAY_PART_ANCHORS:
        for surface, (label, _meridiem) in DAY_PARTS.items():
            table[f"{base} {surface}"] = (offset, label)
    return table


#: surface -> (day offset from the reference, day part the surface itself states)
RELATIVE_DAY_TABLE: dict[str, tuple[Optional[int], Optional[str]]] = _relative_table()

COUNT_WORDS: dict[str, int] = {
    "ஒரு": 1,
    "ஒன்ற": 1,
    "ஒன்று": 1,
    "இரண்டு": 2,
    "இரு": 2,
    "மூன்று": 3,
    "நான்கு": 4,
    "ஐந்து": 5,
    "ஆறு": 6,
    "ஏழு": 7,
    "எட்டு": 8,
    "ஒன்பது": 9,
    "பத்து": 10,
    "பதினொரு": 11,
    "பன்னிரண்டு": 12,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

#: Every counted surface names the calendar unit it counts, so arithmetic never re-reads text.
UNIT_SURFACES: dict[str, str] = {}
for _unit, _surfaces in (
    ("hour", ("மணி நேரத்திற்கு", "மணி நேரமாக", "மணி நேரம்", "மணிநேரம்", "hours", "hour")),
    ("day", ("நாட்களாக", "நாட்களுக்கு", "நாட்கள்", "நாள்கள்", "நாட்கள", "நாட்கு", "நாள்",
             "தினங்களாக", "தினங்கள்", "தினம்", "days", "day")),
    ("week", ("வாரங்களுக்கு", "வாரங்களாக", "வாரங்கள்", "வாரத்திற்கு", "வாரம்", "weeks", "week")),
    ("month", ("மாதங்களுக்கு", "மாதங்களாக", "மாதங்கள்", "மாதம்", "months", "month")),
    ("year", ("ஆண்டுகளுக்கு", "ஆண்டுகளாக", "ஆண்டுகள்", "ஆண்டு", "years", "year")),
):
    for _surface in _surfaces:
        UNIT_SURFACES[_surface] = _unit

PAST_MARKERS = ("கடந்த", "கழிந்த", "சென்ற", "last", "previous", "past")
FUTURE_MARKERS = ("அடுத்த", "வரும்", "எதிர்வரும்", "next", "coming", "upcoming", "following")
PRESENT_MARKERS = ("இந்த", "இந்", "தற்போதைய", "this", "current", "present")
ONGOING_SUFFIXES = ("ஆக", "ாக")
AGO_MARKERS = ("முன்", "முன்னர்", "முன்பாக", "முன்பு", "ago", "earlier", "before")
LATER_MARKERS = ("கழித்து", "பின்னர்", "பின்", "பிறகு", "அடுத்து", "later", "after", "hence")

WEEKDAYS: dict[str, int] = {
    "திங்கட்கிழமை": 1,
    "திங்கள்": 1,
    "செவ்வாய்க்கிழமை": 2,
    "செவ்வாய்": 2,
    "புதன்கிழமை": 3,
    "புதன்": 3,
    "வியாழக்கிழமை": 4,
    "வியாழன்": 4,
    "வெள்ளிக்கிழமை": 5,
    "வெள்ளி": 5,
    "சனிக்கிழமை": 6,
    "சனி": 6,
    "ஞாயிற்றுக்கிழமை": 7,
    "ஞாயிறு": 7,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 7,
}

WEEKDAY_ABBREVIATIONS: dict[str, int] = {
    "mon": 1,
    "tue": 2,
    "tues": 2,
    "wed": 3,
    "thu": 4,
    "thur": 4,
    "thurs": 4,
    "fri": 5,
    "sat": 6,
    "sun": 7,
}

TIMEZONE_CUES = (
    "இந்திய நேரப்படி",
    "இந்திய நேரம்",
    "ஐ.எஸ்.டி.",
    "ஐ.எஸ்.டி",
    "இஸ்டி",
    "indian standard time",
)
TIMEZONE_DECLARATION = "Asia/Kolkata"

#: Verbs of *incoming* information. "என தகவல்" attributes a statement, it does not date one.
REPORTING_CUES = (
    "தகவல் வந்தது",
    "தகவல் கிடைத்தது",
    "தகவலின்படி",
    "தகவல் தெரிவித்து",
    "தெரிவித்தனர்",
    "தெரிவித்துள்ளனர்",
    "புகார் அளித்த",
    "புகார் கொடுத்த",
    "புகார் செய்த",
    "பதிவு செய்த",
    "informed the police",
    "filed a complaint",
    "lodge a complaint",
    "according to information received",
    "told this correspondent",
    "said the police",
    "reported",
)

SENTENCE_BREAKS = ".!?;\n։|"
REPORTING_CUE_WINDOW = 90

MIN_YEAR = 1900
MAX_YEAR = 2199

_TAIL_CLASS = r"0-9A-Za-z\u0B80-\u0BFF"
_LEAD = rf"(?<![{_TAIL_CLASS}])"
_TRAIL = rf"(?![{_TAIL_CLASS}])"

PRECISION_ORDER = (
    TimePrecision.SECOND,
    TimePrecision.MINUTE,
    TimePrecision.HOUR,
    TimePrecision.DAY,
    TimePrecision.WEEK,
    TimePrecision.MONTH,
    TimePrecision.QUARTER,
    TimePrecision.YEAR,
    TimePrecision.DECADE,
    TimePrecision.UNKNOWN,
)

_ABSORBABLE = frozenset(
    {ABSOLUTE_DATE, MONTH_DAY, MONTH_YEAR, YEAR_ONLY, RELATIVE_DAY, CLOCK_TIME, WEEKDAY}
)
_DATE_KINDS = frozenset({ABSOLUTE_DATE, MONTH_DAY, MONTH_YEAR, YEAR_ONLY})


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _key(text: str) -> str:
    """Table lookup form of a matched surface: NFC, trimmed, case-folded."""
    return _nfc(text).strip().rstrip(".").lower()


def _alternation(values) -> str:
    ordered = sorted({str(value) for value in values}, key=lambda value: (-len(value), value))
    return "|".join(re.escape(_nfc(value)) for value in ordered)


def finer(a: TimePrecision, b: TimePrecision) -> TimePrecision:
    return PRECISION_ORDER[min(PRECISION_ORDER.index(a), PRECISION_ORDER.index(b))]


def coarser(a: TimePrecision, b: TimePrecision) -> TimePrecision:
    return PRECISION_ORDER[max(PRECISION_ORDER.index(a), PRECISION_ORDER.index(b))]


_MONTH_ALT = _alternation([*MONTHS, *AMBIGUOUS_MONTHS])
_PART_ALT = _alternation(DAY_PARTS)
_RELATIVE_ALT = _alternation(RELATIVE_DAY_TABLE)
_COUNT_ALT = rf"(?:\d{{1,2}}|{_alternation(COUNT_WORDS)})"
_UNIT_ALT = _alternation(UNIT_SURFACES)
_DIRECTION_ALT = _alternation([*PAST_MARKERS, *FUTURE_MARKERS, *PRESENT_MARKERS])
_AGO_ALT = _alternation(AGO_MARKERS)
_LATER_ALT = _alternation(LATER_MARKERS)
_TAMIL_WEEKDAY_ALT = _alternation(
    [name for name in WEEKDAYS if any("\u0b80" <= character <= "\u0bff" for character in name)]
)
_ALL_WEEKDAY_ALT = _alternation([*WEEKDAYS, *WEEKDAY_ABBREVIATIONS])

_ORD = r"(?:st|nd|rd|th)?"
_DETI = r"(?:(?:\s*ஆம்)?(?:\s*தேதி)?)?"
_MONTH = rf"(?P<month>{_MONTH_ALT})\.?"
_DAY = rf"(?P<day>\d{{1,2}}){_ORD}"
_YEAR = rf"(?P<year>\d{{4}})"
_COUNT = rf"(?P<count>{_COUNT_ALT})"
_UNIT = rf"(?P<unit>{_UNIT_ALT})"
_HOUR = r"(?P<hour>\d{1,2})"
_MINUTE = r"(?P<minute>\d{2})"
_MERIDIEM_ALT = r"(?:a\.m\.|p\.m\.|am|pm)"

_ISO = rf"{_LEAD}(?P<year>\d{{4}})-(?P<month>\d{{1,2}})-(?P<day>\d{{1,2}}){_TRAIL}"
_NUMERIC = rf"{_LEAD}(?P<a>\d{{1,2}})[/.\-](?P<b>\d{{1,2}})[/.\-]{_YEAR}{_TRAIL}"
_MONTH_DAY_YEAR = rf"{_LEAD}{_MONTH}\s+{_DAY}{_DETI},?\s+{_YEAR}{_DETI}{_TRAIL}"
_DAY_MONTH_YEAR = rf"{_LEAD}{_DAY}{_DETI}\s+{_MONTH}\s+{_YEAR}{_DETI}{_TRAIL}"
_MONTH_DAY_ONLY = rf"{_LEAD}{_MONTH}\s+{_DAY}{_DETI}{_TRAIL}"
_DAY_MONTH_ONLY = rf"{_LEAD}{_DAY}{_DETI}\s+{_MONTH}{_TRAIL}"
_NUMERIC_DAY_MONTH_ONLY = rf"{_LEAD}(?P<a>\d{{1,2}})[/\-](?P<b>\d{{1,2}})\s*(?:ஆம்\s*)?தேதி{_TRAIL}"
_MONTH_YEAR = rf"{_LEAD}{_MONTH}\s+{_YEAR}{_DETI}{_TRAIL}"
_DAY_ONLY = rf"{_LEAD}{_DAY}(?:\s*(?:ஆம்\s*)?(?:தேதி|வரை|ஈரான|மட்டும்)){_TRAIL}"
_YEAR_ONLY = (
    rf"{_LEAD}(?P<pre>(?:in|since|from|during|until|upto|up to|of|around|by)\s+(?:the\s+year\s+)?)?"
    rf"{_YEAR}(?P<cue>(?:[-\s](?:ல்|இல்|யில்))|(?:\s*(?:ஆம்\s*)?(?:ஆண்டு|வருஷம்|year|years)))?{_TRAIL}"
)
_CLOCK_COLON = (
    rf"{_LEAD}(?<![+-]){_HOUR}:{_MINUTE}(?::(?P<second>\d{{2}}))?(?:\s*(?P<meridiem>{_MERIDIEM_ALT}))?{_TRAIL}"
)
_CLOCK_MERIDIEM = rf"{_LEAD}{_HOUR}\s*(?P<meridiem>{_MERIDIEM_ALT}){_TRAIL}"
_CLOCK_TAMIL = (
    rf"{_LEAD}{_HOUR}(?:[.:]{_MINUTE})?(?![\d])\s*(?P<mani>மணி(?!\s*நேர)(?:க்கு|யின்)?){_TRAIL}"
)
_RELATIVE = rf"{_LEAD}(?P<relative>{_RELATIVE_ALT}){_TRAIL}"
#: A Tamil dative unit takes an explicit euphonic consonant before பிறகு/பின்.
_EUPHONY = r"(?:ப்|க்|ட்|ய்)?"
_OFFSET = (
    rf"{_LEAD}(?:(?P<lead>in|within|after|from)\s+)?{_COUNT}\s+{_UNIT}{_EUPHONY}\s*"
    rf"(?:(?P<later>{_LATER_ALT})|(?P<ago>{_AGO_ALT}))?{_TRAIL}"
)
_WINDOW = (
    rf"{_LEAD}(?:(?P<dir>{_DIRECTION_ALT})\s+)?(?:{_COUNT}\s+)?{_UNIT}{_TRAIL}"
)
_WEEKDAY_TAMIL = (
    rf"{_LEAD}(?:(?P<dir>{_DIRECTION_ALT})\s+)?(?P<weekday>{_TAMIL_WEEKDAY_ALT})"
    rf"(?P<suffix>க்கிழமை|கிழமை|யில்|ல்|ம்|ஆம்|யன்று)?(?:\s*(?P<tail>அன்று|தேதி))?{_TRAIL}"
)
_WEEKDAY_ENGLISH = (
    rf"{_LEAD}(?:(?P<dir>on|last|next|this|every|by|from)\s+)?(?P<weekday>{_ALL_WEEKDAY_ALT})"
    rf"(?:\s+(?:the\s+)?(?P<day>\d{{1,2}}){_ORD})?{_TRAIL}"
)

_DAY_PART_TRAIL_RE = re.compile(rf"^\s*(?P<part>{_PART_ALT}){_TRAIL}", re.IGNORECASE)
_DAY_PART_LEAD_RE = re.compile(rf"{_LEAD}(?P<part>{_PART_ALT})\s*$", re.IGNORECASE)

_FILLER_ALT = _alternation(
    ("அன்று", "ஆம்", "தேதி", "நேரம்", "நேரத்தில்", "மணி நேரம்", "at", "on", "of", "the")
)
_GAP_RE = re.compile(rf"^[\s,;-]*(?:(?:{_FILLER_ALT})[\s,;-]*)*$", re.IGNORECASE)
_RANGE_TAMIL_RE = re.compile(r"^\s*(?P<marker>முதல்)\s*$")
_RANGE_ENGLISH_RE = re.compile(r"^\s*(?P<marker>from|to|through|until|till|–|—|-)\s*$", re.IGNORECASE)
_RANGE_TAIL_RE = re.compile(
    rf"^\s*(?P<tail>{_alternation(('வரை', 'ஈரான', 'மட்டும்', 'until', 'till'))}){_TRAIL}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Expression:
    """One temporal surface, cut from the text it was found in."""

    kind: str
    rule: str
    surface: str
    char_start: int
    char_end: int
    parts: dict = field(default_factory=dict)
    precision: TimePrecision = TimePrecision.UNKNOWN
    qualifier: TimeQualifier = TimeQualifier.UNKNOWN
    day_part: Optional[str] = None
    meridiem: Optional[str] = None
    offset_days: Optional[int] = None
    count: Optional[int] = None
    unit: Optional[str] = None
    direction: Optional[str] = None
    ongoing: bool = False
    ambiguous: bool = False
    notes: tuple[str, ...] = ()
    endpoints: tuple["Expression", ...] = ()

    @property
    def length(self) -> int:
        return self.char_end - self.char_start

    @property
    def has_clock(self) -> bool:
        return self.parts.get("hour") is not None

    @property
    def has_day(self) -> bool:
        return self.parts.get("day") is not None

    @property
    def has_year(self) -> bool:
        return self.parts.get("year") is not None

    @property
    def is_relative(self) -> bool:
        return self.kind in (RELATIVE_DAY, RELATIVE_OFFSET, RELATIVE_WINDOW)

    @property
    def is_interval(self) -> bool:
        return self.kind in (RELATIVE_WINDOW, DATE_RANGE)

    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    def describe(self) -> str:
        return f"{self.kind}({self.rule}) {self.surface!r} [{self.char_start}:{self.char_end}]"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "rule": self.rule,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "parts": dict(self.parts),
            "precision": self.precision.value,
            "qualifier": self.qualifier.value,
            "day_part": self.day_part,
            "meridiem": self.meridiem,
            "offset_days": self.offset_days,
            "count": self.count,
            "unit": self.unit,
            "direction": self.direction,
            "ongoing": self.ongoing,
            "ambiguous": self.ambiguous,
            "notes": list(self.notes),
            "endpoints": [endpoint.as_dict() for endpoint in self.endpoints],
        }


@dataclass(frozen=True)
class Resolution:
    """What an expression can be turned into, and what stops it going further."""

    value: Optional[datetime]
    precision: TimePrecision
    qualifier: TimeQualifier
    resolved: bool
    reason: Optional[str] = None
    needs_reference: bool = False
    ambiguous: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "value": self.value.isoformat() if self.value else None,
            "precision": self.precision.value,
            "qualifier": self.qualifier.value,
            "resolved": self.resolved,
            "reason": self.reason,
            "needs_reference": self.needs_reference,
            "ambiguous": self.ambiguous,
            "notes": list(self.notes),
        }


NO_YEAR = "the surface names a day and month but no year"
NO_MONTH = "the surface names a day of the month but no month or year"
NO_DATE = "a clock reading with no date cannot be placed on a calendar"
NO_ANCHOR = "a weekday name without a date anchor is not one specific day"
NO_REFERENCE = "a relative expression stays relative without a reference datetime"
ANCHORED = "this expression is anchored to another date the text does not give"
INTERVAL = "an interval has a span, not a single instant"
IMPOSSIBLE = "the surface is not a real calendar date"
AMBIGUOUS_ABBREVIATION = "this abbreviation names two months, so no month number is claimed"

NO_MERIDIEM = "no meridiem stated; the reading is 12-hour ambiguous"


def _unresolved(
    expression: Expression,
    reason: str,
    *,
    needs_reference: bool = False,
    ambiguous: bool = False,
) -> Resolution:
    return Resolution(
        value=None,
        precision=expression.precision,
        qualifier=TimeQualifier.UNKNOWN,
        resolved=False,
        reason=reason,
        needs_reference=needs_reference,
        ambiguous=ambiguous or expression.ambiguous,
        notes=expression.notes,
    )


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime(year, month + 1, 1) - datetime(year, month, 1)).days


def _month_reading(text: str) -> tuple[Optional[int], bool]:
    surface = _key(text)
    if surface.isdigit():
        number = int(surface)
        return (number if 1 <= number <= 12 else None), False
    if surface in MONTHS:
        return MONTHS[surface], False
    if surface in AMBIGUOUS_MONTHS:
        return None, True
    return None, False


def _clock_surface(
    hour: int,
    minute: Optional[int],
    second: Optional[int],
    meridiem: Optional[str],
) -> tuple[dict, Optional[str], TimePrecision]:
    parts: dict = {"hour": hour}
    if minute is not None:
        parts["minute"] = minute
    if second is not None:
        parts["second"] = second
    label = None
    if meridiem:
        label = "am" if meridiem.lower().startswith("a") else "pm"
        parts["meridiem"] = label
    if second is not None:
        precision = TimePrecision.SECOND
    elif minute is not None:
        precision = TimePrecision.MINUTE
    else:
        precision = TimePrecision.HOUR
    return parts, label, precision


def _instant(parts: dict) -> Optional[datetime]:
    year = parts.get("year")
    if year is None or not (MIN_YEAR <= year <= MAX_YEAR):
        return None
    month = parts.get("month") or 1
    day = parts.get("day") or 1
    hour = parts.get("hour")
    if hour is None:
        hour = minute = second = 0
    else:
        minute = parts.get("minute") or 0
        second = parts.get("second") or 0
        if parts.get("meridiem") == "pm" and hour < 12:
            hour += 12
        elif parts.get("meridiem") == "am" and hour == 12:
            hour = 0
    if not 1 <= month <= 12:
        return None
    if not 1 <= day <= _days_in_month(year, month):
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _shift(reference: datetime, count: int, unit: str) -> Optional[datetime]:
    if unit == "hour":
        return reference + timedelta(hours=count)
    if unit == "day":
        return reference + timedelta(days=count)
    if unit == "week":
        return reference + timedelta(weeks=count)
    if unit in ("month", "year"):
        months = count if unit == "month" else 12 * count
        total = reference.year * 12 + reference.month - 1 + months
        year, month = divmod(total, 12)
        month += 1
        day = min(reference.day, _days_in_month(year, month))
        return reference.replace(year=year, month=month, day=day)
    return None


def _start_of(value: datetime, precision: TimePrecision) -> datetime:
    if precision is TimePrecision.MONTH:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision is TimePrecision.YEAR:
        return value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if precision is TimePrecision.WEEK:
        return (value - timedelta(days=value.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


_UNIT_PRECISION = {
    "hour": TimePrecision.HOUR,
    "day": TimePrecision.DAY,
    "week": TimePrecision.WEEK,
    "month": TimePrecision.MONTH,
    "year": TimePrecision.YEAR,
}


def _count_value(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    surface = _key(raw)
    if surface.isdigit():
        return int(surface)
    return COUNT_WORDS.get(surface)


def _expression(
    kind: str,
    rule: str,
    match: re.Match,
    text: str,
    *,
    precision: TimePrecision = TimePrecision.UNKNOWN,
    qualifier: TimeQualifier = TimeQualifier.UNKNOWN,
    parts: Optional[dict] = None,
    day_part: Optional[str] = None,
    meridiem: Optional[str] = None,
    offset_days: Optional[int] = None,
    count: Optional[int] = None,
    unit: Optional[str] = None,
    direction: Optional[str] = None,
    ongoing: bool = False,
    ambiguous: bool = False,
    notes: tuple[str, ...] = (),
) -> Expression:
    start, end = match.start(), match.end()
    return Expression(
        kind=kind,
        rule=rule,
        surface=text[start:end],
        char_start=start,
        char_end=end,
        parts=dict(parts or {}),
        precision=precision,
        qualifier=qualifier,
        day_part=day_part,
        meridiem=meridiem,
        offset_days=offset_days,
        count=count,
        unit=unit,
        direction=direction,
        ongoing=ongoing,
        ambiguous=ambiguous,
        notes=notes,
    )


def _named_date(rule: str, match: re.Match, text: str, *, with_year: bool) -> Optional[Expression]:
    month, ambiguous = _month_reading(match.group("month"))
    day = int(match.group("day"))
    if day > 31:
        return None
    parts: dict = {"day": day}
    if month is not None:
        parts["month"] = month
    notes = ["abbreviated month names two months"] if ambiguous else []
    if with_year:
        year = int(match.group("year"))
        if not MIN_YEAR <= year <= MAX_YEAR:
            return None
        parts["year"] = year
        return _expression(
            ABSOLUTE_DATE, rule, match, text,
            precision=TimePrecision.DAY, qualifier=TimeQualifier.EXACT,
            parts=parts, ambiguous=ambiguous, notes=tuple(notes),
        )
    return _expression(
        MONTH_DAY, rule, match, text,
        precision=TimePrecision.DAY, parts=parts, ambiguous=ambiguous,
        notes=tuple(notes + ["no year in the surface"]),
    )


def _day_month_reading(first: int, second: int):
    """Day and month from two bare numbers, under the declared Indian day-first convention."""
    if first > 31 or second > 31:
        return None
    if second > 12 and first <= 12:
        return second, first, False, ("only the month-first reading is a real date",)
    if first > 12 and second <= 12:
        return first, second, False, ()
    if first <= 12 and second <= 12:
        return first, second, True, ("read day-first under the declared Indian convention",)
    return None


def _parse(rule: str, match: re.Match, text: str) -> Optional[Expression]:
    if rule in (ISO_DATE, NUMERIC_DATE, NUMERIC_DAY_MONTH_ONLY):
        year: Optional[int] = int(match.group("year")) if rule != NUMERIC_DAY_MONTH_ONLY else None
        notes: tuple[str, ...] = ()
        if rule == ISO_DATE:
            month, ambiguous = _month_reading(match.group("month"))
            day = int(match.group("day"))
        else:
            first, second = int(match.group("a")), int(match.group("b"))
            reading = _day_month_reading(first, second)
            if reading is None:
                return None
            day, month, ambiguous, notes = reading
        if year is not None and not MIN_YEAR <= year <= MAX_YEAR:
            return None
        parts: dict = {"day": day}
        if month is not None:
            parts["month"] = month
        if year is None:
            ambiguous = False
            notes = notes + ("no year in the surface",)
        else:
            parts["year"] = year
        return _expression(
            ABSOLUTE_DATE if year is not None else MONTH_DAY,
            rule,
            match,
            text,
            precision=TimePrecision.DAY,
            qualifier=TimeQualifier.EXACT if year is not None else TimeQualifier.UNKNOWN,
            parts=parts,
            ambiguous=ambiguous,
            notes=notes,
        )

    if rule in (MONTH_DAY_YEAR, DAY_MONTH_YEAR, MONTH_DAY_ONLY, DAY_MONTH_ONLY):
        return _named_date(
            rule, match, text, with_year=rule in (MONTH_DAY_YEAR, DAY_MONTH_YEAR)
        )

    if rule == DAY_ONLY:
        day = int(match.group("day"))
        if day > 31:
            return None
        return _expression(
            MONTH_DAY, rule, match, text,
            precision=TimePrecision.DAY, parts={"day": day},
            notes=("no month or year in the surface",),
        )

    if rule == MONTH_YEAR:
        month, ambiguous = _month_reading(match.group("month"))
        year = int(match.group("year"))
        if not MIN_YEAR <= year <= MAX_YEAR:
            return None
        parts = {"year": year}
        if month is not None:
            parts["month"] = month
        notes = ["abbreviated month names two months"] if ambiguous else []
        notes.append("no day in the surface")
        return _expression(
            MONTH_YEAR, rule, match, text,
            precision=TimePrecision.MONTH, qualifier=TimeQualifier.APPROXIMATE,
            parts=parts, ambiguous=ambiguous, notes=tuple(notes),
        )

    if rule == YEAR_ONLY_RULE:
        year = int(match.group("year"))
        if not MIN_YEAR <= year <= MAX_YEAR:
            return None
        if not (match.group("pre") or match.group("cue")):
            return None
        return _expression(
            YEAR_ONLY, rule, match, text,
            precision=TimePrecision.YEAR, qualifier=TimeQualifier.APPROXIMATE,
            parts={"year": year}, notes=("month and day are not stated",),
        )

    if rule in (CLOCK_COLON, CLOCK_MERIDIEM, CLOCK_TAMIL):
        hour = int(match.group("hour"))
        if hour > 23 or (rule == CLOCK_TAMIL and not match.group("mani")):
            return None
        raw_minute = match.groupdict().get("minute")
        minute = int(raw_minute) if raw_minute else None
        raw_second = match.groupdict().get("second")
        second = int(raw_second) if raw_second else None
        if minute is not None and minute > 59:
            return None
        parts, label, precision = _clock_surface(
            hour, minute, second, match.groupdict().get("meridiem")
        )
        notes = () if label else (NO_MERIDIEM,)
        return _expression(
            CLOCK_TIME, rule, match, text,
            precision=precision,
            qualifier=TimeQualifier.EXACT if label else TimeQualifier.APPROXIMATE,
            parts=parts, meridiem=label, notes=notes,
        )

    if rule == RELATIVE_DAY_RULE:
        found = RELATIVE_DAY_TABLE.get(_key(match.group("relative")))
        if found is None:
            return None
        offset, day_part = found
        meridiem = DAY_PART_MERIDIEM.get(day_part) if day_part else None
        notes = []
        if offset is None:
            notes.append("anchored to a date the text has not supplied")
        if day_part:
            notes.append(f"day part {day_part} narrows the day without naming an hour")
        return _expression(
            RELATIVE_DAY, rule, match, text,
            precision=TimePrecision.DAY, day_part=day_part, meridiem=meridiem,
            offset_days=offset, notes=tuple(notes),
        )

    if rule == RELATIVE_OFFSET_RULE:
        count = _count_value(match.group("count"))
        unit = UNIT_SURFACES.get(_key(match.group("unit")))
        lead = _key(match.group("lead") or "")
        if count is None or unit is None:
            return None
        if not (lead or match.group("later") or match.group("ago")):
            return None
        direction = (
            PAST
            if match.group("ago")
            else FUTURE
            if match.group("later") or lead in ("in", "within", "after", "from")
            else PAST
        )
        qualifier = (
            TimeQualifier.EXACT
            if unit in ("hour", "day", "week")
            else TimeQualifier.APPROXIMATE
        )
        return _expression(
            RELATIVE_OFFSET, rule, match, text,
            precision=_UNIT_PRECISION[unit], qualifier=qualifier,
            count=count, unit=unit, direction=direction,
            notes=(f"{count} {unit}(s) {direction} of a reference moment",),
        )

    if rule == RELATIVE_WINDOW_RULE:
        marker = _key(match.group("dir") or "")
        count = _count_value(match.group("count"))
        surface = _key(match.group("unit"))
        unit = UNIT_SURFACES.get(surface)
        if unit is None:
            return None
        ongoing = surface.endswith(ONGOING_SUFFIXES)
        direction = None
        if marker in PAST_MARKERS:
            direction = PAST
        elif marker in FUTURE_MARKERS:
            direction = FUTURE
        elif marker in PRESENT_MARKERS:
            direction = PRESENT
        elif ongoing:
            direction = PAST
        if not (marker or count or ongoing) or direction is None:
            return None
        return _expression(
            RELATIVE_WINDOW, rule, match, text,
            precision=_UNIT_PRECISION[unit],
            qualifier=(
                TimeQualifier.ONGOING
                if direction == PAST and ongoing
                else TimeQualifier.APPROXIMATE
            ),
            count=count or 1, unit=unit, direction=direction, ongoing=ongoing,
            notes=(f"{count or 1} {unit}(s) {direction} window",),
        )

    if rule in (WEEKDAY_TAMIL, WEEKDAY_ENGLISH):
        name = _key(match.group("weekday"))
        number = WEEKDAYS.get(name) or WEEKDAY_ABBREVIATIONS.get(name)
        if number is None:
            return None
        suffix = (match.groupdict().get("suffix") or "").strip()
        tail = _key(match.groupdict().get("tail") or "")
        marker = _key(match.group("dir") or "")
        direction = None
        if marker in PAST_MARKERS or marker == "last":
            direction = PAST
        elif marker in FUTURE_MARKERS or marker == "next":
            direction = FUTURE
        elif marker in PRESENT_MARKERS or marker in ("this", "on", "by", "from", "every"):
            direction = PRESENT
        notes = []
        ambiguous = False
        if rule == WEEKDAY_TAMIL:
            anchored = bool(suffix or marker or tail) or name.endswith("கிழமை")
            if not anchored:
                ambiguous = True
                notes.append("short Tamil weekday names are also ordinary nouns")
        return _expression(
            WEEKDAY, rule, match, text,
            precision=TimePrecision.DAY, parts={"weekday": number},
            direction=direction, ambiguous=ambiguous, notes=tuple(notes),
        )

    return None


RULES: tuple[tuple[str, str, str], ...] = (
    (ISO_DATE, ABSOLUTE_DATE, _ISO),
    (NUMERIC_DATE, ABSOLUTE_DATE, _NUMERIC),
    (MONTH_DAY_YEAR, ABSOLUTE_DATE, _MONTH_DAY_YEAR),
    (DAY_MONTH_YEAR, ABSOLUTE_DATE, _DAY_MONTH_YEAR),
    (MONTH_YEAR, MONTH_YEAR, _MONTH_YEAR),
    (NUMERIC_DAY_MONTH_ONLY, MONTH_DAY, _NUMERIC_DAY_MONTH_ONLY),
    (MONTH_DAY_ONLY, MONTH_DAY, _MONTH_DAY_ONLY),
    (DAY_MONTH_ONLY, MONTH_DAY, _DAY_MONTH_ONLY),
    (DAY_ONLY, MONTH_DAY, _DAY_ONLY),
    (YEAR_ONLY_RULE, YEAR_ONLY, _YEAR_ONLY),
    (RELATIVE_DAY_RULE, RELATIVE_DAY, _RELATIVE),
    (RELATIVE_OFFSET_RULE, RELATIVE_OFFSET, _OFFSET),
    (RELATIVE_WINDOW_RULE, RELATIVE_WINDOW, _WINDOW),
    (CLOCK_COLON, CLOCK_TIME, _CLOCK_COLON),
    (CLOCK_MERIDIEM, CLOCK_TIME, _CLOCK_MERIDIEM),
    (CLOCK_TAMIL, CLOCK_TIME, _CLOCK_TAMIL),
    (WEEKDAY_TAMIL, WEEKDAY, _WEEKDAY_TAMIL),
    (WEEKDAY_ENGLISH, WEEKDAY, _WEEKDAY_ENGLISH),
)

_COMPILED: tuple[tuple[str, re.Pattern], ...] = tuple(
    (rule, re.compile(pattern, re.IGNORECASE)) for rule, _kind, pattern in RULES
)

_PRIORITY = {rule: index for index, (rule, _kind, _pattern) in enumerate(RULES)}


def _scan(text: str) -> list[Expression]:
    found: list[Expression] = []
    for rule, pattern in _COMPILED:
        for match in pattern.finditer(text):
            if match.start() == match.end():
                continue
            expression = _parse(rule, match, text)
            if expression is not None:
                found.append(expression)
    return found


def _resolve_overlaps(candidates: list[Expression]) -> list[Expression]:
    ordered = sorted(
        candidates,
        key=lambda item: (-item.length, item.char_start, _PRIORITY.get(item.rule, 99)),
    )
    kept: list[Expression] = []
    for candidate in ordered:
        if any(
            candidate.char_start < other.char_end and other.char_start < candidate.char_end
            for other in kept
        ):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda item: item.char_start)


def _day_part_of(surface: str) -> Optional[tuple[str, str]]:
    return DAY_PARTS.get(_nfc(surface.strip().lower()))


def _absorb_day_parts(expressions: list[Expression], text: str) -> list[Expression]:
    #: Absorption widens a phrase, so each pass must see the phrases already widened.
    working = list(expressions)
    for index, expression in enumerate(working):
        if expression.kind not in _ABSORBABLE or expression.endpoints:
            continue
        start, end = expression.char_start, expression.char_end
        day_part = expression.day_part
        meridiem = expression.meridiem
        notes = list(expression.notes)
        moved = False

        trailing = _DAY_PART_TRAIL_RE.match(text[end:])
        if trailing and not _overlaps(start, end + trailing.end(), expression, working):
            found = _day_part_of(trailing.group("part"))
            if found:
                end += trailing.end()
                day_part, meridiem, notes = _add_day_part(day_part, meridiem, found, notes)
                moved = True

        if start > 0:
            leading = _DAY_PART_LEAD_RE.search(text[:start])
            if (
                leading
                and leading.end() == start
                and not _overlaps(leading.start(), end, expression, working)
            ):
                found = _day_part_of(leading.group("part"))
                if found:
                    start = leading.start()
                    day_part, meridiem, notes = _add_day_part(day_part, meridiem, found, notes)
                    moved = True

        if not moved:
            continue

        parts = dict(expression.parts)
        has_clock = parts.get("hour") is not None
        if meridiem and has_clock:
            parts["meridiem"] = meridiem
        notes = [note for note in notes if note != NO_MERIDIEM]
        working[index] = Expression(
            kind=expression.kind,
            rule=expression.rule,
            surface=text[start:end],
            char_start=start,
            char_end=end,
            parts=parts,
            precision=expression.precision,
            qualifier=(
                TimeQualifier.EXACT
                if has_clock and meridiem
                else expression.qualifier
            ),
            day_part=day_part,
            meridiem=meridiem,
            offset_days=expression.offset_days,
            count=expression.count,
            unit=expression.unit,
            direction=expression.direction,
            ongoing=expression.ongoing,
            ambiguous=expression.ambiguous,
            notes=tuple(dict.fromkeys(notes)),
            endpoints=expression.endpoints,
        )
    return working


def _add_day_part(
    current: Optional[str],
    meridiem: Optional[str],
    found: tuple[str, str],
    notes: list[str],
) -> tuple[Optional[str], Optional[str], list[str]]:
    label, label_meridiem = found
    if current and current != label:
        notes.append(f"phrase carries two day parts, {current} and {label}")
        return current, meridiem, notes
    return label, meridiem or label_meridiem, notes


def _overlaps(
    start: int, end: int, skip: Expression, expressions: list[Expression]
) -> bool:
    return any(
        other is not skip and other.char_start < end and start < other.char_end
        for other in expressions
    )


def _clockable(expression: Expression) -> bool:
    if expression.kind == RELATIVE_DAY:
        return expression.offset_days is not None
    return expression.kind in (ABSOLUTE_DATE, MONTH_DAY)


def _combine_clock(
    left: Expression, right: Expression, text: str
) -> Expression:
    date = right if left.kind == CLOCK_TIME else left
    clock = left if date is right else right
    parts = {**date.parts, **clock.parts}
    meridiem = clock.meridiem or date.meridiem
    if meridiem and parts.get("hour") is not None:
        parts["meridiem"] = meridiem
    hour = parts.get("hour") or 0
    qualifier = (
        TimeQualifier.EXACT if meridiem or hour > 12 else TimeQualifier.APPROXIMATE
    )
    notes = tuple(dict.fromkeys(date.notes + clock.notes))
    if qualifier is TimeQualifier.APPROXIMATE:
        notes = notes + (NO_MERIDIEM,)
    else:
        notes = tuple(
            note for note in notes
            if note != NO_MERIDIEM and not note.startswith("day part ")
        )
    start, end = left.char_start, right.char_end
    return Expression(
        kind=date.kind,
        rule=RELATIVE_CLOCK if date.kind == RELATIVE_DAY else DATE_CLOCK,
        surface=text[start:end],
        char_start=start,
        char_end=end,
        parts=parts,
        precision=finer(date.precision, clock.precision),
        qualifier=qualifier,
        day_part=date.day_part or clock.day_part,
        meridiem=meridiem,
        offset_days=date.offset_days,
        count=date.count,
        unit=date.unit,
        direction=date.direction,
        ongoing=date.ongoing,
        ambiguous=date.ambiguous or clock.ambiguous,
        notes=notes + ("date and clock read as one phrase",),
    )


def _range_rule(gap: str) -> Optional[tuple[str, str]]:
    tamil = _RANGE_TAMIL_RE.match(gap)
    if tamil:
        return (RANGE_TAMIL, tamil.group("marker"))
    english = _RANGE_ENGLISH_RE.match(gap)
    if english:
        return (RANGE_ENGLISH, english.group("marker"))
    return None


def _combine_range(
    left: Expression, right: Expression, gap: str, rule: tuple[str, str], text: str
) -> Expression:
    name, marker = rule
    end = right.char_end
    tail = _RANGE_TAIL_RE.match(text[end:])
    if tail:
        end += tail.end()
    return Expression(
        kind=DATE_RANGE,
        rule=name,
        surface=text[left.char_start : end],
        char_start=left.char_start,
        char_end=end,
        parts={"from": left.parts, "to": right.parts},
        precision=coarser(left.precision, right.precision),
        qualifier=TimeQualifier.APPROXIMATE,
        day_part=left.day_part or right.day_part,
        meridiem=left.meridiem or right.meridiem,
        ambiguous=left.ambiguous or right.ambiguous,
        notes=(
            f"endpoints joined by {marker!r}",
            "a range is an interval; no single instant is claimed",
        ),
        endpoints=(left, right),
    )


def _merge_phrases(expressions: list[Expression], text: str) -> list[Expression]:
    merged = list(expressions)
    while True:
        for index in range(len(merged) - 1):
            left, right = merged[index], merged[index + 1]
            if right.char_start <= left.char_end:
                continue
            gap = text[left.char_end : right.char_start]
            if any(character in SENTENCE_BREAKS for character in gap):
                continue
            pair = {left.kind, right.kind}
            if CLOCK_TIME in pair:
                date = right if left.kind == CLOCK_TIME else left
                if date.kind == CLOCK_TIME or not _clockable(date):
                    continue
                if not _GAP_RE.match(gap):
                    continue
                merged[index : index + 2] = [_combine_clock(left, right, text)]
                break
            ranged = _range_rule(gap)
            if ranged is None:
                continue
            if not (left.kind in _DATE_KINDS | {WEEKDAY, RELATIVE_DAY} and right.kind in _DATE_KINDS | {WEEKDAY, RELATIVE_DAY}):
                continue
            merged[index : index + 2] = [_combine_range(left, right, gap, ranged, text)]
            break
        else:
            return sorted(merged, key=lambda item: item.char_start)


def find(text: str) -> list[Expression]:
    """Every temporal surface in ``text``, in source order, one span per phrase."""
    expressions = _resolve_overlaps(_scan(text))
    return _merge_phrases(_absorb_day_parts(expressions, text), text)


def _clock_precision(parts: dict) -> TimePrecision:
    if parts.get("second") is not None:
        return TimePrecision.SECOND
    if parts.get("minute") is not None:
        return TimePrecision.MINUTE
    return TimePrecision.HOUR


def resolve(expression: Expression, *, reference: Optional[datetime] = None) -> Resolution:
    """Turn one surface into a partially-known instant, or say exactly why it cannot."""
    notes = expression.notes

    if expression.kind == DATE_RANGE:
        return _unresolved(expression, INTERVAL)

    if expression.kind in (RELATIVE_WINDOW, RELATIVE_OFFSET):
        if expression.count is None or expression.unit is None:
            return _unresolved(expression, INTERVAL)
        if reference is None:
            return _unresolved(expression, NO_REFERENCE, needs_reference=True)
        delta = expression.count
        if expression.direction == PAST:
            delta = -delta
        elif expression.direction == PRESENT and expression.kind == RELATIVE_WINDOW:
            delta = 0
        value = _shift(reference, delta, expression.unit)
        if value is None:
            return _unresolved(expression, NO_REFERENCE, needs_reference=True)
        if expression.kind == RELATIVE_WINDOW:
            value = _start_of(value, expression.precision)
            qualifier = (
                TimeQualifier.ONGOING
                if expression.qualifier is TimeQualifier.ONGOING
                else TimeQualifier.APPROXIMATE
            )
            notes = notes + ("value is the start of the interval",)
        else:
            if expression.unit != "hour":
                value = value.replace(hour=0, minute=0, second=0, microsecond=0)
            qualifier = expression.qualifier
        return Resolution(
            value=value,
            precision=expression.precision,
            qualifier=qualifier,
            resolved=True,
            ambiguous=expression.ambiguous,
            notes=tuple(dict.fromkeys(notes)),
        )

    if expression.kind == RELATIVE_DAY:
        if expression.offset_days is None:
            return _unresolved(expression, ANCHORED)
        if reference is None:
            return _unresolved(expression, NO_REFERENCE, needs_reference=True)
        day = (reference + timedelta(days=expression.offset_days)).date()
        parts = {
            **expression.parts,
            "year": day.year,
            "month": day.month,
            "day": day.day,
        }
        if expression.has_clock:
            instant = _instant(parts)
            if instant is None:
                return _unresolved(expression, IMPOSSIBLE)
            return Resolution(
                value=instant,
                precision=_clock_precision(parts),
                qualifier=expression.qualifier,
                resolved=True,
                ambiguous=expression.ambiguous,
                notes=notes,
            )
        return Resolution(
            value=datetime(day.year, day.month, day.day),
            precision=TimePrecision.DAY,
            qualifier=(
                TimeQualifier.APPROXIMATE if expression.day_part else TimeQualifier.EXACT
            ),
            resolved=True,
            ambiguous=expression.ambiguous,
            notes=tuple(dict.fromkeys(notes + ("day only; no clock reading is stated",))),
        )

    if expression.kind == WEEKDAY:
        return _unresolved(expression, NO_ANCHOR)

    if expression.kind == CLOCK_TIME:
        return _unresolved(expression, NO_DATE)

    if expression.kind == MONTH_DAY:
        if expression.rule == DAY_ONLY:
            return _unresolved(expression, NO_MONTH)
        if expression.ambiguous:
            return _unresolved(expression, AMBIGUOUS_ABBREVIATION)
        return _unresolved(expression, NO_YEAR)

    if expression.kind == YEAR_ONLY:
        value = _instant({**expression.parts, "month": 1, "day": 1})
        if value is None:
            return _unresolved(expression, IMPOSSIBLE)
        return Resolution(
            value=value,
            precision=TimePrecision.YEAR,
            qualifier=TimeQualifier.APPROXIMATE,
            resolved=True,
            notes=tuple(dict.fromkeys(notes)),
        )

    if expression.kind == MONTH_YEAR:
        if expression.parts.get("month") is None:
            return _unresolved(expression, AMBIGUOUS_ABBREVIATION)
        value = _instant({**expression.parts, "day": 1})
        if value is None:
            return _unresolved(expression, IMPOSSIBLE)
        return Resolution(
            value=value,
            precision=TimePrecision.MONTH,
            qualifier=TimeQualifier.APPROXIMATE,
            resolved=True,
            ambiguous=expression.ambiguous,
            notes=tuple(dict.fromkeys(notes)),
        )

    value = _instant(expression.parts)
    if value is None:
        return _unresolved(expression, IMPOSSIBLE)
    precision = expression.precision
    if expression.has_clock:
        precision = finer(precision, _clock_precision(expression.parts))
    qualifier = expression.qualifier
    if qualifier is TimeQualifier.UNKNOWN:
        qualifier = TimeQualifier.EXACT
    if expression.day_part and not expression.has_clock:
        qualifier = TimeQualifier.APPROXIMATE
    return Resolution(
        value=value,
        precision=precision,
        qualifier=qualifier,
        resolved=True,
        ambiguous=expression.ambiguous,
        notes=tuple(dict.fromkeys(notes)),
    )


def timezone_cue(text: str) -> Optional[str]:
    """The declared zone name if the text itself names one, else None."""
    lowered = _nfc(text).lower()
    for cue in TIMEZONE_CUES:
        if _nfc(cue).lower() in lowered:
            return TIMEZONE_DECLARATION
    return None


def _crosses_sentence(text: str, start: int, end: int) -> bool:
    return any(character in SENTENCE_BREAKS for character in text[start:end])


def reporting_cue(
    text: str,
    *,
    char_start: int,
    char_end: int,
    window: int = REPORTING_CUE_WINDOW,
) -> Optional[str]:
    """The reporting verb attributing this expression, if one is in the same sentence."""
    before = text[max(0, char_start - window) : char_start]
    after = text[char_end : min(len(text), char_end + window)]
    for cue in REPORTING_CUES:
        needle = _nfc(cue).lower()
        index = after.find(needle)
        if index != -1 and not _crosses_sentence(after, 0, index):
            return cue
        index = before.rfind(needle)
        if index != -1 and not _crosses_sentence(before, index + len(needle), len(before)):
            return cue
    return None


__all__ = [
    "ABSOLUTE_DATE",
    "AFTERNOON",
    "AMBIGUOUS_ABBREVIATION",
    "AMBIGUOUS_MONTHS",
    "ANCHORED",
    "ANCHORED_RELATIVE_DAYS",
    "CLOCK_COLON",
    "CLOCK_MERIDIEM",
    "CLOCK_TAMIL",
    "CLOCK_TIME",
    "COUNT_WORDS",
    "DATE_CLOCK",
    "DATE_RANGE",
    "DAY_MONTH_ONLY",
    "DAY_MONTH_YEAR",
    "DAY_ONLY",
    "DAY_PARTS",
    "EARLY_MORNING",
    "EVENING",
    "Expression",
    "FUSED_DAY_PARTS",
    "FUTURE",
    "FUTURE_MARKERS",
    "IMPOSSIBLE",
    "INTERVAL",
    "ISO_DATE",
    "KINDS",
    "MAX_YEAR",
    "MIDNIGHT",
    "MIDDAY",
    "MIN_YEAR",
    "MONTH_DAY",
    "MONTH_DAY_ONLY",
    "MONTH_DAY_YEAR",
    "MONTH_YEAR",
    "MONTHS",
    "MORNING",
    "NO_ANCHOR",
    "NO_DATE",
    "NO_MERIDIEM",
    "NO_MONTH",
    "NO_REFERENCE",
    "NO_YEAR",
    "NOON",
    "NUMERIC_DATE",
    "NUMERIC_DAY_MONTH_ONLY",
    "PAST",
    "PAST_MARKERS",
    "PRECISION_ORDER",
    "PRESENT",
    "PRESENT_WORDS",
    "RELATIVE_CLOCK",
    "RELATIVE_DAY",
    "RELATIVE_DAY_RULE",
    "RELATIVE_DAY_TABLE",
    "RELATIVE_OFFSET",
    "RELATIVE_OFFSET_RULE",
    "RELATIVE_WINDOW",
    "RELATIVE_WINDOW_RULE",
    "RELATIVE_DAYS",
    "REPORTING_CUE_WINDOW",
    "REPORTING_CUES",
    "Resolution",
    "SENTENCE_BREAKS",
    "TIMEZONE_CUES",
    "TIMEZONE_DECLARATION",
    "UNIT_SURFACES",
    "WEEKDAY",
    "WEEKDAY_ABBREVIATIONS",
    "WEEKDAY_ENGLISH",
    "WEEKDAY_TAMIL",
    "WEEKDAYS",
    "YEAR_ONLY",
    "YEAR_ONLY_RULE",
    "coarser",
    "finer",
    "find",
    "reporting_cue",
    "resolve",
    "timezone_cue",
]
