"""The stable Intelligence output contract: what the LLM pipeline emits and the dashboard stores."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import OptionalConfidence, StrictModel
from intelligence.models.base import collect_evidence_references, unique_ids
from intelligence.models.enums import ActorRole, Department, EventType, ExtractionMethod
from intelligence.models.enums import MentionRole, MentionType, Modality, ResolutionState
from intelligence.models.enums import SeverityLevel, TimePrecision
from intelligence.models.evidence import Evidence

SCHEMA_VERSION = "2.0"

#: Who is allowed to say an Incident has been checked.
VALIDATOR = "intelligence.intelligence"

_HEX64 = r"^[0-9a-f]{64}$"

_PROBABILISTIC = frozenset({ExtractionMethod.LLM, ExtractionMethod.STATISTICAL})


class ReviewState(str, Enum):
    """How far one claim has been checked, not how sure a model felt about it."""

    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    UNRESOLVED = "unresolved"


class EventStatus(str, Enum):
    """What the source says is happening. Controlled so the dashboard can filter."""

    REPORTED = "reported"
    ONGOING = "ongoing"
    UNDER_INVESTIGATION = "under_investigation"
    ACTION_TAKEN = "action_taken"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class IncidentCategory(str, Enum):
    """Coarse district-administration grouping, one level above ``incident_type``."""

    DISASTER = "disaster"
    WATER = "water"
    AGRICULTURE = "agriculture"
    HEALTH = "health"
    EDUCATION = "education"
    INFRASTRUCTURE = "infrastructure"
    TRANSPORT = "transport"
    LAW_AND_ORDER = "law_and_order"
    LAND = "land"
    ENVIRONMENT = "environment"
    SOCIAL_WELFARE = "social_welfare"
    PUBLIC_SERVICE = "public_service"
    OTHER = "other"
    UNRESOLVED = "unresolved"


class PriorityLevel(str, Enum):
    """Escalation band. Absent rather than guessed, so the field is nullable."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EntityType(str, Enum):
    """Who or what a source names. Places that act as parties live here; sites go to locations."""

    PERSON = "person"
    GOVERNMENT_OFFICIAL = "government_official"
    GOVERNMENT_BODY = "government_body"
    ORGANIZATION = "organization"
    COMMUNITY_GROUP = "community_group"
    POLITICAL_PARTY = "political_party"
    MEDIA_OUTLET = "media_outlet"
    INSTITUTION = "institution"
    HOSPITAL = "hospital"
    SCHOOL = "school"
    MARKET = "market"
    VILLAGE = "village"
    TALUK = "taluk"
    BLOCK = "block"
    OTHER = "other"
    UNKNOWN = "unknown"


class RelationshipKind(str, Enum):
    """How two parts of one Incident relate. Deliberately free of verdicts."""

    LOCATED_AT = "located_at"
    INVOLVES = "involves"
    RESPONDS_TO = "responds_to"
    RELATED_TO = "related_to"
    OTHER = "other"


class IssueCode(str, Enum):
    """Deterministic findings from the validation layer. Never a semantic judgement."""

    UNSUPPORTED_CLAIM = "unsupported_claim"
    SPAN_MISMATCH = "span_mismatch"
    INVALID_VALUE = "invalid_value"
    UNKNOWN_FIELD = "unknown_field"
    UNAUTHORISED_GIS_VALUE = "unauthorised_gis_value"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    MISSING_PROVENANCE = "missing_provenance"
    AMBIGUOUS_QUOTE = "ambiguous_quote"
    PROVIDER_WARNING = "provider_warning"


class Provenance(StrictModel):
    """Where an Incident came from. Copied from the record, never from the model's answer."""

    record_id: str
    source_id: str
    source_type: str

    record_type: Optional[str] = None
    source_url: Optional[str] = None
    raw_reference: Optional[str] = None
    retrieved_at: Optional[datetime] = None

    modality: Modality = Modality.TEXT
    language_hint: Optional[str] = None
    district_hint: Optional[str] = None
    state_hint: Optional[str] = None

    declared_severity: Optional[str] = None
    declared_status: Optional[str] = None

    input_hash: Optional[str] = Field(default=None, pattern=_HEX64)

    @property
    def has_view_source(self) -> bool:
        """True when the dashboard can offer a "View Source" action."""
        return self.source_url is not None or self.raw_reference is not None


