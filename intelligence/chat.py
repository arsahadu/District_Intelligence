from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import REUSABLE_STATES, Checkpoint, CheckpointError
from intelligence.contract import (
    SCHEMA_VERSION,
    ContextType,
    Incident,
    RecordKind,
    ReviewState,
)
from intelligence.copilot import (
    Asker,
    Answer,
    AnswerState,
    AnswerStore,
    AskReport,
    NOT_A_RECORD_QUESTION,
    QuestionIntent,
    StatementKind,
    ask_copilot,
    clean,
    digit_runs,
    norm,
    place_vocabulary,
    script_of,
)
from intelligence.fallback import Chain
from intelligence.llm import LLMRequest, json_schema_for
from intelligence.linking import EventLink, LinkStore
from intelligence.models.base import Confidence, StrictModel
from intelligence.models.enums import ExtractionMethod, Modality
from intelligence.recurrence import IssueFamily, IssueStore, searchable_places
from intelligence.trends import Trend, TrendStore

CHAT_PROMPT_VERSION = "chat-1"
ROUTE_PROMPT_VERSION = "route-1"
GENERAL_PROMPT_VERSION = "general-5"
VALUE_PROMPT_VERSION = "value-5"
LOOKUP_PROVIDER = "intelligence.chat"
LOOKUP_MODEL = "none"
NO_MODEL_NOTE = (
    "no model was asked for this answer: every figure is a field value copied from a record read as a "
    "forecast or as context, not as an incident"
)
COMPOSED_NOTE = (
    "the sentences below are wording a model put together from the field values of the records named in "
    "the citations; the values, the places and the dates are the records' own and none of them was a model's "
    "guess"
)
GENERAL_NOTE = (
    "answered as general background by the model named below, not from this district's records: it cites no "
    "record, quotes no source, and says nothing about what happened anywhere on any day"
)
NOTHING_READ = "the records this chat was pointed at hold no reading to answer from"
NO_MODEL_ASKED = "nothing was asked of a model for this reply"

MAX_QUESTION_CHARS = 400
MAX_MODEL_CALLS_PER_REQUEST = 5
MAX_REQUESTS_PER_SESSION = 20
SESSION_WINDOW_SECONDS = 600
MAX_RECORDS_SHOWN = 8
MAX_FACTS_SHOWN = 12
MAX_COMPOSED_LINES = MAX_RECORDS_SHOWN + 1
MAX_FACTS_PER_SENTENCE = 3
MAX_GENERAL_LINES = 24
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 1200
MAX_TURN_CHARS = 320
MAX_NAMES_PRINTED = 12
DEFAULT_SESSION = "collector"

UNKNOWN_SOURCE_TYPE = "unknown"

PRICE_FACT_NAMES = ("commodity", "market", "unit", "min_price", "max_price", "quantity")
FORECAST_FACT_NAMES = (
    "forecast", "warning", "max_temp_c", "min_temp_c", "rainfall_mm", "humidity_pct", "wind_kmph",
)
INTERNAL_FACT_NAMES = PRICE_FACT_NAMES + FORECAST_FACT_NAMES


class ChatTopic(str, Enum):
    FORECAST = "forecast"
    MARKET = "market"
    RECORDS = "records"
    GENERAL = "general"


class ChatPath(str, Enum):
    RECORD_VALUES = "record_values"
    RECORD_SEARCH = "record_search"
    GENERAL = "general"


CONTEXT_TYPE_BY_TOPIC = {
    ChatTopic.FORECAST: ContextType.WEATHER_FORECAST,
    ChatTopic.MARKET: ContextType.MARKET_PRICE,
}
NOUN_BY_TOPIC = {ChatTopic.FORECAST: "forecast", ChatTopic.MARKET: "price"}

FORECAST_WORDS = (
    "forecast",
    "weather",
    "temperature",
    "humidity",
    "rain",
    "rains",
    "wind",
    "winds",
    "heat",
    "முன்னறிவிப்பு",
    "வானிலை",
    "வெப்பநிலை",
    "வெப்பம்",
    "ஈரப்பதம்",
    "மழை",
    "காற்று",
)
MARKET_WORDS = (
    "price",
    "prices",
    "pricing",
    "rate",
    "rates",
    "mandi",
    "santhai",
    "uzhavar",
    "per kg",
    "rupee",
    "rupees",
    "விலை",
    "சந்தை",
    "உழவர்",
)

# Matcher only: these words ask for more of what was already answered. They route a follow-up back to the
# records; they never supply an answer, and a question that names its own subject is answered on its own.
ELABORATION_WORDS = (
    "detail",
    "details",
    "detailed",
    "elaborate",
    "expand",
    "more",
    "further",
    "full",
    "fully",
    "complete",
    "everything",
    "again",
    "deeper",
    "specifics",
    "in detail",
    "விரிவாக",
    "விவரமாக",
    "விவரம்",
    "பூர்வமாக",
    "முழுமையாக",
    "மேலும்",
    "தெளிவாக",
    "மீண்டும்",
)

# Matcher only: a Tamil commodity word is folded onto the wording the record itself carries, so a Tamil
# question can name a price the records hold in English. Nothing here decides what the district saw.
VALUE_ALIASES = {
    "தக்காளி": "tomato",
    "வெங்காயம்": "onion",
    "உருளைக்கிழங்கு": "potato",
    "வாழை": "banana",
    "நெல்": "paddy",
    "மஞ்சள்": "turmeric",
    "சர்க்கரை": "sugar",
    "எண்ணெய்": "oil",
    "பருத்தி": "cotton",
    "காய்கறி": "vegetable",
}


def fold(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


# Both sides of every alias, folded. A question naming a commodity no record holds is answered as a
# refusal listing these, never as the closest price that happens to be stored.
VALUE_WORDS = frozenset(fold(item) for pair in VALUE_ALIASES.items() for item in pair)


def said_in(question: str, words: Iterable[str]) -> list[str]:
    """Which of these words the question uses. A Latin-script word has to stand as its own word, so 'rate'
    is not found inside 'moderate' and 'wind' not inside 'window'; a word in another script may sit inside
    an inflected form, and a phrase is matched as written."""
    text = fold(question)
    tokens = set(re.split(r"[^0-9a-z]+", text))
    found = []
    for word in words:
        needle = fold(word)
        if needle.isascii() and " " not in needle:
            matched = needle in tokens
        else:
            matched = needle in text
        if matched:
            found.append(needle)
    return sorted(set(found))


def topic_of(question: str) -> ChatTopic:
    if said_in(question, MARKET_WORDS):
        return ChatTopic.MARKET
    if said_in(question, FORECAST_WORDS):
        return ChatTopic.FORECAST
    return ChatTopic.RECORDS


def eligible_values(incidents: Sequence[Incident], topic: ChatTopic) -> list[Incident]:
    wanted = CONTEXT_TYPE_BY_TOPIC[topic]
    kept = [
        item
        for item in incidents
        if item.record_kind in (RecordKind.FORECAST, RecordKind.CONTEXT)
        and item.context_type is wanted
        and not item.review_required
        and any(fact.review is ReviewState.ACCEPTED and fact.value for fact in item.context_facts)
    ]
    return sorted(kept, key=_newest_first, reverse=True)


def _newest_first(incident: Incident) -> tuple[str, str]:
    # ISO text, not the datetime: a saved corpus mixes records that carry an offset with records that
    # do not, and comparing the two raises instead of answering.
    when = incident.event_time
    return ("" if when is None else when.isoformat(), incident.incident_id)


def named_places(question: str, vocabulary: Sequence[str]) -> list[str]:
    """Which of the places these records carry, in the wording the records themselves use, the question
    repeats. A place written in a script the records never used matches nothing and is reported as such."""
    text = fold(question)
    words = set(text.split())
    return sorted({name for name in vocabulary if name and (name in text or name in words)})


def holds_places(incident: Incident, wanted: Sequence[str]) -> bool:
    names = {fold(name) for name in searchable_places(incident)}
    return any(fold(item) in name or name == fold(item) for item in wanted for name in names)


RELATIVE_DAYS = {
    "today": 0,
    "இன்று": 0,
    "yesterday": 1,
    "நேற்று": 1,
    "நேற்றை": 1,
    "tomorrow": -1,
    "நாளை": -1,
}
WEEK_WORDS = {
    "this week": 0,
    "current week": 0,
    "இந்த வாரம்": 0,
    "last week": -1,
    "previous week": -1,
    "கடந்த வாரம்": -1,
}
MONTH_WORDS = {
    "this month": 0,
    "current month": 0,
    "இந்த மாதம்": 0,
    "last month": -1,
    "previous month": -1,
    "கடந்த மாதம்": -1,
}
MONTH_NUMBERS = {
    name: number
    for name, number in (
        ("jan", 1), ("january", 1), ("feb", 2), ("february", 2), ("mar", 3), ("march", 3),
        ("apr", 4), ("april", 4), ("may", 5), ("jun", 6), ("june", 6), ("jul", 7), ("july", 7),
        ("aug", 8), ("august", 8), ("sep", 9), ("sept", 9), ("september", 9), ("oct", 10),
        ("october", 10), ("nov", 11), ("november", 11), ("dec", 12), ("december", 12),
    )
}
ISO_DAY = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})[\s/-]([a-z]{3,9})(?:[\s,/-]+(\d{4}))?(?!\d)")
DAY_MONTH_YEAR = re.compile(r"(?<!\d)(\d{1,2})[\s/-](\d{1,2})[\s/-](\d{4})(?!\d)")
DAYS_AGO = re.compile(r"(?<!\d)(\d{1,2})\s+days?\s+ago")
LAST_DAYS = re.compile(r"(?:last|past|recent)\s+(\d{1,2})\s+days?")


