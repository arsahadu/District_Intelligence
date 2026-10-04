"""Stage 8b status reading: what the source said about whether the situation is still running."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from intelligence.config import operations_cues as vocabulary
from intelligence.extraction import signal_text
from intelligence.extraction.signal_text import METHOD, Refusal, RowMatch, SignalIndex
from intelligence.extraction.spans import SourceField, Span
from intelligence.extraction.mention_text import Prose
from intelligence.models.enums import ExtractionMethod, OperationalState, SignalTier
from intelligence.models.evidence import Evidence
from intelligence.models.status import OperationalStatusInfo, StatusSignal

PROVIDER = "intelligence.extraction.status"

STATUS_INDEX = SignalIndex("STATUS_CUES", vocabulary.STATUS_CUES)

#: Which reading of the source carries the decision when the source said it two ways.
TIER_ORDER: dict[SignalTier, int] = {
    SignalTier.DECLARED: 2,
    SignalTier.STATED: 1,
    SignalTier.IMPLIED: 0,
}

#: How much to trust each way a source can state a condition.
TIER_CONFIDENCE: dict[SignalTier, float] = {
    SignalTier.DECLARED: 0.9,
    SignalTier.STATED: 0.8,
    SignalTier.IMPLIED: 0.6,
}

#: Held back from a status that the same source also argued against.
DISSENT_PENALTY = 0.1

NO_SIGNAL = "no status wording"


@dataclass(frozen=True)
class DeclaredStatus:
    """The record's own status field, handed over as a statement about the case."""

    state: OperationalState
    value: str
    field: str
    evidence_id: str


@dataclass(frozen=True)
class StatusMatch:
    """One printed statement of a condition, with the evidence that quotes it."""

    field: str
    state: OperationalState
    tier: SignalTier
    entry: str
    surface: str
    rule: str
    char_start: int
    char_end: int
    sentence: int
    note: str
    quality: float
    evidence: Optional[Evidence] = None

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    @property
    def evidence_id(self) -> str:
        assert self.evidence is not None
        return self.evidence.evidence_id

    def describe(self) -> str:
        return (
            f"{self.state.value} <- {self.surface!r} in {self.field} "
            f"[{self.char_start}:{self.char_end}] ({self.tier.value}, {self.rule})"
        )

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "state": self.state.value,
            "tier": self.tier.value,
            "entry": self.entry,
            "surface": self.surface,
            "rule": self.rule,
            "quality": self.quality,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "note": self.note,
            "evidence_id": self.evidence_id if self.evidence else None,
        }


@dataclass(frozen=True)
class StatusFinding:
    """What one field said about whether the situation is going on."""

    field: str
    matches: tuple[StatusMatch, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "matches": [match.as_dict() for match in self.matches],
            "refusals": [refusal.as_dict() for refusal in self.refusals],
        }


