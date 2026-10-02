"""Time contracts.

Real Dinamalar records expose only an ``ADDED : <Tamil month> DD, YYYY HH:MM AM/PM``
stamp inside ``data.content``; ``CommonRecord.event_time`` is unusable in
practice. The contract therefore has to say *which* moment a timestamp denotes
and how precise it is, instead of presenting every datetime as an event time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, TimePrecision, TimeQualifier
from intelligence.models.enums import TimeSemantics


class TimeValue(StrictModel):
    """A partially-known point in time.

    ``value=None`` is a valid, complete answer: it means no time could be
    established. A value is never allowed without declaring where it came from.
    """

    value: Optional[datetime] = None
    precision: TimePrecision = TimePrecision.UNKNOWN
    qualifier: TimeQualifier = TimeQualifier.UNKNOWN
    semantics: TimeSemantics = TimeSemantics.UNKNOWN

    #: Verbatim time expression as it appeared, Tamil month names included.
    raw_text: Optional[str] = None

    #: IANA zone name. Left None rather than defaulting to UTC or IST: an
    #: unaware timestamp from a local-language source is not a UTC timestamp.
    timezone: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _value_needs_provenance(self) -> "TimeValue":
        if self.value is None:
            return self

        problems: list[str] = []
        if self.method is ExtractionMethod.UNRESOLVED:
            problems.append("method")
        if self.confidence is None:
            problems.append("confidence")
        if self.semantics is TimeSemantics.UNKNOWN:
            problems.append("semantics (event vs publication time)")
        if self.precision is TimePrecision.UNKNOWN:
            problems.append("precision")
        if problems:
            raise ValueError(
                "a resolved time value is missing its provenance: "
                + ", ".join(problems)
            )

        if self.method is not ExtractionMethod.SOURCE_METADATA and self.raw_text is None:
            raise ValueError(
                "an extracted time value must keep the raw expression it came from"
            )
        return self

    @property
    def is_publication_time_only(self) -> bool:
        """True when the best available time is when the item was published.

        This is the common case for the current news corpus and must not be
        presented to the Collector as when the incident happened.
        """
        return self.value is not None and self.semantics is TimeSemantics.PUBLICATION_TIME

    def as_display_string(self) -> str:
        if self.value is None:
            return "time unresolved"
        if self.precision is TimePrecision.DAY:
            return self.value.strftime("%Y-%m-%d")
        if self.precision in (TimePrecision.MONTH, TimePrecision.YEAR):
            return self.value.strftime("%Y-%m")
        return self.value.isoformat(timespec="seconds")
