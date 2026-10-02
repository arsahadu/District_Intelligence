"""Severity contracts.

Severity is advisory, evidence-based and allowed to be empty. A level may not
exist without at least one cited cue, which is the structural form of "do not
assign severity because a model thinks something looks severe".
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import Confidence, OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, SeverityCueCategory, SeverityLevel


class SeveritySignal(StrictModel):
    """One cue in the source text that argues for a severity level."""

    signal_id: str
    category: SeverityCueCategory
    #: Verbatim cue text, e.g. ``12 பேர் பாதிக்கப்பட்டனர்``.
    cue_text: str
    weight: Confidence = 1.0
    direction: str = Field(default="escalating", pattern="^(escalating|mitigating|neutral)$")
    magnitude_observed: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _signal_needs_provenance(self) -> "SeveritySignal":
        if not self.evidence_ids:
            raise ValueError("a severity signal must cite the evidence it was read from")
        if self.confidence is None:
            raise ValueError("a severity signal must carry confidence")
        if self.category is SeverityCueCategory.UNRESOLVED:
            raise ValueError("an unresolved cue cannot support a severity signal")
        return self


class Severity(StrictModel):
    """Evidence-weighted severity, or an explicit statement that it is unknown."""

    level: SeverityLevel = SeverityLevel.UNRESOLVED
    score: Optional[Confidence] = None
    signals: list[SeveritySignal] = Field(default_factory=list)

    #: Why these signals produce this level, for a reviewer or RTI-style query.
    rationale: Optional[str] = None

    #: Intelligence output is never authoritative: the Collector and the
    #: concerned department confirm severity.
    is_authoritative: bool = False
    confirmed_by: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _level_requires_evidence(self) -> "Severity":
        if self.level is SeverityLevel.UNRESOLVED:
            if self.score is not None:
                raise ValueError("unresolved severity cannot carry a score")
            if self.is_authoritative:
                raise ValueError("unresolved severity cannot be authoritative")
            return self

        if not self.signals:
            raise ValueError(
                f"severity level {self.level.value} requires at least one cited cue; "
                "use level=unresolved when the source gives no severity evidence"
            )
        if self.rationale is None:
            raise ValueError(f"severity level {self.level.value} requires a rationale")
        if self.confidence is None:
            raise ValueError(f"severity level {self.level.value} requires confidence")
        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError(f"severity level {self.level.value} requires a method")
        return self

    @classmethod
    def unresolved(cls, note: Optional[str] = None) -> "Severity":
        """The preferred default: nothing established, nothing invented."""
        return cls(level=SeverityLevel.UNRESOLVED, notes=note)
