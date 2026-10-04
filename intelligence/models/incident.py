"""The Incident contract: the Intelligence module's output."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import StrictModel
from intelligence.models.base import collect_evidence_references, unique_ids
from intelligence.models.actors import Actor
from intelligence.models.classification import ClassificationInfo, RelevanceInfo
from intelligence.models.enums import DataOrigin, DistrictHintAuthority, EventType
from intelligence.models.enums import IncidentStatus, MentionRole, Modality, ResolutionState
from intelligence.models.enums import OperationalState, ReviewReason, SeverityLevel, TimeSemantics
from intelligence.models.evidence import Evidence
from intelligence.models.language import LanguageInfo, SummaryInfo, TitleInfo
from intelligence.models.metadata import ConfidenceSummary, DedupMetadata
from intelligence.models.metadata import ProcessingMetadata, ReviewInfo
from intelligence.models.quantities import Observation
from intelligence.models.severity import Severity
from intelligence.models.spatial import SpatialHint
from intelligence.models.status import OperationalStatusInfo
from intelligence.models.temporal import TimeValue

SCHEMA_VERSION = "1.0"


class Incident(StrictModel):
    """Structured incident derived from one or more CommonRecords."""

    schema_version: str = SCHEMA_VERSION

    incident_id: str
    fingerprint: Optional[str] = None
    status: IncidentStatus = IncidentStatus.CANDIDATE
    origin: DataOrigin = DataOrigin.PIPELINE

    language: LanguageInfo = Field(default_factory=LanguageInfo)
    title: TitleInfo = Field(default_factory=TitleInfo)
    summary: SummaryInfo = Field(default_factory=SummaryInfo)

    relevance: RelevanceInfo = Field(default_factory=RelevanceInfo)
    classification: ClassificationInfo = Field(default_factory=ClassificationInfo)
    actors: list[Actor] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)

    event_time: TimeValue = Field(default_factory=TimeValue)
    reported_at: Optional[datetime] = None

    spatial: SpatialHint = Field(default_factory=SpatialHint)

    severity: Severity = Field(default_factory=Severity)
    operational_status: OperationalStatusInfo = Field(
        default_factory=OperationalStatusInfo
    )

    evidence: list[Evidence] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicts_record_ids: list[str] = Field(default_factory=list)

    dedup: DedupMetadata = Field(default_factory=DedupMetadata)
    confidence: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    review: ReviewInfo = Field(default_factory=ReviewInfo)
    processing: ProcessingMetadata = Field(default_factory=ProcessingMetadata)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _check_identity_and_references(self) -> "Incident":
        evidence_ids = [e.evidence_id for e in self.evidence]
        duplicate_evidence = unique_ids(self.evidence, "evidence_id")
        if duplicate_evidence:
            raise ValueError("duplicate evidence_id values: " + ", ".join(duplicate_evidence))

        known = set(evidence_ids)
        referenced = collect_evidence_references(
            {
                "language": self.language,
                "title": self.title,
                "summary": self.summary,
                "relevance": self.relevance,
                "classification": self.classification,
                "actors": self.actors,
                "observations": self.observations,
                "event_time": self.event_time,
                "spatial": self.spatial,
                "severity": self.severity,
                "operational_status": self.operational_status,
                "confidence": self.confidence,
            }
        )
        dangling = sorted(referenced - known)
        if dangling:
            raise ValueError(
                "evidence references to unknown evidence_id values: " + ", ".join(dangling)
            )

        for label, items, id_field in (
            ("actor", self.actors, "actor_id"),
            ("observation", self.observations, "observation_id"),
            ("mention", self.spatial.mentions, "mention_id"),
        ):
            duplicates = unique_ids(items, id_field)
            if duplicates:
                raise ValueError(
                    f"duplicate {label} {id_field} values: " + ", ".join(duplicates)
                )

        if self.supporting_record_ids:
            if self.evidence:
                evidence_records = {e.record_id for e in self.evidence}
                unsupported = sorted(set(self.supporting_record_ids) - evidence_records)
                if unsupported:
                    raise ValueError(
                        "supporting_record_ids without any evidence from that record: "
                        + ", ".join(unsupported)
                    )
            else:
                raise ValueError(
                    "supporting_record_ids set but the incident carries no evidence"
                )

        conflict = sorted(set(self.supporting_record_ids) & set(self.contradicts_record_ids))
        if conflict:
            raise ValueError(
                "records cannot both support and contradict the same incident: "
                + ", ".join(conflict)
            )

        if self.dedup.self_reference(self.incident_id):
            raise ValueError("an incident cannot be a duplicate of itself")

        affiliation_ids = {m.mention_id for m in self.spatial.mentions}
        for actor in self.actors:
            for field_name in ("affiliation_mention_id", "organization_mention_id"):
                value = getattr(actor, field_name)
                if value is not None and value not in affiliation_ids:
                    raise ValueError(
                        f"actor {actor.actor_id} points at unknown mention {value!r} "
                        f"via {field_name}"
                    )

        if self.status is IncidentStatus.MERGED and self.dedup.duplicate_of is None:
            raise ValueError("status=merged requires dedup.duplicate_of")
        if self.status is IncidentStatus.DISMISSED:
            if self.relevance.state.value == "unresolved" and not self.review.needed:
                raise ValueError(
                    "a dismissed incident must either resolve relevance or be flagged for review"
                )
        return self

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}

    def resolve_evidence(self, evidence_ids: list[str]) -> list[Evidence]:
        index = self.evidence_by_id()
        return [index[eid] for eid in evidence_ids if eid in index]

    def source_quotes(self) -> list[tuple[str, str]]:
        """(describe(), quote) for every evidence entry that pinned a span."""
        return [(e.describe(), e.quote or "") for e in self.evidence if e.has_span]

    def contributing_record_ids(self) -> list[str]:
        return sorted({e.record_id for e in self.evidence})

    @property
    def event_type(self) -> EventType:
        return self.classification.event_type

    @property
    def severity_level(self) -> SeverityLevel:
        return self.severity.level

    @property
    def operational_state(self) -> OperationalState:
        return self.operational_status.state

    @property
    def primary_language(self) -> str:
        return self.language.primary_language

    def derive_review_reasons(self, low_confidence_threshold: float = 0.6) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []

        def add(reason: ReviewReason) -> None:
            if reason not in reasons:
                reasons.append(reason)

        if not self.evidence:
            add(ReviewReason.NO_SUPPORTING_EVIDENCE)
        if self.classification.event_type is EventType.UNRESOLVED:
            add(ReviewReason.UNRESOLVED_EVENT_TYPE)
        if self.relevance.state.value in ("unresolved", "unsure"):
            add(ReviewReason.UNRESOLVED_RELEVANCE)
        if self.severity.level is SeverityLevel.UNRESOLVED:
            add(ReviewReason.UNRESOLVED_SEVERITY)
        elif not self.severity.signals:
            add(ReviewReason.SEVERITY_WITHOUT_EVIDENCE)
        if self.operational_status.conflicting_states:
            add(ReviewReason.STATUS_CONFLICT)

        candidates = [m for m in self.spatial.mentions if m.role in (MentionRole.EVENT_LOCATION, MentionRole.EVENT_CONTAINER)]
        if not candidates:
            add(ReviewReason.NO_EVENT_LOCATION_CANDIDATE)
        elif len({m.text_normalized or m.text for m in candidates}) > 1:
            add(ReviewReason.AMBIGUOUS_LOCATION)
        if self.spatial.competing_districts:
            add(ReviewReason.DISTRICT_CONFLICT)
        if self.spatial.resolution_state in (
            ResolutionState.NOT_ATTEMPTED,
            ResolutionState.PENDING_GIS,
            ResolutionState.AMBIGUOUS,
        ):
            add(ReviewReason.PENDING_GIS_RESOLUTION)
        if (
            self.spatial.district_hint is not None
            and self.spatial.district_hint_authority
            in (DistrictHintAuthority.SOURCE_CONFIGURATION, DistrictHintAuthority.FEED_URL)
            and self.spatial.district_hint_from_text is None
        ):
            add(ReviewReason.AMBIGUOUS_LOCATION)

        if self.event_time.semantics is TimeSemantics.PUBLICATION_TIME:
            add(ReviewReason.PUBLICATION_TIME_ONLY)
        if self.processing.llm_used:
            add(ReviewReason.LLM_ASSISTED)
        if self.contradicts_record_ids:
            add(ReviewReason.CONFLICTING_SOURCES)

        overall = self.confidence.overall
        if overall is not None and overall < low_confidence_threshold:
            add(ReviewReason.LOW_CONFIDENCE)
        elif overall is None and self.evidence:
            add(ReviewReason.LOW_CONFIDENCE)
        return reasons

    def apply_review_flags(self, low_confidence_threshold: float = 0.6) -> "Incident":
        """Fill ``review`` from the derived reasons. Returns self for chaining."""
        derived = self.derive_review_reasons(low_confidence_threshold)
        existing = list(self.review.reasons)
        merged = existing + [r for r in derived if r not in existing]
        self.review = ReviewInfo(
            needed=bool(merged),
            reasons=merged,
            note=self.review.note,
            assigned_to=self.review.assigned_to,
            reviewed_at=self.review.reviewed_at,
            reviewer_decision=self.review.reviewer_decision,
        )
        return self

    def inconsistencies(self, low_confidence_threshold: float = 0.6) -> list[str]:
        """Human-readable notes on weak spots. Empty list means clean."""
        notes: list[str] = []
        if not self.evidence:
            notes.append("no evidence entries: nothing in this incident is traceable")
        if self.classification.event_type is EventType.UNRESOLVED:
            notes.append("event_type is unresolved")
        if self.severity.level is SeverityLevel.UNRESOLVED:
            notes.append("severity is unresolved")
        if self.operational_status.conflicting_states:
            contested = ", ".join(
                state.value for state in self.operational_status.conflicting_states
            )
            notes.append(
                f"operational status is contested between {contested} by the source's own wording"
            )
        if self.event_time.value is None:
            notes.append("no time established at all")
        elif self.event_time.semantics is TimeSemantics.PUBLICATION_TIME:
            notes.append(
                "best available time is the publication stamp, not when the incident happened"
            )
        if self.event_time.value is not None and self.event_time.value.tzinfo is None:
            if self.event_time.timezone is None:
                notes.append("timestamp is timezone-naive with no declared source timezone")
        if self.spatial.district_hint and self.spatial.district_hint_authority is (
            DistrictHintAuthority.SOURCE_CONFIGURATION
        ):
            notes.append(
                "district came from feed configuration, not from the article text"
            )
        if not self.spatial.mentions:
            notes.append("no location mentions extracted")
        if self.language.text_representations and not self.language.source_texts():
            notes.append("no source-language text representation present")
        if self.summary.text.source is None and self.summary.kind.value != "unresolved":
            notes.append("summary kind is resolved but carries no text")
        if self.confidence.overall is not None and self.confidence.overall < low_confidence_threshold:
            notes.append(f"overall confidence {self.confidence.overall:.2f} is below threshold")
        if self.processing.modality in (Modality.IMAGE, Modality.SCANNED_DOCUMENT, Modality.PDF):
            if not any(r.role.value == "ocr_output" for r in self.language.text_representations):
                notes.append("non-text modality recorded without an OCR representation")
        return notes

    def to_storage_document(self) -> dict:
        """JSON-safe document for Stage 9 persistence."""
        return self.model_dump(mode="json")
