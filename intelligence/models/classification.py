"""Relevance, event type and department hints."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import Confidence, OptionalConfidence, StrictModel
from intelligence.models.enums import Department, DepartmentHintBasis, EventType
from intelligence.models.enums import ExtractionMethod, RelevanceState


class DepartmentHint(StrictModel):
    """A suggested owning department. A hint, never an assignment.

    ``TAXONOMY`` basis means "this event type usually belongs to X", which is
    weaker than the source naming the department itself.
    """

    department: Department
    basis: DepartmentHintBasis = DepartmentHintBasis.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _hint_must_be_sourced(self) -> "DepartmentHint":
        if self.department in (Department.UNRESOLVED, Department.OTHER):
            raise ValueError(
                "do not emit a hint for an unresolved department; omit it instead"
            )
        if self.basis is DepartmentHintBasis.UNRESOLVED:
            raise ValueError("a department hint must state its basis")
        if self.confidence is None:
            raise ValueError("a department hint requires confidence")
        if self.basis in (DepartmentHintBasis.TEXT_MENTION, DepartmentHintBasis.BOTH):
            if not self.evidence_ids:
                raise ValueError(
                    f"a {self.basis.value} department hint must cite the text it read"
                )
        return self


class RelevanceInfo(StrictModel):
    """Is this record an incident at all?

    ``is_incident`` stays None while unresolved, so "we decided it is not an
    incident" is never confused with "we have not looked". Ceremonial coverage
    and award announcements are expected to resolve to False.
    """

    state: RelevanceState = RelevanceState.UNRESOLVED
    is_incident: Optional[bool] = None
    reason: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _state_and_flag_agree(self) -> "RelevanceInfo":
        resolved = {
            RelevanceState.INCIDENT: True,
            RelevanceState.NOT_INCIDENT: False,
        }.get(self.state)

        if resolved is None:
            if self.is_incident is not None:
                raise ValueError(
                    f"relevance state {self.state.value} cannot assert is_incident"
                )
            return self

        if self.is_incident is not resolved:
            raise ValueError(
                f"relevance state {self.state.value} requires is_incident={resolved}"
            )
        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError("a resolved relevance requires a method")
        if self.confidence is None:
            raise ValueError("a resolved relevance requires confidence")
        if not self.evidence_ids:
            raise ValueError("a resolved relevance must cite its evidence")
        return self


class ClassificationInfo(StrictModel):
    """Event type decision plus the score distribution behind it."""

    event_type: EventType = EventType.UNRESOLVED
    secondary_event_types: list[EventType] = Field(default_factory=list)

    #: Full distribution over candidate event types, retained rather than
    #: discarded after argmax: a reviewer needs to see the runner-up.
    category_scores: dict[str, Confidence] = Field(default_factory=dict)
    taxonomy_version: Optional[str] = None
    family: Optional[str] = None

    departments: list[DepartmentHint] = Field(default_factory=list)

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_classification(self) -> "ClassificationInfo":
        valid = {member.value for member in EventType}
        unknown_keys = sorted(k for k in self.category_scores if k not in valid)
        if unknown_keys:
            raise ValueError(
                "category_scores keys must be EventType values; unknown: "
                + ", ".join(unknown_keys)
            )

        if EventType.UNRESOLVED in self.secondary_event_types:
            raise ValueError("UNRESOLVED is not a valid secondary event type")
        if self.event_type in self.secondary_event_types:
            raise ValueError("the primary event type must not repeat as secondary")
        duplicates = {
            e for e in self.secondary_event_types if self.secondary_event_types.count(e) > 1
        }
        if duplicates:
            raise ValueError(
                "duplicate secondary event types: " + ", ".join(sorted(e.value for e in duplicates))
            )

        department_values = [d.department for d in self.departments]
        if len(set(department_values)) != len(department_values):
            raise ValueError("department hints must not repeat a department")

        if self.event_type is EventType.UNRESOLVED:
            if self.departments:
                raise ValueError("unresolved event type cannot carry department hints")
            if self.confidence is not None and self.confidence > 0.5:
                raise ValueError("unresolved event type cannot claim high confidence")
            return self

        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError("a resolved event type requires a method")
        if self.confidence is None:
            raise ValueError("a resolved event type requires confidence")
        if not self.evidence_ids:
            raise ValueError("a resolved event type must cite its evidence")

        if self.category_scores:
            top = max(self.category_scores, key=lambda k: self.category_scores[k])
            if top != self.event_type.value and self.method is not ExtractionMethod.HUMAN:
                raise ValueError(
                    f"event_type {self.event_type.value} is not the highest-scoring "
                    f"category {top}; only a human override may pick a lower score"
                )
        return self

    def is_low_confidence(self, threshold: float = 0.6) -> bool:
        return (
            self.event_type is EventType.UNRESOLVED
            or self.confidence is None
            or self.confidence < threshold
        )
