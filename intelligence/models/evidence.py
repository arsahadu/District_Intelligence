"""Evidence: the traceability primitive every extracted fact points at."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, Modality, SpanValidation

_HEX64 = r"^[0-9a-f]{64}$"


class Evidence(StrictModel):
    """A verbatim quote pinned to a character range in one CommonRecord field.

    This is the only sanctioned way to claim "the source says X". The offsets
    are indices into the *original* value of ``field`` in ``record_id`` (never
    into a translation or a normalised copy), and ``field_text_hash`` lets a
    later replay prove the text still says what we recorded.

    Stage 2 (``extraction/spans.py``) is intended to be the only code that
    computes ``quote``/``char_start``/``char_end`` together. The consistency
    validator below is what makes a hand-written or LLM-invented span noisy
    rather than silently plausible.
    """

    evidence_id: str
    record_id: str
    source_id: str
    source_type: str

    #: Dotted path into the CommonRecord, e.g. ``title``, ``data.content``,
    #: ``location.raw_text``, ``severity``. Kept open so new source types and
    #: new ``data`` keys do not require a vocabulary change.
    field: str

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED

    quote: Optional[str] = None
    char_start: Optional[int] = Field(default=None, ge=0)
    char_end: Optional[int] = Field(default=None, ge=0)

    #: sha256 hex of the full original text of ``field``.
    field_text_hash: Optional[str] = Field(default=None, pattern=_HEX64)

    span_validation: SpanValidation = SpanValidation.UNVALIDATED
    modality: Modality = Modality.TEXT

    #: Record-level provenance, copied from CommonRecord so an Incident is
    #: self-describing without a join.
    source_url: Optional[str] = None
    raw_reference: Optional[str] = None
    retrieved_at: Optional[datetime] = None

    confidence: OptionalConfidence = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_span_consistency(self) -> "Evidence":
        if self.method is not ExtractionMethod.SOURCE_METADATA:
            missing = [
                name
                for name, value in (
                    ("quote", self.quote),
                    ("char_start", self.char_start),
                    ("char_end", self.char_end),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "evidence from a non-metadata method must pin a span: "
                    f"missing {', '.join(missing)}"
                )

        if self.char_start is not None and self.char_end is not None:
            if self.char_end < self.char_start:
                raise ValueError("char_end must be >= char_start")
            if self.quote is not None:
                if len(self.quote) != self.char_end - self.char_start:
                    raise ValueError(
                        "quote length must equal char_end - char_start "
                        f"({len(self.quote)} != {self.char_end - self.char_start})"
                    )
        elif self.quote is not None and self.char_start is not None:
            raise ValueError("quote with a start offset also needs char_end")

        if self.span_validation is SpanValidation.VALIDATED:
            if self.field_text_hash is None:
                raise ValueError("VALIDATED spans require field_text_hash")
            if self.quote is None or self.char_start is None or self.char_end is None:
                raise ValueError("VALIDATED spans require a pinned quote")

        return self

    @property
    def is_source_fact(self) -> bool:
        """True when this was copied from the record rather than inferred."""
        return self.method is ExtractionMethod.SOURCE_METADATA

    @property
    def has_span(self) -> bool:
        return (
            self.quote is not None
            and self.char_start is not None
            and self.char_end is not None
        )

    def describe(self) -> str:
        if self.has_span:
            return f"{self.record_id}:{self.field}[{self.char_start}:{self.char_end}]"
        return f"{self.record_id}:{self.field}"
