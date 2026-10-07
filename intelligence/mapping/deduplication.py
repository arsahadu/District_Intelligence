"""Stage 8: which of these incidents account for one event, without overwriting any of them."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Iterable, Optional, Sequence

from intelligence.config.event_type_cues import is_observation_feed
from intelligence.extraction import correlation
from intelligence.extraction.correlation import IncidentProfile
from intelligence.extraction.mention_text import fold
from intelligence.mapping.assembly import IncidentDraft, verify_draft
from intelligence.mapping.enrichment import read_prose
from intelligence.mapping.record_input import TITLE_PATH
from intelligence.models.correlation import CorrelationReport, IncidentCluster, PairRelationship
from intelligence.models.enums import DedupDecision, ExtractionMethod
from intelligence.models.incident import Incident
from intelligence.models.metadata import DedupMetadata

PROVIDER = "intelligence.mapping.deduplication"
STAGE_VERSION = "8"
DEDUP_SECTION = "dedup"

MERGING = (DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE)


@dataclass(frozen=True)
class CorrelationResult:
    """The annotated drafts and the report that explains them. The input drafts are untouched."""

    drafts: tuple[IncidentDraft, ...]
    report: CorrelationReport
    warnings: tuple[str, ...] = ()
    by_incident: dict[str, IncidentDraft] = field(default_factory=dict)

    @property
    def incidents(self) -> tuple[Incident, ...]:
        return tuple(draft.incident for draft in self.drafts)

    def cluster_for(self, incident_id: str) -> Optional[IncidentCluster]:
        return self.report.cluster_for(incident_id)

    def sources_for(self, incident_id: str) -> list[tuple[str, Optional[str]]]:
        return self.report.sources_for(incident_id)

    def decision_for(self, incident_id: str) -> DedupDecision:
        return self.report.decisions[incident_id]


def profile_of(draft: IncidentDraft) -> IncidentProfile:
    """What Stage 8 may compare one incident on, read off the incident and the record behind it."""
    incident = draft.incident
    record = draft.record
    body = " ".join(read_prose(draft, path).text for path in record.text_paths if path != TITLE_PATH)
    title = incident.title.text.source or record.title or ""
    body_tokens = correlation.tokenize(body)
    district = record.district or incident.spatial.district_hint

    surfaces = {
        fold(mention.text)
        for mention in incident.spatial.mentions
        if mention.granularity not in correlation.DISTRICT_LEVEL
        and fold(mention.text)
        and (district is None or fold(mention.text) != fold(district))
    }
    names = {
        fold(actor.name_text) for actor in incident.actors if actor.is_named
    } - {""}

    return IncidentProfile(
        incident_id=incident.incident_id,
        record_id=record.record_id,
        source_id=record.source_id,
        source_type=record.source_type,
        record_type=record.record_type,
        fingerprint=correlation.text_fingerprint(body),
        token_signature=correlation.token_signature(body_tokens),
        record_signature=record_signature(draft),
        title_tokens=correlation.tokenize(title),
        body_tokens=body_tokens,
        event_type=incident.classification.event_type,
        secondary_types=frozenset(incident.classification.secondary_event_types),
        relevance_state=incident.relevance.state,
        is_observation=is_observation_feed(record.source_type, record.record_type),
        event_instant=incident.event_time.value,
        event_precision=incident.event_time.precision,
        time_semantics=incident.event_time.semantics,
        reported_at=incident.reported_at or record.retrieved_at,
        district=district,
        place_surfaces=frozenset(surfaces),
        actor_names=frozenset(names),
        source_url=record.source_url,
        evidence_ids=frozenset(evidence.evidence_id for evidence in incident.evidence),
        language=incident.language.primary_language,
    )


def record_signature(draft: IncidentDraft) -> str:
    """A digest of everything the record printed - prose, scalars and headline - not of its identity."""
    parts = [f"{path}={text}" for path, text in (*draft.record.texts, *draft.record.scalars)]
    return correlation.text_fingerprint("|".join(sorted(parts)))


def correlate_incidents(drafts: Sequence[IncidentDraft]) -> CorrelationResult:
    """Compare every pair worth comparing, cluster what merges, annotate every incident."""
    profiles = tuple(profile_of(draft) for draft in drafts)
    ids = [profile.incident_id for profile in profiles]
    if len(set(ids)) != len(ids):
        raise ValueError("the same incident was supplied twice; correlate a set, not a list")

    relationships = tuple(
        correlation.compare_profiles(profiles[left], profiles[right])
        for left, right in candidate_pairs(profiles)
    )
    clusters = tuple(
        correlation.build_cluster(members, relationships)
        for members in correlation.profile_groups(
            profiles, correlation.merge_groups(profiles, relationships)
        )
    )
    annotated = tuple(
        _annotate(draft, profile, relationships, clusters)
        for draft, profile in zip(drafts, profiles)
    )

    decisions = {draft.incident.incident_id: draft.incident.dedup.decision for draft in annotated}
    counts: dict[str, int] = defaultdict(int)
    for incident_id in ids:
        counts[decisions[incident_id].value] += 1
    for relationship in relationships:
        counts["pair_" + relationship.decision.value] += 1

    warnings = tuple(
        dict.fromkeys(
            warning
            for incident in (draft.incident for draft in annotated)
            for warning in _warnings(incident.dedup)
        )
    )
    report = CorrelationReport(
        algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
        incident_ids=sorted(ids),
        decisions={incident_id: decisions[incident_id] for incident_id in sorted(ids)},
        clusters=list(clusters),
        relationships=[
            relationship
            for relationship in relationships
            if relationship.decision is not DedupDecision.UNIQUE
        ],
        counts=dict(sorted(counts.items())),
        warnings=list(warnings),
    )
    return CorrelationResult(
        drafts=annotated,
        report=report,
        warnings=warnings,
        by_incident={draft.incident.incident_id: draft for draft in annotated},
    )


def candidate_pairs(profiles: Sequence[IncidentProfile]) -> list[tuple[int, int]]:
    """Pairs that share something concrete. A district and a date shortlist a pair; they never settle it."""
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, profile in enumerate(profiles):
        for key in dict.fromkeys(_bucket_keys(profile)):
            buckets[key].append(index)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        pairs.update(combinations(sorted(members), 2))
    return sorted(pairs)


def _bucket_keys(profile: IncidentProfile) -> list[tuple[str, str]]:
    keys = [("record", profile.record_id), ("digest", profile.record_signature)]
    page = correlation.page_of(profile.source_url)
    if page:
        keys.append(("page", page))
    if profile.has_prose:
        keys.extend([("print", profile.fingerprint), ("words", profile.token_signature)])
        if profile.district:
            district = fold(profile.district)
            for value in (profile.event_instant, profile.reported_at):
                if value is not None:
                    keys.append(("district-day", district + "|" + value.date().isoformat()))
    if profile.title_tokens:
        keys.append(("title", correlation.token_signature(profile.title_tokens)))
    return keys


def _annotate(
    draft: IncidentDraft,
    profile: IncidentProfile,
    relationships: Sequence[PairRelationship],
    clusters: Sequence[IncidentCluster],
) -> IncidentDraft:
    mine = [
        relationship
        for relationship in relationships
        if profile.incident_id in (relationship.left_incident_id, relationship.right_incident_id)
    ]
    cluster = next((c for c in clusters if profile.incident_id in c.incident_ids), None)
    incident = draft.incident.model_copy(deep=True)
    incident.fingerprint = profile.fingerprint if profile.has_prose else profile.record_signature
    incident.dedup = _dedup(profile.incident_id, mine, cluster)
    incident.processing = incident.processing.model_copy(
        update={
            "stage_versions": {
                **incident.processing.stage_versions,
                "deduplication": STAGE_VERSION,
                "dedup_algorithm": correlation.DEDUP_ALGORITHM_VERSION,
            }
        }
    )
    result = replace(draft, incident=incident)
    return replace(result, checks=verify_draft(result))


def _dedup(
    incident_id: str,
    mine: Sequence[PairRelationship],
    cluster: Optional[IncidentCluster],
) -> DedupMetadata:
    if cluster is not None:
        partners = [other for other in cluster.incident_ids if other != incident_id]
        outside = [item for item in mine if item.decision not in MERGING]
        is_canonical = cluster.canonical_incident_id == incident_id
        best = _best(mine)
        return DedupMetadata(
            algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
            decision=cluster.relationship,
            duplicate_of=None if is_canonical else cluster.canonical_incident_id,
            cluster_id=cluster.cluster_id,
            merged_from=partners if is_canonical else [],
            linked_incident_ids=_partners(outside, incident_id, DedupDecision.LINKED),
            contradicting_incident_ids=_partners(
                outside, incident_id, DedupDecision.CONTRADICTS
            ),
            similarity_features=best.features if best else {},
            max_similarity=best.similarity if best else None,
            method=ExtractionMethod.RULE,
            confidence=cluster.confidence,
            notes=_note([*cluster.notes, *_why(cluster.incident_ids, mine, incident_id)]),
        )

    if not mine:
        return DedupMetadata(
            algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
            decision=DedupDecision.UNIQUE,
            method=ExtractionMethod.RULE,
            notes=_note(
                [
                    "nothing else in this batch shares its record id, page, printed text, "
                    "word set, headline or district and day, so there was no pair to compare"
                ]
            ),
        )

    best = _best(mine)
    doubts = [item for item in mine if item.decision is DedupDecision.UNRESOLVED]
    conflicts = [item for item in mine if item.decision is DedupDecision.CONTRADICTS]
    links = [item for item in mine if item.decision is DedupDecision.LINKED]

    if doubts:
        closest = max(doubts, key=lambda item: item.similarity)
        return DedupMetadata(
            algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
            linked_incident_ids=_partners(mine, incident_id, DedupDecision.LINKED),
            contradicting_incident_ids=_partners(mine, incident_id, DedupDecision.CONTRADICTS),
            similarity_features=closest.features,
            max_similarity=closest.similarity,
            method=ExtractionMethod.UNRESOLVED,
            notes=_note(
                [
                    "closest candidate "
                    + _other(closest, incident_id)
                    + " at composite "
                    + f"{closest.similarity:.2f}"
                    + ": above the link floor, below the merge floor"
                ]
            ),
        )
    if conflicts:
        return DedupMetadata(
            algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
            decision=DedupDecision.CONTRADICTS,
            contradicting_incident_ids=_partners(mine, incident_id, DedupDecision.CONTRADICTS),
            similarity_features=best.features if best else {},
            max_similarity=best.similarity if best else None,
            method=ExtractionMethod.RULE,
            confidence=max(item.confidence or 0.0 for item in conflicts),
            notes=_note([reason for item in conflicts for reason in item.reasons]),
        )
    if links:
        return DedupMetadata(
            algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
            decision=DedupDecision.LINKED,
            linked_incident_ids=_partners(mine, incident_id, DedupDecision.LINKED),
            similarity_features=best.features if best else {},
            max_similarity=best.similarity if best else None,
            method=ExtractionMethod.RULE,
            confidence=max(item.confidence or 0.0 for item in links),
            notes=_note(
                [
                    "related to, but not the same event as: "
                    + ", ".join(_partners(mine, incident_id, DedupDecision.LINKED))
                ]
            ),
        )

    separated = all(item.blockers for item in mine)
    confidences = [item.confidence for item in mine if item.confidence is not None]
    return DedupMetadata(
        algorithm_version=correlation.DEDUP_ALGORITHM_VERSION,
        decision=DedupDecision.UNIQUE,
        similarity_features=best.features if best else {},
        max_similarity=best.similarity if best else None,
        method=ExtractionMethod.RULE,
        confidence=min(confidences, default=correlation.CONF_SEPARATION if separated else None),
        notes=_note(
            [
                "compared with " + str(len(mine)) + " other incident(s); none of them merge-worthy"
            ]
        ),
    )


def _note(reasons: Iterable[str]) -> str:
    """DedupMetadata keeps one note string; the pair decisions keep lists, so they are joined here."""
    return "; ".join(dict.fromkeys(reason for reason in reasons if reason))


def _best(mine: Sequence[PairRelationship]) -> Optional[PairRelationship]:
    scored = [item for item in mine if item.similarity > 0]
    return max(scored, key=lambda item: item.similarity, default=None)


def _other(item: PairRelationship, incident_id: str) -> str:
    return item.right_incident_id if item.left_incident_id == incident_id else item.left_incident_id


def _partners(
    items: Iterable[PairRelationship], incident_id: str, decision: DedupDecision
) -> list[str]:
    return sorted(
        {
            _other(item, incident_id)
            for item in items
            if item.decision is decision and _other(item, incident_id) != incident_id
        }
    )


def _why(
    member_ids: Sequence[str], mine: Sequence[PairRelationship], incident_id: str
) -> list[str]:
    reasons = [
        item.reasons[0]
        for item in mine
        if item.decision in MERGING and item.reasons and _other(item, incident_id) in member_ids
    ]
    return list(dict.fromkeys(reasons))[:3]


def _warnings(dedup: DedupMetadata) -> tuple[str, ...]:
    if dedup.decision not in MERGING:
        return ()
    target = dedup.duplicate_of or ", ".join(dedup.merged_from)
    return (dedup.decision.value + " of " + target + " in cluster " + str(dedup.cluster_id),)


__all__ = [
    "DEDUP_SECTION",
    "MERGING",
    "PROVIDER",
    "STAGE_VERSION",
    "CorrelationResult",
    "candidate_pairs",
    "correlate_incidents",
    "profile_of",
    "record_signature",
]
