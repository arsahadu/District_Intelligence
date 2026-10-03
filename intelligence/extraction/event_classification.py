"""Stage 7 decisions: whether this record states an event, and which kind of event the evidence supports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from intelligence.config import event_type_cues as vocabulary
from intelligence.config.vocabularies import TAXONOMY_VERSION, family_of, primary_department_for
from intelligence.extraction.cues import CueExtraction, CueMatch
from intelligence.extraction.mention_text import fold
from intelligence.models.actors import Actor
from intelligence.models.classification import ClassificationInfo, DepartmentHint, RelevanceInfo
from intelligence.models.enums import (
    Department,
    DepartmentHintBasis,
    ExtractionMethod,
    EventType,
    RelevanceState,
)
from intelligence.models.spatial import SpatialHint

PROVIDER = "intelligence.extraction.event_classification"

#: What one cue is worth on its own: a supporting word never decides an event type alone.
WEIGHTS = {vocabulary.PRIMARY: 0.80, vocabulary.SUPPORTING: 0.30}
STRUCTURED_WEIGHT = 0.35
CORROBORATION = 0.05
MAX_CORROBORATION = 0.15
SCORE_CAP = 0.95

#: A score under this leaves the event type unresolved, however it was reached.
RESOLVE_FLOOR = 0.55
#: Two candidates this close are a tie, not a winner.
TIE_MARGIN = 0.10
#: Below this a candidate is noise, not a second opinion worth recording.
CANDIDATE_FLOOR = 0.24
MAX_SECONDARIES = 3

CONF_NOT_EVENT = 0.85
CONF_OBSERVATION = 0.80
CONF_EVENT_CUE = 0.90

NOTICE_UNRESOLVED = "left unresolved rather than guessed"


@dataclass(frozen=True)
class TypeScore:
    """One candidate event type, what it scored, and the evidence that scored it."""

    event_type: EventType
    score: float
    sources: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    has_primary_cue: bool

    def describe(self) -> str:
        return f"{self.event_type.value} {self.score:.2f} from " + ", ".join(self.sources)


def score_types(cues: CueExtraction, record_type: str) -> tuple[TypeScore, ...]:
    """Every type this record argued for, strongest first. A word twice corroborates nothing."""
    gathered: dict[EventType, list[tuple[float, str, Optional[str]]]] = {}
    primaries: dict[EventType, bool] = {}

    for match in cues.matches:
        gathered.setdefault(match.event_type, []).append(
            (WEIGHTS[match.strength] * match.quality, match.entry, match.evidence_id)
        )
        if match.strength == vocabulary.PRIMARY:
            primaries[match.event_type] = True
    for candidate in vocabulary.candidates_for(record_type):
        gathered.setdefault(candidate, []).append((STRUCTURED_WEIGHT, "record-kind", None))

    scores: list[TypeScore] = []
    for event_type, contributions in gathered.items():
        best = max(value for value, _, _ in contributions)
        sources = tuple(sorted({source for _, source, _ in contributions}))
        bonus = min(MAX_CORROBORATION, CORROBORATION * (len(sources) - 1))
        evidence_ids = tuple(dict.fromkeys(eid for _, _, eid in contributions if eid))
        scores.append(
            TypeScore(
                event_type=event_type,
                score=round(min(SCORE_CAP, best + bonus), 4),
                sources=sources,
                evidence_ids=evidence_ids,
                has_primary_cue=primaries.get(event_type, False),
            )
        )
    return tuple(sorted(scores, key=lambda item: (-item.score, item.event_type.value)))


def _worth_recording(scores: Sequence[TypeScore]) -> tuple[TypeScore, ...]:
    kept = [item for item in scores if item.score >= CANDIDATE_FLOOR]
    return tuple(kept[:MAX_SECONDARIES])


def _names(items: Sequence[object], attribute: str) -> str:
    return ", ".join(sorted({_shown(getattr(item, attribute)) for item in items}))


def _shown(value: object) -> str:
    return value.value if isinstance(value, Enum) else str(value)


@dataclass(frozen=True)
class Anchor:
    """What ties this record to a district this platform covers, and the evidence that says so."""

    district_hint: Optional[str] = None
    from_text: bool = False
    competing_districts: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return bool(self.evidence_ids)

    @property
    def contested(self) -> bool:
        return bool(self.competing_districts) and not self.from_text


def anchor_of(spatial: SpatialHint, district_evidence_ids: Sequence[str] = ()) -> Anchor:
    """Read the district state Stage 5 and Stage 6 settled, without touching it.

    A place the text names is not yet a district this platform covers: only GIS resolves that,
    so the anchor is the district the record states and the text that agrees with it.
    """
    if not spatial.district_hint:
        return Anchor(competing_districts=spatial.competing_districts)
    from_text = spatial.district_hint_from_text
    cited = list(district_evidence_ids)
    if from_text:
        agreed = {fold(from_text), fold(spatial.district_hint)}
        cited.extend(
            evidence_id
            for mention in spatial.mentions
            if fold(mention.text) in agreed or fold(mention.text_normalized or "") in agreed
            for evidence_id in mention.evidence_ids
        )
    return Anchor(
        district_hint=spatial.district_hint,
        from_text=bool(from_text),
        competing_districts=spatial.competing_districts,
        evidence_ids=tuple(dict.fromkeys(cited)),
    )


def _primary_matches(cues: CueExtraction) -> tuple[CueMatch, ...]:
    return tuple(match for match in cues.matches if match.decisive)


def decide_relevance(
    *,
    source_type: str,
    record_type: str,
    cues: CueExtraction,
    anchor: Anchor,
    kind_ids: Sequence[str] = (),
) -> RelevanceInfo:
    """Is this record an event this district cares about? Only a stated answer is asserted."""
    markers = cues.non_events
    primary = _primary_matches(cues)
    every_cue = tuple(match.evidence_id for match in cues.matches)
    marker_ids = tuple(marker.evidence_id for marker in markers)

    if markers and not primary:
        return RelevanceInfo(
            state=RelevanceState.NOT_INCIDENT,
            is_incident=False,
            reason=(
                "the only event-shaped words are "
                + _names(markers, "entry")
                + ", which state paid space rather than something that happened"
            ),
            method=ExtractionMethod.RULE,
            confidence=CONF_NOT_EVENT,
            evidence_ids=list(marker_ids),
        )

    if vocabulary.is_observation_feed(source_type, record_type):
        if anchor.contested:
            return _unsure(
                "record_type "
                + repr(record_type)
                + " is a district observation and the text places it in more than one district: "
                + ", ".join(anchor.competing_districts),
                evidence_ids=(*kind_ids, *anchor.evidence_ids, *every_cue),
            )
        if not anchor.present:
            return _unsure(
                "record_type "
                + repr(record_type)
                + " is a district observation and nothing in the record says which district it "
                "observes",
                evidence_ids=(*kind_ids, *every_cue),
            )
        return RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=True,
            reason=(
                "source_type "
                + repr(source_type)
                + " with record_type "
                + repr(record_type)
                + " is an observation this platform tracks, and the record states the district it "
                "observes"
            ),
            method=ExtractionMethod.SOURCE_METADATA,
            confidence=CONF_OBSERVATION,
            evidence_ids=[*kind_ids, *anchor.evidence_ids, *every_cue],
        )

    if primary:
        if anchor.contested:
            return _unsure(
                "the text states an event and names more than one district, so which district this "
                "event belongs to was not decided: " + ", ".join(anchor.competing_districts),
                evidence_ids=(*anchor.evidence_ids, *every_cue),
            )
        if not anchor.present:
            return _unsure(
                "the text states "
                + _names(primary, "event_type")
                + " and nothing in the record ties it to a district this platform covers",
                evidence_ids=every_cue,
            )
        return RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=True,
            reason=(
                "the text states "
                + _names(primary, "event_type")
                + " as what happened, and the record states the district it happened in"
            ),
            method=ExtractionMethod.DICTIONARY,
            confidence=CONF_EVENT_CUE,
            evidence_ids=[*every_cue, *anchor.evidence_ids],
        )

    if markers:
        return _unsure(
            "the text carries paid-space markers "
            + _names(markers, "entry")
            + " beside real event words, so neither was believed",
            evidence_ids=(*marker_ids, *every_cue),
        )

    if cues.matches:
        return _unsure(
            "the event words found name a thing involved ("
            + _names(cues.matches, "event_type")
            + ") rather than something that happened",
            evidence_ids=list(every_cue),
        )

    return RelevanceInfo(
        state=RelevanceState.UNRESOLVED,
        reason=(
            "no event word, no non-event marker and no tracked observation record kind: relevance "
            "was not decided from nothing"
        ),
    )


def _unsure(reason: str, *, evidence_ids: Sequence[str]) -> RelevanceInfo:
    return RelevanceInfo(
        state=RelevanceState.UNSURE,
        reason=reason,
        method=ExtractionMethod.RULE,
        confidence=None,
        evidence_ids=[evidence_id for evidence_id in evidence_ids if evidence_id],
    )


def decide_event_type(
    *,
    scores: Sequence[TypeScore],
    relevance: RelevanceInfo,
    actors: Sequence[Actor] = (),
) -> ClassificationInfo:
    """Name a type only when one candidate clearly outscored the rest."""
    distribution = {item.event_type.value: item.score for item in scores}

    if relevance.state is RelevanceState.NOT_INCIDENT:
        return ClassificationInfo(
            notes=(
                "relevance says this record states no event ("
                + str(relevance.reason)
                + "), so no event type was asserted"
            ),
        )
    if not scores:
        return ClassificationInfo(
            notes=(
                "no event cue and no record-kind candidate in the fields this policy read; "
                + NOTICE_UNRESOLVED
            ),
        )

    top = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    margin = 1.0 if runner_up is None else round(top.score - runner_up.score, 4)

    if top.score < RESOLVE_FLOOR:
        kept = _worth_recording(scores)
        return ClassificationInfo(
            category_scores=distribution,
            taxonomy_version=TAXONOMY_VERSION,
            secondary_event_types=[item.event_type for item in kept],
            notes=(
                "the strongest candidate "
                + top.event_type.value
                + f" scored {top.score:.2f}, under the {RESOLVE_FLOOR:.2f} a type needs; "
                + (_names(kept, "event_type") or "nothing")
                + " kept as candidates, "
                + NOTICE_UNRESOLVED
            ),
        )

    if runner_up is not None and margin < TIE_MARGIN:
        tied = _worth_recording(
            [item for item in scores if round(top.score - item.score, 4) < TIE_MARGIN]
        )
        return ClassificationInfo(
            category_scores=distribution,
            taxonomy_version=TAXONOMY_VERSION,
            secondary_event_types=[item.event_type for item in tied],
            notes=(
                top.event_type.value
                + f" scored {top.score:.2f} and "
                + runner_up.event_type.value
                + f" scored {runner_up.score:.2f}, a margin under {TIE_MARGIN:.2f}; "
                + _names(tied, "event_type")
                + " left unresolved between them"
            ),
        )

    return ClassificationInfo(
        event_type=top.event_type,
        category_scores=distribution,
        taxonomy_version=TAXONOMY_VERSION,
        family=family_of(top.event_type),
        departments=department_hints(top.event_type, top.score, actors),
        method=ExtractionMethod.DICTIONARY,
        confidence=top.score,
        evidence_ids=list(top.evidence_ids),
        notes=(
            top.event_type.value
            + f" scored {top.score:.2f} from "
            + ", ".join(top.sources)
            + (
                f"; the nearest candidate {runner_up.event_type.value} scored {runner_up.score:.2f}"
                if runner_up
                else "; no other candidate was argued for"
            )
        ),
    )


def department_hints(
    event_type: EventType, confidence: float, actors: Sequence[Actor] = ()
) -> list[DepartmentHint]:
    """The office the taxonomy names, plus any office a party named in the text belongs to."""
    hints: dict[Department, DepartmentHint] = {}
    nodal = primary_department_for(event_type)
    if nodal is not None and nodal not in (Department.UNRESOLVED, Department.OTHER):
        hints[nodal] = DepartmentHint(
            department=nodal,
            basis=DepartmentHintBasis.TAXONOMY,
            confidence=confidence,
        )

    for actor in actors:
        department = vocabulary.ACTOR_DEPARTMENT_HINTS.get(actor.actor_type)
        if department is None or not actor.evidence_ids or actor.confidence is None:
            continue
        existing = hints.get(department)
        if existing is not None:
            hints[department] = existing.model_copy(
                update={
                    "basis": DepartmentHintBasis.BOTH,
                    "confidence": max(existing.confidence or 0.0, actor.confidence),
                    "evidence_ids": list(
                        dict.fromkeys((*existing.evidence_ids, *actor.evidence_ids))
                    ),
                }
            )
            continue
        hints[department] = DepartmentHint(
            department=department,
            basis=DepartmentHintBasis.TEXT_MENTION,
            confidence=actor.confidence,
            evidence_ids=list(actor.evidence_ids),
        )
    return list(hints.values())


__all__ = [
    "CANDIDATE_FLOOR",
    "CONF_EVENT_CUE",
    "CONF_NOT_EVENT",
    "CONF_OBSERVATION",
    "MAX_SECONDARIES",
    "PROVIDER",
    "RESOLVE_FLOOR",
    "STRUCTURED_WEIGHT",
    "TIE_MARGIN",
    "WEIGHTS",
    "Anchor",
    "TypeScore",
    "anchor_of",
    "decide_event_type",
    "decide_relevance",
    "department_hints",
    "score_types",
]
