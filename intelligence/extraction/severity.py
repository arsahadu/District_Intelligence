"""Stage 8b severity reading: the wording that argued an event was serious, and the counts that sized it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from intelligence.config import operations_cues as vocabulary
from intelligence.config.vocabularies import SEVERITY_LEVEL_ORDER, severity_rank
from intelligence.extraction import mention_text, signal_text
from intelligence.extraction.mention_text import Prose
from intelligence.extraction.signal_text import METHOD, Refusal, RowMatch, SignalIndex
from intelligence.extraction.spans import SourceField, Span
from intelligence.extraction.time_expressions import COUNT_WORDS
from intelligence.models.enums import (
    ExtractionMethod,
    ObservationKind,
    ObservationQualifier,
    SeverityCueCategory,
    SeverityLevel,
)
from intelligence.models.evidence import Evidence
from intelligence.models.quantities import Observation
from intelligence.models.severity import Severity, SeveritySignal

PROVIDER = "intelligence.extraction.severity"

CUE_INDEX = SignalIndex("SEVERITY_CUES", vocabulary.SEVERITY_CUES)
HEAD_INDEX = SignalIndex("COUNT_HEADS", vocabulary.COUNT_HEADS)

#: The floor a band starts at; more evidence on the same event adds to it, never replaces it.
BAND_BASE: dict[SeverityLevel, float] = {
    SeverityLevel.LOW: 0.25,
    SeverityLevel.MODERATE: 0.50,
    SeverityLevel.HIGH: 0.75,
    SeverityLevel.CRITICAL: 0.90,
}
SIGNAL_STEP = 0.03
SCORE_CAP = 0.95

CONF_SINGLE_CUE = 0.70
CONF_TWO_CATEGORIES = 0.80
CONF_QUANTIFIED = 0.85
CONF_AUTHORITATIVE = 0.90
CONF_CAP = 0.90

#: How much a stated count is worth once it has cleared one of the magnitude thresholds.
MAGNITUDE_WEIGHT = 0.8

#: What class of evidence a counted kind is, when no cue in its sentence named one.
KIND_CATEGORIES: dict[ObservationKind, SeverityCueCategory] = {
    ObservationKind.FATALITIES: SeverityCueCategory.FATALITY,
    ObservationKind.INJURIES: SeverityCueCategory.INJURY,
    ObservationKind.AFFECTED_PERSONS: SeverityCueCategory.AFFECTED_POPULATION,
    ObservationKind.AFFECTED_HOUSEHOLDS: SeverityCueCategory.AFFECTED_POPULATION,
    ObservationKind.DISPLACED_PERSONS: SeverityCueCategory.AFFECTED_POPULATION,
    ObservationKind.PROTEST_PARTICIPANTS: SeverityCueCategory.AFFECTED_POPULATION,
    ObservationKind.DETENTIONS: SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION,
    ObservationKind.AFFECTED_AREA_AGRICULTURE: SeverityCueCategory.AFFECTED_AREA,
    ObservationKind.AREA_COVERED: SeverityCueCategory.AFFECTED_AREA,
    ObservationKind.BUILDING_COUNT: SeverityCueCategory.ECONOMIC_LOSS,
    ObservationKind.POWER_OUTAGE_DURATION: SeverityCueCategory.SERVICE_DISRUPTION,
    ObservationKind.WATER_LEVEL: SeverityCueCategory.CONTINUING_HAZARD,
    ObservationKind.RAINFALL: SeverityCueCategory.CONTINUING_HAZARD,
}

NO_UNIT = "stated as a number"


@dataclass(frozen=True)
class DeclaredSeverity:
    """The record's own severity field, on a scale this module was told to trust."""

    level: SeverityLevel
    value: str
    field: str
    evidence_id: str
    source_id: str
    kind: str = "alert scale"


