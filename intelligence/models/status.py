"""Operational status: whether the reported situation is still running, as the source said it."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, OperationalState, SignalTier

#: The states a signal may argue for. UNKNOWN is the absence of a signal, never a signal.
ASSERTABLE_STATES: frozenset[OperationalState] = frozenset(
    {OperationalState.ONGOING, OperationalState.RESOLVED, OperationalState.CLOSED}
)


class StatusSignal(StrictModel):
    """One printed statement that the situation is, or has ceased to be, running."""

    signal_id: str
    state: OperationalState
    tier: SignalTier = SignalTier.STATED
    cue_text: str
    field: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _signal_proves_itself(self) -> "StatusSignal":
        if self.state not in ASSERTABLE_STATES:
            raise ValueError(
                f"status signal state {self.state.value} asserts no condition; "
                "a signal that says nothing is not a signal"
            )
        if not self.cue_text.strip():
            raise ValueError("a status signal must quote the wording it rests on")
        if not self.evidence_ids:
            raise ValueError("a status signal must cite the evidence it was read from")
        if self.confidence is None:
            raise ValueError("a status signal must carry confidence")
        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError("a status signal must state how it was read")
        return self


class OperationalStatusInfo(StrictModel):
    """The operational state, or an explicit statement that the source never said."""

    state: OperationalState = OperationalState.UNKNOWN
    signals: list[StatusSignal] = Field(default_factory=list)
    reason: Optional[str] = None

    #: States the source also argued for, kept whether or not the precedence rule settled them.
    conflicting_states: list[OperationalState] = Field(default_factory=list)

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _state_requires_evidence(self) -> "OperationalStatusInfo":
        duplicates = {state for state in self.conflicting_states if self.conflicting_states.count(state) > 1}
        if duplicates:
            raise ValueError(
                "duplicate conflicting states: " + ", ".join(sorted(s.value for s in duplicates))
            )

        if self.state is OperationalState.UNKNOWN:
            if self.confidence is not None:
                raise ValueError("unknown operational status cannot carry confidence")
            if self.conflicting_states and not self.evidence_ids:
                raise ValueError(
                    "a status left unknown by conflict must keep the evidence that conflicted"
                )
            return self

        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError("a resolved operational state requires a method")
        if self.confidence is None:
            raise ValueError("a resolved operational state requires confidence")
        if not self.evidence_ids:
            raise ValueError("a resolved operational state must cite its evidence")
        if self.reason is None:
            raise ValueError("a resolved operational state requires a reason")

        stated = {signal.state for signal in self.signals}
        if self.state not in stated:
            raise ValueError(
                f"operational state {self.state.value} needs a signal arguing for it, "
                "not one arguing for something else"
            )
        if self.state in self.conflicting_states:
            raise ValueError(
                f"{self.state.value} cannot be both the decided state and an unresolved conflict"
            )
        return self

    @classmethod
    def unknown(cls, note: str, *, evidence_ids: Optional[list[str]] = None) -> "OperationalStatusInfo":
        """Nothing in the source stated a condition, so none is asserted."""
        return cls(
            state=OperationalState.UNKNOWN,
            notes=note,
            evidence_ids=list(evidence_ids or []),
        )

    @property
    def is_contested(self) -> bool:
        return len(self.conflicting_states) > 0


__all__ = [
    "ASSERTABLE_STATES",
    "OperationalStatusInfo",
    "StatusSignal",
]
