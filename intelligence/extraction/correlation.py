"""Stage 8: the deterministic signals that decide whether two incidents account for one event."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from intelligence.extraction.mention_text import fold
from intelligence.models.correlation import IncidentCluster, PairRelationship
from intelligence.models.enums import (
    DedupDecision,
    EventType,
    GranularityLevel,
    RelevanceState,
    TimePrecision,
    TimeSemantics,
)

DEDUP_ALGORITHM_VERSION = "2026.10-stage8"

W_CONTENT = 0.50
W_TITLE = 0.20
W_TIME = 0.10
W_PLACE = 0.10
W_ACTOR = 0.10

# Unrelated Dinamalar articles share ~0.65 of their tokens through the page's navigation boilerplate,
# so every floor that can merge sits above that band.
DUPLICATE_CONTENT = 0.85
# Containment is size-guarded, so unrelated articles top out near 0.31 shared wording; these floors
# sit well above that band so only wording the two accounts really share can carry a merge.
MERGE_CONTENT = 0.55
MERGE_COMPOSITE = 0.60
UNCERTAIN_COMPOSITE = 0.45
LINK_COMPOSITE = 0.35
CONTRADICTION_CONTENT = 0.60
GROUP_FLOOR = 0.5

MIN_CONTAINMENT_TOKENS = 12
MIN_CONTAINMENT_RATIO = 0.5
NEAR_HOURS = 6
SAME_DAY_HOURS = 24
RELATED_DAYS = 3

CONF_IDENTICAL = 0.98
CONF_SAME_PAGE = 0.95
CONF_CONTRADICTION = 0.70
CONF_SEPARATION = 0.85

DISTRICT_LEVEL = frozenset(
    {
        GranularityLevel.COUNTRY,
        GranularityLevel.STATE,
        GranularityLevel.DISTRICT,
        GranularityLevel.UNKNOWN,
    }
)
_COARSE_TIME = frozenset(
    {TimePrecision.DAY, TimePrecision.WEEK, TimePrecision.MONTH, TimePrecision.YEAR}
)
_TOKEN_PATTERN = re.compile(r"[0-9a-z\u0b80-\u0bff]+")
_NO_STAMP = float(2**53)

DISTRICT_BLOCKER = "the records name different districts"
TYPE_BLOCKER = "the two records were classified as different event types"
TIME_BLOCKER = "the stated event times are more than three days apart"
RESTATE_BLOCKER = "each record states its own event day, so one is not a second copy of the other"
RELEVANCE_BLOCKER = "at least one of the two is not established as an incident"
OBSERVATION_BLOCKER = "an observation feed states a condition, not an event another feed reports"


@dataclass(frozen=True)
class IncidentProfile:
    """Everything Stage 8 may compare one incident on."""

    incident_id: str
    record_id: str
    source_id: str
    source_type: str
    record_type: str

    fingerprint: str
    token_signature: str
    record_signature: str

    title_tokens: frozenset[str]
    body_tokens: frozenset[str]

    event_type: EventType = EventType.UNRESOLVED
    secondary_types: frozenset[str] = frozenset()
    relevance_state: RelevanceState = RelevanceState.UNRESOLVED
    is_observation: bool = False

    event_instant: Optional[datetime] = None
    event_precision: TimePrecision = TimePrecision.UNKNOWN
    time_semantics: TimeSemantics = TimeSemantics.UNKNOWN
    reported_at: Optional[datetime] = None

    district: Optional[str] = None
    place_surfaces: frozenset[str] = frozenset()
    actor_names: frozenset[str] = frozenset()

    source_url: Optional[str] = None
    evidence_ids: frozenset[str] = frozenset()
    language: str = "und"

    @property
    def is_incident(self) -> bool:
        return self.relevance_state is RelevanceState.INCIDENT

    @property
    def type_is_resolved(self) -> bool:
        return self.event_type is not EventType.UNRESOLVED

    @property
    def has_prose(self) -> bool:
        return bool(self.body_tokens)


@dataclass(frozen=True)
class PairSignals:
    """Signals for one pair. District, date and type alone stay under every floor by design."""

    content: float = 0.0
    title: float = 0.0
    time: float = 0.0
    place: float = 0.0
    actor: float = 0.0

    @property
    def composite(self) -> float:
        return round(
            W_CONTENT * self.content
            + W_TITLE * self.title
            + W_TIME * self.time
            + W_PLACE * self.place
            + W_ACTOR * self.actor,
            4,
        )

    @property
    def independent_groups(self) -> int:
        return sum(
            1
            for value in (self.content, self.title, self.time, self.place, self.actor)
            if value >= GROUP_FLOOR
        )

    def as_features(self) -> dict[str, float]:
        return {
            "content": self.content,
            "title": self.title,
            "time": self.time,
            "place": self.place,
            "actor": self.actor,
            "composite": self.composite,
        }


@dataclass(frozen=True)
class PairReading:
    """The signals plus the sentences that explain them."""

    signals: PairSignals
    time_note: str
    place_note: str


def tokenize(text: str) -> frozenset[str]:
    """Folded word tokens; a lone mark carries no meaning and is dropped."""
    return frozenset(
        token
        for token in (fold(part) for part in _TOKEN_PATTERN.findall(text.lower()))
        if len(token) > 1
    )


def text_fingerprint(text: str) -> str:
    """Order-sensitive print fingerprint."""
    collapsed = " ".join(text.split()).casefold()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:16]


def token_signature(tokens: Iterable[str]) -> str:
    """Order-insensitive signature, so a story restated with its blocks moved is still caught."""
    joined = "|".join(sorted(tokens))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def page_of(url: Optional[str]) -> Optional[str]:
    """The page a url points at without fragment or query: one article, however anchored."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.netloc or not parts.path:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard, plus containment once the two sides are near enough the same length.

    Containment is what catches a story restated with a sentence dropped; against a much longer
    document it would only ever measure how much boilerplate the short one repeats.
    """
    if not a or not b:
        return 0.0
    shared = len(a & b)
    smaller, larger = sorted((len(a), len(b)))
    jaccard = shared / (smaller + larger - shared)
    if smaller >= MIN_CONTAINMENT_TOKENS and smaller * MIN_CONTAINMENT_RATIO >= larger:
        return round(max(jaccard, 0.9 * shared / smaller), 4)
    return round(jaccard, 4)


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def time_signal(a: IncidentProfile, b: IncidentProfile) -> tuple[float, str]:
    first, second = _naive(a.event_instant), _naive(b.event_instant)
    if first is None or second is None:
        gap = _stamp_gap(a, b)
        if gap is not None and gap <= SAME_DAY_HOURS:
            return 0.35, "both were fetched the same day; neither states when it happened"
        return 0.0, "no time established on either side"

    gap = abs((first - second).total_seconds()) / 3600
    same_day = first.date() == second.date()
    if not _both_event_times(a, b):
        if gap <= 1:
            return 0.5, "both carry the same publication stamp"
        if gap <= SAME_DAY_HOURS:
            return 0.35, "published the same day; neither side states an event time"
        return 0.0, "publication stamps days apart"
    if gap <= 1 or (same_day and (_COARSE_TIME & {a.event_precision, b.event_precision})):
        return 1.0, "the event time is the same"
    if gap <= NEAR_HOURS:
        return 0.7, "event time within six hours"
    if gap <= SAME_DAY_HOURS:
        return 0.5, "event time within a day"
    if gap <= RELATED_DAYS * 24:
        return 0.2, "event time within three days"
    return 0.0, "event times " + str(int(gap // 24)) + " days apart"


def _stamp_gap(a: IncidentProfile, b: IncidentProfile) -> Optional[float]:
    """Hours apart at publication, and only when neither side claims an event time."""
    if a.time_semantics is TimeSemantics.EVENT_TIME or b.time_semantics is TimeSemantics.EVENT_TIME:
        return None
    first, second = _naive(a.reported_at), _naive(b.reported_at)
    if first is None or second is None:
        return None
    return abs((first - second).total_seconds()) / 3600


def place_signal(a: IncidentProfile, b: IncidentProfile) -> tuple[float, str]:
    shared = sorted(a.place_surfaces & b.place_surfaces)
    if shared:
        return 0.9, "the text names the same place: " + ", ".join(shared[:3])
    districts = [profile.district for profile in (a, b) if profile.district]
    if len(districts) == 2 and fold(districts[0]) == fold(districts[1]):
        return 0.3, "same district, but no shared place named in the text"
    return 0.0, "no place in common"


def _both_event_times(a: IncidentProfile, b: IncidentProfile) -> bool:
    """Only a stated event time can separate two accounts; a publication stamp cannot."""
    return (
        a.time_semantics is TimeSemantics.EVENT_TIME
        and b.time_semantics is TimeSemantics.EVENT_TIME
        and a.event_instant is not None
        and b.event_instant is not None
    )


def _restated(a: IncidentProfile, b: IncidentProfile) -> bool:
    """Two records that each state an event day, and state different ones, are two reports.

    A district forecast page re-read every day, or a daily price row, prints near enough the same words
    each time and lives at one url; only the day it speaks of tells them apart.
    """
    if not _both_event_times(a, b):
        return False
    return _naive(a.event_instant).date() != _naive(b.event_instant).date()


def type_conflicts(a: IncidentProfile, b: IncidentProfile) -> bool:
    if not (a.type_is_resolved and b.type_is_resolved):
        return False
    if a.event_type is b.event_type:
        return False
    return b.event_type not in a.secondary_types and a.event_type not in b.secondary_types


def read_pair(a: IncidentProfile, b: IncidentProfile) -> PairReading:
    time_score, time_note = time_signal(a, b)
    place_score, place_note = place_signal(a, b)
    content = _overlap(a.body_tokens, b.body_tokens)
    if a.fingerprint == b.fingerprint and a.has_prose:
        content = 1.0
    signals = PairSignals(
        content=content,
        title=_overlap(a.title_tokens, b.title_tokens),
        time=time_score,
        place=place_score,
        actor=_overlap(a.actor_names, b.actor_names),
    )
    return PairReading(signals=signals, time_note=time_note, place_note=place_note)


def compare_profiles(a: IncidentProfile, b: IncidentProfile) -> PairRelationship:
    """One pair's verdict, the signals it rests on, and what kept it from being stronger."""
    if a.incident_id == b.incident_id:
        raise ValueError("an incident cannot be correlated with itself")

    read = read_pair(a, b)
    signals = read.signals
    same_source = a.source_id == b.source_id and a.source_type == b.source_type
    conflict = type_conflicts(a, b)
    page_first, page_second = page_of(a.source_url), page_of(b.source_url)
    same_page = (
        page_first is not None
        and page_first == page_second
        and a.has_prose
        and b.has_prose
    )
    identical_text = a.has_prose and b.has_prose and (
        a.fingerprint == b.fingerprint or a.token_signature == b.token_signature
    )
    identical_record = a.record_signature == b.record_signature
    restates = _restated(a, b)

    reasons: list[str] = []
    if a.record_id == b.record_id:
        reasons.append("the same record id was supplied twice")
    if identical_record:
        reasons.append("every readable field of the two records prints the same text")
    if identical_text and a.fingerprint == b.fingerprint:
        reasons.append("the printed text is identical")
    elif identical_text:
        reasons.append("the same words, printed in a different order")
    if same_page and same_source and not restates:
        reasons.append("the same page, fetched a second time")
    if signals.content >= DUPLICATE_CONTENT and a.body_tokens:
        reasons.append("body text " + f"{signals.content:.2f}" + " alike")
    if signals.title >= 0.8:
        reasons.append("the headline is " + f"{signals.title:.2f}" + " alike")
    if signals.actor >= GROUP_FLOOR:
        reasons.append("the same parties are named")

    blockers: list[str] = []
    districts = [profile.district for profile in (a, b) if profile.district]
    if len(districts) == 2 and fold(districts[0]) != fold(districts[1]):
        blockers.append(DISTRICT_BLOCKER)
    if conflict:
        blockers.append(TYPE_BLOCKER)
    if signals.time == 0.0 and _both_event_times(a, b):
        blockers.append(TIME_BLOCKER)
    if restates:
        blockers.append(RESTATE_BLOCKER)
    if not (a.is_incident and b.is_incident):
        blockers.append(RELEVANCE_BLOCKER)
    if a.is_observation or b.is_observation:
        blockers.append(OBSERVATION_BLOCKER)

    apart = DISTRICT_BLOCKER in blockers
    if apart and not (identical_text or identical_record):
        decision, confidence = DedupDecision.UNIQUE, CONF_SEPARATION
    elif a.record_id == b.record_id:
        decision, confidence = DedupDecision.DUPLICATE, CONF_IDENTICAL
    elif restates:
        reasons.append("separated: " + RESTATE_BLOCKER)
        decision, confidence = DedupDecision.UNIQUE, CONF_SEPARATION
    elif identical_record and not conflict:
        decision, confidence = DedupDecision.DUPLICATE, CONF_IDENTICAL
    elif same_page and same_source and not conflict:
        decision, confidence = DedupDecision.DUPLICATE, CONF_SAME_PAGE
    elif identical_text and not conflict:
        decision, confidence = DedupDecision.DUPLICATE, CONF_IDENTICAL
    elif conflict and signals.content >= CONTRADICTION_CONTENT:
        reasons.append("one story, two incompatible accounts of it")
        decision, confidence = DedupDecision.CONTRADICTS, CONF_CONTRADICTION
    elif same_source and signals.content >= DUPLICATE_CONTENT:
        decision, confidence = DedupDecision.DUPLICATE, round(
            min(0.95, 0.65 + 0.3 * signals.content), 2
        )
    elif not blockers and signals.content >= MERGE_CONTENT and (
        signals.composite >= MERGE_COMPOSITE
    ):
        reasons.extend([read.time_note, read.place_note])
        decision, confidence = DedupDecision.MERGE_CANDIDATE, round(
            min(0.9, 0.5 + 0.5 * signals.composite), 2
        )
    elif signals.composite >= UNCERTAIN_COMPOSITE:
        reasons.append("overlap worth a human look: composite " + f"{signals.composite:.2f}")
        decision, confidence = DedupDecision.UNRESOLVED, None
    elif _linkable(blockers) and signals.composite >= LINK_COMPOSITE and (
        signals.independent_groups >= 2
    ):
        reasons.extend([read.time_note, read.place_note])
        decision, confidence = DedupDecision.LINKED, round(
            min(0.65, 0.4 + 0.5 * signals.composite), 2
        )
    else:
        if blockers:
            reasons.append("separated: " + "; ".join(blockers))
        else:
            reasons.append("no content, time, place or party overlap worth recording")
        decision, confidence = DedupDecision.UNIQUE, CONF_SEPARATION if blockers else None

    return PairRelationship(
        left_incident_id=a.incident_id,
        right_incident_id=b.incident_id,
        decision=decision,
        similarity=signals.composite,
        features=signals.as_features(),
        same_source=same_source,
        cross_source=not same_source,
        reasons=list(dict.fromkeys(reason for reason in reasons if reason)),
        blockers=blockers,
        confidence=confidence,
    )


