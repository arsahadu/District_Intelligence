"""Spatial contracts: raw mentions for GIS, plus a slot GIS writes back into."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import DistrictHintAuthority, ExtractionMethod
from intelligence.models.enums import GranularityLevel, MentionRole, MentionType
from intelligence.models.enums import ResolutionState, ScriptType

_EVENT_LOCATION_ROLES = (MentionRole.EVENT_LOCATION, MentionRole.EVENT_CONTAINER)


class LocationMention(StrictModel):
    """One place name as it appeared in the source text."""

    mention_id: str
    text: str
    text_normalized: Optional[str] = None
    transliterated_latin: Optional[str] = None

    script: ScriptType = ScriptType.UNKNOWN
    language: str = Field(default="ta", min_length=2, max_length=3)

    mention_type: MentionType = MentionType.UNKNOWN
    granularity: GranularityLevel = GranularityLevel.UNKNOWN
    role: MentionRole = MentionRole.UNRESOLVED

    context_window: Optional[str] = None

    is_event_location_candidate: bool = False
    gazetteer_matched: bool = False
    gazetteer_source: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _candidate_needs_evidence(self) -> "LocationMention":
        if self.is_event_location_candidate:
            if self.role not in _EVENT_LOCATION_ROLES:
                raise ValueError(
                    "an event-location candidate must carry an event-location role"
                )
            if not self.evidence_ids:
                raise ValueError(
                    "an event-location candidate must cite the evidence it came from"
                )
        return self


class GisResolution(StrictModel):
    """Write-back target for the GIS module. Intelligence leaves this unset."""

    canonical_place_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_district: Optional[str] = None
    canonical_taluk: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    geometry_source: Optional[str] = None
    geocoder_version: Optional[str] = None

    mention_id: Optional[str] = None

    resolved_by: str
    resolved_at: Optional[datetime] = None
    confidence: OptionalConfidence = None

    @model_validator(mode="after")
    def _coordinates_come_as_a_set(self) -> "GisResolution":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be present or both absent")
        if self.latitude is not None and self.confidence is None:
            raise ValueError("resolved coordinates require a resolution confidence")
        return self


class SpatialHint(StrictModel):
    """The spatial picture of an incident, pre-resolution."""

    mentions: list[LocationMention] = Field(default_factory=list)
    best_event_location_mention_id: Optional[str] = None

    district_hint: Optional[str] = None
    district_hint_authority: DistrictHintAuthority = DistrictHintAuthority.NONE
    district_hint_confidence: OptionalConfidence = None
    district_hint_from_text: Optional[str] = None
    competing_districts: list[str] = Field(default_factory=list)

    sub_district_hint: Optional[str] = None
    locality_hints: list[str] = Field(default_factory=list)

    resolution_state: ResolutionState = ResolutionState.NOT_ATTEMPTED
    gis: Optional[GisResolution] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "SpatialHint":
        mention_ids = [m.mention_id for m in self.mentions]
        if len(set(mention_ids)) != len(mention_ids):
            duplicates = sorted({m for m in mention_ids if mention_ids.count(m) > 1})
            raise ValueError(f"duplicate mention ids: {', '.join(duplicates)}")

        if self.best_event_location_mention_id is not None:
            target = next(
                (m for m in self.mentions if m.mention_id == self.best_event_location_mention_id),
                None,
            )
            if target is None:
                raise ValueError(
                    "best_event_location_mention_id does not reference a known mention"
                )
            if target.role not in _EVENT_LOCATION_ROLES:
                raise ValueError(
                    "best event location must be a mention whose role is an event "
                    f"location, not {target.role.value}"
                )

        if self.resolution_state in (ResolutionState.RESOLVED, ResolutionState.REJECTED):
            if self.gis is None:
                raise ValueError(
                    f"resolution_state={self.resolution_state.value} requires a GisResolution"
                )
        elif self.gis is not None:
            raise ValueError(
                "a GisResolution is only valid once GIS has reported RESOLVED or REJECTED"
            )

        if self.district_hint is not None and self.district_hint_authority is DistrictHintAuthority.NONE:
            raise ValueError("a district hint must declare where it came from")
        if self.district_hint is None and self.district_hint_authority is not DistrictHintAuthority.NONE:
            raise ValueError("district_hint_authority set without a district hint")
        return self

    @property
    def has_gis_output(self) -> bool:
        return self.gis is not None

    @property
    def event_location_candidates(self) -> list[LocationMention]:
        return [m for m in self.mentions if m.is_event_location_candidate]

    def mention_by_id(self, mention_id: str) -> LocationMention | None:
        return next((m for m in self.mentions if m.mention_id == mention_id), None)


def unresolved_spatial_hint() -> SpatialHint:
    """Explicit 'nothing established yet' state, pending GIS hand-off."""
    return SpatialHint(resolution_state=ResolutionState.PENDING_GIS)
