"""One record in, one provider call, one validated Incident out."""

from __future__ import annotations

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
from intelligence.geography import source_geography
from intelligence.llm import LLMProvider, LLMRequest, StructuredOutput, json_schema_for
from intelligence.models.base import OptionalConfidence
from intelligence.models.enums import (
    ActorRole,
    Department,
    EventType,
    ExtractionMethod,
    MentionRole,
    MentionType,
    SeverityLevel,
    TimePrecision,
)
from intelligence.models.evidence import Evidence
from intelligence.records import CONTENT_PATH
from intelligence.spans import SpanError, find_spans

PROVIDER = "intelligence.intelligence"
PROMPT_VERSION = "stage-2.8"

#: Strict mode makes every list item a full object, so "name every place and person" is an unbounded
#: answer that overruns one completion. The ceiling is stated in the prompt and the first items kept.
MAX_ANSWER_ITEMS = 8

#: A model that reports no confidence is answering anyway, so the number is attributed to us.
DEFAULT_LLM_CONFIDENCE = 0.5

#: Said on the field itself, so an attributed number is never read as the model's own.
UNREPORTED_CONFIDENCE = (
    f"confidence was not reported, so {DEFAULT_LLM_CONFIDENCE} is the pipeline's own attribution"
)

#: Only free text the record itself carries may be grounded by echoing the value back.
VERBATIM_FIELDS = ("title", "description")

#: A model cannot resolve GIS and cannot mint evidence ids. Anything trying to is dropped loudly.
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
    """The answer is not shaped as this schema requires, so no Incident is built from it."""


class _Wire(BaseModel):
    """What the model answers with. Unknown keys become findings about the answer, never claims."""

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

    #: Two bare tokens, not objects: what kind of statement the record makes, and if it states no
    #: event, the shape of the information it does carry.
    record_kind: Optional[str] = None
    context_type: Optional[str] = None

    locations: list[LocationDraft] = Field(default_factory=list)
    entities: list[EntityDraft] = Field(default_factory=list)
    relationships: list[RelationshipDraft] = Field(default_factory=list)
    context_facts: list[ContextFactDraft] = Field(default_factory=list)


REQUEST_SCHEMA = json_schema_for(ExtractionDraft)

#: name -> the type the answer must land on, or "text" for a field quoted from the record.
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

#: The enum-valued keys of the item lists, listed for the model the same way the scalars are.
ITEM_FIELDS: tuple[tuple[str, Type[Enum]], ...] = (
    ("locations[].location_type", MentionType),
    ("locations[].role", MentionRole),
    ("entities[].entity_type", EntityType),
    ("entities[].role", ActorRole),
    ("relationships[].kind", RelationshipKind),
)


@dataclass(frozen=True)
class _Grounded:
    """One quote's fate: the evidence it produced, or the reason it did not."""

    evidence: Optional[Evidence] = None
    review: ReviewState = ReviewState.UNRESOLVED
    reason: Optional[str] = None
    issue: Optional[Issue] = None

    @property
    def evidence_ids(self) -> list[str]:
        return [] if self.evidence is None else [self.evidence.evidence_id]


class _Report:
    """Findings collected while the answer is checked, then handed to the contract."""

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