class Claim(StrictModel):
    """One asserted Incident field, the quote it came from and how it was checked."""

    field: str
    value: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None

    evidence_ids: list[str] = Field(default_factory=list)
    review: ReviewState = ReviewState.UNRESOLVED
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_claim(self) -> "Claim":
        if any(character.isspace() for character in self.field):
            raise ValueError("claim field must be a field name or dotted path without whitespace")
        if self.value is None and self.review is not ReviewState.UNRESOLVED:
            raise ValueError(
                f"claim {self.field!r} has no value, so it cannot be {self.review.value}"
            )
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            if self.method is not ExtractionMethod.SOURCE_METADATA:
                raise ValueError(
                    f"accepted claim {self.field!r} cites no evidence: an unquoted value is "
                    "review_required at best, unless the record itself carried the field"
                )
        if self.method in _PROBABILISTIC and self.confidence is None:
            raise ValueError(
                f"{self.method.value} claim {self.field!r} requires confidence: it is a "
                "guess, so say how good"
            )
        return self


class Location(StrictModel):
    """One place as the source named it. GIS fills the canonical fields later."""

    location_id: str
    text: str

    normalized_name: Optional[str] = None
    location_type: MentionType = MentionType.UNKNOWN
    role: MentionRole = MentionRole.UNRESOLVED

    district: Optional[str] = None
    taluk: Optional[str] = None
    block: Optional[str] = None
    village: Optional[str] = None

    canonical_location_id: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    resolution_state: ResolutionState = ResolutionState.PENDING_GIS

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    review: ReviewState = ReviewState.UNRESOLVED
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _canonical_values_need_gis(self) -> "Location":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude come as a pair, not one at a time")
        grounded = (
            self.latitude is not None
            or self.canonical_location_id is not None
        )
        if grounded and self.resolution_state is not ResolutionState.RESOLVED:
            raise ValueError(
                "a canonical location id or coordinates are only valid on a location GIS has "
                "resolved; extraction leaves them unset and the state pending"
            )
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            raise ValueError(
                f"accepted location {self.location_id!r} cites no evidence"
            )
        return self


class Entity(StrictModel):
    """One named party, institution or grouped body."""

    entity_id: str
    text: str

    normalized_name: Optional[str] = None
    entity_type: EntityType = EntityType.UNKNOWN
    role: ActorRole = ActorRole.UNKNOWN

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    review: ReviewState = ReviewState.UNRESOLVED
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _accepted_needs_evidence(self) -> "Entity":
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            raise ValueError(f"accepted entity {self.entity_id!r} cites no evidence")
        return self


class Relationship(StrictModel):
    """A stated link between two parts of this Incident."""

    relationship_id: str
    kind: RelationshipKind = RelationshipKind.RELATED_TO

    from_ref: str
    to_ref: str

    description: Optional[str] = None

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    review: ReviewState = ReviewState.UNRESOLVED
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _sides_differ(self) -> "Relationship":
        if self.from_ref == self.to_ref:
            raise ValueError(
                f"relationship {self.relationship_id!r} links {self.from_ref!r} to itself"
            )
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            raise ValueError(f"accepted relationship {self.relationship_id!r} cites no evidence")
        return self


class Issue(StrictModel):
    """One deterministic finding about the model's answer."""

    code: IssueCode
    field: Optional[str] = None
    detail: str

    def describe(self) -> str:
        if self.field is None:
            return f"{self.code.value}: {self.detail}"
        return f"{self.field} {self.code.value}: {self.detail}"


class Validation(StrictModel):
    """Whether this Incident may be treated as authoritative, and what said otherwise."""

    state: ReviewState = ReviewState.UNRESOLVED
    issues: list[Issue] = Field(default_factory=list)
    validator: str = VALIDATOR
    checked_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _state_matches_findings(self) -> "Validation":
        if self.state is ReviewState.ACCEPTED and self.issues:
            raise ValueError(
                "validation.state=accepted with findings is a silent acceptance: "
                + "; ".join(issue.describe() for issue in self.issues)
            )
        return self

    @property
    def review_required(self) -> bool:
        return self.state is not ReviewState.ACCEPTED


class Generation(StrictModel):
    """Reproducibility record for one LLM call."""

    provider: str
    model: str
    prompt_version: str

    input_hash: Optional[str] = Field(default=None, pattern=_HEX64)
    generated_at: Optional[datetime] = None
    latency_ms: Optional[float] = Field(default=None, ge=0.0)
    warnings: list[str] = Field(default_factory=list)


#: The Incident fields that carry a model's claim rather than a list of them.
SCALAR_CLAIM_FIELDS = (
    "title",
    "description",
    "incident_type",
    "category",
    "department",
    "severity",
    "priority",
    "event_status",
    "event_time",
)


