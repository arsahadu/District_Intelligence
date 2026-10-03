"""Time contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, TimePrecision, TimeQualifier
from intelligence.models.enums import TimeSemantics


class TimeValue(StrictModel):
    """A partially-known point in time."""

    value: Optional[datetime] = None
    precision: TimePrecision = TimePrecision.UNKNOWN
    qualifier: TimeQualifier = TimeQualifier.UNKNOWN
    semantics: TimeSemantics = TimeSemantics.UNKNOWN

    raw_text: Optional[str] = None

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
        """True when the best available time is when the item was published."""
        return self.value is not None and self.semantics is TimeSemantics.PUBLICATION_TIME

    def as_display_string(self) -> str:
        if self.value is None:
            return "time unresolved"
        if self.precision is TimePrecision.DAY:
            return self.value.strftime("%Y-%m-%d")
        if self.precision in (TimePrecision.MONTH, TimePrecision.YEAR):
            return self.value.strftime("%Y-%m")
        return self.value.isoformat(timespec="seconds")