def extraction_request(context: RecordContext) -> LLMRequest:
    """One prompt, one call. No chains, no per-domain cues, no per-source branching."""
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
    vocabulary = {
        name: [member.value for member in spec]
        for name, spec in SCALAR_FIELDS
        if spec != "text"
    }
    vocabulary.update((name, [member.value for member in spec]) for name, spec in ITEM_FIELDS)
    vocabulary["event_time_precision"] = [member.value for member in TimePrecision]
    vocabulary["record_kind"] = [member.value for member in RecordKind]
    vocabulary["context_type"] = [member.value for member in ContextType]
    system = (
        "You read one record about events in a Tamil Nadu district and report only what that record "
        "supports. Answer with one JSON object in exactly the shape of the schema you are given.\n\n"
        "Rules:\n"
        "1. title, description, incident_type, category, department, severity, priority, "
        "event_status and event_time are each an OBJECT with the keys their schema names - never a "
        "bare string. Sentence text never goes into incident_type, category, department, severity, "
        "priority or event_status: those carry one token in `value`. Only record_kind and "
        "context_type are bare tokens.\n"
        "2. Every quote names the field whose text it copies, and `quote` is an exact substring of "
        "that field - never paraphrased, never translated, never reconstructed from memory of the "
        "meaning. Keep it short: the fewest characters that still support the value, not the "
        "sentence around it. `title` and the body are different fields: words read in the body are "
        "quoted from the body, so never cite the title for what the body says and never cite the "
        "body for words only the title carries. The pipeline re-reads the field you name and "
        "refuses words that are not in it. A field another kind of source usually carries does not "
        "exist here unless it is printed.\n"
        "3. A quote proves what happened; it does not have to contain the word for it. For "
        "incident_type, category, department, severity, priority and event_status the `value` is "
        "this taxonomy's name for an event and the `quote` is the record's own words for it, so "
        "they rarely match literally - that is a good answer, not a reason to abstain. Choose the "
        "token from what the record says happened, to whom, where and in what condition, and take "
        "the most specific token the evidence supports: `other` is for an event the record states "
        "plainly that the list has no name for, not a way out of reading the list. Read the three "
        "classification fields as three different questions about one event: incident_type is the "
        "act itself, category is the district's area of work the act falls in, and department is "
        "whose job it is - the authority the record names as acting or responsible, or the subject "
        "the event belongs to when it names none. One body sentence may carry all three and one "
        "quote may support all three. What stays null is a token with no event behind it: a topic, "
        "a heading, an institution's routine work or your own expectation of such records.\n"
        "4. severity, priority and event_status are three different reads of one event. severity is "
        "the harm or scale the text states - lives lost or threatened, injuries, how many people, "
        "fields or shops are affected, how long power, water or a road is out, the size of a loss, "
        "a warning level the record prints. Answer the band the evidence carries and no higher; a "
        "subject being police, fraud or disaster carries no band. Where the record states no harm, "
        "scale, loss or printed level, severity stays unresolved: `info` is for a record that "
        "itself presents the matter as informational, not a softer guess. priority is how soon the "
        "district must act, read from urgency the text shows - an active danger, a response under "
        "way, water or heat still rising, a date it names - related, never copied, and null when "
        "nothing says the clock matters; a settled disaster may be low or absent and a small urgent "
        "thing may be high. event_status is what the source says about now: ongoing while it is "
        "still happening, under_investigation while someone is enquiring, examining, searching into "
        "it or has filed a case about it, action_taken once an authority has done something about "
        "it - repaired, relieved, served notice, paid - resolved once the record says it is "
        "settled, closed or over, reported when the text only establishes that it happened, unknown "
        "only when the record truly says nothing about now. Take status from those words and from "
        "nothing else: not from the feed that carried the record, and not from the kind of event - a "
        "crime described is not by itself an investigation, and an authority mentioned is not by "
        "itself action taken. An event whose own sentence speaks of the present is not unknown. A "
        "record that states no happening has no status to report: leave the field null rather than "
        "answering `unknown` for it.\n"
        "5. Not every record is an incident, and the kind is the first read of a record, not the "
        "verdict on the last. Ask one question before any label: does this record state that "
        "something happened - that an actor did something, that something was done to someone or "
        "to something, or that a state changed? An incident says something happened and mattered, "
        "and a happening stays a happening whoever wrote about it: an investigation opened, a "
        "search carried out, material seized, a case registered, relief delivered, an order "
        "issued, a transfer made or a ceremony held is an act of the administration and an event "
        "at once. A forecast says something may happen. A price line, a temperature, a warning "
        "level, a station id, an appeal, a profile or a listing of what is scheduled reports a "
        "state or informs without stating anything that took place. Answer record_kind as incident "
        "for the first kind, forecast for the second and context for the rest, with context_type "
        "naming what the record does carry. The kind is settled by the event, not by the fields "
        "that follow it: source_type and record_type say what a record is, never how serious it "
        "is, and can never be the reason for an incident. So never file a happening as context "
        "because no taxonomy token fits it or because it arrived in an ordinary news feed, and "
        "never leave an event's own fields blank because you called the record context - answer "
        "every field the evidence carries and let only the rest stay null, because one unreadable "
        "field does not undo an evidenced happening. Nor does the label you print prove anything: "
        "the happening must be quoted, and a record whose content is values stays context with "
        "those values kept. A record you call forecast or context therefore leaves description, "
        "incident_type, category, department, severity, priority and event_status null: a price or "
        "a temperature written into an account is still a state, not a happening. For such a "
        "record answer `context_facts`: each item names one of this record's own field paths in "
        "`field`, gives that field's `value` exactly as the field holds it, and quotes the same "
        "field verbatim in `quote`. Two to eight, the most informative first. A fact is one data "
        "point a field holds - a price, a count, a level, a name, an id - never a headline and "
        "never the body's prose: prose is an account or nothing. A fact copies and never "
        "interprets: no restating, converting, rounding, translating, no unit the field does not "
        "carry, and never a field the record does not print. An incident may carry facts too for "
        "figures its event fields do not hold, but its event fields are answered first.\n"
        "6. Read the event from the body, not from the headline: the sentence saying what happened "
        "is in the body text, it is the evidence, and one span may support several fields. "
        "`description` is the concise factual account of that event - the record's own sentence, or "
        "a shorter restatement of it - and its `quote` names the field carrying the words it "
        "restates. For a record you called an incident it is almost always answerable: a body that "
        "states what happened states enough to summarise. Never pad it, never restate the headline "
        "alone, never add a detail the text does not carry, and never write an account you cannot "
        "quote for: that field stays null and the finding stands.\n"
        "7. A name belongs to one list, decided by the words around it, not by its sound: where "
        "something happened, or the area it happened in, is a `locations` item; a person, an "
        "official, a department, a committee, a party, a school, a hospital or a market named as an "
        "actor is an `entities` item, and a place becomes an entity only when it acts as a party - "
        "the panchayat ordered something, not that something happened in the panchayat's village. "
        "Never report the same words as both. A place's `text` or `normalized_name` may be the "
        "cleaned or usual form of a name, but its `quote` must be the words the field really holds: "
        "a rewritten name is not evidence. Name the people, places and bodies the event itself "
        f"involves, the most important first, and never more than {MAX_ANSWER_ITEMS} in any list - "
        "a longer list cannot be answered inside one completion. `locations` and `entities` are "
        "empty only for a record that names nothing, never because its text is in another language: "
        "a place's `text` keeps the record's language while its token fields stay English.\n"
        "8. Offsets count from the start of the field you named, not from the start of this prompt. "
        "Always set `char_start`, even when you believe the words appear once: this pipeline refuses "
        "a quote that occurs more than once and it will not choose an occurrence for you.\n"
        "9. Record metadata is not text. Never name a field such as 'metadata', 'record metadata' "
        "or 'source metadata'. Metadata never supports a classification, a location, an entity or a "
        "kind: a declared severity, status, district or timestamp in the block printed below is "
        "context that the record's own text must confirm, not proof by itself. When event_time is "
        "the record's own stamp, answer `value` with no `field` and no `quote` - the pipeline reads "
        "it from the record.\n"
        "10. `confidence` is how sure you are of the meaning you picked, not whether the quote "
        "exists: a verbatim span with an uncertain reading is around 0.5 and nothing is 1.0 just "
        "because it was found in the text. Report it for every field you answer; leaving it out is "
        "recorded as unreported, never as certain.\n"
        "11. When the record supports nothing for a field, answer null for that whole field, and "
        "`locations`, `entities`, `relationships` and `context_facts` are empty lists when the "
        "record names or states none. "
        "Null is recorded as unresolved and is the right answer far more often than a guess - but an "
        "event the record states in plain sentences answered with every field null is a wrong "
        "answer, so read the body again before abstaining. Abstain one field at a time: what the "
        "record supports is still answered, its kind included.\n"
        "12. Enumerated fields take one of the listed tokens, lower case, exactly as written: no "
        "synonym, nothing invented, and when nothing listed fits, answer null.\n"
        "13. Do not resolve anything. Coordinates, canonical place ids and evidence ids are not "
        "part of this schema; a location stays pending GIS.\n"
        "14. A field may open with page navigation or a timestamp before its real text. It is "
        "still the record's own text - quote it exactly, in its own language, and do not read "
        "furniture as an event. Navigation links, related-story listings and date stamps describe "
        "no event and support no classification, entity or location.\n\n"
        "Tokens per enumerated field:\n"
        + "\n".join(f"- {name}: {', '.join(tokens)}" for name, tokens in vocabulary.items())
        + "\n\nThe record's own metadata, for context only, never quotable: "
        + repr(metadata)
    )
    user = (
        "These are the only fields this record offers. Report what they support.\n\n"
        + context.prompt_block()
        + "\n\nEvery `field` names one of these, exactly as printed above, and every quote is "
        "verbatim inside it: "
        + ", ".join(context.paths)
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
    """One record, one call, one Incident. Provider failures propagate untouched."""
    context = build_context(obj, text_keys=text_keys, extra_keys=extra_keys)
    output = provider.generate_structured(extraction_request(context))
    return validate_extraction(output, context)


def _bound_lists(draft: ExtractionDraft, report: _Report) -> ExtractionDraft:
    """Keep the items one completion can carry, and say plainly which were dropped."""
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


def validate_extraction(output: StructuredOutput, context: RecordContext) -> Incident:
    """Check the answer field by field. Nothing the record cannot repeat stays a fact."""
    try:
        draft = ExtractionDraft.model_validate(output.payload)
    except ValidationError as error:
        raise InvalidExtraction(str(error)) from error

    report = _Report()
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
    """Keep the reason a field was refused next to the fact that its number was not the model's."""
    if reported is not None:
        return reason
    return f"{reason}; {UNREPORTED_CONFIDENCE}" if reason else UNREPORTED_CONFIDENCE


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


def _ground(
    context: RecordContext,
    answer: Any,
    *,
    subject: str,
    report: _Report,
    confidence: OptionalConfidence,
    allow_value_as_quote: bool = False,
) -> _Grounded:
    """Replay a quote against the field it names. Only a verified span becomes evidence."""
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
        return finding(
            IssueCode.UNKNOWN_FIELD,
            f"quoted {field_path!r}, which this record does not carry; the fields available are "
            + ", ".join(context.paths),
            f"field {field_path!r} is not text this record offers",
        )

    try:
        spans = find_spans(source.text, str(quote))
    except SpanError as error:
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the quote is unusable as a span: {error}",
            str(error),
        )
    if not spans:
        elsewhere = [item.field for item in context.fields if str(quote) in item.text]
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the quote {str(quote)[:80]!r} is not verbatim in {source.field}"
            + (
                f"; it is verbatim in {', '.join(elsewhere)}, which is the field a re-run should name"
                if elsewhere
                else ""
            ),
            "the quote does not occur in that field",
        )

    chosen = spans[0]
    if len(spans) > 1:
        stated = getattr(answer, "char_start", None)
        matched = next((span for span in spans if span.char_start == stated), None)
        if matched is None:
            return finding(
                IssueCode.AMBIGUOUS_QUOTE,
                f"{str(quote)[:80]!r} occurs {len(spans)} times in {source.field} and no "
                "char_start chose between them",
                f"the quote occurs {len(spans)} times",
            )
        chosen = matched

    try:
        evidence = source.evidence_at(
            chosen,
            method=ExtractionMethod.LLM,
            confidence=_confidence(confidence, report, subject=subject),
            notes=None if confidence is not None else UNREPORTED_CONFIDENCE,
        )
    except (SpanError, ValueError) as error:
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the span could not be built: {error}",
            str(error),
        )
    return _Grounded(evidence=evidence, review=ReviewState.ACCEPTED)


