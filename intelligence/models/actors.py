"""People, bodies and institutions named in the source."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.enums import ActorRole, ActorType, ExtractionMethod, ScriptType


class Actor(StrictModel):
    """A named participant, with the role the text gave it."""

    actor_id: str
    name_text: str
    name_normalized: Optional[str] = None
    transliterated_latin: Optional[str] = None
    official_title: Optional[str] = None
    official_title_transliterated: Optional[str] = None

    actor_type: ActorType = ActorType.UNKNOWN
    role: ActorRole = ActorRole.UNKNOWN
    script: ScriptType = ScriptType.UNKNOWN
    language: str = Field(default="ta", min_length=2, max_length=3)

    affiliation_mention_id: Optional[str] = None
    organization_mention_id: Optional[str] = None

    is_named: bool = True

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _role_needs_evidence(self) -> "Actor":
        if self.role is ActorRole.UNKNOWN:
            if self.evidence_ids and self.confidence is not None:
                raise ValueError(
                    "an actor with evidence should state the role the text gave it"
                )
            return self
        if not self.evidence_ids:
            raise ValueError(f"role {self.role.value} must cite its evidence")
        if self.confidence is None:
            raise ValueError(f"role {self.role.value} requires confidence")
        if self.method is ExtractionMethod.UNRESOLVED:
            raise ValueError(f"role {self.role.value} requires a method")
        return self