@dataclass(frozen=True)
class StatusExtraction:
    """Every status wording this record printed, in the order it was read."""

    record_id: str
    findings: tuple[StatusFinding, ...] = ()

    @property
    def matches(self) -> tuple[StatusMatch, ...]:
        return tuple(match for finding in self.findings for match in finding.matches)

    @property
    def refusals(self) -> tuple[Refusal, ...]:
        return tuple(refusal for finding in self.findings for refusal in finding.refusals)

    def states(self) -> tuple[OperationalState, ...]:
        seen: dict[OperationalState, None] = {}
        for match in self.matches:
            seen.setdefault(match.state, None)
        return tuple(seen)

    def evidence(self) -> tuple[Evidence, ...]:
        collected: dict[str, Evidence] = {}
        for match in self.matches:
            if match.evidence is not None:
                collected.setdefault(match.evidence.evidence_id, match.evidence)
        return tuple(collected.values())

    def warnings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f"status candidate {refusal.surface!r} in {refusal.field} "
                f"[{refusal.char_start}:{refusal.char_end}] refused: {refusal.reason}"
                for refusal in self.refusals
            )
        )

    def describe(self) -> str:
        return (
            f"{len(self.matches)} status statement(s) over {len(self.findings)} field(s), "
            f"{len(self.refusals)} refused"
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _to_status_match(match: RowMatch) -> StatusMatch:
    cue: vocabulary.StatusCue = match.row
    return StatusMatch(
        field=match.field,
        state=cue.state,
        tier=cue.tier,
        entry=match.entry,
        surface=match.surface,
        rule=match.rule,
        char_start=match.char_start,
        char_end=match.char_end,
        sentence=match.sentence,
        note=cue.note,
        quality=match.quality,
    )


def find_status(source: SourceField, prose: Prose) -> StatusFinding:
    """Read one field for what it stated about the situation still running."""
    matches, refusals = signal_text.find_matches(prose, STATUS_INDEX)
    cited = tuple(
        replace(
            _to_status_match(match),
            evidence=source.evidence_at(
                match.span,
                method=METHOD,
                confidence=match.quality,
                notes=(
                    f"operational state {match.row.state.value} read from {match.surface!r} "
                    f"by the {match.rule} rule ({match.row.note}): {match.row.tier.value} wording"
                ),
            ),
        )
        for match in matches
    )
    return StatusFinding(field=prose.field, matches=cited, refusals=refusals)


def extract(readings: Sequence[tuple[SourceField, Prose]]) -> StatusExtraction:
    """Every status statement the record's own fields made."""
    if not readings:
        return StatusExtraction(record_id="")
    record_id = readings[0][0].record_id
    findings = tuple(
        finding for finding in (find_status(source, prose) for source, prose in readings)
        if finding.matches or finding.refusals
    )
    return StatusExtraction(record_id=record_id, findings=findings)


def _signal(match: StatusMatch, *, record_id: str, number: int) -> StatusSignal:
    return StatusSignal(
        signal_id=f"st-{record_id}-{number}",
        state=match.state,
        tier=match.tier,
        cue_text=match.surface,
        field=match.field,
        method=ExtractionMethod.DICTIONARY,
        confidence=round(match.quality, 2),
        evidence_ids=[match.evidence_id],
    )


def _declared_signal(record_id: str, declared: DeclaredStatus) -> StatusSignal:
    return StatusSignal(
        signal_id=f"st-{record_id}-declared",
        state=declared.state,
        tier=SignalTier.DECLARED,
        cue_text=declared.value,
        field=declared.field,
        method=ExtractionMethod.SOURCE_METADATA,
        confidence=TIER_CONFIDENCE[SignalTier.DECLARED],
        evidence_ids=[declared.evidence_id],
    )


def decide_status(
    record_id: str,
    matches: Sequence[StatusMatch],
    declared: Optional[DeclaredStatus] = None,
) -> OperationalStatusInfo:
    """Let the most direct statement win, and keep every other statement that was made."""
    signals = [_signal(match, record_id=record_id, number=index) for index, match in enumerate(matches)]
    if declared is not None:
        signals.append(_declared_signal(record_id, declared))
    if not signals:
        return OperationalStatusInfo.unknown(
            f"{NO_SIGNAL}: neither the record's own status field nor its text stated whether the "
            "situation was still running"
        )

    top = max(signal.tier for signal in signals)
    leading = [signal for signal in signals if signal.tier is top]
    decided = {signal.state for signal in leading}
    others = sorted(
        {signal.state for signal in signals} - decided - {OperationalState.UNKNOWN},
        key=lambda state: state.value,
    )
    evidence_ids = [signal.evidence_ids[0] for signal in signals]

    if len(decided) > 1:
        states = sorted(decided, key=lambda state: state.value)
        return OperationalStatusInfo(
            state=OperationalState.UNKNOWN,
            signals=signals,
            reason=(
                "the source stated "
                + " and ".join(state.value for state in states)
                + " at the same level of directness, so none of them was picked for it"
            ),
            conflicting_states=[*states, *[state for state in others if state not in states]],
            evidence_ids=evidence_ids,
            notes="left for a reader because two statements of equal weight contradicted each other",
        )

    state = next(iter(decided))
    tier_confidence = TIER_CONFIDENCE[top] - (DISSENT_PENALTY if others else 0.0)
    leading_for_state = [signal for signal in leading if signal.state is state]
    return OperationalStatusInfo(
        state=state,
        signals=signals,
        reason=(
            f"{len(leading_for_state)} statement(s) at the {top.value} level said {state.value}: "
            + "; ".join(f"{signal.cue_text!r} in {signal.field}" for signal in leading_for_state)
            + (
                "; the source also argued " + ", ".join(other.value for other in others)
                + " at a weaker level, which is kept but not followed"
                if others
                else ""
            )
        ),
        conflicting_states=others,
        method=ExtractionMethod.DICTIONARY
        if top is not SignalTier.DECLARED
        else ExtractionMethod.SOURCE_METADATA,
        confidence=round(tier_confidence, 2),
        evidence_ids=evidence_ids,
        notes=(
            "the record's own status field decided it"
            if top is SignalTier.DECLARED
            else "wording in the record's own text decided it; no event type was consulted"
        ),
    )


__all__ = [
    "DISSENT_PENALTY",
    "NO_SIGNAL",
    "STATUS_INDEX",
    "TIER_CONFIDENCE",
    "TIER_ORDER",
    "DeclaredStatus",
    "OperationalStatusInfo",
    "StatusExtraction",
    "StatusFinding",
    "StatusMatch",
    "StatusSignal",
    "decide_status",
    "extract",
    "find_status",
]