def _linkable(blockers: Sequence[str]) -> bool:
    """An observation feed states a condition; only identical text can tie it to anything else."""
    return not (
        DISTRICT_BLOCKER in blockers
        or TYPE_BLOCKER in blockers
        or OBSERVATION_BLOCKER in blockers
    )


def merge_groups(
    profiles: Sequence[IncidentProfile], relationships: Iterable[PairRelationship]
) -> list[list[str]]:
    """Connected components over duplicates and merge candidates; links and doubts never merge."""
    parent = {profile.incident_id: profile.incident_id for profile in profiles}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for relationship in relationships:
        if relationship.decision not in (DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE):
            continue
        left, right = find(relationship.left_incident_id), find(relationship.right_incident_id)
        if left != right:
            parent[max(left, right)] = min(left, right)

    groups: dict[str, list[str]] = {}
    for profile in profiles:
        groups.setdefault(find(profile.incident_id), []).append(profile.incident_id)
    return sorted(sorted(members) for members in groups.values() if len(members) > 1)


def canonical_of(members: Sequence[IncidentProfile]) -> IncidentProfile:
    """The incident a merge should survive on: cleanest source url, richest evidence, oldest."""
    return min(members, key=_canonical_key)


def _canonical_key(profile: IncidentProfile) -> tuple[int, int, float, str]:
    url = profile.source_url or ""
    anchored = 0 if page_of(url) is not None and "#" not in url else 1
    stamp = _naive(profile.reported_at)
    return (
        anchored,
        -len(profile.evidence_ids),
        stamp.timestamp() if stamp is not None else _NO_STAMP,
        profile.incident_id,
    )