def _enum(
    enum: Type[Enum], raw: Optional[str], *, subject: str, report: _Report
) -> Optional[Enum]:
    """Read one controlled token. A value outside the taxonomy is a finding, never a coercion."""
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
    """True when the answer repeats the timestamp the record already carried, not a read span."""
    if declared is None:
        return False
    if parsed == declared:
        return True
    naive = parsed.tzinfo is None and declared.tzinfo is None
    return naive and parsed.date() == declared.date()


def _key(name: str) -> str:
    return " ".join(str(name).strip().casefold().split())


def _kept(review: ReviewState) -> bool:
    return review is ReviewState.ACCEPTED


def _value_is_in_field(context: RecordContext, path: Optional[str], value: str) -> bool:
    """A fact's value must be what the field it names really holds, or that field's own number."""
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
        """One span cannot be both a place and a party, whatever the answer put it in."""
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

    for name, spec in SCALAR_FIELDS:
        answer = getattr(draft, name)
        if answer is None or answer.value in (None, ""):
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
            )
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
            if not _kept(grounded.review) and _is_declared_stamp(parsed, record.event_time):
                method = ExtractionMethod.SOURCE_METADATA
                grounded = _Grounded(review=ReviewState.ACCEPTED,
                                     reason="copied from the record's own event_time field")
        if _kept(grounded.review):
            event_time = parsed
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
        locations.append(
            Location(
                location_id=f"loc-{index}",
                text=text,
                normalized_name=item.normalized_name,
                location_type=_enum(MentionType, item.location_type,
                                    subject=f"{subject}.location_type", report=report)
                or MentionType.UNKNOWN,
                role=_enum(MentionRole, item.role, subject=f"{subject}.role", report=report)
                or MentionRole.UNRESOLVED,
                district=item.district,
                taluk=item.taluk,
                block=item.block,
                village=item.village,
                method=ExtractionMethod.LLM,
                confidence=_confidence(item.confidence, report, subject=subject),
                review=grounded.review,
                evidence_ids=publish(grounded),
                notes=_notes(grounded.reason, item.confidence),
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
            grounded = _Grounded(
                evidence=grounded.evidence,
                review=ReviewState.REVIEW_REQUIRED,
                reason="the value is not what the field it names holds",
                issue=Issue(
                    code=IssueCode.UNSUPPORTED_CLAIM,
                    field=subject,
                    detail=f"{item.value!r} is not a value {item.field!r} carries: a fact copies "
                    "its field, so this stays for a person to read against the source",
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
    open_claims = any(claim.review is ReviewState.REVIEW_REQUIRED for claim in claims)
    open_items = any(
        item.review is not ReviewState.ACCEPTED
        for item in locations + entities + relationships + facts
    )

    # Kind comes from evidence, never from the model's self-assertion: an evidenced event makes an
    # incident, a bare declaration manufactures none, and quoted field values never demote a happening.
    declared_kind = _enum(RecordKind, draft.record_kind, subject="record_kind", report=report)
    declared_context = _enum(ContextType, draft.context_type, subject="context_type", report=report)
    typed = values.get("incident_type") not in (None, EventType.UNRESOLVED)
    # A happening is read from the answer's own `incident` label or from a status only an event can
    # carry. Either way the quote had to replay, so a label manufactures nothing and values decide none.
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
        # The answer named an event no quote established. It is not filed as one, and the only
        # reading left is the context the same answer offered for it.
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
        # A record whose evidenced content is quoted field values, with no event evidenced anywhere,
        # is context whatever feed carried it; that is read from the evidence, not from source_type.
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

    # A record read as an event owes the dashboard the event: an incident nobody can describe is a
    # half answer, so the missing account is said out loud instead of passing as accepted.
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
        # A null the dashboard reads is said out loud too: both of these stay independent of the
        # bands above them and stay unresolved rather than being filled from what the feed stamped.
        if values.get("event_time") is None and not any(
            issue.field == "event_time" for issue in report.issues
        ):
            report.warn(
                "no span and no stamp of the record's own placed this event on a clock, so "
                "event_time stays unresolved instead of borrowing the retrieval time"
            )
        if values.get("priority") is None and not any(
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

    # The record's own geography reaches the GIS boundary whatever the answer said: model mentions
    # stay pending resolution, source values are kept beside them under the paths that carried them.
    geography, geography_evidence = source_geography(context.geography)
    known = {item.evidence_id for item in evidence}
    evidence.extend(item for item in geography_evidence if item.evidence_id not in known)

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