class Incident(StrictModel):
    """Structured intelligence derived from one or more CommonRecords."""

    schema_version: str = SCHEMA_VERSION

    incident_id: str
    provenance: Provenance

    title: Optional[str] = None
    description: Optional[str] = None

    incident_type: EventType = EventType.UNRESOLVED
    category: IncidentCategory = IncidentCategory.UNRESOLVED
    department: Department = Department.UNRESOLVED

    severity: SeverityLevel = SeverityLevel.UNRESOLVED
    priority: Optional[PriorityLevel] = None
    event_status: EventStatus = EventStatus.UNKNOWN

    event_time: Optional[datetime] = None
    event_time_precision: TimePrecision = TimePrecision.UNKNOWN

    locations: list[Location] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    claims: list[Claim] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    generation: Optional[Generation] = None

    @model_validator(mode="after")
    def _check_contract(self) -> "Incident":
        duplicates = unique_ids(self.evidence, "evidence_id")
        if duplicates:
            raise ValueError("duplicate evidence_id values: " + ", ".join(duplicates))

        for label, items, id_field in (
            ("claim", self.claims, "field"),
            ("location", self.locations, "location_id"),
            ("entity", self.entities, "entity_id"),
            ("relationship", self.relationships, "relationship_id"),
        ):
            repeated = unique_ids(items, id_field)
            if repeated:
                raise ValueError(
                    f"duplicate {label} {id_field} values: " + ", ".join(repeated)
                )

        known = {e.evidence_id for e in self.evidence}
        referenced = collect_evidence_references(
            {
                "claims": self.claims,
                "locations": self.locations,
                "entities": self.entities,
                "relationships": self.relationships,
            }
        )
        dangling = sorted(referenced - known)
        if dangling:
            raise ValueError(
                "evidence references to unknown evidence_id values: " + ", ".join(dangling)
            )

        self._check_values_are_claimed()
        self._check_validation_state()
        return self

    def _claims_by_field(self) -> dict[str, Claim]:
        return {claim.field: claim for claim in self.claims}

    def _value_is_open(self, name: str) -> bool:
        value = getattr(self, name)
        if value is None:
            return True
        if isinstance(value, Enum):
            return value.value in ("unresolved", "unknown")
        return False

    def _check_values_are_claimed(self) -> None:
        claims = self._claims_by_field()
        for name in SCALAR_CLAIM_FIELDS:
            if self._value_is_open(name):
                continue
            claim = claims.get(name)
            if claim is None:
                raise ValueError(
                    f"{name} carries {getattr(self, name)!s} with no claim behind it: every "
                    "resolved field needs its quote, method and review state recorded"
                )

    def _check_validation_state(self) -> None:
        claims_by_field = self._claims_by_field()
        ungrounded = [
            name
            for name in SCALAR_CLAIM_FIELDS
            if not self._value_is_open(name)
            and claims_by_field.get(name) is not None
            and claims_by_field[name].review is not ReviewState.ACCEPTED
        ]
        flagged = [
            f"{kind}[{getattr(item, id_field)}]"
            for kind, items, id_field in (
                ("locations", self.locations, "location_id"),
                ("entities", self.entities, "entity_id"),
                ("relationships", self.relationships, "relationship_id"),
            )
            for item in items
            if item.review is not ReviewState.ACCEPTED
        ]
        for ident in flagged:
            if ident not in ungrounded:
                ungrounded.append(ident)

        if self.validation.state is ReviewState.UNRESOLVED:
            grounded = [c.field for c in self.claims if c.review is ReviewState.ACCEPTED]
            if grounded:
                raise ValueError(
                    "validation.state=unresolved but claims are accepted: "
                    + ", ".join(grounded)
                )
        elif self.validation.state is ReviewState.ACCEPTED:
            if ungrounded:
                raise ValueError(
                    "validation.state=accepted while these are not grounded: "
                    + ", ".join(ungrounded)
                )
        elif not ungrounded and not self.validation.issues:
            raise ValueError(
                "validation.state=review_required says nothing is wrong: accept it or "
                "record the finding that asks for a person"
            )

    @property
    def review_required(self) -> bool:
        return self.validation.review_required

    @property
    def is_empty(self) -> bool:
        """Nothing was established: an honest result, not a usable Incident."""
        return not any(claim.review is ReviewState.ACCEPTED for claim in self.claims)

    def claim(self, field: str) -> Optional[Claim]:
        return self._claims_by_field().get(field)

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}

    def resolve_evidence(self, evidence_ids: list[str]) -> list[Evidence]:
        index = self.evidence_by_id()
        return [index[eid] for eid in evidence_ids if eid in index]

    def unaccepted_fields(self) -> list[str]:
        """Every part of this Incident a person should look at, in field order."""
        open_claims = [
            claim.field for claim in self.claims if claim.review is not ReviewState.ACCEPTED
        ]
        open_items = [
            f"locations[{item.location_id}]"
            for item in self.locations
            if item.review is not ReviewState.ACCEPTED
        ] + [
            f"entities[{item.entity_id}]"
            for item in self.entities
            if item.review is not ReviewState.ACCEPTED
        ] + [
            f"relationships[{item.relationship_id}]"
            for item in self.relationships
            if item.review is not ReviewState.ACCEPTED
        ]
        return open_claims + open_items

    def to_storage_document(self) -> dict:
        """JSON-safe document for the Intelligence database."""
        return self.model_dump(mode="json")