def build_cluster(
    members: Sequence[IncidentProfile], relationships: Iterable[PairRelationship]
) -> IncidentCluster:
    """One cluster over a merged group, keeping every record, source, url and evidence id."""
    ordered = sorted(members, key=lambda profile: profile.incident_id)
    canonical = canonical_of(ordered)
    ids = {profile.incident_id for profile in ordered}
    internal = [
        relationship
        for relationship in relationships
        if relationship.left_incident_id in ids and relationship.right_incident_id in ids
    ]
    if not internal:
        raise ValueError("a cluster needs at least one relationship between its members")
    similarities = [relationship.similarity for relationship in internal]
    cross_source = len({profile.source_id for profile in ordered}) > 1
    confidence = max(
        (relationship.confidence for relationship in internal if relationship.confidence),
        default=None,
    )
    districts = sorted({profile.district for profile in ordered if profile.district})
    agreement = len(districts) == 1
    return IncidentCluster(
        cluster_id="CLU-" + hashlib.sha256(
            "|".join(sorted(ids)).encode("utf-8")
        ).hexdigest()[:12],
        canonical_incident_id=canonical.incident_id,
        relationship=DedupDecision.MERGE_CANDIDATE if cross_source else DedupDecision.DUPLICATE,
        incident_ids=sorted(ids),
        record_ids=[profile.record_id for profile in ordered],
        source_ids=sorted({profile.source_id for profile in ordered}),
        source_types=sorted({profile.source_type for profile in ordered}),
        source_urls=[profile.source_url for profile in ordered],
        evidence_ids=sorted(
            {evidence_id for profile in ordered for evidence_id in profile.evidence_ids}
        ),
        event_types=sorted(
            {profile.event_type for profile in ordered if profile.type_is_resolved},
            key=lambda event_type: event_type.value,
        ),
        district=canonical.district if agreement else None,
        languages=sorted({profile.language for profile in ordered}),
        cross_source=cross_source,
        max_similarity=max(similarities),
        min_similarity=min(similarities),
        confidence=confidence,
        notes=[
            canonical.record_id + " carries the cleanest source of " + str(len(ordered)),
            "members keep their own evidence ledger; no document was overwritten",
        ]
        + ([] if agreement else ["the members name different districts: " + ", ".join(districts)]),
    )