def _month_floor(year: int, month: int) -> date:
    return date(year, month, 1)


def _month_end(year: int, month: int) -> date:
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return following - timedelta(days=1)


def window_of(question: str, today: date) -> tuple[Optional[date], Optional[date], str]:
    """The days the question names, read from its own wording: no model is asked, and a question naming no
    period gets no window rather than a guessed one."""
    text = fold(question)
    stamp = ISO_DAY.search(text)
    if stamp:
        try:
            day = date(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)))
        except ValueError:
            return None, None, ""
        return day, day, day.isoformat()
    numbered = DAY_MONTH_YEAR.search(text)
    if numbered:
        try:
            day = date(int(numbered.group(3)), int(numbered.group(2)), int(numbered.group(1)))
        except ValueError:
            return None, None, ""
        return day, day, day.strftime("%d %b %Y")
    ago = DAYS_AGO.search(text)
    if ago:
        day = today - timedelta(days=int(ago.group(1)))
        return day, day, ago.group(0)
    span = LAST_DAYS.search(text)
    if span:
        return today - timedelta(days=int(span.group(1)) - 1), today, span.group(0)
    named = said_in(question, RELATIVE_DAYS)
    if named:
        day = today - timedelta(days=RELATIVE_DAYS[named[0]])
        return day, day, named[0]
    for month_day in MONTH_DAY.finditer(text):
        month = MONTH_NUMBERS.get(month_day.group(2))
        if month is None:
            continue
        year = int(month_day.group(3) or (today.year + (int(month_day.group(1)) > today.month + 6)))
        try:
            day = date(year, month, int(month_day.group(1)))
        except ValueError:
            continue
        return day, day, day.strftime("%d %b %Y")
    named = said_in(question, WEEK_WORDS)
    if named:
        monday = today - timedelta(days=today.weekday() + 7 * WEEK_WORDS[named[0]])
        return monday, monday + timedelta(days=6), named[0]
    named = said_in(question, MONTH_WORDS)
    if named:
        offset = MONTH_WORDS[named[0]]
        year, month = today.year, today.month + offset
        while month < 1:
            month, year = month + 12, year - 1
        while month > 12:
            month, year = month - 12, year + 1
        return _month_floor(year, month), _month_end(year, month), named[0]
    return None, None, ""


def value_words(value: str) -> set[str]:
    words = {value}
    for alias, english in VALUE_ALIASES.items():
        if fold(english) == value or fold(english) in value:
            words.add(fold(alias))
    return words


def value_fields(incidents: Sequence[Incident]) -> dict[str, dict[str, set[str]]]:
    """One field to one word a question may use for it to the records holding that word. Identifier
    fields are left out: a collector asks about a market, never about a market_id."""
    index: dict[str, dict[str, set[str]]] = {}
    for incident in incidents:
        for fact in accepted_facts(incident):
            if fact.field.removeprefix("data.").endswith("_id"):
                continue
            words = index.setdefault(fact.field, {})
            for word in value_words(fold(fact.value)):
                words.setdefault(word, set()).add(incident.incident_id)
    return index


def named_fields(
    question: str, index: Mapping[str, Mapping[str, set[str]]]
) -> list[tuple[str, list[str], set[str]]]:
    narrowed: list[tuple[str, list[str], set[str]]] = []
    for field, words in sorted(index.items()):
        hit = said_in(question, words)
        if not hit:
            continue
        ids: set[str] = set()
        for word in hit:
            ids |= words[word]
        narrowed.append((field, hit, ids))
    return narrowed


def named_label(narrowed: Sequence[tuple[str, Sequence[str], set[str]]]) -> str:
    return "; ".join(
        f"{field.removeprefix('data.')} {', '.join(words)}" for field, words, _ in narrowed
    )


def held_names(incidents: Sequence[Incident]) -> list[str]:
    """The values these records do hold, as the field and the value it carries, for a refusal that has to
    name both sides. Identifier fields are left out: they are not what a collector asked about."""
    seen: list[str] = []
    for incident in incidents:
        for fact in accepted_facts(incident):
            if fact.field.removeprefix("data.").endswith("_id"):
                continue
            label = fact_line(fact)
            if label not in seen:
                seen.append(label)
    return seen[:MAX_NAMES_PRINTED]


def matching_values(
    incidents: Sequence[Incident], question: str
) -> tuple[list[Incident], list[str]]:
    """The records the question actually names. Every facet it names has to hold in the same record, so a
    question asking one market's tomato price cannot be answered with every market's tomato and one
    market's every commodity."""
    kept = list(incidents)
    vocabulary = place_vocabulary(kept)
    places = named_places(question, vocabulary)
    notes: list[str] = []
    if places:
        inside = [item for item in kept if holds_places(item, places)]
        if not inside:
            return [], [
                "no record here names "
                + ", ".join(repr(item) for item in places[:MAX_NAMES_PRINTED])
                + "; these records name "
                + (", ".join(vocabulary[:MAX_NAMES_PRINTED]) or "no place at all")
            ]
        kept = inside
        notes.append("the place the question names: " + ", ".join(places))
    elif vocabulary:
        notes.append(
            "the question named no place these records carry, so records from every place were read"
        )

    index = value_fields(kept)
    narrowed = named_fields(question, index)
    if not narrowed:
        held = {word for words in index.values() for word in words}
        asked = [word for word in said_in(question, VALUE_WORDS) if word not in held]
        if asked:
            return [], [
                "no record here holds "
                + ", ".join(repr(item) for item in asked[:MAX_NAMES_PRINTED])
                + ", and these records hold "
                + ("; ".join(held_names(kept)) or "no field value of their own")
            ]
        return kept, notes

    wanted = [item for item in kept if all(item.incident_id in ids for _, _, ids in narrowed)]
    if not wanted:
        return [], [
            "no single record here carries every field value the question names ("
            + named_label(narrowed)
            + "): "
            + "; ".join(
                f"{', '.join(repr(word) for word in words)} names {len(ids)} of the {len(kept)} record(s)"
                + f" still in scope for {field.removeprefix('data.')}"
                for field, words, ids in narrowed
            )
        ]
    kept = wanted
    notes.append("the field values the question names: " + named_label(narrowed))
    return kept, notes


def accepted_facts(incident: Incident) -> list[Any]:
    return [
        fact
        for fact in incident.context_facts
        if fact.review is ReviewState.ACCEPTED and clean(fact.value)
    ]


def fact_line(fact: Any) -> str:
    return f"{fact.field.removeprefix('data.')} {fact.value}"


def number(value: Any) -> str:
    text = str(value).strip()
    try:
        as_float = float(text)
    except ValueError:
        return text
    return str(int(as_float)) if as_float.is_integer() else f"{as_float:g}"


def quotable_facts(incident: Incident) -> dict[str, Any]:
    """The record's own accepted values that a quotation in the record backs, keyed by field name without
    its data. prefix. A value no evidence backs is left out rather than printed on the record's authority."""
    return {
        fact.field.removeprefix("data."): fact
        for fact in accepted_facts(incident)
        if fact.evidence_ids and not fact.field.removeprefix("data.").endswith("_id")
    }


def record_day(incident: Incident) -> str:
    return incident.event_time.strftime("%d %b %Y") if incident.event_time else "no date the record gave"


def record_place(incident: Incident) -> str:
    """Where the record says it is, in the record's own words: the location it carries before any place a
    search matched on, and of the names it only matched on the shortest, which is why a forecast reads as
    its city and not as its state."""
    for location in incident.locations:
        if clean(location.text):
            return clean(location.text)
    names = sorted(
        (name for name in searchable_places(incident) if name), key=lambda name: (len(name), name)
    )
    if names:
        return names[0]
    return clean(incident.title) or incident.provenance.record_id


def price_line(incident: Incident) -> tuple[str, list[str]]:
    facts = quotable_facts(incident)
    used = [fact for name, fact in facts.items() if name in PRICE_FACT_NAMES]
    if not used:
        return "", []
    commodity = clean(str(facts["commodity"].value)) if "commodity" in facts else ""
    market = clean(str(facts["market"].value)) if "market" in facts else ""
    unit = clean(str(facts["unit"].value)) if "unit" in facts else ""
    low = number(facts["min_price"].value) if "min_price" in facts else ""
    high = number(facts["max_price"].value) if "max_price" in facts else ""
    subject = " at ".join(item for item in (commodity, market) if item) or (
        clean(incident.title) or incident.provenance.record_id
    )
    if low and high:
        money = f"₹{low}–₹{high} per {unit or 'unit'}"
    elif low or high:
        money = f"₹{low or high} per {unit or 'unit'}"
    elif unit:
        money = f"priced per {unit}"
    else:
        money = ""
    quantity = (
        f", {number(facts['quantity'].value)} {unit} reported"
        if "quantity" in facts and unit
        else (f", {number(facts['quantity'].value)} reported" if "quantity" in facts else "")
    )
    line = f"{subject} ({record_day(incident)}): {money}{quantity}".replace(": ,", ":").strip()
    return line, sorted({eid for fact in used for eid in fact.evidence_ids})


