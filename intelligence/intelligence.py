from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Type, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intelligence.context import RecordContext, build_context
from intelligence.contract import (
    Claim,
    ContextFact,
    ContextType,
    EVENT_ONLY_FIELDS,
    Entity,
    EntityType,
    EventStatus,
    Generation,
    Incident,
    IncidentCategory,
    Issue,
    IssueCode,
    Location,
    PriorityLevel,
    RecordKind,
    Relationship,
    RelationshipKind,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.geography import source_geography, source_locations
from intelligence.llm import LLMProvider, LLMRequest, StructuredOutput, json_schema_for
from intelligence.models.base import OptionalConfidence
from intelligence.models.enums import (
    ActorRole,
    Department,
    EventType,
    ExtractionMethod,
    MentionRole,
    MentionType,
    Modality,
    SeverityLevel,
    TimePrecision,
)
from intelligence.models.evidence import Evidence
from intelligence.records import CONTENT_PATH
from intelligence.spans import Span, SpanError, find_spans

PROVIDER = "intelligence.intelligence"
PROMPT_VERSION = "stage-2.29"

# ingestion's news normalizer copies published_at into event_time, so that stamp says when the page was written
PUBLICATION_STAMPED_SOURCES = frozenset({"news"})

MAX_ANSWER_ITEMS = 8

DEFAULT_LLM_CONFIDENCE = 0.5

UNREPORTED_CONFIDENCE = (
    f"confidence was not reported, so {DEFAULT_LLM_CONFIDENCE} is the pipeline's own attribution"
)

VERBATIM_FIELDS = ("title", "description")

COPIED_FIELDS = ("title",)

BRACKET_RUN_MINIMUM = 8

BRACKET_OCCURRENCES = 8

BRACKET_REACH = 2.0

FORBIDDEN_ITEM_KEYS = frozenset(
    {
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "geometry",
        "canonical_location_id",
        "canonical_id",
        "resolution_state",
    }
)
FORBIDDEN_TOP_LEVEL = frozenset(
    {"evidence", "evidence_ids", "provenance", "incident_id", "schema_version"}
)


class InvalidExtraction(ValueError):
    ...


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GroundedValue(_Wire):
    value: Optional[str] = None
    field: Optional[str] = None
    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    precision: Optional[str] = None
    confidence: OptionalConfidence = None


class LocationDraft(_Wire):
    text: Optional[str] = None
    normalized_name: Optional[str] = None
    location_type: Optional[str] = None
    role: Optional[str] = None
    district: Optional[str] = None
    taluk: Optional[str] = None
    block: Optional[str] = None
    village: Optional[str] = None
    field: Optional[str] = None
    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    confidence: OptionalConfidence = None


class EntityDraft(_Wire):
    text: Optional[str] = None
    normalized_name: Optional[str] = None
    entity_type: Optional[str] = None
    role: Optional[str] = None
    field: Optional[str] = None
    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    confidence: OptionalConfidence = None


class RelationshipDraft(_Wire):
    kind: Optional[str] = None
    subject: Optional[str] = None
    object: Optional[str] = None
    description: Optional[str] = None
    field: Optional[str] = None
    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    confidence: OptionalConfidence = None


class ContextFactDraft(_Wire):
    """One value the record carries. `field` is both what the fact is about and where it is quoted."""

    field: Optional[str] = None
    value: Optional[str] = None
    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    confidence: OptionalConfidence = None


class ExtractionDraft(_Wire):
    """The whole model answer for one record."""

    title: Optional[GroundedValue] = None
    description: Optional[GroundedValue] = None
    incident_type: Optional[GroundedValue] = None
    category: Optional[GroundedValue] = None
    department: Optional[GroundedValue] = None
    severity: Optional[GroundedValue] = None
    priority: Optional[GroundedValue] = None
    event_status: Optional[GroundedValue] = None
    event_time: Optional[GroundedValue] = None

    record_kind: Optional[str] = None
    context_type: Optional[str] = None

    locations: list[LocationDraft] = Field(default_factory=list)
    entities: list[EntityDraft] = Field(default_factory=list)
    relationships: list[RelationshipDraft] = Field(default_factory=list)
    context_facts: list[ContextFactDraft] = Field(default_factory=list)


REQUEST_SCHEMA = json_schema_for(ExtractionDraft)

SCALAR_FIELDS: tuple[tuple[str, Union[str, Type[Enum]]], ...] = (
    ("title", "text"),
    ("description", "text"),
    ("incident_type", EventType),
    ("category", IncidentCategory),
    ("department", Department),
    ("severity", SeverityLevel),
    ("priority", PriorityLevel),
    ("event_status", EventStatus),
)

OPTIONAL_SCALARS: tuple[str, ...] = tuple(
    name
    for name, spec in SCALAR_FIELDS
    if isinstance(spec, type)
    and issubclass(spec, Enum)
    and not any(member.value in ("unresolved", "unknown") for member in spec)
)

ITEM_FIELDS: tuple[tuple[str, Type[Enum]], ...] = (
    ("locations[].location_type", MentionType),
    ("locations[].role", MentionRole),
    ("entities[].entity_type", EntityType),
    ("entities[].role", ActorRole),
    ("relationships[].kind", RelationshipKind),
)


@dataclass(frozen=True)
class _Grounded:
    evidence: Optional[Evidence] = None
    review: ReviewState = ReviewState.UNRESOLVED
    reason: Optional[str] = None
    issue: Optional[Issue] = None

    @property
    def evidence_ids(self) -> list[str]:
        return [] if self.evidence is None else [self.evidence.evidence_id]


class _Report:
    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.warnings: list[str] = []

    def issue(self, code: IssueCode, *, field: Optional[str], detail: str) -> None:
        if not any(
            existing.code is code and existing.field == field and existing.detail == detail
            for existing in self.issues
        ):
            self.issues.append(Issue(code=code, field=field, detail=detail))

    def warn(self, detail: str) -> None:
        if detail not in self.warnings:
            self.warnings.append(detail)


# the draft takes these fields as plain strings so an unlisted token is reported as that field's
# invalid_value while the rest of the answer stands; a schema enum would fail the whole parse
def prompt_vocabulary() -> dict[str, list[str]]:
    vocabulary = {
        name: [member.value for member in spec]
        for name, spec in SCALAR_FIELDS
        if spec != "text"
    }
    vocabulary.update((name, [member.value for member in spec]) for name, spec in ITEM_FIELDS)
    vocabulary["event_time_precision"] = [member.value for member in TimePrecision]
    vocabulary["record_kind"] = [member.value for member in RecordKind]
    vocabulary["context_type"] = [member.value for member in ContextType]
    return vocabulary


BARE = "bare token"


def answer_shape() -> str:
    defs = REQUEST_SCHEMA.get("$defs") or {}
    vocabulary = prompt_vocabulary()

    def name_of(spec: Mapping[str, Any]) -> str:
        return str(spec.get("$ref", "")).rsplit("/", 1)[-1]

    def keys_of(ref: str) -> str:
        return ", ".join(((defs.get(ref) or {}).get("properties") or {}).keys())

    def example(field: str) -> str:
        return (vocabulary.get(field) or ["a token"])[0]

    def typed(field: str, spec: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for option in (spec.get("anyOf") or [spec]):
            if "$ref" in option:
                parts.append(f"object{{{keys_of(name_of(option))}}}")
            elif option.get("type") == "array":
                items = option.get("items") or {}
                parts.append(
                    "list of "
                    + (f"object{{{keys_of(name_of(items))}}}" if "$ref" in items else str(items))
                )
            elif option.get("type") == "string":
                parts.append(BARE)
            else:
                parts.append(str(option.get("type")))
        return " | ".join(dict.fromkeys(parts))

    def line(signature: str, fields: list[str]) -> str:
        text = signature
        if BARE in signature:
            text = text.replace(
                BARE,
                "a bare token as a string, never an object - write "
                + " or ".join(f'"{example(field)}"' for field in fields)
                + f', not {{"value": "{example(fields[0])}"}}',
            )
        return f"- {', '.join(fields)}: {text}"

    groups: dict[str, list[str]] = {}
    for field, spec in REQUEST_SCHEMA["properties"].items():
        groups.setdefault(typed(field, spec), []).append(field)

    return (
        "Answer shape, typed from the schema this answer is validated against, as `field, field: type`. "
        "An `object{...}` carries exactly those keys, `null` answers that whole field "
        "with nothing, and a `list of object{...}` is empty when the record carries none:\n"
        + "\n".join(line(signature, fields) for signature, fields in groups.items())
        + "\n\n"
    )


def prompt_text() -> str:
    return (
        "You read one record about events in a Tamil Nadu district and report only what that record "
        "supports. Answer with one JSON object in exactly the shape printed under `Answer shape`.\n\n"
        "Rules:\n"
        "1. Every quote names the field whose text it copies and is one unbroken run of that field's "
        "characters - an exact substring of it, never paraphrased, translated, reinflected or "
        "stitched from two fields or two clauses. Take the shortest span "
        "that identifies one occurrence: long enough to be found once in that field. `title` and the "
        "body are different fields, so never cite the title for what the body says nor the body for words "
        "only the title carries. A field another kind of source usually carries does not exist here unless "
        "it is printed.\n"
        "2. Nothing but this record's text is evidence: not your reasoning, a flattened copy of your own "
        "answer, this prompt, the metadata block printed with it, or a field, "
        "place, date or detail the record does not carry. `field` names a path printed after the record, "
        "exactly as printed.\n"
        "3. `title` is the record's own headline, printed as a field of its own: copy its characters "
        "exactly, in its own language - never a summary or a translation.\n"
        "4. A quote proves what happened; it does not have to contain the word for it. For "
        "`incident_type`, `category`, `department`, `severity`, `priority` and `event_status` the `value` "
        "is a taxonomy name and the `quote` the record's own words, so they rarely match literally - "
        "a good answer, not a reason to abstain. Take the most specific token the evidence supports: "
        "`other` is for an event the record states plainly that the list has no name for. Read the three "
        "classification fields as three different questions about one event - the act, the area of work it "
        "falls in, whose job it is - and cite each from the clause saying what was "
        "done, to whom or by whom: a span supports a field only when that span alone gives that field its "
        "answer. One body sentence may carry all three and one quote may support all three. The word for "
        "what an article is about - the water, the road, the crop - names its subject, runs through the "
        "whole text and proves nobody's job; where they are words the article keeps "
        "repeating, null it, because a repeated word cannot say which of its copies you meant. A token "
        "with no event behind it - a topic, a heading, routine work, expectation - stays null.\n"
        "5. severity, priority and event_status are three reads of one event, related, never copied. "
        "severity is the harm or scale the whole article states - lives, injuries, how many people, fields "
        "or shops, how long power, water, a road or a service has been out, suffering still going on, a "
        "warning level the record prints; answer the "
        "band the evidence carries and no higher, since a subject being police, fraud or disaster carries "
        "no band, an unfilled post or a shortage people are still suffering is stated harm, not "
        "automatically `info`, and where nothing states harm severity stays unresolved - `info` is for a "
        "record that itself "
        "presents the matter as informational, not a softer guess. priority is how soon the district must "
        "act, read from urgency the text shows - an active danger, a response under way, water or heat "
        "still rising, a service still without staff or supply, a date it names - never raised or lowered "
        "to match severity, null when nothing says the clock matters. event_status is what "
        "the source says about now: ongoing while it is still happening, under_investigation while someone "
        "is enquiring, examining, searching into it or has filed a case about it, action_taken once an "
        "authority has repaired, relieved, served notice or paid, resolved once the record says the matter "
        "is settled, reported when the text only establishes that it happened, unknown only when the "
        "record truly says nothing about now. Take status from those words and nothing else: not from the "
        "feed that carried the record, and not from the kind of event - a crime described is not by itself "
        "an investigation, an authority mentioned or an appeal that a post be filled or a repair made is "
        "not by itself action taken or an ending, and a protest, a strike, "
        "a court hearing, an inspection or a meeting raised or examined an issue rather than ended it, so "
        "such a record is never `resolved`. A record that states no happening has no status to report: "
        "leave the field null rather than answering `unknown`.\n"
        "6. Not every record is an incident, and the kind is the first read. Ask first: does "
        "this record state that something happened - an actor did "
        "something, something was done to someone, a state changed? An incident "
        "says something happened and mattered. A forecast says something may happen. A price line, a "
        "temperature, a profile or a schedule listing informs without stating anything that "
        "took place. Answer record_kind as incident for the first kind, forecast for the second and "
        "context for the rest, with context_type naming what the record does carry, and a happening stays "
        "a happening whoever wrote about it: an investigation opened, a case registered, material seized, "
        "relief delivered, an order issued, a transfer made or a ceremony held is an act of the "
        "administration and an event at once. The kind is settled by the event, not by the fields that "
        "follow it, and never by source_type or record_type. So never file a happening as context because "
        "no token fits it or it came from an ordinary feed, and never leave an event's own "
        "fields blank because you called the record context - one unreadable field does not undo an "
        "evidenced happening. Nor does the label you print prove anything: the happening must be quoted. "
        "A record you call forecast or context leaves description, incident_type, category, department, "
        "severity, priority and event_status null - a price or a temperature written into an account is "
        "still a state.\n"
        "7. For a record whose content is values answer `context_facts`: each item names one of this "
        "record's field paths in `field`, gives that field's `value` exactly as the field holds it, "
        "and quotes that field verbatim in `quote`. Two to eight, the most informative first. A fact "
        "is one data point a field holds - a price, a count, a level, a name, an id - never a headline and "
        "never the body's prose. A fact copies and never interprets, so a "
        "translated or glossed value is not a copy even when its quote is verbatim. An incident answers "
        "its event fields first and may carry facts for the figures those fields do not hold.\n"
        "8. Read the event from the body, not from the headline: the sentence saying what happened is the "
        "evidence. `description` is the concise factual account "
        "of that event - the record's own sentence or a shorter restatement of it - and its `quote` names "
        "the field carrying the words. For an incident it is almost always answerable. Never "
        "pad it, never restate the headline alone, never add a detail the text does not carry or an "
        "account you cannot quote for.\n"
        "9. A name belongs to one list, decided by the words around it: where something happened, or its "
        "area, is a `locations` item; a person, an official, a department, a committee, a party, a "
        "school, a hospital or a market named as an actor is an `entities` item - a place joins it only "
        "when it acts as a party, the panchayat ordering something rather than something happening in "
        "its village. Never report the same words as both. A place's `text` is the record's own words "
        "for it, `normalized_name` its usual English form, and its `quote` the words the field really "
        "holds: a rewritten name is not evidence. Name the people, places and bodies the event itself "
        f"involves, most important first, and never more than {MAX_ANSWER_ITEMS} in any list. Both "
        "lists are empty only for a record that names nothing, never because its text is in another "
        "language; token fields stay English. `role`: event_location is the most specific place the "
        "text says it happened, event_container a wider area around it, reporting_origin a dateline "
        "or filing place, mentioned_only any other - a district named in passing "
        "or as a dateline is not where it happened. district, taluk, block and village hold only levels "
        "the text names, never one you know: a district mention yields no taluk, town, "
        "village or ward. Never resolve: no coordinates, canonical place ids or evidence ids.\n"
        "10. An offset counts in Unicode characters of the field you named: `char_start` 0 is that "
        "field's first character - not bytes, not UTF-16 units, not from the start of this prompt. Always "
        "set `char_start` when you can. A repeated quote is refused unless the offset "
        "names exactly one of its copies, and an offset that is nearly right is refused as flatly as one "
        "that is far wrong: extend the span into the clause around it until it identifies the one "
        "occurrence you are reading.\n"
        "11. Record metadata is not text. Never name a field such as 'metadata' or 'record metadata'. "
        "Metadata never supports a classification, a location, an entity or a kind - "
        "a declared severity, status, district or timestamp is context the record's own text must confirm, "
        "not proof by itself. `event_time` is the one field you may answer with `value` and no `field` and "
        "no `quote`, and only when that value is the record's own stamp, which the pipeline reads from the "
        "record and keeps as its own: never quote the "
        "page's date stamp to dress that up as article evidence, and a value with no field to copy "
        "from is a guess. Navigation links, related-story listings and date stamps describe no event, "
        "support no classification, entity or location, and a stamp says when the page was written, never "
        "when something happened - though a field may open with furniture, quote it exactly, in its own "
        "language, do not read furniture as an event, and take event_time from the words that place the "
        "event, null when none do.\n"
        "12. `confidence` is how sure you are of the meaning you picked, not whether the quote exists: a "
        "verbatim span with an uncertain reading is around 0.5 and nothing is 1.0 just because it was "
        "found. Report it for every field you answer; leaving it out is recorded as unreported, "
        "never as certain.\n"
        "13. Enumerated fields take one of the listed tokens, lower case, exactly as written: no synonym, "
        "nothing invented, never prose in a one-token field. When nothing listed "
        "fits, the record supports nothing, or says two different things, null that "
        "whole field rather than picking a side. Null is recorded as unresolved and is right far more "
        "often than a "
        "guess - but an event stated in plain sentences answered with every field null is "
        "a wrong answer, so read the body again before abstaining. Abstain one field at a time: what the "
        "record supports is still answered, its kind included.\n\n"
        + answer_shape()
        + "Tokens per enumerated field:\n"
        + "\n".join(f"- {name}: {', '.join(tokens)}" for name, tokens in prompt_vocabulary().items())
        + "\n\n"
    )


PROMPT_DIGEST = hashlib.sha256(
    (prompt_text() + json.dumps(REQUEST_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def extraction_request(context: RecordContext) -> LLMRequest:
    record = context.record
    metadata = {
        "record_id": record.record_id,
        "source_id": record.source_id,
        "source_type": record.source_type,
        "record_type": record.record_type,
        "event_time": None if record.event_time is None else record.event_time.isoformat(),
        "declared_severity": record.severity,
        "declared_status": record.status,
        "district": record.district,
        "state": record.state,
        "location": record.location_raw_text,
        "language": record.language_hint,
    }
    system = (
        prompt_text()
        + "The record's own metadata, for context only, never quotable: "
        + repr(metadata)
    )
    user = (
        "These are the only fields this record offers. Report what they support.\n\n"
        + context.prompt_block()
        + "\n\nEvery `field` names one of these, exactly as printed above, and every quote is "
        "verbatim inside it: "
        + ", ".join(context.paths)
        + (
            ""
            if context.document is None
            else "\n\nThe data.page_* fields are a document's pages as read by OCR or from its text "
            "layer, and may hold misread characters: quote them exactly as printed, misreadings "
            "included, never corrected, completed or translated."
        )
        + "\n\nRespond with the JSON object only."
    )
    return LLMRequest(
        system=system,
        user=user,
        json_schema=REQUEST_SCHEMA,
        meta={"record_id": record.record_id, "prompt_version": PROMPT_VERSION},
    )


def extract_incident(
    obj: Any,
    *,
    provider: LLMProvider,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Incident:
    context = build_context(obj, text_keys=text_keys, extra_keys=extra_keys)
    output = provider.generate_structured(extraction_request(context))
    return validate_extraction(output, context)


def _bound_lists(draft: ExtractionDraft, report: _Report) -> ExtractionDraft:
    kept: dict[str, Any] = {}
    for name in ("locations", "entities", "relationships", "context_facts"):
        items = getattr(draft, name)
        if len(items) > MAX_ANSWER_ITEMS:
            kept[name] = items[:MAX_ANSWER_ITEMS]
            report.issue(
                IssueCode.PROVIDER_WARNING,
                field=name,
                detail=f"the answer named {len(items)}, which is more than one completion can "
                f"carry, so the first {MAX_ANSWER_ITEMS} were kept and the rest were not read",
            )
    return draft.model_copy(update=kept) if kept else draft


DIAGNOSIS_FIELDS = 8


def _received(value: Any) -> str:
    if isinstance(value, Mapping):
        keys = ", ".join(sorted(str(key) for key in value))
        return f"an object{{{keys}}}" if keys else "an empty object"
    if isinstance(value, list):
        return f"a list of {len(value)} item(s)"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f"a string ({value[:32]!r})"
    if isinstance(value, bool):
        return f"the boolean {value}"
    return f"a {type(value).__name__} ({value})"


def draft_diagnosis(error: ValidationError) -> str:
    said = [
        f"{'.'.join(str(part) for part in item.get('loc') or ()) or '(the whole answer)'} arrived "
        f"as {_received(item.get('input'))}: {item.get('msg')}"
        for item in error.errors(include_url=False)[:DIAGNOSIS_FIELDS]
    ]
    left = error.error_count() - len(said)
    return (
        "the answer does not match the schema it is validated against - "
        + "; ".join(said)
        + (f"; {left} further field(s) refused the same way" if left > 0 else "")
        + ". Nothing was coerced, repaired or quietly dropped, so no part of this answer becomes a "
        "fact and the record stays unread by this model."
    )


def validate_extraction(output: StructuredOutput, context: RecordContext) -> Incident:
    try:
        draft = ExtractionDraft.model_validate(output.payload)
    except ValidationError as error:
        raise InvalidExtraction(draft_diagnosis(error)) from error

    report = _Report()
    for page in () if context.document is None else context.document.pages:
        trouble = page.unreadable or page.low_quality
        if trouble:
            report.warn(f"{context.document.file_name} page {page.number}: {trouble}")
    draft = _bound_lists(draft, report)
    payload = output.payload
    for key in sorted(set(payload) & FORBIDDEN_TOP_LEVEL):
        report.issue(
            IssueCode.PROVIDER_WARNING,
            field=key,
            detail="the model supplied a field only this pipeline may set, so the record's own "
            "value was used instead",
        )
    stray = sorted(set(payload) - set(ExtractionDraft.model_fields))
    if stray:
        report.issue(
            IssueCode.PROVIDER_WARNING,
            field=None,
            detail="unrecognised top-level keys were ignored: " + ", ".join(stray),
        )
    return _assemble(draft, context, output, report)


def _confidence(
    value: OptionalConfidence, report: _Report, *, subject: str
) -> float:
    if value is None:
        report.warn(
            f"{subject} reported no confidence, so {DEFAULT_LLM_CONFIDENCE} was attributed by "
            "the pipeline"
        )
        return DEFAULT_LLM_CONFIDENCE
    return float(value)


def _notes(reason: Optional[str], reported: OptionalConfidence) -> Optional[str]:
    if reported is not None:
        return reason
    return f"{reason}; {UNREPORTED_CONFIDENCE}" if reason else UNREPORTED_CONFIDENCE


ADMIN_LEVELS: tuple[tuple[str, MentionType], ...] = (
    ("district", MentionType.DISTRICT),
    ("taluk", MentionType.TALUK),
    ("block", MentionType.BLOCK),
    ("village", MentionType.VILLAGE),
)


def _fold(text: Optional[str]) -> str:
    return " ".join(str(text or "").casefold().split())


def _stated_levels(
    item: LocationDraft,
    *,
    kind: MentionType,
    grounded: bool,
    context: RecordContext,
    subject: str,
    report: _Report,
) -> dict[str, Optional[str]]:
    texts = [_fold(field.text) for field in context.fields]
    declared = [_fold(field.text) for field in context.geography]
    own = {_fold(item.text), _fold(item.normalized_name)} - {""}
    kept: dict[str, Optional[str]] = {}
    for level, level_kind in ADMIN_LEVELS:
        value = getattr(item, level)
        folded = _fold(value)
        if not folded:
            kept[level] = None
            continue
        if (
            any(folded in text for text in texts)
            or any(folded in text for text in declared)
            or (grounded and kind is level_kind and folded in own)
        ):
            kept[level] = value
            continue
        kept[level] = None
        report.issue(
            IssueCode.UNSUPPORTED_CLAIM,
            field=f"{subject}.{level}",
            detail=f"{level} {value!r} is named nowhere in the record's text or its declared place, "
            "so that level stays unresolved rather than filled from what is known about the place",
        )
    return kept


def _raw_items(payload: Mapping[str, Any], key: str) -> list[Any]:
    items = payload.get(key)
    return list(items) if isinstance(items, list) else []


def _drop_unauthorised(
    raw: Any, subject: str, report: _Report
) -> None:
    if not isinstance(raw, Mapping):
        return
    for key in sorted(set(raw) & FORBIDDEN_ITEM_KEYS):
        report.issue(
            IssueCode.UNAUTHORISED_GIS_VALUE,
            field=subject,
            detail=f"{key} is an answer GIS gives, not one extraction may claim, so it was "
            "dropped and the item stays pending",
        )


def _verbatim_elsewhere(
    context: RecordContext, named: str, quote: str
) -> Optional[tuple[Any, Span]]:
    carriers: list[tuple[Any, Span, float]] = []
    for item in context.fields:
        if item.field == named or quote not in item.text:
            continue
        found = find_spans(item.text, quote)
        if len(found) == 1:
            carriers.append((item, found[0], len(quote) / max(len(item.text), 1)))
    if not carriers:
        return None
    carriers.sort(key=lambda hit: hit[2], reverse=True)
    if len(carriers) > 1 and carriers[1][2] == carriers[0][2]:
        return None
    return carriers[0][0], carriers[0][1]


def _word_edge(text: str, index: int) -> bool:
    if index in (0, len(text)):
        return True
    return not (text[index - 1].isalnum() and text[index].isalnum())


def _bracket_cover(
    context: RecordContext, citation: str
) -> tuple[Optional[tuple[Any, Span]], tuple[tuple[Any, Span], ...]]:
    covers: dict[tuple[str, int, int], tuple[Any, Span, int]] = {}
    for item in context.fields:
        heads: list[str] = []
        for size in range(len(citation) - BRACKET_RUN_MINIMUM, BRACKET_RUN_MINIMUM - 1, -1):
            if citation[:size] in item.text and _word_edge(citation, size):
                heads.append(citation[:size])
        heads.sort(key=len, reverse=True)
        tails: list[str] = []
        for size in range(len(citation) - BRACKET_RUN_MINIMUM, BRACKET_RUN_MINIMUM - 1, -1):
            if citation[-size:] in item.text and _word_edge(citation, len(citation) - size):
                tails.append(citation[-size:])
        tails.sort(key=len, reverse=True)
        places: dict[str, list[Span]] = {}
        for run in {*heads, *tails}:
            found = find_spans(item.text, run)
            if 0 < len(found) <= BRACKET_OCCURRENCES:
                places[run] = found
        for head in heads:
            if head not in places:
                continue
            for tail in tails:
                if tail == head or tail not in places or len(head) + len(tail) > len(citation):
                    continue
                for opening in places[head]:
                    for closing in places[tail]:
                        if closing.char_start < opening.char_end:
                            continue
                        covered = item.text[opening.char_start:closing.char_end]
                        if len(covered) > BRACKET_REACH * len(citation):
                            continue
                        found = find_spans(item.text, covered)
                        if len(found) != 1:
                            continue
                        key = (item.field, found[0].char_start, found[0].char_end)
                        score = len(head) + len(tail)
                        held = covers.get(key)
                        if held is None or score > held[2]:
                            covers[key] = (item, found[0], score)
    if not covers:
        return None, ()
    ranked = sorted(covers.values(), key=lambda hit: (-hit[2], hit[1].char_start))
    tied = tuple((hit[0], hit[1]) for hit in ranked if hit[2] == ranked[0][2])
    if len(tied) > 1:
        return None, tied
    return (ranked[0][0], ranked[0][1]), ()


def _ground(
    context: RecordContext,
    answer: Any,
    *,
    subject: str,
    report: _Report,
    confidence: OptionalConfidence,
    allow_value_as_quote: bool = False,
    inexact_citation_allowed: bool = False,
) -> _Grounded:
    field_path = getattr(answer, "field", None)
    quote = getattr(answer, "quote", None)
    if quote in (None, "") and allow_value_as_quote:
        quote = getattr(answer, "value", None)

    def finding(code: IssueCode, detail: str, reason: str) -> _Grounded:
        return _Grounded(
            reason=reason, issue=Issue(code=code, field=subject, detail=detail)
        )

    if quote in (None, ""):
        return finding(
            IssueCode.UNSUPPORTED_CLAIM,
            "a value with no quote from the record is not kept as a fact",
            "no quote was offered",
        )

    source = context.field(str(field_path or ""))
    if source is None:
        verbatim = [item.field for item in context.fields if str(quote) in item.text]
        if field_path in (None, ""):
            detail = (
                f"the answer quoted {str(quote)[:80]!r} but named no field, and a quote has to say "
                f"whose text it copies; the fields this record carries are " + ", ".join(context.paths)
            )
            reason = "no field was named for the quote"
        else:
            detail = (
                f"quoted {field_path!r}, which this record does not carry; the fields available are "
                + ", ".join(context.paths)
            )
            reason = f"field {field_path!r} is not text this record offers"
        detail += (
            f"; those words are verbatim in {', '.join(verbatim)}, which is the field to name"
            if verbatim
            else "; those words are verbatim in none of them"
        )
        return finding(IssueCode.UNKNOWN_FIELD, detail, reason)

    try:
        spans = find_spans(source.text, str(quote))
    except SpanError as error:
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the quote is unusable as a span: {error}",
            str(error),
        )
    anchored: Optional[str] = None
    if not spans:
        moved = _verbatim_elsewhere(context, source.field, str(quote))
        bracketed = False
        if moved is None and inexact_citation_allowed:
            moved, brackets = _bracket_cover(context, str(quote))
            bracketed = moved is not None
            if brackets:
                places = ", ".join(
                    f"{item.field} {span.describe()}" for item, span in brackets
                )
                return finding(
                    IssueCode.AMBIGUOUS_QUOTE,
                    f"the citation {str(quote)[:60]!r} is not verbatim and its own opening and closing "
                    f"runs bracket {places} with the same weight, so nothing in the answer says which "
                    "of these the record said and no stretch is chosen for it here",
                    "the answer's runs bracket more than one stretch of the record equally",
                )
        if moved is None:
            carriers = [item.field for item in context.fields if str(quote) in item.text]
            return finding(
                IssueCode.UNSUPPORTED_CLAIM if not carriers else IssueCode.AMBIGUOUS_QUOTE,
                f"the quote {str(quote)[:80]!r} is not verbatim in {source.field}"
                + (
                    f"; it sits only in {', '.join(carriers)}, where nothing the answer gave picks "
                    "one of its copies, so no field can be cited for it"
                    if carriers
                    else ": those words are verbatim in no field this record carries, so nothing in "
                    "the record supports them and the value is not one this record states"
                ),
                "the quote is not one field's own words",
            )
        source, span = moved
        spans = [span]
        if bracketed:
            report.warn(
                f"the citation {str(quote)[:60]!r} is not the record's own words but its opening and "
                f"closing runs are, so the evidence was anchored on the one stretch of "
                f"{source.field!r} those two runs bracket: what the answer changed between them is "
                "dropped rather than copied as though the record said it"
            )
            anchored = (
                "the answer's own opening and closing runs bracket this sentence in the record, and "
                "the words between them are the record's rather than the answer's"
            )
        else:
            report.warn(
                f"the answer cited {str(field_path)!r} for words that are verbatim only in "
                f"{source.field!r}, so the evidence was anchored in the field that really holds them"
            )

    chosen = spans[0]
    if len(spans) > 1:
        stated = getattr(answer, "char_start", None)
        matched = next((span for span in spans if span.char_start == stated), None)
        if matched is None:
            places = ", ".join(str(span.char_start) for span in spans[:12])
            if len(spans) > 12:
                places += ", ..."
            return finding(
                IssueCode.AMBIGUOUS_QUOTE,
                f"{str(quote)[:80]!r} occurs {len(spans)} times in {source.field} "
                f"(at {places}) and no char_start chose between them; "
                + (
                    f"the answer named {stated}, which is none of them"
                    if stated is not None
                    else "the answer named no char_start"
                )
                + " - cite the clause that states the act or names the actor rather than a word "
                "the article keeps repeating",
                f"the quote occurs {len(spans)} times",
            )
        chosen = matched

    reading = context.reading(source.field)
    notes = None if confidence is not None else UNREPORTED_CONFIDENCE
    if reading is not None:
        notes = "; ".join(part for part in (context.document.note(reading), notes) if part)
    try:
        evidence = source.evidence_at(
            chosen,
            method=ExtractionMethod.LLM,
            confidence=_confidence(confidence, report, subject=subject),
            modality=Modality.TEXT if reading is None else reading.modality,
            notes=notes,
        )
    except (SpanError, ValueError) as error:
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the span could not be built: {error}",
            str(error),
        )
    doubt = None if reading is None else context.document.doubt(
        reading, chosen.char_start, chosen.char_end
    )
    if doubt is not None:
        return _Grounded(
            evidence=evidence,
            review=ReviewState.REVIEW_REQUIRED,
            reason="the recognised text at this span is uncertain",
            issue=Issue(code=IssueCode.UNCERTAIN_RECOGNITION, field=subject, detail=doubt),
        )
    return _Grounded(evidence=evidence, review=ReviewState.ACCEPTED, reason=anchored)


def _enum(
    enum: Type[Enum], raw: Optional[str], *, subject: str, report: _Report
) -> Optional[Enum]:
    if raw in (None, ""):
        return None
    token = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    for member in enum:
        if member.value == token or member.name.lower() == token:
            return member
    report.issue(
        IssueCode.INVALID_VALUE,
        field=subject,
        detail=f"{raw!r} is not a {enum.__name__}, so {subject} stays unresolved rather than "
        "taking a category the taxonomy does not carry",
    )
    return None


def _timestamp(raw: Optional[str], *, subject: str, report: _Report) -> Optional[datetime]:
    if raw in (None, ""):
        return None
    candidate = str(raw).strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        report.issue(
            IssueCode.INVALID_VALUE, field=subject, detail=f"{raw!r} is not an ISO timestamp"
        )
        return None


def _is_declared_stamp(parsed: datetime, declared: Optional[datetime]) -> bool:
    if declared is None:
        return False
    if parsed == declared:
        return True
    naive = parsed.tzinfo is None and declared.tzinfo is None
    return naive and parsed.date() == declared.date()


def _stamps_publication(record: Any) -> bool:
    return str(record.source_type or "").strip().casefold() in PUBLICATION_STAMPED_SOURCES


def _is_the_capture_stamp(parsed: datetime, captured: datetime) -> bool:
    if parsed == captured:
        return True
    if parsed.tzinfo is None and captured.tzinfo is None:
        return parsed.replace(microsecond=0) == captured.replace(microsecond=0)
    return False


def _key(name: str) -> str:
    return " ".join(str(name).strip().casefold().split())


def _kept(review: ReviewState) -> bool:
    return review is ReviewState.ACCEPTED


def _value_is_in_field(context: RecordContext, path: Optional[str], value: str) -> bool:
    source = context.field(str(path or ""))
    if source is None:
        return False
    stated, held = _key(value), _key(source.text)
    if stated and stated in held:
        return True
    try:
        return float(stated.replace(",", "")) == float(held.replace(",", ""))
    except ValueError:
        return False


def _assemble(
    draft: ExtractionDraft,
    context: RecordContext,
    output: StructuredOutput,
    report: _Report,
) -> Incident:
    payload = output.payload
    record = context.record
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    values: dict[str, Any] = {}

    def publish(grounded: _Grounded) -> list[str]:
        if grounded.issue is not None and not any(
            existing == grounded.issue for existing in report.issues
        ):
            report.issues.append(grounded.issue)
        if grounded.evidence is not None and grounded.evidence.evidence_id not in {
            item.evidence_id for item in evidence
        }:
            evidence.append(grounded.evidence)
        return grounded.evidence_ids

    spans_seen: dict[tuple[str, int, int], tuple[str, str]] = {}

    def flag_shared_span(grounded: _Grounded, subject: str, kind: str) -> None:
        item = grounded.evidence
        if item is None:
            return
        key = (item.field, item.char_start, item.char_end)
        other = spans_seen.get(key)
        if other is not None and other[1] != kind:
            report.issue(
                IssueCode.PROVIDER_WARNING,
                field=subject,
                detail=f"{item.quote!r} is cited as {other[1]} by {other[0]} and as {kind} by "
                f"{subject}; the same words cannot be both, so one of them is misread",
            )
            return
        spans_seen.setdefault(key, (subject, kind))

    def own_copy(name: str) -> Optional[tuple[str, _Grounded]]:
        held = context.field(name)
        if held is None or not held.text.strip():
            return None
        return held.text, _Grounded(
            evidence=held.metadata_evidence(),
            review=ReviewState.ACCEPTED,
            reason=f"the record carries this {name} itself",
        )

    def copied_claim(name: str, own: tuple[str, _Grounded]) -> Claim:
        text, grounded = own
        return Claim(
            field=name,
            value=text,
            method=ExtractionMethod.SOURCE_METADATA,
            evidence_ids=publish(grounded),
            review=ReviewState.ACCEPTED,
            notes=f"copied from the record's own {name}, which carries it whole",
        )

    abstained_copies: dict[str, tuple[str, _Grounded]] = {}
    omitted: set[str] = set()
    for name, spec in SCALAR_FIELDS:
        answer = getattr(draft, name)
        own = own_copy(name) if name in COPIED_FIELDS else None
        if answer is None or answer.value in (None, ""):
            if own is not None:
                abstained_copies[name] = own
            continue
        resolved = answer.value if spec == "text" else _enum(
            spec, answer.value, subject=name, report=report
        )
        if resolved is None:
            grounded = _Grounded(review=ReviewState.REVIEW_REQUIRED,
                                 reason="the value named no token this taxonomy carries")
        else:
            grounded = _ground(
                context,
                answer,
                subject=name,
                report=report,
                confidence=answer.confidence,
                allow_value_as_quote=name in VERBATIM_FIELDS,
                inexact_citation_allowed=spec != "text",
            )
        if (
            own is not None
            and _kept(grounded.review)
            and grounded.evidence is not None
            and grounded.evidence.field != name
        ):
            cited = grounded.evidence.field
            grounded = _Grounded(
                review=ReviewState.REVIEW_REQUIRED,
                reason=f"the span this answer grounded sits in {cited!r}, not in {name!r}",
                issue=Issue(
                    code=IssueCode.UNSUPPORTED_CLAIM,
                    field=name,
                    detail=f"the answer built {name} from a span of {cited!r} while this record "
                    f"prints its own {name} ({own[0][:80]!r}): a headline the record carried is "
                    "copied from the field that holds it, never assembled from body text or from "
                    "the navigation and date stamps a scraped page puts in front of it, so the "
                    "answer's words were dropped and the record's own kept as the record's own",
                ),
            )
        if own is not None and not _kept(grounded.review):
            if grounded.issue is not None:
                report.issue(
                    grounded.issue.code,
                    field=grounded.issue.field,
                    detail=grounded.issue.detail,
                )
            rewrote = _key(str(answer.value)) != _key(own[0])
            report.issue(
                IssueCode.UNSUPPORTED_CLAIM,
                field=name,
                detail=f"the answer put {str(answer.value)[:80]!r} in {name}, "
                + (
                    f"which is not the {name} this record carries ({own[0][:80]!r}): a headline the "
                    "record printed is copied, never summarised, rewritten or translated, so the "
                    "answer's words were dropped and the record's own kept as the record's own"
                    if rewrote
                    else "which are this record's own words, quoted without naming the field or the "
                    f"span they sit in, so {name} is filled from the record as its own copy while "
                    "the answer itself stays reported as refused"
                ),
            )
            claims.append(copied_claim(name, own))
            values[name] = own[0]
            continue
        if (
            name in OPTIONAL_SCALARS
            and not _kept(grounded.review)
            and grounded.evidence is None
        ):
            report.warn(
                f"the answer offered {name}={str(answer.value)[:60]!r} and {grounded.reason}, so "
                "the field is left as the record leaves it instead of carrying a claim for it"
            )
            omitted.add(name)
            continue
        confidence = _confidence(answer.confidence, report, subject=name)
        claims.append(
            Claim(
                field=name,
                value=answer.value,
                method=ExtractionMethod.LLM,
                confidence=confidence,
                evidence_ids=publish(grounded),
                review=grounded.review,
                notes=_notes(grounded.reason, answer.confidence),
            )
        )
        if _kept(grounded.review):
            values[name] = resolved

    event_time: Optional[datetime] = None
    event_precision = TimePrecision.UNKNOWN
    time_from_metadata = False
    time_was_publication = False
    answer = draft.event_time
    if answer is not None and answer.value not in (None, ""):
        parsed = _timestamp(answer.value, subject="event_time", report=report)
        method = ExtractionMethod.LLM
        if parsed is None:
            grounded = _Grounded(review=ReviewState.REVIEW_REQUIRED,
                                 reason="the stamp did not parse")
        else:
            grounded = _ground(context, answer, subject="event_time", report=report,
                               confidence=answer.confidence)
            unquoted = not _kept(grounded.review)
            declared = unquoted and _is_declared_stamp(parsed, record.event_time)
            quoted_own_clock = _kept(grounded.review) and parsed == record.event_time
            if (declared or quoted_own_clock) and _stamps_publication(record):
                time_was_publication = True
                grounded = _Grounded(
                    review=ReviewState.UNRESOLVED,
                    reason="a news record's own event_time is when the page was published, kept as "
                    "provenance.declared_event_time and never as when something happened",
                )
                report.warn(
                    "event_time stays unresolved: the answer offered this news record's own stamp, "
                    "which is its publication time and is kept as provenance.declared_event_time"
                )
            elif declared:
                method = ExtractionMethod.SOURCE_METADATA
                grounded = _Grounded(
                    review=ReviewState.ACCEPTED,
                    reason="the record's own declared event_time, copied from its metadata and "
                    "carrying no span of its text",
                )
            elif _kept(grounded.review) and _is_the_capture_stamp(parsed, record.retrieved_at):
                report.issue(
                    IssueCode.PROVIDER_WARNING,
                    field="event_time",
                    detail="the answer's event moment is the instant this record was captured, so "
                    "the page's own clock was read as the time something happened; the field stays "
                    "unresolved",
                )
                grounded = _Grounded(
                    evidence=grounded.evidence,
                    review=ReviewState.REVIEW_REQUIRED,
                    reason="the stamp repeats the record's capture time, not a moment the article "
                    "evidenced",
                )
        if _kept(grounded.review):
            event_time = parsed
            time_from_metadata = method is ExtractionMethod.SOURCE_METADATA
            precision = _enum(TimePrecision, answer.precision,
                              subject="event_time_precision", report=report)
            event_precision = precision or TimePrecision.UNKNOWN
        claims.append(
            Claim(
                field="event_time",
                value=answer.value,
                method=method,
                confidence=_confidence(answer.confidence, report, subject="event_time"),
                evidence_ids=publish(grounded),
                review=grounded.review,
                notes=_notes(grounded.reason, answer.confidence),
            )
        )

    locations: list[Location] = []
    raw_locations = _raw_items(payload, "locations")
    for index, item in enumerate(draft.locations, start=1):
        subject = f"locations[loc-{index}]"
        _drop_unauthorised(
            raw_locations[index - 1] if index <= len(raw_locations) else None, subject, report
        )
        text = item.text or item.normalized_name
        if text in (None, ""):
            report.issue(
                IssueCode.UNSUPPORTED_CLAIM,
                field=subject,
                detail="a location with no text identifies no place",
            )
            continue
        grounded = _ground(context, item, subject=subject, report=report,
                           confidence=item.confidence)
        flag_shared_span(grounded, subject, "a location")
        kind = _enum(MentionType, item.location_type,
                     subject=f"{subject}.location_type", report=report) or MentionType.UNKNOWN
        levels = _stated_levels(item, kind=kind, grounded=grounded.evidence is not None,
                                context=context, subject=subject, report=report)
        normalized = item.normalized_name
        reason = grounded.reason
        cited = grounded.evidence
        if cited is not None:
            held = context.field(cited.field)
            if held is not None and _fold(text) not in _fold(held.text):
                reason = (f"the answer wrote {text!r}, which {cited.field} does not carry, so text "
                          "keeps the record's own words")
                normalized = normalized or text
                text = cited.quote
        locations.append(
            Location(
                location_id=f"loc-{index}",
                text=text,
                normalized_name=normalized,
                location_type=kind,
                role=_enum(MentionRole, item.role, subject=f"{subject}.role", report=report)
                or MentionRole.UNRESOLVED,
                method=ExtractionMethod.LLM,
                confidence=_confidence(item.confidence, report, subject=subject),
                review=grounded.review,
                evidence_ids=publish(grounded),
                notes=_notes(reason, item.confidence),
                **levels,
            )
        )

    entities: list[Entity] = []
    raw_entities = _raw_items(payload, "entities")
    for index, item in enumerate(draft.entities, start=1):
        subject = f"entities[ent-{index}]"
        _drop_unauthorised(
            raw_entities[index - 1] if index <= len(raw_entities) else None, subject, report
        )
        text = item.text or item.normalized_name
        if text in (None, ""):
            report.issue(
                IssueCode.UNSUPPORTED_CLAIM,
                field=subject,
                detail="an entity with no text names nobody",
            )
            continue
        grounded = _ground(context, item, subject=subject, report=report,
                           confidence=item.confidence)
        flag_shared_span(grounded, subject, "an entity")
        entities.append(
            Entity(
                entity_id=f"ent-{index}",
                text=text,
                normalized_name=item.normalized_name,
                entity_type=_enum(EntityType, item.entity_type,
                                  subject=f"{subject}.entity_type", report=report)
                or EntityType.UNKNOWN,
                role=_enum(ActorRole, item.role, subject=f"{subject}.role", report=report)
                or ActorRole.UNKNOWN,
                method=ExtractionMethod.LLM,
                confidence=_confidence(item.confidence, report, subject=subject),
                review=grounded.review,
                evidence_ids=publish(grounded),
                notes=_notes(grounded.reason, item.confidence),
            )
        )

    referable: dict[str, str] = {}
    for item in entities + locations:
        identifier = getattr(item, "entity_id", None) or getattr(item, "location_id", None)
        for name in (item.text, item.normalized_name):
            if name:
                referable.setdefault(_key(name), identifier)

    relationships: list[Relationship] = []
    for index, item in enumerate(draft.relationships, start=1):
        subject = f"relationships[rel-{index}]"
        from_ref = referable.get(_key(item.subject or ""))
        to_ref = referable.get(_key(item.object or ""))
        if from_ref is None or to_ref is None:
            report.issue(
                IssueCode.UNRESOLVED_REFERENCE,
                field=subject,
                detail="its subject or object names no entity or location in this answer",
            )
        if from_ref is not None and from_ref == to_ref:
            report.issue(
                IssueCode.UNRESOLVED_REFERENCE,
                field=subject,
                detail=f"{item.subject!r} and {item.object!r} are the same item, so this link "
                "joins nothing to itself and was dropped",
            )
            continue
        grounded = _ground(context, item, subject=subject, report=report,
                           confidence=item.confidence)
        relationships.append(
            Relationship(
                relationship_id=f"rel-{index}",
                kind=_enum(RelationshipKind, item.kind, subject=f"{subject}.kind",
                           report=report)
                or RelationshipKind.RELATED_TO,
                from_ref=from_ref or str(item.subject or "unresolved"),
                to_ref=to_ref or str(item.object or "unresolved"),
                description=item.description,
                method=ExtractionMethod.LLM,
                confidence=_confidence(item.confidence, report, subject=subject),
                review=(
                    grounded.review
                    if from_ref is not None and to_ref is not None
                    else ReviewState.UNRESOLVED
                ),
                evidence_ids=publish(grounded),
                notes=_notes(grounded.reason, item.confidence),
            )
        )

    facts: list[ContextFact] = []
    for index, item in enumerate(draft.context_facts, start=1):
        subject = f"context_facts[fact-{index}]"
        if item.value in (None, ""):
            report.issue(
                IssueCode.UNSUPPORTED_CLAIM,
                field=subject,
                detail="a context fact with no value preserves nothing",
            )
            continue
        grounded = _ground(context, item, subject=subject, report=report,
                           confidence=item.confidence)
        if _kept(grounded.review) and not _value_is_in_field(context, item.field, str(item.value)):
            cited = grounded.evidence.quote if grounded.evidence is not None else ""
            grounded = _Grounded(
                evidence=grounded.evidence,
                review=ReviewState.REVIEW_REQUIRED,
                reason="the value is not what the field it names holds",
                issue=Issue(
                    code=IssueCode.UNSUPPORTED_CLAIM,
                    field=subject,
                    detail=f"{item.value!r} is not a value {item.field!r} carries; the words at the "
                    f"span this fact cites are {cited!r}. A fact copies its field in the "
                    "language the field is written in, so this stays for a person to read against "
                    "the source",
                ),
            )
        facts.append(
            ContextFact(
                fact_id=f"fact-{index}",
                field=str(item.field or "unresolved"),
                value=str(item.value),
                method=ExtractionMethod.LLM,
                confidence=_confidence(item.confidence, report, subject=subject),
                review=grounded.review,
                evidence_ids=publish(grounded),
                notes=_notes(grounded.reason, item.confidence),
            )
        )

    grounded_any = any(claim.review is ReviewState.ACCEPTED for claim in claims) or any(
        item.review is ReviewState.ACCEPTED for item in locations + entities + facts
    )
    # the record's own headline is published only beside something the reading grounded: the contract
    # refuses an accepted claim in an `unresolved` document, so a record nobody read stays unread
    if grounded_any:
        for name, held in abstained_copies.items():
            claims.append(copied_claim(name, held))
            values[name] = held[0]
    open_claims = any(claim.review is ReviewState.REVIEW_REQUIRED for claim in claims)
    open_items = any(
        item.review is not ReviewState.ACCEPTED
        for item in locations + entities + relationships + facts
    )

    declared_kind = _enum(RecordKind, draft.record_kind, subject="record_kind", report=report)
    declared_context = _enum(ContextType, draft.context_type, subject="context_type", report=report)
    typed = values.get("incident_type") not in (None, EventType.UNRESOLVED)
    account = values.get("description") not in (None, "")
    happened = values.get("event_status") not in (None, EventStatus.UNKNOWN)
    event_established = typed or (account and (
        declared_kind is RecordKind.INCIDENT or happened
    ))
    context_type = None
    if event_established:
        record_kind = RecordKind.INCIDENT
        labels = [
            token
            for token in (
                declared_kind.value
                if declared_kind in (RecordKind.FORECAST, RecordKind.CONTEXT)
                else None,
                declared_context.value if declared_context is not None else None,
            )
            if token
        ]
        if labels:
            report.warn(
                "the answer filed this as " + " and ".join(labels) + " while one of its own "
                "quotes established "
                + (
                    values["incident_type"].value
                    if typed
                    else "an event and an account of it"
                )
                + ": an evidenced event is read as an incident and the context label is dropped"
            )
    elif declared_kind is RecordKind.INCIDENT:
        record_kind = (
            RecordKind.CONTEXT if declared_context is not None else RecordKind.UNRESOLVED
        )
        context_type = declared_context if record_kind is RecordKind.CONTEXT else None
        report.issue(
            IssueCode.PROVIDER_WARNING,
            field="record_kind",
            detail="the answer called this an incident while no quote evidenced an event or an "
            "account of one, so the record is not filed as it",
        )
    elif declared_kind in (RecordKind.FORECAST, RecordKind.CONTEXT):
        record_kind = declared_kind
        context_type = declared_context
    elif declared_context is not None:
        record_kind = RecordKind.CONTEXT
        context_type = declared_context
    else:
        record_kind = RecordKind.UNRESOLVED
        if any(fact.review is ReviewState.ACCEPTED for fact in facts):
            record_kind = RecordKind.CONTEXT
            report.warn(
                "the answer named no record_kind, so the record is filed as context on the strength "
                "of the field values it quoted and of no event being evidenced; no context_type "
                "was named for it"
            )
        elif grounded_any:
            report.warn(
                "the answer named no record_kind, so the record is filed as unresolved even though "
                "fields were grounded"
            )

    if record_kind in (RecordKind.FORECAST, RecordKind.CONTEXT):
        for name in EVENT_ONLY_FIELDS:
            taken = values.get(name)
            # the same absence tokens Incident._value_is_open reads
            if taken is None or taken.value in ("unresolved", "unknown"):
                continue
            values.pop(name)
            for position, claim in enumerate(claims):
                if claim.field == name:
                    claims[position] = claim.model_copy(
                        update={"review": ReviewState.REVIEW_REQUIRED}
                    )
            report.issue(
                IssueCode.UNSUPPORTED_CLAIM,
                field=name,
                detail=f"the answer put {taken.value!r} in {name} on a record it filed as "
                f"{record_kind.value}: {name} answers a question about a happening and this record "
                "states no happening, so the value was dropped, the field keeps its absence value "
                "and context_type with context_facts carry what the record actually says",
            )

    if record_kind is RecordKind.INCIDENT:
        if not typed and not any(
            issue.field == "incident_type" for issue in report.issues
        ):
            report.issue(
                IssueCode.PROVIDER_WARNING,
                field="incident_type",
                detail="the record was read as an incident on the strength of its own account of "
                "what happened while no incident_type token was evidenced, so the kind stands and "
                "the type is left for a person",
            )
        if values.get("description") is None and not any(
            issue.field == "description" for issue in report.issues
        ):
            report.issue(
                IssueCode.PROVIDER_WARNING,
                field="description",
                detail="the record was read as an incident but no account of it was grounded in "
                "the record's own text, so description stays unresolved for a person",
            )
        if values.get("event_status") is None and not any(
            issue.field == "event_status" for issue in report.issues
        ):
            report.warn(
                "an incident whose text grounded no event_status is filed as unknown, which is "
                "this pipeline saying the source never said whether it is still happening"
            )
        if event_time is None and not time_was_publication and not any(
            issue.field == "event_time" for issue in report.issues
        ):
            report.warn(
                "no span and no stamp of the record's own placed this event on a clock, so "
                "event_time stays unresolved instead of borrowing the retrieval time"
            )
        elif time_from_metadata:
            report.warn(
                "event_time is the timestamp this record carries as its own, copied from its "
                "metadata with no span of its text cited: it is the record's clock, not a moment "
                "the article evidenced, and for a news page that clock is usually when the page "
                "was written"
            )
        if values.get("priority") is None and "priority" not in omitted and not any(
            issue.field == "priority" for issue in report.issues
        ):
            report.warn(
                "nothing in the record said how soon the district must act, so priority is left "
                "null rather than copied from the severity"
            )

    if report.issues or open_claims or open_items:
        state = ReviewState.REVIEW_REQUIRED if grounded_any else ReviewState.UNRESOLVED
    elif grounded_any:
        state = ReviewState.ACCEPTED
    else:
        state = ReviewState.UNRESOLVED

    geography, geography_evidence = source_geography(context.geography)
    known = {item.evidence_id for item in evidence}
    evidence.extend(item for item in geography_evidence if item.evidence_id not in known)
    if not draft.locations and source_locations(geography):
        report.warn(
            "the reader named no place so locations is empty: the places this record declared "
            "about itself are carried under source_geography, and a feed's own district tag is "
            "not a place the account evidenced"
        )

    generation = Generation(
        provider=str(getattr(output.response, "provider", PROVIDER) or PROVIDER),
        model=str(output.response.model or "unknown"),
        prompt_version=PROMPT_VERSION,
        input_hash=context.context_hash(),
        generated_at=datetime.now(),
        latency_ms=output.response.latency_ms,
        warnings=list(report.warnings),
    )

    return Incident(
        schema_version=SCHEMA_VERSION,
        incident_id=f"INC-{record.record_id}",
        provenance=context.provenance(),
        record_kind=record_kind,
        context_type=context_type,
        title=values.get("title"),
        description=values.get("description"),
        incident_type=values.get("incident_type") or EventType.UNRESOLVED,
        category=values.get("category") or IncidentCategory.UNRESOLVED,
        department=values.get("department") or Department.UNRESOLVED,
        severity=values.get("severity") or SeverityLevel.UNRESOLVED,
        priority=values.get("priority"),
        event_status=values.get("event_status") or EventStatus.UNKNOWN,
        event_time=event_time,
        event_time_precision=event_precision,
        locations=locations,
        entities=entities,
        relationships=relationships,
        context_facts=facts,
        source_geography=geography,
        evidence=evidence,
        claims=claims,
        validation=Validation(state=state, issues=list(report.issues), checked_at=datetime.now()),
        generation=generation,
    )
