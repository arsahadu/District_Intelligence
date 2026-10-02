"""Evidence span arithmetic - the only sanctioned producer of Stage 1 Evidence.

An extractor states *what the source says*; this module decides *where it says
it*. Callers never type ``char_start``, ``char_end`` or ``field_text_hash``: the
offsets are derived from the original field text, so a span cannot drift from
the quote it claims, and a later replay can prove the field still reads the way
the Evidence recorded it.

Offsets are Python character indices into the text exactly as the record stored
it - not byte offsets, and not indices into a normalised copy. A Tamil grapheme
can be several code points (``நீ`` is ``ந`` + U+0BC0), so byte arithmetic would
land the end offset inside the wrong syllable, and normalising before spanning
would break the Stage 1 invariant ``len(quote) == char_end - char_start``.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import ConfigDict, field_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, Modality, SpanValidation
from intelligence.models.evidence import Evidence

#: Methods whose output is a probability, so produced Evidence must carry a score.
#: Exact-match methods (regex, rule, dictionary, verbatim copy, human) may leave
#: confidence unset rather than imply a fake precision of 1.0.
PROBABILISTIC_METHODS = frozenset({ExtractionMethod.LLM, ExtractionMethod.STATISTICAL})


class SpanError(ValueError):
    """Base for span arithmetic failures."""


class EmptyQuoteError(SpanError):
    """The quote could not identify anything even if it were found."""


class QuoteNotFoundError(SpanError):
    """The quote is not present in the source field as written."""


class AmbiguousQuoteError(SpanError):
    """The quote occurs more than once and the caller did not choose one."""

    def __init__(self, quote: str, occurrences: list["Span"]) -> None:
        self.quote = quote
        self.occurrences = occurrences
        offsets = ", ".join(span.describe() for span in occurrences)
        super().__init__(
            f"quote {quote!r} occurs {len(occurrences)} times at [{offsets}]; "
            "pass occurrence=<index> instead of letting a span be chosen for you"
        )


class OccurrenceOutOfBoundsError(SpanError):
    """The requested occurrence does not exist."""


@dataclass(frozen=True)
class Span:
    """A character range that is known to reproduce a quote from a text."""

    quote: str
    char_start: int
    char_end: int

    @property
    def length(self) -> int:
        return self.char_end - self.char_start

    def slice_from(self, text: str) -> str:
        return text[self.char_start : self.char_end]

    def matches(self, text: str) -> bool:
        return self.slice_from(text) == self.quote

    def describe(self) -> str:
        return f"{self.char_start}:{self.char_end}"

    def as_dict(self) -> dict:
        return {"quote": self.quote, "char_start": self.char_start, "char_end": self.char_end}


def compute_field_hash(text: str) -> str:
    """sha256 hex of the UTF-8 encoding of the *whole* original field text.

    Field identity, not quote identity: two quotes from one article share this
    hash, so one recomputation verifies all of them.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalisation_hint(text: str, quote: str) -> str:
    """Explain a near-miss without repairing it."""
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        if unicodedata.normalize(form, quote) in unicodedata.normalize(form, text):
            return (
                f" It matches only under Unicode normalisation {form}. Spans stay "
                "anchored to the untouched source, so put the normalised variant in "
                "its own text representation instead of bending these offsets."
            )
    return ""


def find_spans(text: str, quote: str) -> list[Span]:
    """Every occurrence of ``quote`` in ``text``, overlapping ones included.

    Overlap matters: ``"aa"`` in ``"aaa"`` occurs twice, and reporting one match
    would hide the ambiguity the caller needs to resolve.
    """
    if not quote:
        raise EmptyQuoteError("an empty quote identifies nothing in the source")
    if not quote.strip():
        raise EmptyQuoteError(
            f"a whitespace-only quote ({quote!r}) is not a fact; quote the text it qualifies"
        )

    spans: list[Span] = []
    start = text.find(quote)
    while start != -1:
        spans.append(Span(quote=quote, char_start=start, char_end=start + len(quote)))
        start = text.find(quote, start + 1)
    return spans


def locate(text: str, quote: str, *, occurrence: Optional[int] = None) -> Span:
    """Cut one span out of ``text``, refusing to guess which occurrence is meant.

    ``occurrence`` is an index into :func:`find_spans` results and may be
    negative. With more than one match and no index, this raises - a silently
    chosen first occurrence is how an extractor ends up citing the wrong place.
    """
    spans = find_spans(text, quote)
    if not spans:
        raise QuoteNotFoundError(
            f"quote {quote!r} does not occur in the source field "
            f"({len(text)} characters)." + _normalisation_hint(text, quote)
        )

    if occurrence is None:
        if len(spans) > 1:
            raise AmbiguousQuoteError(quote, spans)
        return spans[0]

    if not -len(spans) <= occurrence < len(spans):
        raise OccurrenceOutOfBoundsError(
            f"occurrence {occurrence} is out of range: {quote!r} occurs {len(spans)} time(s)"
        )
    return spans[occurrence]