def forecast_line(incident: Incident) -> tuple[str, list[str]]:
    facts = quotable_facts(incident)
    used = [fact for name, fact in facts.items() if name in FORECAST_FACT_NAMES]
    if not used:
        return "", []
    parts = [
        clean(str(facts[name].value)).rstrip(".") + "."
        for name in ("forecast", "warning")
        if name in facts and clean(str(facts[name].value))
    ]
    temps = [
        f"{label} {number(facts[name].value)}°C"
        for label, name in (("Max", "max_temp_c"), ("min", "min_temp_c"))
        if name in facts
    ]
    parts += [", ".join(temps) + "."] if temps else []
    if not parts:
        parts = [fact_line(fact) for fact in used]
    return (
        f"{record_place(incident)} ({record_day(incident)}): " + " ".join(parts),
        sorted({eid for fact in used for eid in fact.evidence_ids}),
    )


def plain_line(incident: Incident) -> tuple[str, list[str]]:
    facts = [
        fact
        for name, fact in sorted(quotable_facts(incident).items())
        if name not in INTERNAL_FACT_NAMES
    ][:MAX_FACTS_SHOWN]
    if not facts:
        return "", []
    head = clean(incident.title) or incident.provenance.record_id
    return (
        f"{head} ({record_day(incident)}): " + "; ".join(fact_line(fact) for fact in facts),
        sorted({eid for fact in facts for eid in fact.evidence_ids}),
    )

LINE_BY_TOPIC = {ChatTopic.MARKET: price_line, ChatTopic.FORECAST: forecast_line}


def value_line(incident: Incident, topic: ChatTopic) -> tuple[str, list[str]]:
    line, ids = LINE_BY_TOPIC.get(topic, plain_line)(incident)
    return (line, ids) if line else plain_line(incident)


def price_range(incidents: Sequence[Incident]) -> str:
    """The spread across the records printed, from the lowest min to the highest max they each carry."""
    facts = [fact for item in incidents for fact in quotable_facts(item).values()]
    lows = [float(fact.value) for fact in facts if fact.field == "data.min_price"]
    highs = [float(fact.value) for fact in facts if fact.field == "data.max_price"]
    units = {
        clean(str(fact.value)) for fact in facts if fact.field == "data.unit" and clean(str(fact.value))
    }
    commodities = {
        clean(str(fact.value)) for fact in facts if fact.field == "data.commodity" and clean(str(fact.value))
    }
    markets = {
        clean(str(fact.value)) for fact in facts if fact.field == "data.market" and clean(str(fact.value))
    }
    if not lows and not highs:
        return ""
    money = f"₹{number(min(lows))}–₹{number(max(highs))}" if lows and highs else ""
    unit = f" per {units.pop()}" if len(units) == 1 else ""
    subject = ", ".join(sorted(commodities)) if len(commodities) <= 3 else f"{len(commodities)} commodities"
    where = (
        ", ".join(sorted(markets))
        if len(markets) <= 3
        else f"{len(markets)} markets"
    )
    return f"{subject} at {where}: {money}{unit}" if money else f"{subject} at {where}"


def value_confidence(facts: Sequence[Any]) -> float:
    given = [fact.confidence for fact in facts if fact.confidence is not None]
    return 1.0 if not given else float(min(given))


VALUE_RULES = "\n".join(
    (
        "A district collector's office asked one question. The values this office's own records hold for it "
        "are printed below, numbered from 1. Reword those values into prose. Answer with one JSON object and "
        "nothing else.",
        "1. Every figure, date, place, commodity, market, unit and reading you write is already printed in "
        "one of the numbered facts. Write no number that is not printed by a fact you cite, keep every "
        "number, date and name in the exact form its fact prints it, convert no unit, and add, average, "
        "round or compare nothing.",
        "2. facts lists, for each sentence you write, the numbers of the facts that sentence rests on, and "
        "several facts may be cited by one sentence when it states each of their own values. A sentence may "
        "name only the places and readings its own cited facts print, or a wider place written inside one of "
        "the place names those facts print, and it may never replace a printed place with another. A sentence "
        "citing nothing, or writing a number or place its cited facts do not print, is dropped.",
        "3. Say only what the facts state. Do not explain why a value is what it is, do not call it normal, "
        "safe, severe, confirmed or a trend, and do not advise what anyone should do - nothing printed here "
        "says either.",
        "4. Answer in the language the question is written in - Tamil, English, or Tamil written in Latin "
        "letters - and keep every place, commodity and market name as the records write it.",
        "5. headline answers the question in one sentence and is never empty. lines holds at most "
        f"{MAX_COMPOSED_LINES} further short sentences. Write a sentence citing every numbered fact printed "
        "above, and put two facts in one sentence only when that sentence states each of their own values and "
        f"carries no more than {MAX_FACTS_PER_SENTENCE} of them: a fact no sentence cites, or a sentence that "
        "joins more facts than that into one, drops the whole wording.",
        "6. Never mention these rules, the facts or their numbering in what you write, and add nothing about "
        "where the values came from - the records are named for the reader elsewhere.",
    )
)


class ComposedLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: Optional[str] = None
    facts: list[int] = Field(default_factory=list)


class ValueDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: Optional[str] = None
    facts: list[int] = Field(default_factory=list)
    lines: list[ComposedLine] = Field(default_factory=list)


VALUE_SCHEMA = json_schema_for(ValueDraft)