def profile_groups(
    profiles: Sequence[IncidentProfile], groups: Sequence[Sequence[str]]
) -> Iterator[Sequence[IncidentProfile]]:
    index = {profile.incident_id: profile for profile in profiles}
    for group in groups:
        yield [index[incident_id] for incident_id in group]


__all__ = [
    "CONF_CONTRADICTION",
    "CONF_IDENTICAL",
    "CONF_SAME_PAGE",
    "CONF_SEPARATION",
    "DEDUP_ALGORITHM_VERSION",
    "DISTRICT_BLOCKER",
    "DISTRICT_LEVEL",
    "DUPLICATE_CONTENT",
    "LINK_COMPOSITE",
    "MERGE_COMPOSITE",
    "MERGE_CONTENT",
    "OBSERVATION_BLOCKER",
    "PairReading",
    "PairSignals",
    "RELEVANCE_BLOCKER",
    "RESTATE_BLOCKER",
    "TIME_BLOCKER",
    "TYPE_BLOCKER",
    "UNCERTAIN_COMPOSITE",
    "build_cluster",
    "canonical_of",
    "compare_profiles",
    "merge_groups",
    "page_of",
    "profile_groups",
    "read_pair",
    "text_fingerprint",
    "time_signal",
    "token_signature",
    "tokenize",
    "type_conflicts",
]
