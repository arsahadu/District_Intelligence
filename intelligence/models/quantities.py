"""Typed quantities read out of the source text."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, ObservationKind
from intelligence.models.enums import ObservationQualifier


class Observation(StrictModel):
    """A count, amount, area or measurement the source actually stated."""

    observation_id: str
    kind: ObservationKind = ObservationKind.UNRESOLVED

    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None

    raw_text: Optional[str] = None
    qualifier: ObservationQualifier = ObservationQualifier.UNKNOWN

    attributed_instead_of_observed: bool = False

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _value_needs_provenance(self) -> "Observation":
        has_value = self.value_numeric is not None or self.value_text is not None
        if not has_value:
            if self.raw_text:
                raise ValueError(
                    "raw_text without a parsed value is evidence, not an observation; "
                    "record it as Evidence instead"
                )
            return self

        if self.value_numeric is not None and self.value_text is not None:
            raise ValueError("set value_numeric or value_text, not both")
        if self.kind is ObservationKind.UNRESOLVED:
            raise ValueError("a valued observation requires a resolved kind")
        if self.raw_text is None:
            raise ValueError("a valued observation must keep the verbatim raw_text")
        if not self.evidence_ids:
            raise ValueError("a valued observation must cite its evidence")
        if self.confidence is None:
            raise ValueError("a valued observation requires confidence")
        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError("a valued observation requires a method")
        return self

    @property
    def display_value(self) -> str:
        if self.value_numeric is not None:
            rendered = (
                str(int(self.value_numeric))
                if float(self.value_numeric).is_integer()
                else f"{self.value_numeric:g}"
            )
        elif self.value_text is not None:
            rendered = self.value_text
        else:
            return "unresolved"
        return f"{rendered} {self.unit}".strip() if self.unit else rendered
