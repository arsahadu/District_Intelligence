"""Correlation contracts: what Stage 8 says about a set of incidents, and what it kept of each source."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import Confidence, OptionalConfidence, StrictModel
from intelligence.models.enums import DedupDecision, EventType


def _repeats(values: list[str]) -> list[str]:
    """Id lists are plain strings here, so the model's attribute-based helper does not apply."""
    seen: set[str] = set()
    repeats: list[str] = []
    for value in values:
        if value in seen and value not in repeats:
            repeats.append(value)
        seen.add(value)
    return repeats


class PairRelationship(StrictModel):
    """One comparison between two incidents: the verdict, the signals behind it, what blocked it."""

    left_incident_id: str
    right_incident_id: str

    decision: DedupDecision
    similarity: Confidence
    features: dict[str, Confidence] = Field(default_factory=dict)

    same_source: bool
    cross_source: bool

    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    confidence: OptionalConfidence = None

    @model_validator(mode="after")
    def _check_relationship(self) -> "PairRelationship":
        if self.left_incident_id == self.right_incident_id:
            raise ValueError("an incident cannot be compared with itself")
        if self.same_source == self.cross_source:
            raise ValueError("a pair must be either same-source or cross-source, exactly once")
        if self.decision in (DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE):
            if not self.reasons:
                raise ValueError(
                    f"decision={self.decision.value} requires the evidence it rests on"
                )
            if self.confidence is None:
                raise ValueError(f"decision={self.decision.value} requires confidence")
        if self.decision is DedupDecision.UNRESOLVED and self.confidence is not None:
            raise ValueError("an uncertain relationship must not assert confidence")
        return self


class IncidentCluster(StrictModel):
    """Incidents believed to account for one event. Members keep their own evidence."""

    cluster_id: str
    canonical_incident_id: str
    relationship: DedupDecision
    incident_ids: list[str]
    record_ids: list[str]
    source_ids: list[str]
    source_types: list[str]
    source_urls: list[Optional[str]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    event_types: list[EventType] = Field(default_factory=list)
    district: Optional[str] = None
    languages: list[str] = Field(default_factory=list)

    cross_source: bool
    max_similarity: Confidence
    min_similarity: OptionalConfidence = None
    confidence: OptionalConfidence = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cluster(self) -> "IncidentCluster":
        for label, values in (
            ("incident", self.incident_ids),
            ("source", self.source_ids),
        ):
            duplicates = _repeats(values)
            if duplicates:
                raise ValueError(f"duplicate {label} ids in cluster: " + ", ".join(duplicates))
        if len(self.incident_ids) < 2:
            raise ValueError("a cluster needs at least two incidents")
        if self.canonical_incident_id not in self.incident_ids:
            raise ValueError("the canonical incident must be a member of the cluster")
        for label, values in (("record", self.record_ids), ("url", self.source_urls)):
            if len(values) != len(self.incident_ids):
                raise ValueError(
                    f"{label} ids must line up positionally with the cluster's incidents"
                )
        if self.relationship not in (DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE):
            raise ValueError(
                "clusters form only over duplicates and merge candidates, not "
                + self.relationship.value
            )
        if self.confidence is None:
            raise ValueError("a cluster must state how much it is trusted")
        return self


class CorrelationReport(StrictModel):
    """Stage 8's output: one verdict per incident, plus the clusters and the pairs worth reading."""

    algorithm_version: str
    incident_ids: list[str]
    decisions: dict[str, DedupDecision] = Field(default_factory=dict)
    clusters: list[IncidentCluster] = Field(default_factory=list)
    relationships: list[PairRelationship] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_report(self) -> "CorrelationReport":
        duplicates = _repeats(self.incident_ids)
        if duplicates:
            raise ValueError("duplicate incident ids in report: " + ", ".join(duplicates))
        if set(self.decisions) - set(self.incident_ids):
            raise ValueError("decisions recorded for incidents this report never read")
        clustered = {i for c in self.clusters for i in c.incident_ids}
        if clustered - set(self.incident_ids):
            raise ValueError(
                "clusters reference incidents this report never read: "
                + ", ".join(sorted(clustered - set(self.incident_ids)))
            )
        for relationship in self.relationships:
            unknown = {
                relationship.left_incident_id,
                relationship.right_incident_id,
            } - set(self.incident_ids)
            if unknown:
                raise ValueError(
                    "a relationship references an incident this report never read: "
                    + ", ".join(sorted(unknown))
                )
        return self

    def cluster_for(self, incident_id: str) -> Optional[IncidentCluster]:
        return next(
            (c for c in self.clusters if incident_id in c.incident_ids),
            None,
        )

    def sources_for(self, incident_id: str) -> list[tuple[str, Optional[str]]]:
        """(record id, source url) for every source behind the cluster this incident belongs to."""
        cluster = self.cluster_for(incident_id)
        if cluster is None:
            return []
        return list(zip(cluster.record_ids, cluster.source_urls))


__all__ = ["CorrelationReport", "IncidentCluster", "PairRelationship"]