class SourceField(StrictModel):
    """One field of one record, exactly as stored - the only text a span may be cut from.

    Frozen because a span is only meaningful against the text it was taken from:
    mutating ``text`` after building Evidence would silently invalidate offsets
    while the Evidence still claimed ``validated``.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str
    source_id: str
    source_type: str

    #: Dotted path, matching ``Evidence.field``: ``title``, ``data.content``,
    #: ``location.raw_text``.
    field: str

    #: The untouched original. Never stripped, never normalised.
    text: str

    source_url: Optional[str] = None
    raw_reference: Optional[str] = None
    retrieved_at: Optional[datetime] = None

    @field_validator("field")
    @classmethod
    def _field_is_a_path(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError(
                "field must be a dotted path without whitespace, e.g. data.content"
            )
        return value

    @property
    def text_hash(self) -> str:
        return compute_field_hash(self.text)

    def describe(self) -> str:
        return f"{self.record_id}:{self.field}"

    def evidence(
        self,
        quote: str,
        *,
        method: ExtractionMethod,
        confidence: OptionalConfidence = None,
        occurrence: Optional[int] = None,
        evidence_id: Optional[str] = None,
        modality: Modality = Modality.TEXT,
        notes: Optional[str] = None,
    ) -> Evidence:
        return build_evidence(
            self,
            quote,
            method=method,
            confidence=confidence,
            occurrence=occurrence,
            evidence_id=evidence_id,
            modality=modality,
            notes=notes,
        )

    def evidence_at(
        self,
        span: Span,
        *,
        method: ExtractionMethod,
        confidence: OptionalConfidence = None,
        evidence_id: Optional[str] = None,
        modality: Modality = Modality.TEXT,
        notes: Optional[str] = None,
    ) -> Evidence:
        return build_evidence_at(
            self,
            span,
            method=method,
            confidence=confidence,
            evidence_id=evidence_id,
            modality=modality,
            notes=notes,
        )


def derive_evidence_id(source: SourceField, span: Span, method: ExtractionMethod) -> str:
    """Deterministic id so the same fact from the same field always reuses it.

    Re-running a stage over unchanged text therefore does not mint a second
    identity for one quote, which is what lets Stage 8 merge incidents without
    inventing another reconciliation step.
    """
    digest = hashlib.sha256(
        "\x1f".join(
            (
                source.record_id,
                source.field,
                str(span.char_start),
                str(span.char_end),
                method.value,
                span.quote,
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"ev-{source.record_id}-{digest}"


def _check_method(method: ExtractionMethod, confidence: OptionalConfidence) -> None:
    if method is ExtractionMethod.UNRESOLVED:
        raise ValueError(
            "Evidence must state how it was produced; unresolved is a state on a "
            "fact, not a way of quoting a source"
        )
    if method in PROBABILISTIC_METHODS and confidence is None:
        raise ValueError(
            f"{method.value} evidence requires confidence: it is a guess, so say how good"
        )


def build_evidence_at(
    source: SourceField,
    span: Span,
    *,
    method: ExtractionMethod,
    confidence: OptionalConfidence = None,
    evidence_id: Optional[str] = None,
    modality: Modality = Modality.TEXT,
    notes: Optional[str] = None,
) -> Evidence:
    """Produce Evidence for a span the caller already located (regex, OCR, LLM offsets).

    The span is still verified against the source text here. A caller-supplied
    range is trusted for *where*, never for *whether*: if the range does not
    reproduce ``span.quote``, this raises instead of emitting a plausible lie.
    """
    _check_method(method, confidence)

    if span.char_start < 0 or span.char_end < span.char_start:
        raise SpanError(f"invalid span {span.describe()}")
    if span.char_end > len(source.text):
        raise SpanError(
            f"span {span.describe()} runs past the end of "
            f"{source.describe()} ({len(source.text)} characters)"
        )
    if not span.quote:
        raise EmptyQuoteError("a span over an empty quote identifies nothing")
    if not span.matches(source.text):
        raise SpanError(
            f"span {span.describe()} does not reproduce {span.quote!r} in "
            f"{source.describe()} (it holds {source.text[span.char_start : span.char_end]!r})"
        )

    return Evidence(
        evidence_id=evidence_id or derive_evidence_id(source, span, method),
        record_id=source.record_id,
        source_id=source.source_id,
        source_type=source.source_type,
        field=source.field,
        method=method,
        quote=span.quote,
        char_start=span.char_start,
        char_end=span.char_end,
        field_text_hash=source.text_hash,
        span_validation=SpanValidation.VALIDATED,
        modality=modality,
        source_url=source.source_url,
        raw_reference=source.raw_reference,
        retrieved_at=source.retrieved_at,
        confidence=confidence,
        notes=notes,
    )


def build_evidence(
    source: SourceField,
    quote: str,
    *,
    method: ExtractionMethod,
    confidence: OptionalConfidence = None,
    occurrence: Optional[int] = None,
    evidence_id: Optional[str] = None,
    modality: Modality = Modality.TEXT,
    notes: Optional[str] = None,
) -> Evidence:
    """Quote a source field and get Evidence with offsets derived, never typed."""
    span = locate(source.text, quote, occurrence=occurrence)
    return build_evidence_at(
        source,
        span,
        method=method,
        confidence=confidence,
        evidence_id=evidence_id,
        modality=modality,
        notes=notes,
    )


@dataclass(frozen=True)
class SpanCheck:
    """The outcome of replaying one Evidence against a field's current text."""

    evidence_id: str
    validation: SpanValidation
    reason: str
    expected_hash: Optional[str] = None
    actual_hash: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.validation is SpanValidation.VALIDATED

    def describe(self) -> str:
        return f"{self.evidence_id} -> {self.validation.value}: {self.reason}"