@dataclass(frozen=True)
class SeverityMatch:
    """One printed wording that argued for a band, with the evidence that quotes it."""

    field: str
    entry: str
    surface: str
    rule: str
    category: SeverityCueCategory
    band: SeverityLevel
    weight: float
    direction: str
    strength: float
    char_start: int
    char_end: int
    sentence: int
    note: str
    quality: float
    count_kind: Optional[ObservationKind] = None
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
            f"{self.category.value} {self.band.value} <- {self.surface!r} in {self.field} "
            f"[{self.char_start}:{self.char_end}] ({self.direction}, {self.strength:.2f})"
        )

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "entry": self.entry,
            "surface": self.surface,
            "rule": self.rule,
            "category": self.category.value,
            "band": self.band.value,
            "direction": self.direction,
            "strength": round(self.strength, 4),
            "char_start": self.char_start,
            "char_end": self.char_end,
            "note": self.note,
            "evidence_id": self.evidence_id if self.evidence else None,
        }


@dataclass(frozen=True)
class SeverityFinding:
    """What one field argued about how serious the event was."""

    field: str
    matches: tuple[SeverityMatch, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "matches": [match.as_dict() for match in self.matches],
            "refusals": [refusal.as_dict() for refusal in self.refusals],
        }


@dataclass(frozen=True)
class SeverityExtraction:
    """Every severity cue this record printed, in the order it was read."""

    record_id: str
    findings: tuple[SeverityFinding, ...] = ()

    @property
    def matches(self) -> tuple[SeverityMatch, ...]:
        return tuple(match for finding in self.findings for match in finding.matches)

    @property
    def refusals(self) -> tuple[Refusal, ...]:
        return tuple(refusal for finding in self.findings for refusal in finding.refusals)

    def accepted(self) -> tuple[SeverityMatch, ...]:
        return tuple(match for match in self.matches if match.strength >= vocabulary.ACCEPTANCE_FLOOR - 1e-9)

    def evidence(self) -> tuple[Evidence, ...]:
        collected: dict[str, Evidence] = {}
        for match in self.matches:
            if match.evidence is not None:
                collected.setdefault(match.evidence.evidence_id, match.evidence)
        return tuple(collected.values())

    def warnings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f"severity candidate {refusal.surface!r} in {refusal.field} "
                f"[{refusal.char_start}:{refusal.char_end}] refused: {refusal.reason}"
                for refusal in self.refusals
            )
        )

    def describe(self) -> str:
        return (
            f"{len(self.matches)} severity cue(s) ({len(self.accepted())} believed) over "
            f"{len(self.findings)} field(s), {len(self.refusals)} refused"
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "findings": [finding.as_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class QuantityMatch:
    """A number the source stated and the noun it counted, held as one span."""

    field: str
    entry: str
    surface: str
    value: float
    kind: ObservationKind
    unit: str
    rule: str
    char_start: int
    char_end: int
    sentence: int
    evidence: Optional[Evidence] = None

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    @property
    def quality(self) -> float:
        return signal_text.QUALITY[self.rule]

    @property
    def evidence_id(self) -> str:
        assert self.evidence is not None
        return self.evidence.evidence_id

    @property
    def display(self) -> str:
        rendered = str(int(self.value)) if float(self.value).is_integer() else f"{self.value:g}"
        return f"{rendered} {self.unit}".strip()

    def describe(self) -> str:
        return f"{self.kind.value} {self.display} <- {self.surface!r} in {self.field}"

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "head": self.entry,
            "surface": self.surface,
            "kind": self.kind.value,
            "value": self.value,
            "unit": self.unit,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "evidence_id": self.evidence_id if self.evidence else None,
        }


def count_value(surface: str) -> Optional[float]:
    """The number this printed word states, in digits or in words, or None when it states none."""
    from intelligence.extraction.mention_text import fold

    text = surface.strip().replace(",", "")
    if text.isdigit():
        return float(int(text))
    stated = COUNT_WORDS.get(text)
    if stated is None:
        stated = COUNT_WORDS.get(fold(text))
    return float(stated) if stated is not None else None


def _to_severity_match(match: RowMatch) -> SeverityMatch:
    cue: vocabulary.SeverityCue = match.row
    return SeverityMatch(
        field=match.field,
        entry=match.entry,
        surface=match.surface,
        rule=match.rule,
        category=cue.category,
        band=cue.band,
        weight=cue.weight,
        direction=cue.direction,
        strength=match.strength,
        char_start=match.char_start,
        char_end=match.char_end,
        sentence=match.sentence,
        note=cue.note,
        quality=match.quality,
        count_kind=cue.count_kind,
    )


def find_severity(source: SourceField, prose: Prose) -> SeverityFinding:
    """Read one field for what it argued about how serious the event was."""
    matches, refusals = signal_text.find_matches(prose, CUE_INDEX)
    cited = tuple(
        replace(
            _to_severity_match(match),
            evidence=source.evidence_at(
                match.span,
                method=METHOD,
                confidence=match.quality,
                notes=(
                    f"{match.row.category.value} cue for the {match.row.band.value} band read "
                    f"from {match.surface!r} by the {match.rule} rule ({match.row.note}); "
                    f"weight {match.row.weight:g} x quality {match.quality:g} = "
                    f"{match.strength:.2f}"
                ),
            ),
        )
        for match in matches
    )
    return SeverityFinding(field=prose.field, matches=cited, refusals=refusals)


def find_counts(source: SourceField, prose: Prose) -> tuple[QuantityMatch, ...]:
    """Read one field for every number the source itself attached to a noun it counts."""
    if prose.is_empty:
        return ()
    words = prose.words
    found: list[QuantityMatch] = []
    claimed: set[int] = set()
    for position, word in enumerate(words):
        if position in claimed:
            continue
        value = count_value(word.text)
        if value is None:
            continue
        for offset in range(1, signal_text.HEAD_WINDOW + 1):
            head_at = position + offset
            if head_at >= len(words) or words[head_at].sentence != word.sentence:
                break
            fitted = HEAD_INDEX.row_at(words, head_at, source_text=prose.text)
            if fitted is None:
                continue
            compiled, length = fitted
            end = head_at + length
            if not mention_text.contiguous(words, position, end, source_text=prose.text):
                continue
            span = mention_text.quote_of(words, position, end, prose.text)
            head: vocabulary.CountHead = compiled.row
            found.append(
                QuantityMatch(
                    field=prose.field,
                    entry=head.surface,
                    surface=span.quote,
                    value=value,
                    kind=head.kind,
                    unit=head.unit or NO_UNIT,
                    rule=compiled.rule,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    sentence=word.sentence,
                    evidence=source.evidence_at(
                        span,
                        method=METHOD,
                        confidence=match_quality(compiled.rule),
                        notes=(
                            f"the source stated {span.quote!r}: {value:g} {head.unit} counted "
                            f"as {head.kind.value} ({head.note})"
                        ),
                    ),
                )
            )
            claimed.update(range(position, end))
            break
    return tuple(found)


def match_quality(rule: str) -> float:
    return signal_text.QUALITY[rule]


def extract(
    readings: Sequence[tuple[SourceField, Prose]],
) -> tuple[SeverityExtraction, tuple[QuantityMatch, ...]]:
    """Every severity cue and every stated count across the fields this record can be read in."""
    if not readings:
        return SeverityExtraction(record_id=""), ()
    record_id = readings[0][0].record_id
    findings = tuple(
        finding
        for finding in (find_severity(source, prose) for source, prose in readings)
        if finding.matches or finding.refusals
    )
    counts = tuple(count for source, prose in readings for count in find_counts(source, prose))
    return SeverityExtraction(record_id=record_id, findings=findings), counts


def _typed(
    counts: Sequence[QuantityMatch], matches: Sequence[SeverityMatch]
) -> tuple[QuantityMatch, ...]:
    """Give a bare count of people the kind its own sentence's cue named, when one cue named one."""
    typed: list[QuantityMatch] = []
    for count in counts:
        if count.kind is not ObservationKind.COUNT_GENERIC:
            typed.append(count)
            continue
        around = {
            match.count_kind
            for match in matches
            if match.count_kind is not None
            and match.field == count.field
            and match.sentence == count.sentence
        }
        if len(around) == 1:
            (kind,) = tuple(around)
            typed.append(replace(count, kind=kind))
        else:
            typed.append(count)
    return tuple(typed)


def _cue_signal(record_id: str, match: SeverityMatch, *, number: int) -> SeveritySignal:
    return SeveritySignal(
        signal_id=f"sig-{record_id}-{number}",
        category=match.category,
        cue_text=match.surface,
        weight=round(match.weight, 2),
        direction=match.direction,
        method=ExtractionMethod.DICTIONARY,
        confidence=round(match.quality, 2),
        evidence_ids=[match.evidence_id],
    )


def _count_signal(
    record_id: str, count: QuantityMatch, *, number: int
) -> SeveritySignal:
    return SeveritySignal(
        signal_id=f"sig-{record_id}-{number}",
        category=KIND_CATEGORIES.get(count.kind, SeverityCueCategory.OTHER),
        cue_text=count.surface,
        weight=MAGNITUDE_WEIGHT,
        direction=vocabulary.ESCALATING,
        magnitude_observed=count.display,
        method=ExtractionMethod.DICTIONARY,
        confidence=round(count.quality, 2),
        evidence_ids=[count.evidence_id],
    )


def _declared_signal(declared: DeclaredSeverity) -> SeveritySignal:
    return SeveritySignal(
        signal_id=f"sig-{declared.source_id}-{declared.level.value}",
        category=SeverityCueCategory.AUTHORITY_ORDER,
        cue_text=declared.value,
        weight=1.0,
        direction=vocabulary.ESCALATING,
        method=ExtractionMethod.SOURCE_METADATA,
        confidence=CONF_AUTHORITATIVE,
        evidence_ids=[declared.evidence_id],
    )


def _next_down(level: SeverityLevel) -> SeverityLevel:
    rank = severity_rank(level)
    if rank is None or rank <= severity_rank(SeverityLevel.LOW):
        return level
    return SEVERITY_LEVEL_ORDER[rank - 1]


def assess_severity(
    record_id: str,
    extraction: SeverityExtraction,
    counts: Sequence[QuantityMatch] = (),
    *,
    declared: Optional[DeclaredSeverity] = None,
    source_value: Optional[str] = None,
    carried_evidence: Sequence[str] = (),
) -> tuple[Severity, list[Observation]]:
    """Weigh what the source argued, lift it by what it counted, and never round up on a hunch."""
    typed = _typed(counts, extraction.matches)
    observations = [
        Observation(
            observation_id=f"obs-{record_id}-{number}",
            kind=count.kind,
            value_numeric=count.value,
            unit=count.unit,
            raw_text=count.surface,
            qualifier=ObservationQualifier.EXACT,
            method=ExtractionMethod.DICTIONARY,
            confidence=round(count.quality, 2),
            evidence_ids=[count.evidence_id],
            notes=(
                "the source stated this count itself; it is not an estimate made from wording "
                "about the event"
            ),
        )
        for number, count in enumerate(typed)
    ]

    cues = extraction.accepted()
    signals: list[SeveritySignal] = [
        _cue_signal(record_id, match, number=number) for number, match in enumerate(cues)
    ]
    argued: list[SeverityLevel] = [match.band for match in cues]

    next_number = len(cues)
    for count in typed:
        band = vocabulary.band_for_count(count.kind, count.value)
        if band is None:
            continue
        signals.append(_count_signal(record_id, count, number=next_number))
        argued.append(band)
        next_number += 1

    if declared is not None:
        signals.append(_declared_signal(declared))
        argued.append(declared.level)

    pairs = list(zip(signals, argued))
    escalating = [pair for pair in pairs if pair[0].direction == vocabulary.ESCALATING]
    mitigating = [pair for pair in pairs if pair[0].direction == vocabulary.MITIGATING]

    if not escalating:
        note = (
            "nothing in this record argued that the event was serious: "
            f"{len(extraction.matches)} cue(s) read, {len(extraction.refusals)} refused"
            + (
                f", {len(mitigating)} of them reading the situation as contained, which states "
                "no level on its own"
                if mitigating
                else ""
            )
            + (
                f"; the source's own severity field {source_value!r} names no scale this module "
                "is told to trust"
                if source_value is not None
                else ""
            )
        )
        return (
            Severity(
                level=SeverityLevel.UNRESOLVED,
                notes=note,
                signals=signals,
                evidence_ids=list(
                    dict.fromkeys(
                        (
                            *carried_evidence,
                            *(signal.evidence_ids[0] for signal in signals),
                            *(match.evidence_id for match in extraction.matches),
                        )
                    )
                ),
            ),
            observations,
        )

    escalating_signals = [signal for signal, _ in escalating]
    escalating_levels = [level for _, level in escalating]
    top = max(escalating_levels, key=lambda band: severity_rank(band))
    fatal = any(
        signal.category is SeverityCueCategory.FATALITY for signal in escalating_signals
    )
    level = _next_down(top) if mitigating and not fatal else top

    categories = {signal.category for signal in escalating_signals}
    quantified = any(signal.magnitude_observed for signal in escalating_signals)
    if declared is not None:
        confidence = CONF_AUTHORITATIVE
    elif quantified:
        confidence = CONF_QUANTIFIED
    elif len(categories) > 1:
        confidence = CONF_TWO_CATEGORIES
    else:
        confidence = CONF_SINGLE_CUE
    confidence = min(CONF_CAP, confidence)
    score = min(
        SCORE_CAP, BAND_BASE[level] + SIGNAL_STEP * max(0, len(escalating_signals) - 1)
    )

    rationale = (
        f"{len(escalating_signals)} escalating cue(s) over {len(categories)} category(ies) "
        + "("
        + ", ".join(sorted(category.value for category in categories))
        + f") argued as far as {top.value}"
        + (
            f", which {len(mitigating)} cue(s) reading the other way ("
            + ", ".join(repr(signal.cue_text) for signal, _ in mitigating)
            + ") held at "
            + level.value
            if level is not top
            else ", which nothing argued down"
        )
        + f", so the level is {level.value}"
        + ("; the source set this one on its own scale" if declared is not None else "")
    )
    if observations:
        rationale += (
            "; counts the source stated itself: "
            + ", ".join(
                f"{observation.display_value} {observation.kind.value}" for observation in observations
            )
        )

    return (
        Severity(
            level=level,
            score=round(score, 2),
            signals=signals,
            rationale=rationale,
            is_authoritative=declared is not None,
            confirmed_by=f"{declared.source_id} {declared.kind}: {declared.value}"
            if declared is not None
            else None,
            method=ExtractionMethod.SOURCE_METADATA
            if declared is not None
            else ExtractionMethod.DICTIONARY,
            confidence=confidence,
            evidence_ids=list(
                dict.fromkeys(
                    (
                        *carried_evidence,
                        *(signal.evidence_ids[0] for signal in signals),
                    )
                )
            ),
            notes=(
                "bands came from cue categories and from counts the source stated; the event "
                "type was never consulted"
            ),
        ),
        observations,
    )


__all__ = [
    "BAND_BASE",
    "CONF_CAP",
    "CUE_INDEX",
    "HEAD_INDEX",
    "KIND_CATEGORIES",
    "MAGNITUDE_WEIGHT",
    "PROVIDER",
    "SCORE_CAP",
    "SIGNAL_STEP",
    "DeclaredSeverity",
    "Observation",
    "QuantityMatch",
    "Severity",
    "SeverityExtraction",
    "SeverityFinding",
    "SeverityMatch",
    "SeveritySignal",
    "assess_severity",
    "count_value",
    "extract",
    "find_counts",
    "find_severity",
]