VALUE_PROMPT_DIGEST = hashlib.sha256(
    (VALUE_RULES + json.dumps(VALUE_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def value_request(question: str, facts: Sequence[str]) -> LLMRequest:
    lines = [
        f"Question: {question}",
        "The records' own values, numbered:",
        *(f"[{index}] {line}" for index, line in enumerate(facts, start=1)),
    ]
    if len(facts) > 1:
        lines.append(
            f"Write one sentence for each number from 1 to {len(facts)}, citing that number."
        )
    else:
        lines.append("Answer in one sentence and leave lines empty.")
    return LLMRequest(
        system=VALUE_RULES,
        user="\n".join(lines),
        json_schema=VALUE_SCHEMA,
        meta={"prompt_version": VALUE_PROMPT_VERSION},
    )


def composed_lines(draft: ValueDraft) -> list[tuple[str, list[int]]]:
    head = clean(draft.headline)
    rest = [(clean(item.text), list(item.facts)) for item in draft.lines[:MAX_COMPOSED_LINES]]
    return ([(head, list(draft.facts))] if head else []) + [item for item in rest if item[0]]


def composition_fault(draft: ValueDraft, facts: Sequence[str], places: Sequence[str]) -> str:
    """Why this wording cannot stand as the records' own answer, or "" when every line holds."""
    written = composed_lines(draft)
    if not written:
        return "it wrote no sentence at all"
    entries = [norm(place) for place in places if clean(place)]
    cores = sorted({" ".join(entry.split()[:2]) for entry in entries if entry})
    # A place this recordset writes beside another is the same record's own geography, so a sentence may widen
    # to it; a place written nowhere beside a printed one is a different place, and swapping one for the other
    # is a claim the record never makes.
    related: dict[str, set[str]] = {}
    for entry in entries:
        inside = [core for core in cores if core in entry]
        for core in inside:
            related.setdefault(core, set()).update(inside)
    used: set[int] = set()
    seen: set[str] = set()
    for text, cited in written:
        if norm(text) in seen:
            return f"it wrote {text[:70]!r} twice, which tells the reader nothing the first time did not"
        seen.add(norm(text))
        outside = sorted({item for item in cited if not 1 <= item <= len(facts)})
        if outside:
            return f"it cited fact {outside[0]}, which is not one of the {len(facts)} printed"
        if not cited:
            return f"the sentence {text[:70]!r} rests on no fact printed for it"
        source = " ".join(facts[item - 1] for item in sorted(set(cited)))
        invented = sorted(set(digit_runs(text)) - set(digit_runs(source)))
        if invented:
            return f"it wrote {', '.join(invented[:3])}, a number no fact it cites prints"
        said = norm(source)
        printed = [core for core in cores if core in said]
        if printed:
            moved = sorted(
                {
                    core
                    for core in cores
                    if core in norm(text)
                    and core not in said
                    and not any(core in related.get(other, ()) for other in printed)
                }
            )
            if moved:
                return f"it named {moved[0]}, a place no fact it cites prints"
        if len(set(cited)) > MAX_FACTS_PER_SENTENCE:
            # Every printed value still has to be said, so a model under that demand can join them all into one
            # sentence: grounded, and unreadable as an answer.
            return (
                f"it joined {len(set(cited))} of the {len(facts)} printed facts into one sentence instead of "
                "writing each its own"
            )
        for item in cited:
            if 1 <= item <= len(facts):
                used.add(item)
    unwritten = sorted(set(range(1, len(facts) + 1)) - used)
    if unwritten:
        return f"it left fact {unwritten[0]}, of the {len(facts)} printed, unwritten"
    return ""


def history_lines(history: Sequence[tuple[str, str]]) -> list[str]:
    """The conversation so far, oldest first, printed above a question so a follow-up has a subject."""
    lines: list[str] = []
    for question, answer in history:
        asked = clean(question)
        if not asked:
            continue
        lines.append(f"Asked: {asked}")
        said = clean(answer)
        if said:
            lines.append(f"Answered: {said}")
    return lines


ROUTE_RULES = "\n".join(
    (
        "A district collector's office assistant is asked one question. Decide only where its answer comes "
        "from, and answer with one JSON object and nothing else.",
        "1. needs_records is true when only this district's filed records can answer it: what happened, is "
        "happening or will happen here, what a value here is, how many there were, which place, department, "
        "incident type, severity or priority is involved, or what the records, market prices, weather "
        "forecasts, recurring issues or briefings here say.",
        "2. needs_records is false when a well-informed reader with no record in front of them could answer "
        "it: a greeting or courtesy, what this assistant is or can do, an explanation of a term, scheme, "
        "institution or process, a fact of geography, history, science, law or sport, arithmetic or "
        "reasoning, writing, summarising, translating, or code in any language.",
        "3. general is true when an explanation, comparison, example, translation, programme or advice "
        "belongs in the answer, whether or not records are needed as well.",
        "4. Lines printed above the question, when there are any, are this conversation so far. When the "
        "question only asks for more of an earlier answer, for a change to it, or for the same in another "
        "language, decide for the subject of that earlier question.",
        "5. When a question could be read either way, set needs_records true: a search that finds nothing "
        "costs one more question, while an answer from memory that should have been read from records cannot "
        "be taken back.",
        "6. confidence is how sure you are of these two flags, from 0.0 to 1.0.",
    )
)


class RouteDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    needs_records: Optional[bool] = None
    general: Optional[bool] = None
    confidence: Optional[float] = None


ROUTE_SCHEMA = json_schema_for(RouteDraft)

ROUTE_PROMPT_DIGEST = hashlib.sha256(
    (ROUTE_RULES + json.dumps(ROUTE_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def _question_prompt(question: str, history: Sequence[tuple[str, str]]) -> str:
    lines = history_lines(history)
    if lines:
        lines = ["Conversation so far, oldest first:", *lines, ""]
    lines.append(f"Question: {question}")
    return "\n".join(lines)


def route_request(question: str, *, history: Sequence[tuple[str, str]] = ()) -> LLMRequest:
    return LLMRequest(
        system=ROUTE_RULES,
        user=_question_prompt(question, history),
        json_schema=ROUTE_SCHEMA,
        meta={"prompt_version": ROUTE_PROMPT_VERSION},
    )


GENERAL_RULES = "\n".join(
    (
        "A district collector's office asks one question of the assistant that reads this district's filed "
        "records and answers the rest from general knowledge. No record of this district is in front of you "
        "and nothing you write here comes from one. Answer with one JSON object and nothing else.",
        "1. answerable is true when a reader could answer without this district's records: a greeting or "
        "courtesy, what you are or can do, an explanation of a term, scheme, institution or process, a fact "
        "of geography, history, science, law, sport or world affairs, arithmetic or reasoning, writing, "
        "summarising, translating, or code in any language.",
        "2. answerable is false when the question asks what happened here, what a value here is, how many "
        "there were, or what the records, market prices, weather forecasts, recurring issues or briefings of "
        "this district say. Then leave headline and lines empty and write in needs_records what the records "
        "would have to hold before the question could be answered.",
        f"3. headline answers in one sentence and lines carries the rest, at most {MAX_GENERAL_LINES - 1} "
        "further lines, each standing on its own. For code give one line of code per entry, keep its "
        "indentation in spaces and write no markdown fence. For an explanation, a translation or a worked "
        "answer use as few lines as carry it, and write the whole of it: a reader sees nothing else.",
        "4. Give a figure when the question asks for one a reader anywhere could give, such as a count of "
        "administrative divisions, a physical constant, a year or the syntax of a language. Give no price, "
        "temperature, rainfall amount, casualty count or dated happening of this district.",
        "5. district_claims is true only when something you wrote says what happened, is happening or will "
        "happen in this district, or gives a value measured here. What holds for any reader is not a district "
        "claim, and neither is a fact of the state or the country that does not come from these records.",
        "6. Answer in the language the question is written in - Tamil, English, or Tamil written in Latin "
        "letters - and keep a technical term in the wording records of India carry it in. Code, mathematics "
        "and a language being translated into keep their own syntax whatever the question's language.",
        "7. Plain text only: no emoji, no decoration, no markup, and no sentence written twice.",
        "8. When your own knowledge may have moved on since you learned it, still answer what you know and "
        "add one line saying which part of it may have changed.",
        "9. When you cannot give the fact a question asks for, say so in one plain sentence and then give "
        "what does hold for any reader. Keep answerable true and answer below 0.5.",
        "10. confidence is how sure you are of what you wrote, from 0.0 to 1.0; answer below 0.5 when "
        "guessing.",
        "11. When the question is what or who this assistant is, answer as this office's records assistant - "
        "it reads this district's filed records and answers the rest from general knowledge - and claim no "
        "company, product or app name as yourself, and no version or training date you were not told.",
        "12. Lines printed above this question, when there are any, are this conversation so far: an earlier "
        "question and the answer given to it. A question may then ask only for more of that answer, for a "
        "change to code already given, for the same in another language, or for what to do about it - answer "
        "about that subject then, carry the earlier answer forward, and say what you are answering.",
    )
)


class GeneralDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answerable: Optional[bool] = None
    district_claims: Optional[bool] = None
    headline: Optional[str] = None
    lines: list[str] = Field(default_factory=list)
    needs_records: Optional[str] = None
    confidence: Optional[float] = None


GENERAL_SCHEMA = json_schema_for(GeneralDraft)

GENERAL_PROMPT_DIGEST = hashlib.sha256(
    (GENERAL_RULES + json.dumps(GENERAL_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]

EMOJI_IN_TEXT = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")


def general_request(question: str, *, history: Sequence[tuple[str, str]] = ()) -> LLMRequest:
    return LLMRequest(
        system=GENERAL_RULES,
        user=_question_prompt(question, history),
        json_schema=GENERAL_SCHEMA,
        meta={"prompt_version": GENERAL_PROMPT_VERSION},
    )


def _keep_indent(value: Any) -> str:
    """A background answer keeps the spaces that indent code: only the newlines wrapping it go."""
    return str(value or "").strip("\r\n").rstrip()


def general_refusal(draft: GeneralDraft, *, places: Sequence[str], value_shaped: bool) -> str:
    """Why this background answer cannot be shown to a collector, or "" when it can."""
    if draft.answerable is not True:
        wanted = clean(draft.needs_records)
        return "the model says the question is answered from records, not from background" + (
            f": {wanted}" if wanted else ""
        )
    written = [clean(draft.headline), *(clean(item) for item in draft.lines)]
    kept = [item for item in written if item]
    if not kept:
        return "the model said the question is answerable and then wrote nothing"
    if len(kept) > MAX_GENERAL_LINES:
        return (
            f"it wrote {len(kept)} lines, more than the {MAX_GENERAL_LINES} an answer here holds, so a "
            "collector would be handed an essay instead of an answer"
        )
    text = " ".join(kept)
    mark = EMOJI_IN_TEXT.search(text)
    if mark is not None:
        return f"it wrote {mark.group(0)!r}, and this office's answers are plain text in any language"
    answer = norm(text)
    hit = sorted({item for item in places if item and item in answer})
    if hit:
        return (
            f"it names {', '.join(hit[:3])}, a place these records hold, so it would read as a finding about "
            "this district rather than as background"
        )
    if value_shaped:
        numbers = sorted(set(digit_runs(text)))
        if numbers:
            return (
                f"it carries the number(s) {', '.join(numbers[:3])} for a question about a value only a "
                "record can give"
            )
    if draft.district_claims is True:
        return (
            "the model says its own answer claims something about this district, so it is background no "
            "record supports"
        )
    return ""


class ChatCitation(StrictModel):
    record_id: str
    incident_id: str
    source_id: str
    source_type: str
    source_url: Optional[str] = None
    field: str
    quote: Optional[str] = None
    evidence_id: str
    record_kind: RecordKind = RecordKind.UNRESOLVED
    modality: Modality = Modality.TEXT
    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    when: Optional[datetime] = None


class ChatStatement(StrictModel):
    text: str
    kind: StatementKind
    evidence_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    review: ReviewState = ReviewState.UNRESOLVED
    confidence: Confidence = 0.0

    @property
    def backing(self) -> str:
        return backing_of(self.kind)

    @model_validator(mode="after")
    def _a_statement_backs_what_it_says(self) -> "ChatStatement":
        if self.kind is StatementKind.SOURCE_FACT and not self.evidence_ids:
            raise ValueError(
                "a source fact names the record it copies or it is a claim about the district with no "
                "record behind it"
            )
        if self.review is ReviewState.ACCEPTED and self.kind not in (
            StatementKind.SOURCE_FACT,
            StatementKind.CALCULATED,
        ):
            raise ValueError(f"a {self.kind.value} is offered for a person to weigh, never accepted")
        return self


def backing_of(kind: StatementKind) -> str:
    return "evidence" if kind in (StatementKind.SOURCE_FACT, StatementKind.CALCULATED) else "explanation"


def reply_id_for(session_id: str, question: str, carried_from: Optional[str]) -> str:
    seed = "\n".join((str(session_id), norm(question), clean(carried_from)))
    return "CHAT-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class ChatReply(StrictModel):
    model_config = ConfigDict(protected_namespaces=())

    schema_version: str = SCHEMA_VERSION
    reply_id: str
    session_id: str
    question: str
    script: str
    topic: ChatTopic
    path: ChatPath
    state: AnswerState
    headline: Optional[str] = None
    understood: Optional[str] = None
    statements: list[ChatStatement] = Field(default_factory=list)
    citations: list[ChatCitation] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    record_kinds: list[RecordKind] = Field(default_factory=list)
    events: int = 0
    records: int = 0
    sources: int = 0
    limitations: list[str] = Field(default_factory=list)
    standing: list[str] = Field(default_factory=list)
    missing: Optional[str] = None
    carried_from: Optional[str] = None
    model_calls: int = 0
    provider: str
    model: str
    prompt_version: str
    prompt_digest: Optional[str] = None
    asked_at: datetime

    @model_validator(mode="after")
    def _a_reply_claims_only_what_it_carries(self) -> "ChatReply":
        if self.reply_id != reply_id_for(self.session_id, self.question, self.carried_from):
            raise ValueError("reply id is not the one its session, question and carry-over derive")
        if self.records != len(self.record_ids):
            raise ValueError(
                f"records {self.records} is not the {len(self.record_ids)} record id(s) this reply lists"
            )
        if self.sources != len({item.source_id for item in self.citations}):
            raise ValueError(
                f"sources {self.sources} is not the {len({item.source_id for item in self.citations})} "
                "publisher id(s) this reply cites"
            )
        outside = sorted({item.record_id for item in self.citations} - set(self.record_ids))
        if outside:
            raise ValueError("a citation names records this reply does not list: " + ", ".join(outside))
        printed = {item.evidence_id for item in self.citations}
        invented = sorted({eid for item in self.statements for eid in item.evidence_ids} - printed)
        if invented:
            raise ValueError(
                "a statement cites evidence this reply carries no quotation for: " + ", ".join(invented)
            )
        kinds = {item.record_kind for item in self.citations}
        if kinds - set(self.record_kinds):
            raise ValueError("a citation carries a record kind this reply does not name")
        if self.state is AnswerState.NOT_ANSWERABLE:
            if self.statements or self.headline:
                raise ValueError("a reply the records cannot answer states nothing")
            if not self.missing:
                raise ValueError("a reply that refuses says what it could not answer and why")
        elif not self.statements:
            raise ValueError("a reply that answers states something; refuse it instead")
        if self.missing is not None and self.state is not AnswerState.NOT_ANSWERABLE:
            raise ValueError("only a refusal carries a missing reason")
        if self.path is ChatPath.RECORD_VALUES:
            if self.model_calls not in (0, 1):
                raise ValueError("a reply of the records' own field values asks no model or one composition")
            if self.model_calls:
                if self.prompt_digest != VALUE_PROMPT_DIGEST or self.prompt_version != VALUE_PROMPT_VERSION:
                    raise ValueError("a value answer that asked a model names the composition prompt")
                if not self.provider or not self.model or self.model == LOOKUP_MODEL:
                    raise ValueError("a value answer that asked a model names the provider and model it asked")
            else:
                if self.prompt_digest is not None:
                    raise ValueError("a value answer that asked no model carries no prompt digest")
                if self.provider != LOOKUP_PROVIDER or self.model != LOOKUP_MODEL:
                    raise ValueError("a reply of the records' own field values names no provider or model")
            if any(
                item.kind not in (StatementKind.SOURCE_FACT, StatementKind.CALCULATED)
                for item in self.statements
            ):
                raise ValueError("a field value copied from a record is never an explanation")
            if self.state is not AnswerState.NOT_ANSWERABLE and (
                not self.citations or any(not item.evidence_ids for item in self.statements)
            ):
                raise ValueError("a value copied from a record cites the field it was copied from")
        elif self.path is ChatPath.GENERAL:
            if not self.model_calls:
                raise ValueError("a general background answer asked a model and says which one it asked")
            if self.citations or self.record_ids or self.events:
                raise ValueError("a general background answer cites no record, because none was read for it")
            if self.state is AnswerState.GROUNDED:
                raise ValueError("a general background answer is never grounded in the records")
            if any(item.backing != "explanation" for item in self.statements):
                raise ValueError("every line of a general background answer is an explanation, not evidence")
        elif self.model_calls and not (self.provider and self.model):
            raise ValueError("a reply that asked a model names the provider and model it asked")
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass
class Reading:
    directory: str = ""
    incidents: list[Incident] = field(default_factory=list)
    links: list[EventLink] = field(default_factory=list)
    families: list[IssueFamily] = field(default_factory=list)
    trends: list[Trend] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    waiting: int = 0

    @property
    def records(self) -> int:
        return len({item.provenance.record_id for item in self.incidents})

    @property
    def sources(self) -> int:
        return len({item.provenance.source_id for item in self.incidents})

    def note(self) -> list[str]:
        lines = [
            f"answered from {len(self.incidents)} reading(s) in {self.records} record(s) filed by "
            f"{self.sources} publisher(s)"
            + (f", held in {self.directory}" if self.directory else ""),
            *self.caveats,
        ]
        if not self.links:
            lines.append(
                "no link decisions were saved beside these records, so repeated reports of one happening "
                "count separately"
            )
        if self.waiting:
            lines.append(
                f"{self.waiting} record(s) waiting for a person's review were left out of every search"
            )
        if self.unreadable:
            lines.append(
                f"{len(self.unreadable)} saved reading(s) were not usable as contract {SCHEMA_VERSION} "
                "input: " + "; ".join(self.unreadable[:3])
            )
        return lines


def read_state_dir(directory: Any) -> Reading:
    where = Path(str(directory))
    reading = Reading(directory=str(where))
    checkpoint = Checkpoint(where).load()
    if checkpoint.incomplete:
        reading.caveats.append(f"the saved readings are read as they stand: {checkpoint.incomplete}")
    if checkpoint.stale_version:
        reading.caveats.append(
            f"the manifest beside these readings was written for contract {checkpoint.stale_version}, "
            f"not {SCHEMA_VERSION}; each saved reading still had to validate against {SCHEMA_VERSION}"
        )
    for record_id, entry in sorted(checkpoint.entries.items()):
        if entry.state not in REUSABLE_STATES or not isinstance(entry.incident, dict):
            continue
        try:
            incident = Incident.model_validate(entry.incident)
        except (ValidationError, TypeError) as error:
            reading.unreadable.append(f"{record_id}: {error}")
            continue
        reading.waiting += int(incident.review_required)
        reading.incidents.append(incident)
    try:
        reading.links = list(LinkStore(where).load().links.values())
        reading.families = [
            family
            for group in IssueStore(where).load().buckets.values()
            for family in group
            if family.review is ReviewState.ACCEPTED
        ]
        reading.trends = [
            item
            for item in TrendStore(where).load().by_id.values()
            if item.review is ReviewState.ACCEPTED
        ]
    except CheckpointError as error:
        reading.unreadable.append(f"saved groups: {error}")
    return reading


@dataclass
class ChatLimits:
    max_question_chars: int = MAX_QUESTION_CHARS
    max_model_calls: int = MAX_MODEL_CALLS_PER_REQUEST
    requests_per_window: int = MAX_REQUESTS_PER_SESSION
    window_seconds: int = SESSION_WINDOW_SECONDS
    records_shown: int = MAX_RECORDS_SHOWN
    facts_shown: int = MAX_FACTS_SHOWN
    history_turns: int = MAX_HISTORY_TURNS
    history_chars: int = MAX_HISTORY_CHARS
    turn_chars: int = MAX_TURN_CHARS


@dataclass
class _Session:
    asked: list[datetime] = field(default_factory=list)
    last_question: Optional[str] = None
    turns: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Composed:
    statements: list[ChatStatement] = field(default_factory=list)
    headline: str = ""
    note: str = NO_MODEL_NOTE
    asked: bool = False
    provider: str = ""
    model: str = ""


class ChatEngine:
    def __init__(
        self,
        reading: Reading,
        *,
        chain_factory: Callable[[], Chain],
        store: Optional[AnswerStore] = None,
        limits: Optional[ChatLimits] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.reading = reading
        self.chain_factory = chain_factory
        self.store = store
        self.limits = limits or ChatLimits()
        self.clock = clock
        self.sessions: dict[str, _Session] = {}
        self.corpus = list(reading.incidents)

    def add(self, incidents: Iterable[Incident]) -> int:
        """Records the service was not started with join the corpus the next question is answered from."""
        known = {item.incident_id for item in self.corpus}
        added = 0
        for incident in incidents:
            if incident.incident_id in known:
                continue
            self.corpus.append(incident)
            known.add(incident.incident_id)
            self.reading.waiting += int(incident.review_required)
            added += 1
        self.reading.incidents = self.corpus
        return added

    def ask(self, question: str, *, session_id: str = DEFAULT_SESSION) -> ChatReply:
        reply = self._answer(question, session_id=session_id)
        self._remember(session_id, reply)
        return reply

    def _remember(self, session_id: str, reply: ChatReply) -> None:
        """What a person just read is what the next question in this session is about."""
        if reply.state is AnswerState.NOT_ANSWERABLE or not reply.statements:
            return
        session = self.sessions.setdefault(reply.session_id, _Session())
        session.turns.append((reply.question, self._plain(reply)))
        del session.turns[: -self.limits.history_turns]
        while len(session.turns) > 1 and sum(
            len(asked) + len(answered) for asked, answered in session.turns
        ) > self.limits.history_chars:
            del session.turns[0]

    def _plain(self, reply: ChatReply) -> str:
        lines: list[str] = []
        for line in [clean(reply.headline), *(clean(item.text) for item in reply.statements)]:
            if line and line not in lines:
                lines.append(line)
        return " ".join(lines)[: self.limits.turn_chars]

    def _answer(self, question: str, *, session_id: str) -> ChatReply:
        text = clean(question)
        topic = topic_of(text)
        stopped = self._limit(text)
        if stopped is not None:
            # The reply quotes the wording back as it arrived: a blank question is still what was sent.
            return self._refuse(
                text or str(question), session_id, stopped, topic=topic, path=ChatPath.RECORD_SEARCH
            )
        session = self.sessions.setdefault(session_id, _Session())
        waited = self.within_rate(session)
        if waited is not None:
            return self._refuse(text, session_id, waited, topic=topic, path=ChatPath.RECORD_SEARCH)
        session.asked.append(self.clock())
        previous = session.last_question
        if (
            topic not in CONTEXT_TYPE_BY_TOPIC
            and previous
            and previous != text
            and said_in(text, ELABORATION_WORDS)
        ):
            # A bare request for more of the answer before it is asked of the records again, in the earlier
            # question's own wording; the records hold the detail, so no model is asked for it.
            combined = f"{previous} {text}"
            followed = topic_of(combined)
            stored = eligible_values(self.corpus, followed) if followed in CONTEXT_TYPE_BY_TOPIC else []
            if stored:
                values = self._from_values(
                    combined, session_id=session_id, topic=followed, stored=stored, carried=previous
                )
                return self._or_general(
                    values, session_id=session_id, previous=previous, history=session.turns
                )
        if topic in CONTEXT_TYPE_BY_TOPIC:
            noun = NOUN_BY_TOPIC[topic]
            stored = eligible_values(self.corpus, topic)
            if stored:
                values = self._from_values(text, session_id=session_id, topic=topic, stored=stored)
                session.last_question = text
                return self._or_general(
                    values, session_id=session_id, previous=previous, history=session.turns
                )
            waiting = len(
                [
                    item
                    for item in self.corpus
                    if item.context_type is CONTEXT_TYPE_BY_TOPIC[topic] and item.review_required
                ]
            )
            if waiting:
                return self._refuse(
                    text,
                    session_id,
                    f"the {waiting} {noun} record(s) stored here wait for a person's review, which a chat "
                    "does not answer over",
                    topic=topic,
                    path=ChatPath.RECORD_VALUES,
                )
            note = (
                f"the question asks about {noun} records and none is stored here, so it was searched among "
                "the incidents instead: an answer names an incident the records evidence, never a value a "
                f"{noun} record would have given"
            )
            search = self._from_search(
                text, session, session_id=session_id, topic=topic, note=note
            )
            return self._or_general(
                search, session_id=session_id, previous=previous, history=session.turns
            )
        route, note, spent = self._route(text, session)
        if route is not None and route.needs_records is False:
            asked = self._refuse(
                text,
                session_id,
                NOT_A_RECORD_QUESTION,
                topic=ChatTopic.GENERAL,
                path=ChatPath.GENERAL,
                model_calls=spent,
            )
            return self._or_general(
                asked, session_id=session_id, previous=previous, history=session.turns
            )
        search = self._from_search(text, session, session_id=session_id, topic=topic)
        search.model_calls += spent
        if note:
            search.limitations = self._notes(note, *search.limitations)
        return self._or_general(
            search, session_id=session_id, previous=previous, history=session.turns
        )

    def _route(self, text: str, session: _Session) -> tuple[Optional[RouteDraft], str, int]:
        """One cheap question before the expensive one: whether the records have to be read for this."""
        got = Asker(self.chain_factory).draft(
            route_request(text, history=session.turns), RouteDraft, "route"
        )
        spent = 1 if got.asked else 0
        if got.draft is None:
            return (
                None,
                "the routing question asked before the records were searched was not answered"
                + (f": {got.note}" if got.note else "")
                + ", so the records were searched as though it needed them",
                spent,
            )
        return got.draft, "", spent

    def _limit(self, text: str) -> Optional[str]:
        if not text:
            return "no question was asked"
        if len(text) > self.limits.max_question_chars:
            return (
                f"the question is {len(text)} characters and this chat takes at most "
                f"{self.limits.max_question_chars}"
            )
        return None

    def within_rate(self, session: _Session) -> Optional[str]:
        now = self.clock()
        session.asked = [
            item for item in session.asked if (now - item).total_seconds() < self.limits.window_seconds
        ]
        if len(session.asked) < self.limits.requests_per_window:
            return None
        first = min(session.asked)
        wait = int(self.limits.window_seconds - (now - first).total_seconds()) + 1
        return (
            f"this chat takes at most {self.limits.requests_per_window} question(s) per "
            f"{self.limits.window_seconds} seconds in one session, so nothing was asked of a model; "
            f"ask again in {max(wait, 1)} second(s)"
        )

    def _notes(self, *lines: str) -> list[str]:
        """What this answer itself turns on, kept apart from the run's standing disclosure."""
        kept: list[str] = []
        for line in lines:
            text = clean(line)
            if text and text not in kept:
                kept.append(text)
        return kept

    def _standing(self, *lines: str) -> list[str]:
        return self._notes(*lines, *self.reading.note())

    def _refuse(
        self,
        text: str,
        session_id: str,
        reason: str,
        *,
        topic: ChatTopic,
        path: ChatPath,
        limitations: Optional[Sequence[str]] = None,
        standing: Optional[Sequence[str]] = None,
        model_calls: int = 0,
        carried: Optional[str] = None,
    ) -> ChatReply:
        carry_note = (
            f"this question follows an earlier one in the same conversation ({carried!r}), so the wording "
            "of both was matched together"
            if carried
            else ""
        )
        return ChatReply(
            reply_id=reply_id_for(session_id, text, carried),
            session_id=session_id,
            question=text,
            script=script_of(text).value,
            topic=topic,
            path=path,
            state=AnswerState.NOT_ANSWERABLE,
            missing=reason,
            carried_from=carried,
            limitations=self._notes(
                carry_note,
                *(limitations or []),
                *(() if model_calls else (NO_MODEL_ASKED,)),
            ),
            standing=self._standing(*(standing or [])),
            model_calls=model_calls,
            provider=LOOKUP_PROVIDER,
            model=LOOKUP_MODEL,
            prompt_version=CHAT_PROMPT_VERSION,
            asked_at=self.clock(),
        )

    def _or_general(
        self,
        refusal: ChatReply,
        *,
        session_id: str,
        previous: Optional[str] = None,
        history: Sequence[tuple[str, str]] = (),
    ) -> ChatReply:
        """A refusal a model reached is worth one more question: what background answers it."""
        if refusal.state is not AnswerState.NOT_ANSWERABLE:
            return refusal
        declared = refusal.missing == NOT_A_RECORD_QUESTION
        if not declared and (
            refusal.missing == NOTHING_READ
            or not 1 <= refusal.model_calls < self.limits.max_model_calls
        ):
            if not refusal.model_calls:
                refusal.limitations = self._notes(
                    "the records refused this without a model being asked, and no background question is "
                    "asked of a model for a refusal that asked none",
                    *refusal.limitations,
                )
            return refusal
        held = None if declared or refusal.carried_from or previous == refusal.question else previous
        asked = " ".join(item for item in (refusal.question, held) if item)
        got = Asker(self.chain_factory).draft(
            general_request(refusal.question, history=history or ([(held, "")] if held else ())),
            GeneralDraft,
            "general answer",
        )
        if got.draft is None:
            if got.note:
                refusal.limitations = self._notes(
                    f"no general background answer came back: {got.note}", *refusal.limitations
                )
            return refusal
        reason = general_refusal(
            got.draft,
            places=self._unasked_places(asked),
            value_shaped=refusal.topic in CONTEXT_TYPE_BY_TOPIC,
        )
        if reason:
            refusal.limitations = self._notes(
                f"a general background answer was offered and dropped: {reason}", *refusal.limitations
            )
            return refusal
        return self._general_reply(refusal, got, session_id=session_id, carried=held)

    def _unasked_places(self, question: str) -> list[str]:
        """The places these records hold that the question never mentioned. A background answer may use the
        place a collector named; carrying in one they did not would read as a finding about this district."""
        vocabulary = place_vocabulary(self.corpus)
        asked = set(named_places(question, vocabulary))
        return [item for item in vocabulary if item not in asked]

    def _general_reply(
        self, refusal: ChatReply, got: Any, *, session_id: str, carried: Optional[str] = None
    ) -> ChatReply:
        draft = got.draft
        confidence = float(draft.confidence) if draft.confidence is not None else 0.0
        lines = [_keep_indent(draft.headline), *(_keep_indent(item) for item in draft.lines)]
        statements = [
            ChatStatement(
                text=line,
                kind=StatementKind.POSSIBLE_EXPLANATION,
                review=ReviewState.REVIEW_REQUIRED,
                confidence=confidence,
            )
            for line in lines
            if line.strip()
        ]
        declared = refusal.missing == NOT_A_RECORD_QUESTION
        if declared:
            # No search ran, so the notes a search would carry describe a reading this answer never used.
            limitations = [GENERAL_NOTE]
        else:
            limitations = self._notes(
                GENERAL_NOTE,
                f"the records were asked first and did not answer this: {refusal.missing}",
                *refusal.limitations,
            )
        carried = carried or refusal.carried_from
        if carried:
            limitations = self._notes(
                f"this question follows an earlier one in the same conversation ({carried!r}), and that "
                "earlier subject is what is answered here",
                *limitations,
            )
        return ChatReply(
            reply_id=reply_id_for(session_id, refusal.question, carried),
            session_id=session_id,
            question=refusal.question,
            script=refusal.script,
            topic=ChatTopic.GENERAL,
            path=ChatPath.GENERAL,
            state=AnswerState.QUALIFIED,
            headline=statements[0].text if len(statements) > 1 else None,
            understood=None,
            statements=statements,
            limitations=limitations,
            standing=refusal.standing,
            carried_from=carried,
            model_calls=refusal.model_calls + 1,
            provider=got.provider,
            model=got.model,
            prompt_version=GENERAL_PROMPT_VERSION,
            prompt_digest=GENERAL_PROMPT_DIGEST,
            asked_at=self.clock(),
        )

    def _from_values(
        self,
        text: str,
        *,
        session_id: str,
        topic: ChatTopic,
        stored: Sequence[Incident],
        carried: Optional[str] = None,
    ) -> ChatReply:
        noun = NOUN_BY_TOPIC[topic]
        kept, notes = matching_values(stored, text)
        if not kept:
            return self._refuse(
                text,
                session_id,
                " ".join(notes) or f"none of the {noun} records stored here answers the question as worded",
                topic=topic,
                path=ChatPath.RECORD_VALUES,
                limitations=[
                    NO_MODEL_NOTE,
                    f"{len(stored)} accepted {noun} record(s) are stored and none of them answers the "
                    "question as it was worded",
                ],
                carried=carried,
            )

        start, end, label = window_of(text, self.clock().date())
        missed = ""
        if start is not None and carried:
            notes.append(
                f"the earlier question named {label} ({start.isoformat()} to {end.isoformat()}) and this "
                "one asks for more of that reading, so every record in scope is printed with its own date, "
                "newest first, instead of only the day named"
            )
        elif start is not None:
            dated = [
                item
                for item in kept
                if item.event_time is not None and start <= item.event_time.date() <= end
            ]
            if dated:
                kept = dated
                notes.append(f"dated {label} ({start.isoformat()} to {end.isoformat()})")
            else:
                nearest = max(
                    (item.event_time.date() for item in kept if item.event_time is not None),
                    default=None,
                )
                span = f" ({start.isoformat()})" if start == end else f" ({start.isoformat()} to {end.isoformat()})"
                missed = (
                    f"No {noun} record here carries {label}{span}."
                    + (f" The nearest stored reading is {nearest}." if nearest else "")
                )
                notes.append(
                    f"the question named {label}{span} and no record in scope carries that date, so the "
                    "nearest stored reading is printed instead"
                    if nearest
                    else f"the question named {label}{span} and no record in scope carries a date at all"
                )

        shown = kept[: self.limits.records_shown]
        limitations: list[str] = [*notes]
        citations: list[ChatCitation] = []
        statements: list[ChatStatement] = []
        printed: list[Incident] = []

        for incident in shown:
            facts = accepted_facts(incident)
            evidence_by_id = incident.evidence_by_id()
            line, ids = value_line(incident, topic)
            backed = sorted({item for item in ids if item in evidence_by_id})
            if not backed:
                limitations.append(
                    f"{incident.provenance.record_id} carries accepted value(s) with no quotation pinned "
                    "to them in the record, so nothing from it is printed here"
                )
                continue
            printed.append(incident)
            cited: set[str] = set()
            for fact in facts[: self.limits.facts_shown]:
                for evidence_id in fact.evidence_ids:
                    if evidence_id not in backed or evidence_id in cited:
                        continue
                    evidence = evidence_by_id[evidence_id]
                    cited.add(evidence_id)
                    citations.append(
                        ChatCitation(
                            record_id=incident.provenance.record_id,
                            incident_id=incident.incident_id,
                            source_id=incident.provenance.source_id,
                            source_type=incident.provenance.source_type,
                            source_url=incident.provenance.source_url,
                            field=fact.field,
                            quote=str(evidence.quote if evidence.quote is not None else fact.value),
                            evidence_id=evidence_id,
                            record_kind=incident.record_kind,
                            modality=evidence.modality,
                            method=fact.method,
                            when=incident.event_time,
                        )
                    )
            statements.append(
                ChatStatement(
                    text=line,
                    kind=StatementKind.SOURCE_FACT,
                    evidence_ids=backed,
                    review=ReviewState.ACCEPTED,
                    confidence=value_confidence(facts[: self.limits.facts_shown]),
                )
            )
            if len(facts) > self.limits.facts_shown:
                limitations.append(
                    f"{incident.provenance.record_id} carries {len(facts)} valued field(s) and "
                    f"{self.limits.facts_shown} are printed here"
                )

        if not printed:
            return self._refuse(
                text,
                session_id,
                f"the {noun} records that match this question carry values no quotation in the record "
                "backs, so nothing can be printed as the record's own words",
                topic=topic,
                path=ChatPath.RECORD_VALUES,
                carried=carried,
                limitations=[NO_MODEL_NOTE, *limitations],
            )

        if len(kept) > len(shown):
            limitations.append(
                f"{len(kept)} {noun} record(s) match the question and {len(shown)} are printed here"
            )
        dated = [item.event_time for item in printed if item.event_time is not None]
        today = self.clock().date()
        if dated and max(dated).date() < today:
            limitations.append(
                f"the newest {noun} record answering this carries its own date {max(dated).date()}, "
                f"{(today - max(dated).date()).days} day(s) before this question arrived on {today}, so "
                "it is that record's reading and not a reading for the day asked"
            )
        undated = len([item for item in printed if item.event_time is None])
        if undated:
            limitations.append(f"{undated} printed record(s) carry no date of their own")

        summary = price_range(printed) if topic is ChatTopic.MARKET and len(printed) > 1 else ""
        if summary:
            statements.insert(
                0,
                ChatStatement(
                    text=summary,
                    kind=StatementKind.CALCULATED,
                    evidence_ids=sorted({eid for item in statements for eid in item.evidence_ids}),
                    review=ReviewState.ACCEPTED,
                    confidence=min(item.confidence for item in statements),
                ),
            )
        composed = self._compose(text, statements, citations)
        record_ids = sorted({item.provenance.record_id for item in printed})
        headline = f"{missed} {composed.headline}" if missed else composed.headline
        limitations.insert(0, composed.note)
        if carried:
            limitations.insert(
                1,
                f"this question follows an earlier one in the same conversation ({carried!r}), so it is "
                "answered from the records that answer that one",
            )
        return ChatReply(
            reply_id=reply_id_for(session_id, text, carried),
            session_id=session_id,
            question=text,
            script=script_of(text).value,
            topic=topic,
            path=ChatPath.RECORD_VALUES,
            state=AnswerState.QUALIFIED if missed else AnswerState.GROUNDED,
            headline=headline,
            understood=f"{len(printed)} stored {noun} record(s) were read for their own field values",
            carried_from=carried,
            statements=composed.statements,
            citations=citations,
            record_ids=record_ids,
            record_kinds=sorted({item.record_kind for item in printed}, key=lambda kind: kind.value),
            events=0,
            records=len(record_ids),
            sources=len({item.source_id for item in citations}),
            limitations=self._notes(*limitations),
            standing=self._standing(),
            model_calls=1 if composed.asked else 0,
            provider=composed.provider or LOOKUP_PROVIDER,
            model=composed.model or LOOKUP_MODEL,
            prompt_version=VALUE_PROMPT_VERSION if composed.asked else CHAT_PROMPT_VERSION,
            prompt_digest=VALUE_PROMPT_DIGEST if composed.asked else None,
            asked_at=self.clock(),
        )

    def _compose(
        self,
        question: str,
        statements: list[ChatStatement],
        citations: list[ChatCitation],
    ) -> Composed:
        """One call, for wording only: the model may reword the records' own values and nothing else."""
        facts = [item.text for item in statements]
        got = Asker(self.chain_factory).draft(value_request(question, facts), ValueDraft, "value wording")
        if not got.asked:
            return Composed(statements=list(statements), headline=facts[0])
        dropped = (
            got.note or "no wording came back at all"
            if got.draft is None
            else composition_fault(got.draft, facts, place_vocabulary(self.corpus))
        )
        identity = Composed(
            statements=list(statements),
            headline=facts[0],
            asked=True,
            provider=got.provider,
            model=got.model,
            note=(
                f"a model was asked to word this answer and its wording was dropped: {dropped}"
                if dropped
                else COMPOSED_NOTE
            ),
        )
        if dropped:
            return identity
        owner = {item.evidence_id: item.record_id for item in citations}
        built: list[ChatStatement] = []
        for line, cited in composed_lines(got.draft):
            ids = sorted({eid for index in cited for eid in statements[index - 1].evidence_ids})
            spread = len({owner[eid] for eid in ids if eid in owner})
            built.append(
                ChatStatement(
                    text=line,
                    kind=(
                        StatementKind.CALCULATED if spread > 1 else StatementKind.SOURCE_FACT
                    ),
                    evidence_ids=ids,
                    review=ReviewState.ACCEPTED,
                    confidence=min(statements[index - 1].confidence for index in cited),
                )
            )
        return Composed(
            statements=built,
            headline=built[0].text,
            note=COMPOSED_NOTE,
            asked=True,
            provider=got.provider,
            model=got.model,
        )

    def _from_search(
        self,
        text: str,
        session: _Session,
        *,
        session_id: str,
        topic: ChatTopic,
        note: Optional[str] = None,
    ) -> ChatReply:
        if not self.corpus:
            return self._refuse(
                text,
                session_id,
                NOTHING_READ,
                topic=topic,
                path=ChatPath.RECORD_SEARCH,
                limitations=[note] if note else [],
            )
        carried: Optional[str] = None
        earlier = session.last_question if session.last_question != text else None
        report = self._read(text, previous=earlier)
        answer = report.answers[0]
        spent = 0
        general = answer.interpretation.intent is QuestionIntent.GENERAL
        # Only the read, which is shown the earlier question, decides that this one continues it. A question
        # the read sets no filter for is a fresh look at the whole corpus, not necessarily a follow-up.
        follows = answer.interpretation.follow_up
        if (
            not general
            and follows
            and session.last_question
            and session.last_question != text
            and report.read + report.asked < self.limits.max_model_calls
        ):
            spent = report.read + report.asked
            carried = session.last_question
            text = f"{carried} {text}"
            report = self._read(text)
            answer = report.answers[0]
        if not general:
            session.last_question = text
        return self._reply(
            report,
            answer,
            text,
            session_id=session_id,
            topic=topic,
            carried=carried,
            note=note,
            spent=spent,
        )

    def _read(self, question: str, previous: Optional[str] = None) -> AskReport:
        return ask_copilot(
            self.corpus,
            [question],
            chain_factory=self.chain_factory,
            links=self.reading.links,
            families=self.reading.families,
            measured=self.reading.trends,
            store=self.store,
            max_requests=1,
            now=self.clock(),
            previous=previous,
        )

    def _reply(
        self,
        report: AskReport,
        answer: Answer,
        text: str,
        *,
        session_id: str,
        topic: ChatTopic,
        carried: Optional[str],
        note: Optional[str] = None,
        spent: int = 0,
    ) -> ChatReply:
        citations = [
            ChatCitation(
                record_id=item.record_id,
                incident_id=item.incident_id,
                source_id=item.source_id,
                source_type=self._source_type(item.incident_id, item.record_id),
                source_url=item.source_url,
                field=item.field,
                quote=item.quote,
                evidence_id=item.evidence_id,
                record_kind=RecordKind.INCIDENT,
                modality=item.modality,
                method=item.method,
                when=item.when,
            )
            for item in answer.citations
        ]
        statements = [
            ChatStatement(
                text=item.text,
                kind=item.kind,
                evidence_ids=list(item.evidence_ids),
                event_ids=list(item.event_ids),
                review=item.review,
                confidence=item.confidence,
            )
            for item in answer.statements
        ]
        specifics = [line for line in [note] if line]
        if carried:
            specifics.append(
                f"this question was read together with the question before it ({carried!r}) because it "
                "asks about that answer; the search and the answer cover both, and every reading is "
                "counted in the model calls"
            )
        if answer.interpretation.findings:
            specifics.append(
                "how the question was read carries its own findings: "
                + "; ".join(answer.interpretation.findings)
            )
        explanation = answer.explanation
        refused = answer.state is AnswerState.NOT_ANSWERABLE
        quoted = list(dict.fromkeys(item.record_id for item in citations))
        if quoted and len(quoted) < len(answer.record_ids):
            specifics.append(
                f"the search found {len(answer.record_ids)} record(s) and this answer quotes {len(quoted)} "
                "of them, so the records and publishers named here are the quoted ones"
            )
        limitations = self._notes(*specifics)
        standing = self._standing(*report.limitations())
        return ChatReply(
            reply_id=reply_id_for(session_id, text, carried),
            session_id=session_id,
            question=text,
            script=answer.script.value,
            topic=topic,
            path=ChatPath.RECORD_SEARCH,
            state=answer.state,
            headline=None if refused or explanation is None else explanation.headline,
            understood=answer.interpretation.understood,
            statements=[] if refused else statements,
            citations=[] if refused else citations,
            record_ids=[] if refused else quoted,
            record_kinds=[] if refused else [RecordKind.INCIDENT],
            events=0 if refused else answer.events,
            records=0 if refused else len(quoted),
            sources=0 if refused else len({item.source_id for item in citations}),
            limitations=limitations,
            standing=standing,
            missing=answer.missing,
            carried_from=carried,
            model_calls=report.read + report.asked + spent,
            provider=answer.provider,
            model=answer.model,
            prompt_version=answer.prompt_version,
            prompt_digest=answer.prompt_digest,
            asked_at=answer.decided_at,
        )

    def _source_type(self, incident_id: str, record_id: str) -> str:
        for incident in self.corpus:
            if incident.incident_id == incident_id or incident.provenance.record_id == record_id:
                return incident.provenance.source_type
        return UNKNOWN_SOURCE_TYPE


def print_reply(reply: ChatReply, *, stream: Any = None, detailed: bool = False) -> None:
    out = stream if stream is not None else sys.stdout
    print(f"Question ({reply.script}): {reply.question}", file=out)
    if reply.understood:
        print(f"Searched: {reply.understood}", file=out)
    if reply.headline:
        print(f"Answer: {reply.headline}", file=out)
    if reply.missing:
        print(f"Not answered: {reply.missing}", file=out)
    evidence = [item for item in reply.statements if item.backing == "evidence"]
    reading = [item for item in reply.statements if item.backing == "explanation"]
    if evidence:
        print("From the records:", file=out)
        for item in evidence:
            marks = " [cited: " + ", ".join(item.evidence_ids) + "]" if item.evidence_ids else ""
            print(f"  - {item.text}{marks}", file=out)
    if reading:
        print(
            "General background, from no record:"
            if reply.path is ChatPath.GENERAL
            else "A model's reading of the same records, never a finding:",
            file=out,
        )
        for item in reading:
            held = "" if item.review is ReviewState.ACCEPTED else " (held for a person)"
            print(f"  - {item.text} ({item.kind.value}){held}", file=out)
    if detailed:
        for item in reply.citations:
            print(
                f"  [{item.evidence_id}] {item.record_kind.value} {item.record_id} {item.field}: "
                f"{item.quote} ({item.source_id}, {item.source_url or 'no source url named'})",
                file=out,
            )
    print(
        f"Records: {reply.records} | publishers: {reply.sources} | events: {reply.events} | "
        f"model calls: {reply.model_calls} | answer: {reply.state.value}",
        file=out,
    )
    if reply.limitations:
        print("Limitations:", file=out)
        for line in reply.limitations:
            print(f"  - {line}", file=out)
    if reply.standing:
        print("Standing notes on these records:", file=out)
        for line in reply.standing:
            print(f"  - {line}", file=out)


__all__ = [
    "CHAT_PROMPT_VERSION",
    "COMPOSED_NOTE",
    "CONTEXT_TYPE_BY_TOPIC",
    "DEFAULT_SESSION",
    "ELABORATION_WORDS",
    "FORECAST_WORDS",
    "GENERAL_NOTE",
    "GENERAL_PROMPT_DIGEST",
    "GENERAL_PROMPT_VERSION",
    "GENERAL_RULES",
    "GENERAL_SCHEMA",
    "LOOKUP_MODEL",
    "LOOKUP_PROVIDER",
    "MARKET_WORDS",
    "MAX_MODEL_CALLS_PER_REQUEST",
    "MAX_NAMES_PRINTED",
    "MAX_COMPOSED_LINES",
    "MAX_GENERAL_LINES",
    "MAX_HISTORY_CHARS",
    "MAX_HISTORY_TURNS",
    "MAX_QUESTION_CHARS",
    "MAX_RECORDS_SHOWN",
    "MAX_REQUESTS_PER_SESSION",
    "MAX_FACTS_SHOWN",
    "MAX_TURN_CHARS",
    "NO_MODEL_ASKED",
    "NO_MODEL_NOTE",
    "NOTHING_READ",
    "NOUN_BY_TOPIC",
    "ROUTE_PROMPT_DIGEST",
    "ROUTE_PROMPT_VERSION",
    "ROUTE_RULES",
    "ROUTE_SCHEMA",
    "SESSION_WINDOW_SECONDS",
    "UNKNOWN_SOURCE_TYPE",
    "VALUE_ALIASES",
    "VALUE_PROMPT_DIGEST",
    "VALUE_PROMPT_VERSION",
    "VALUE_RULES",
    "VALUE_SCHEMA",
    "VALUE_WORDS",
    "ChatCitation",
    "ChatEngine",
    "ChatLimits",
    "ChatPath",
    "ChatReply",
    "ChatStatement",
    "ChatTopic",
    "Composed",
    "ComposedLine",
    "GeneralDraft",
    "Reading",
    "RouteDraft",
    "ValueDraft",
    "accepted_facts",
    "backing_of",
    "composition_fault",
    "composed_lines",
    "eligible_values",
    "fact_line",
    "fold",
    "general_refusal",
    "general_request",
    "history_lines",
    "holds_places",
    "matching_values",
    "named_fields",
    "named_label",
    "named_places",
    "place_vocabulary",
    "print_reply",
    "read_state_dir",
    "reply_id_for",
    "route_request",
    "said_in",
    "topic_of",
    "value_confidence",
    "value_fields",
    "value_request",
    "window_of",
]
