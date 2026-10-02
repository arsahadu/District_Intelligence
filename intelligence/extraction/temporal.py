"""Temporal roles: which moment a field establishes, and which one it merely printed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from intelligence.extraction import boilerplate
from intelligence.extraction import time_expressions as surfaces
from intelligence.extraction.normalize import MappedText
from intelligence.extraction.spans import SourceField, Span, build_evidence_at, build_metadata_evidence
from intelligence.extraction.time_expressions import Expression, Resolution
from intelligence.models.base import OptionalConfidence
from intelligence.models.enums import ExtractionMethod, TimePrecision, TimeQualifier, TimeSemantics
from intelligence.models.evidence import Evidence
from intelligence.models.temporal import TimeValue

PROVIDER = "intelligence.extraction.temporal"

CONFIDENCE_DATE_CLOCK = 0.95
CONFIDENCE_DATE = 0.9
CONFIDENCE_MONTH = 0.85
CONFIDENCE_RELATIVE = 0.8
CONFIDENCE_AMBIGUOUS = 0.6
CONFIDENCE_METADATA = 1.0

#: The only semantics a publish stamp is ever allowed to carry.
STAMP_SEMANTICS = TimeSemantics.PUBLICATION_TIME

NO_EVENT_TIME = "no event time was established from the text"
STAMP_ONLY = "the field carries a publication stamp but no event time"


class TemporalError(ValueError):
    """A field could not be read for time because its inputs disagreed."""


def _join(parts: Iterable[object]) -> Optional[str]:
    text = "; ".join(str(part) for part in parts if part)
    return text or None


def _method_for(expression: Expression) -> ExtractionMethod:
    return ExtractionMethod.RULE if expression.is_relative else ExtractionMethod.REGEX


def _confidence_for(expression: Expression, resolution: Resolution) -> OptionalConfidence:
    if not resolution.resolved:
        return None
    if expression.ambiguous:
        return CONFIDENCE_AMBIGUOUS
    if expression.has_clock:
        return CONFIDENCE_DATE_CLOCK
    if expression.is_relative:
        return CONFIDENCE_RELATIVE
    if expression.kind in (surfaces.MONTH_YEAR, surfaces.YEAR_ONLY):
        return CONFIDENCE_MONTH
    return CONFIDENCE_DATE


@dataclass(frozen=True)
class TemporalMention:
    """One temporal surface, cited in the original field, plus what it can be read as."""

    expression: Expression
    resolution: Resolution
    semantics: TimeSemantics
    span: Span
    evidence: Evidence
    timezone: Optional[str] = None
    endpoints: tuple["TemporalMention", ...] = ()

    @property
    def kind(self) -> str:
        return self.expression.kind

    @property
    def rule(self) -> str:
        return self.expression.rule

    @property
    def raw_text(self) -> str:
        return self.span.quote

    @property
    def char_start(self) -> int:
        return self.span.char_start

    @property
    def char_end(self) -> int:
        return self.span.char_end

    @property
    def precision(self) -> TimePrecision:
        return self.resolution.precision

    @property
    def qualifier(self) -> TimeQualifier:
        return self.resolution.qualifier

    @property
    def value(self) -> Optional[datetime]:
        return self.resolution.value

    @property
    def resolved(self) -> bool:
        return self.resolution.resolved and self.resolution.value is not None

    @property
    def reason(self) -> Optional[str]:
        return self.resolution.reason

    @property
    def ambiguous(self) -> bool:
        return self.resolution.ambiguous or self.expression.ambiguous

    @property
    def needs_reference(self) -> bool:
        return self.resolution.needs_reference

    @property
    def is_interval(self) -> bool:
        return self.expression.is_interval

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.expression.notes, *self.resolution.notes)))

    @property
    def method(self) -> ExtractionMethod:
        return _method_for(self.expression)

    @property
    def confidence(self) -> OptionalConfidence:
        return _confidence_for(self.expression, self.resolution)

    @property
    def evidence_ids(self) -> list[str]:
        collected = [self.evidence.evidence_id]
        collected.extend(endpoint.evidence.evidence_id for endpoint in self.endpoints)
        return collected

    def interval(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """The endpoint instants the text delimited, either of which may be absent."""
        if not self.endpoints:
            return None, None
        return self.endpoints[0].value, self.endpoints[-1].value

    def time_value(self) -> TimeValue:
        if not self.resolved:
            return TimeValue(
                value=None,
                precision=self.precision,
                qualifier=TimeQualifier.UNKNOWN,
                semantics=TimeSemantics.UNKNOWN,
                raw_text=self.raw_text,
                timezone=self.timezone,
                method=ExtractionMethod.UNRESOLVED,
                evidence_ids=self.evidence_ids,
                notes=_join((self.reason, *self.notes)),
            )
        return TimeValue(
            value=self.value,
            precision=self.precision,
            qualifier=self.qualifier,
            semantics=self.semantics,
            raw_text=self.raw_text,
            timezone=self.timezone,
            method=self.method,
            confidence=self.confidence,
            evidence_ids=self.evidence_ids,
            notes=_join(self.notes),
        )

    def describe(self) -> str:
        state = self.value.isoformat() if self.resolved else f"unresolved ({self.reason})"
        return f"{self.kind} {self.raw_text!r} [{self.char_start}:{self.char_end}] -> {state}"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "rule": self.rule,
            "semantics": self.semantics.value,
            "raw_text": self.raw_text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "precision": self.precision.value,
            "qualifier": self.qualifier.value,
            "value": self.value.isoformat() if self.resolved else None,
            "timezone": self.timezone,
            "method": self.method.value if self.resolved else ExtractionMethod.UNRESOLVED.value,
            "confidence": self.confidence,
            "resolved": self.resolved,
            "reason": self.reason,
            "needs_reference": self.needs_reference,
            "ambiguous": self.ambiguous,
            "notes": list(self.notes),
            "evidence_id": self.evidence.evidence_id,
            "endpoints": [endpoint.as_dict() for endpoint in self.endpoints],
        }


@dataclass(frozen=True)
class TemporalExtraction:
    """Everything one field says about time, kept in the role each part can support."""

    source: SourceField
    split: boilerplate.BodySplit
    mentions: tuple[TemporalMention, ...]
    publication: tuple[TemporalMention, ...]
    retrieval: Optional[TimeValue]
    retrieval_evidence: Optional[Evidence]
    reference: Optional[datetime]
    timezone: Optional[str]

    @property
    def body(self) -> MappedText:
        return self.split.body

    @property
    def field(self) -> str:
        return self.source.field

    def mentions_with(self, semantics: TimeSemantics) -> list[TemporalMention]:
        return [mention for mention in self.mentions if mention.semantics is semantics]

    @property
    def event_time_mentions(self) -> list[TemporalMention]:
        return self.mentions_with(TimeSemantics.EVENT_TIME)

    @property
    def reported_time_mentions(self) -> list[TemporalMention]:
        return self.mentions_with(TimeSemantics.REPORTED_TIME)

    @property
    def resolved_mentions(self) -> list[TemporalMention]:
        return [mention for mention in self.mentions if mention.resolved]

    @property
    def unresolved(self) -> list[TemporalMention]:
        return [mention for mention in self.mentions if not mention.resolved]

    @property
    def intervals(self) -> list[TemporalMention]:
        return [mention for mention in self.mentions if mention.is_interval]

    @property
    def evidence(self) -> list[Evidence]:
        collected = [mention.evidence for mention in self.mentions]
        collected.extend(
            endpoint.evidence
            for mention in self.mentions
            for endpoint in mention.endpoints
        )
        collected.extend(mention.evidence for mention in self.publication)
        if self.retrieval_evidence is not None:
            collected.append(self.retrieval_evidence)
        return collected

    def _rank(self, mention: TemporalMention) -> tuple[int, int, int]:
        return (
            surfaces.PRECISION_ORDER.index(mention.precision),
            1 if mention.ambiguous else 0,
            mention.char_start,
        )

    def _best(self, mentions: Iterable[TemporalMention]) -> Optional[TemporalMention]:
        candidates = [mention for mention in mentions if mention.resolved]
        if not candidates:
            return None
        return min(candidates, key=self._rank)

    def event_time(self) -> TimeValue:
        """The best event time the text supports, or an explicit absence. Never a stamp."""
        best = self._best(self.event_time_mentions)
        if best is not None:
            return best.time_value()
        reasons = [NO_EVENT_TIME]
        if self.publication:
            reasons.append(STAMP_ONLY)
        reasons.extend(
            f"{mention.raw_text!r}: {mention.reason}"
            for mention in self.event_time_mentions
            if mention.reason
        )
        return unresolved_time(
            timezone=self.timezone,
            evidence_ids=[mention.evidence.evidence_id for mention in self.event_time_mentions],
            notes=_join(reasons),
        )

    def reported_time(self) -> TimeValue:
        best = self._best(self.reported_time_mentions)
        if best is not None:
            return best.time_value()
        return unresolved_time(
            timezone=self.timezone,
            notes="the text attributes no statement to a moment",
        )

    def publication_time(self) -> TimeValue:
        """When this text became current: the latest stamp that carries a parseable date."""
        candidates = [mention for mention in self.publication if mention.resolved]
        if candidates:
            return max(candidates, key=lambda mention: mention.value).time_value()
        return unresolved_time(
            timezone=self.timezone,
            precision=TimePrecision.UNKNOWN,
            notes=_join(
                [
                    "the field carries no publish stamp"
                    if not self.split.items
                    else "the field carries publish stamps that printed no parseable date"
                ]
            ),
            evidence_ids=[mention.evidence.evidence_id for mention in self.publication],
        )

    @property
    def has_only_publication_time(self) -> bool:
        return self.event_time().value is None and any(
            mention.resolved for mention in self.publication
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.source.record_id,
            "field": self.source.field,
            "timezone": self.timezone,
            "reference": self.reference.isoformat() if self.reference else None,
            "stamps": self.split.stamps_as_dicts(),
            "event_time": self.event_time().model_dump(mode="json"),
            "reported_time": self.reported_time().model_dump(mode="json"),
            "publication_time": self.publication_time().model_dump(mode="json"),
            "retrieval_time": (
                self.retrieval.model_dump(mode="json") if self.retrieval else None
            ),
            "mentions": [mention.as_dict() for mention in self.mentions],
            "unresolved": [mention.raw_text for mention in self.unresolved],
        }


def _body_semantics(text: str, expression: Expression) -> TimeSemantics:
    cue = surfaces.reporting_cue(
        text, char_start=expression.char_start, char_end=expression.char_end
    )
    if cue is not None:
        return TimeSemantics.REPORTED_TIME
    return TimeSemantics.EVENT_TIME


def _mention(
    body: MappedText,
    source: SourceField,
    expression: Expression,
    *,
    reference: Optional[datetime],
    timezone: Optional[str],
    semantics: Optional[TimeSemantics] = None,
    note: Optional[str] = None,
) -> TemporalMention:
    resolution = surfaces.resolve(expression, reference=reference)
    role = semantics if semantics is not None else _body_semantics(body.text, expression)
    span = body.project(expression.char_start, expression.char_end)
    evidence = body.evidence_at(
        source,
        span,
        method=_method_for(expression),
        confidence=_confidence_for(expression, resolution),
        notes=note
        or _join((f"{role.value} temporal surface", resolution.reason)),
    )
    return TemporalMention(
        expression=expression,
        resolution=resolution,
        semantics=role,
        span=span,
        evidence=evidence,
        timezone=timezone,
        endpoints=tuple(
            _mention(
                body,
                source,
                endpoint,
                reference=reference,
                timezone=timezone,
                semantics=role,
                note=note,
            )
            for endpoint in expression.endpoints
        ),
    )


def _stamp_mention(
    item: boilerplate.Boilerplate,
    source: SourceField,
    timezone: Optional[str],
) -> Optional[TemporalMention]:
    """A stamp as publication metadata. Its printed text is parsed, never promoted."""
    span = item.timestamp_span()
    if span is None:
        return None
    found = surfaces.find(item.timestamp_text or "")
    if not found:
        return None
    expression = max(found, key=lambda surface: surface.length)
    resolution = surfaces.resolve(expression)
    evidence = build_evidence_at(
        source,
        span,
        method=ExtractionMethod.REGEX,
        confidence=_confidence_for(expression, resolution),
        notes=_stamp_note(item),
    )
    return TemporalMention(
        expression=expression,
        resolution=resolution,
        semantics=STAMP_SEMANTICS,
        span=span,
        evidence=evidence,
        timezone=timezone,
    )


def _stamp_note(item: boilerplate.Boilerplate) -> str:
    return f"{item.label.lower()} stamp: publication metadata, never event time"


def _retrieval_time(
    source: SourceField, timezone: Optional[str]
) -> tuple[Optional[TimeValue], Optional[Evidence]]:
    value = source.retrieved_at
    if value is None:
        return None, None
    declared = value.tzname() or timezone
    evidence = build_metadata_evidence(
        source,
        confidence=CONFIDENCE_METADATA,
        notes=_join(
            (
                "retrieval time from record metadata, not when anything happened",
                f"declared zone {declared}" if declared else None,
            )
        ),
    )
    time = TimeValue(
        value=value,
        precision=(
            TimePrecision.SECOND
            if (value.second or value.microsecond)
            else TimePrecision.MINUTE
        ),
        qualifier=TimeQualifier.EXACT,
        semantics=TimeSemantics.RETRIEVAL_TIME,
        timezone=declared,
        method=ExtractionMethod.SOURCE_METADATA,
        confidence=CONFIDENCE_METADATA,
        evidence_ids=[evidence.evidence_id],
        notes="when this record was fetched, not when anything happened",
    )
    return time, evidence


def _stamp_residue(
    source: SourceField, item: boilerplate.Boilerplate
) -> Optional[tuple[int, int]]:
    """The rest of a stamp line whose timestamp shape the stamp grammar did not capture."""
    if item.timestamp_text is not None:
        return None
    start = item.char_end
    newline = source.text.find("\n", start)
    end = len(source.text) if newline == -1 else newline
    return (start, end) if end > start else None


def _residue_note(item: boilerplate.Boilerplate) -> str:
    return (
        f"{item.label.lower()} stamp line: publication metadata printed in a shape "
        "the stamp grammar does not parse, never event time"
    )


def extract(
    source: SourceField,
    *,
    reference: Optional[datetime] = None,
    timezone: Optional[str] = None,
    split: Optional[boilerplate.BodySplit] = None,
    lowercase: bool = True,
    **normalize_options,
) -> TemporalExtraction:
    """Read one field for time: body surfaces, publish stamps, and the retrieval clock."""
    body_split = (
        split
        if split is not None
        else boilerplate.split(source, lowercase=lowercase, **normalize_options)
    )
    if body_split.source != source:
        raise TemporalError(
            f"the body was derived from {body_split.source.describe()}, not {source.describe()}"
        )

    declared = timezone or surfaces.timezone_cue(source.text)
    residues = [
        (item, range_)
        for item in body_split.items
        if (range_ := _stamp_residue(source, item)) is not None
    ]

    body_expressions: list[Expression] = []
    residue_expressions: list[tuple[Expression, boilerplate.Boilerplate]] = []
    for expression in surfaces.find(body_split.body.text):
        printed = body_split.body.project(expression.char_start, expression.char_end)
        owner = next(
            (
                item
                for item, (start, end) in residues
                if start <= printed.char_start and printed.char_end <= end
            ),
            None,
        )
        if owner is None:
            body_expressions.append(expression)
        else:
            residue_expressions.append((expression, owner))

    mentions = tuple(
        _mention(
            body_split.body,
            source,
            expression,
            reference=reference,
            timezone=declared,
        )
        for expression in body_expressions
    )
    publication = tuple(
        mention
        for mention in (
            _stamp_mention(item, source, declared) for item in body_split.items
        )
        if mention is not None
    ) + tuple(
        _mention(
            body_split.body,
            source,
            expression,
            reference=reference,
            timezone=declared,
            semantics=STAMP_SEMANTICS,
            note=_residue_note(item),
        )
        for expression, item in residue_expressions
    )
    retrieval, retrieval_evidence = _retrieval_time(source, declared)
    return TemporalExtraction(
        source=source,
        split=body_split,
        mentions=mentions,
        publication=publication,
        retrieval=retrieval,
        retrieval_evidence=retrieval_evidence,
        reference=reference,
        timezone=declared,
    )


def unresolved_time(
    *,
    raw_text: Optional[str] = None,
    precision: TimePrecision = TimePrecision.UNKNOWN,
    timezone: Optional[str] = None,
    notes: Optional[str] = None,
    evidence_ids: Iterable[str] = (),
) -> TimeValue:
    """A time the text never established, recorded as an absence rather than a guess."""
    return TimeValue(
        value=None,
        precision=precision,
        qualifier=TimeQualifier.UNKNOWN,
        semantics=TimeSemantics.UNKNOWN,
        raw_text=raw_text,
        timezone=timezone,
        method=ExtractionMethod.UNRESOLVED,
        evidence_ids=list(evidence_ids),
        notes=notes or NO_EVENT_TIME,
    )


__all__ = [
    "CONFIDENCE_AMBIGUOUS",
    "CONFIDENCE_DATE",
    "CONFIDENCE_DATE_CLOCK",
    "CONFIDENCE_METADATA",
    "CONFIDENCE_MONTH",
    "CONFIDENCE_RELATIVE",
    "NO_EVENT_TIME",
    "PROVIDER",
    "STAMP_ONLY",
    "STAMP_SEMANTICS",
    "TemporalError",
    "TemporalExtraction",
    "TemporalMention",
    "extract",
    "unresolved_time",
]