def verify_evidence(evidence: Evidence, source_text: str) -> SpanCheck:
    """Check an Evidence against the text the field holds *now*.

    A mismatch is reported, never repaired. Re-finding the quote elsewhere would
    let an edited source pretend it always said what we recorded, which is the
    failure mode an evidence layer exists to catch.
    """
    actual_hash = compute_field_hash(source_text)

    if not evidence.has_span:
        return SpanCheck(
            evidence.evidence_id,
            SpanValidation.NOT_APPLICABLE,
            "no span recorded; this evidence carries field-level provenance only",
            actual_hash=actual_hash,
        )

    start = evidence.char_start
    end = evidence.char_end
    observed = source_text[start:end]

    if evidence.field_text_hash is None:
        # The offsets may still be right, but without a hash the field's identity
        # is unproven, and Stage 1 reserves `validated` for hash-backed spans.
        detail = (
            "the span reproduces the quote"
            if observed == evidence.quote
            else f"the span holds {observed!r}, not {evidence.quote!r}"
        )
        return SpanCheck(
            evidence.evidence_id,
            SpanValidation.UNVALIDATED,
            f"no field_text_hash recorded, so field identity is unproven; {detail}",
            actual_hash=actual_hash,
        )

    if evidence.field_text_hash != actual_hash:
        return SpanCheck(
            evidence.evidence_id,
            SpanValidation.MISMATCH,
            "source field text changed after extraction; this span is no longer anchored",
            expected_hash=evidence.field_text_hash,
            actual_hash=actual_hash,
        )

    if observed != evidence.quote:
        return SpanCheck(
            evidence.evidence_id,
            SpanValidation.MISMATCH,
            f"offsets {start}:{end} now hold {observed!r}, not {evidence.quote!r}",
            expected_hash=evidence.field_text_hash,
            actual_hash=actual_hash,
        )

    return SpanCheck(
        evidence.evidence_id,
        SpanValidation.VALIDATED,
        "field hash matches and the span reproduces the quote",
        expected_hash=evidence.field_text_hash,
        actual_hash=actual_hash,
    )


def revalidate(evidence: Evidence, source_text: str) -> tuple[Evidence, SpanCheck]:
    """Return the evidence in its honest validation state, plus the check.

    Rebuilt through the model rather than by assignment so an impossible
    downgrade still fails loudly instead of reaching storage. An unchanged
    verdict returns the same object: replaying verification must not append the
    same note to a record on every run.
    """
    check = verify_evidence(evidence, source_text)
    if check.validation is evidence.span_validation:
        return evidence, check

    payload = evidence.model_dump()
    payload["span_validation"] = check.validation
    payload["notes"] = (
        f"{evidence.notes} | {check.reason}" if evidence.notes else check.reason
    )
    return Evidence.model_validate(payload), check
