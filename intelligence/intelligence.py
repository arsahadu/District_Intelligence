"""Stage 1 orchestration: one record in, one call, one validated Incident out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Type, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intelligence.context import RecordContext, build_context
from intelligence.contract import (
    Claim,
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
    Relationship,
    RelationshipKind,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.extraction.spans import SpanError, find_spans
from intelligence.llm import LLMProvider, LLMRequest, StructuredOutput, json_schema_for
from intelligence.mapping.record_input import CONTENT_PATH
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

PROVIDER = "intelligence.intelligence"
PROMPT_VERSION = "stage-2.2"

#: A model that reports no confidence is answering anyway, so the number is attributed to us.
DEFAULT_LLM_CONFIDENCE = 0.5

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
    """What the model answers with. Unknown keys are kept as findings, never as facts."""

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

    locations: list[LocationDraft] = Field(default_factory=list)
    entities: list[EntityDraft] = Field(default_factory=list)
    relationships: list[RelationshipDraft] = Field(default_factory=list)


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
    system = (
        "You read one record about events in a Tamil Nadu district and report only what that record "
        "supports. Answer with one JSON object in exactly the shape of the schema you are given.\n\n"
        "Rules:\n"
        "1. title, description, incident_type, category, department, severity, priority, "
        "event_status and event_time are each an OBJECT with the keys their schema names - never a "
        "bare string. Sentence text never goes into incident_type, category, department, severity, "
        "priority or event_status: those carry one token in `value`.\n"
        "2. Quote only from the fields printed below, and name one of them exactly as printed in "
        "`field`. `quote` is an exact substring of that field, copied character for character - "
        "never paraphrased, never translated, never reconstructed from memory of the meaning. Keep "
        "it short: the fewest characters that still support the value, not the sentence around it. "
        "A field another kind of source usually carries does not exist here unless it is printed.\n"
        "3. Offsets count from the start of the named field. Always set `char_start`: this pipeline "
        "refuses a quote that occurs more than once and it will not choose an occurrence for you.\n"
        "4. Record metadata is not text. Never name a field such as 'metadata', 'record metadata' "
        "or 'source metadata'. When event_time is the record's own stamp, answer `value` with no "
        "`field` and no `quote` - the pipeline reads it from the record.\n"
        "5. When the record supports nothing for a field, answer null for that whole field. Null is "
        "recorded as unresolved and is the right answer far more often than a guess. `locations`, "
        "`entities` and `relationships` are empty lists when the record names none.\n"
        "6. Not every record is an incident. A forecast, a price line or an announcement is often a "
        "contextual observation, and a moderate rain or a routine price needs no response from "
        "anyone. incident_type, severity, priority, event_status and department stay null unless the "
        "record's own text supports them: source_type and record_type say what a record is, never "
        "how serious it is.\n"
        "7. Enumerated fields take one of the listed tokens, lower case, exactly as written. There "
        "is no synonym: when none of the tokens fits, leave that value out.\n"
        "8. Do not resolve anything. Coordinates, canonical place ids and evidence ids are not part "
        "of this schema; a location stays pending GIS.\n"
        "9. A field may open with page navigation or a timestamp before its real text. It is still "
        "the record's own text - quote it exactly, in its own language, and do not read furniture "
        "as an event.\n\n"
        "Tokens per enumerated field:\n"
        + "\n".join(f"- {name}: {', '.join(tokens)}" for name, tokens in vocabulary.items())
        + "\n\nThe record's own metadata, for context only, never quotable: "
        + repr(metadata)
    )
    user = (
        "These are the only fields this record offers. Report what they support.\n\n"
        + context.prompt_block()
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


def validate_extraction(output: StructuredOutput, context: RecordContext) -> Incident:
    """Check the answer field by field. Nothing the record cannot repeat stays a fact."""
    try:
        draft = ExtractionDraft.model_validate(output.payload)
    except ValidationError as error:
        raise InvalidExtraction(str(error)) from error

    report = _Report()
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
        return finding(
            IssueCode.SPAN_MISMATCH,
            f"the quote {str(quote)[:80]!r} is not verbatim in {source.field}",
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
                notes=grounded.reason,
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
                notes=grounded.reason,
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
                notes=grounded.reason,
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
                notes=grounded.reason,
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
                notes=grounded.reason,
            )
        )

    grounded_any = any(claim.review is ReviewState.ACCEPTED for claim in claims) or any(
        item.review is ReviewState.ACCEPTED for item in locations + entities
    )
    open_claims = any(claim.review is ReviewState.REVIEW_REQUIRED for claim in claims)
    open_items = any(
        item.review is not ReviewState.ACCEPTED
        for item in locations + entities + relationships
    )
    if report.issues or open_claims or open_items:
        state = ReviewState.REVIEW_REQUIRED if grounded_any else ReviewState.UNRESOLVED
    elif grounded_any:
        state = ReviewState.ACCEPTED
    else:
        state = ReviewState.UNRESOLVED

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
        evidence=evidence,
        claims=claims,
        validation=Validation(state=state, issues=list(report.issues), checked_at=datetime.now()),
        generation=generation,
    )
