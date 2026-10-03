"""Processing, review, deduplication and confidence metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import Confidence, OptionalConfidence, StrictModel
from intelligence.models.enums import DedupDecision, ExtractionMethod, Modality
from intelligence.models.enums import ReviewReason


class ProcessingMetadata(StrictModel):
    """Reproducibility record: which code, config and models produced this."""

    pipeline_version: str = "0.1.0"
    config_hash: Optional[str] = None
    stage_versions: dict[str, str] = Field(default_factory=dict)
    input_record_hash: Optional[str] = None
    processed_at: Optional[datetime] = None
    duration_ms: Optional[float] = Field(default=None, ge=0)
    modality: Modality = Modality.TEXT
    source_record_count: int = Field(default=0, ge=0)

    llm_used: bool = False
    llm_tasks: list[str] = Field(default_factory=list)
    llm_models: list[str] = Field(default_factory=list)
    llm_prompt_versions: dict[str, str] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _llm_usage_is_consistent(self) -> "ProcessingMetadata":
        if self.llm_used:
            if not self.llm_tasks:
                raise ValueError("llm_used=True requires the tasks the LLM performed")
            if not self.llm_models:
                raise ValueError("llm_used=True requires the model names used")
        else:
            if self.llm_tasks or self.llm_models or self.llm_prompt_versions:
                raise ValueError(
                    "llm_used=False but LLM tasks/models are recorded; set llm_used=True"
                )
        return self


class ReviewInfo(StrictModel):
    """Whether a human should look, and why."""

    needed: bool = False
    reasons: list[ReviewReason] = Field(default_factory=list)
    note: Optional[str] = None
    assigned_to: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewer_decision: Optional[str] = None

    @model_validator(mode="after")
    def _reasons_required(self) -> "ReviewInfo":
        if self.needed and not self.reasons:
            raise ValueError("review.needed=True requires at least one reason")
        if not self.needed and self.reasons:
            raise ValueError("review has reasons but needed=False; set needed=True")
        duplicates = {r for r in self.reasons if self.reasons.count(r) > 1}
        if duplicates:
            raise ValueError(
                "duplicate review reasons: " + ", ".join(sorted(d.value for d in duplicates))
            )
        return self

    def has(self, reason: ReviewReason) -> bool:
        return reason in self.reasons


class DedupMetadata(StrictModel):
    """Merge and cluster bookkeeping. Populated by Stage 8."""

    algorithm_version: Optional[str] = None
    decision: DedupDecision = DedupDecision.UNRESOLVED
    duplicate_of: Optional[str] = None
    cluster_id: Optional[str] = None
    merged_from: list[str] = Field(default_factory=list)
    linked_incident_ids: list[str] = Field(default_factory=list)
    contradicting_incident_ids: list[str] = Field(default_factory=list)

    similarity_features: dict[str, Confidence] = Field(default_factory=dict)
    max_similarity: Optional[Confidence] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_dedup(self) -> "DedupMetadata":
        if self.duplicate_of is not None and self.decision is DedupDecision.UNIQUE:
            raise ValueError("decision=unique contradicts duplicate_of")
        if self.duplicate_of is not None and not self.similarity_features:
            raise ValueError("marking an incident as a duplicate requires similarity evidence")
        if self.decision in (DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE):
            if self.duplicate_of is None and not self.merged_from:
                raise ValueError(
                    f"decision={self.decision.value} requires duplicate_of or merged_from"
                )
            if self.confidence is None:
                raise ValueError(f"decision={self.decision.value} requires confidence")
        if self.merged_from and self.confidence is None:
            raise ValueError("merged_from requires confidence in the merge decision")
        return self

    def self_reference(self, incident_id: str) -> bool:
        return self.duplicate_of == incident_id


class ConfidenceSummary(StrictModel):
    """Aggregate confidence, with the inputs that produced it."""

    overall: OptionalConfidence = None
    components: dict[str, Confidence] = Field(default_factory=dict)
    rule: Optional[str] = None
    unresolved_field_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _overall_needs_inputs(self) -> "ConfidenceSummary":
        if self.overall is not None:
            if not self.components:
                raise ValueError("an overall confidence requires the components behind it")
            if self.rule is None:
                raise ValueError("an overall confidence requires the combining rule")
        return self
