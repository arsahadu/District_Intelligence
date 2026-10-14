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

VALIDATOR = "intelligence.intelligence"

_HEX64 = r"^[0-9a-f]{64}$"

_PROBABILISTIC = frozenset({ExtractionMethod.LLM, ExtractionMethod.STATISTICAL})


class ReviewState(str, Enum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    UNRESOLVED = "unresolved"


class EventStatus(str, Enum):
    REPORTED = "reported"
    ONGOING = "ongoing"
    UNDER_INVESTIGATION = "under_investigation"
    ACTION_TAKEN = "action_taken"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class IncidentCategory(str, Enum):
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
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RecordKind(str, Enum):
    INCIDENT = "incident"
    FORECAST = "forecast"
    CONTEXT = "context"
    UNRESOLVED = "unresolved"


class ContextType(str, Enum):
    MARKET_PRICE = "market_price"
    WEATHER_FORECAST = "weather_forecast"
    ADMINISTRATIVE_NOTICE = "administrative_notice"
    GENERAL_INFORMATION = "general_information"
    OTHER = "other"


class EntityType(str, Enum):
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
    LOCATED_AT = "located_at"
    INVOLVES = "involves"
    RESPONDS_TO = "responds_to"
    RELATED_TO = "related_to"
    OTHER = "other"


class IssueCode(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    SPAN_MISMATCH = "span_mismatch"
    INVALID_VALUE = "invalid_value"
    UNKNOWN_FIELD = "unknown_field"
    UNAUTHORISED_GIS_VALUE = "unauthorised_gis_value"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    MISSING_PROVENANCE = "missing_provenance"
    AMBIGUOUS_QUOTE = "ambiguous_quote"
    PROVIDER_WARNING = "provider_warning"
    UNCERTAIN_RECOGNITION = "uncertain_recognition"


class Provenance(StrictModel):
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

    declared_event_time: Optional[datetime] = None
    declared_event_time_precision: Optional[TimePrecision] = None

    fields_without_value: list[str] = Field(default_factory=list)

    input_hash: Optional[str] = Field(default=None, pattern=_HEX64)

    @property
    def has_view_source(self) -> bool:
        return self.source_url is not None or self.raw_reference is not None


class Claim(StrictModel):
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


class ContextFact(StrictModel):
    fact_id: str
    field: str
    value: str

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    review: ReviewState = ReviewState.UNRESOLVED
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _accepted_needs_evidence(self) -> "ContextFact":
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            raise ValueError(f"accepted context fact {self.fact_id!r} cites no evidence")
        return self


class SourceGeography(StrictModel):
    geography_id: str
    field: str
    value: str

    method: ExtractionMethod = ExtractionMethod.SOURCE_METADATA
    confidence: OptionalConfidence = None
    review: ReviewState = ReviewState.ACCEPTED
    evidence_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _copied_not_inferred(self) -> "SourceGeography":
        if any(character.isspace() for character in self.field):
            raise ValueError("source geography field must be a dotted path without whitespace")
        if self.method in _PROBABILISTIC:
            raise ValueError(
                f"source geography {self.field!r} cannot come from {self.method.value}: a value "
                "the model inferred is a location, not something the record carried"
            )
        if self.review is ReviewState.ACCEPTED and not self.evidence_ids:
            raise ValueError(f"accepted source geography {self.geography_id!r} cites no evidence")
        return self


class Issue(StrictModel):
    code: IssueCode
    field: Optional[str] = None
    detail: str

    def describe(self) -> str:
        if self.field is None:
            return f"{self.code.value}: {self.detail}"
        return f"{self.field} {self.code.value}: {self.detail}"


class Validation(StrictModel):
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
    provider: str
    model: str
    prompt_version: str

    input_hash: Optional[str] = Field(default=None, pattern=_HEX64)
    generated_at: Optional[datetime] = None
    latency_ms: Optional[float] = Field(default=None, ge=0.0)
    warnings: list[str] = Field(default_factory=list)


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

# each of these answers a question about a happening, so a record stating none leaves it open
EVENT_ONLY_FIELDS = ("category", "department", "severity", "priority", "event_status")


class Incident(StrictModel):
    schema_version: str = SCHEMA_VERSION

    incident_id: str
    provenance: Provenance

    record_kind: RecordKind = RecordKind.UNRESOLVED
    context_type: Optional[ContextType] = None

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

    context_facts: list[ContextFact] = Field(default_factory=list)

    source_geography: list[SourceGeography] = Field(default_factory=list)

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
            ("context fact", self.context_facts, "fact_id"),
            ("source geography", self.source_geography, "geography_id"),
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
                "context_facts": self.context_facts,
                "source_geography": self.source_geography,
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

    @model_validator(mode="after")
    def _kind_is_what_the_evidence_said(self) -> "Incident":
        typed = not self._value_is_open("incident_type")
        if self.record_kind is RecordKind.INCIDENT and not (typed or self._account_is_an_event()):
            raise ValueError(
                "record_kind=incident with nothing evidencing an event: neither incident_type nor "
                "a grounded account of what happened is quoted, so call it context or leave it "
                "unresolved, never an incident without the event"
            )
        if self.record_kind in (RecordKind.FORECAST, RecordKind.CONTEXT) and typed:
            raise ValueError(
                f"record_kind={self.record_kind.value} while incident_type carries "
                f"{self.incident_type.value}: an evidenced event is not context"
            )
        if self.record_kind in (RecordKind.FORECAST, RecordKind.CONTEXT):
            filled = [
                f"{name}={getattr(self, name).value}"
                for name in EVENT_ONLY_FIELDS
                if not self._value_is_open(name)
            ]
            if filled:
                raise ValueError(
                    "record_kind=" + self.record_kind.value + " while an event field is filled ("
                    + ", ".join(filled) + "): each of these answers a question about a happening - "
                    "which event, whose job, how much harm, how soon to act, whether it is still "
                    "going - and a record read as a forecast or as context states no happening, so "
                    "each holds its absence value while context_type and context_facts carry what "
                    "the record actually says"
                )
        if self.context_type is not None and self.record_kind not in (
            RecordKind.FORECAST,
            RecordKind.CONTEXT,
        ):
            raise ValueError(
                "context_type is only carried by a record this pipeline read as a forecast or as "
                "context, never by an incident"
            )
        return self

    def _account_is_an_event(self) -> bool:
        claim = self._claims_by_field().get("description")
        return (
            self.description is not None
            and claim is not None
            and claim.review is ReviewState.ACCEPTED
        )

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
                ("context_facts", self.context_facts, "fact_id"),
                ("source_geography", self.source_geography, "geography_id"),
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
        return not any(claim.review is ReviewState.ACCEPTED for claim in self.claims)

    def claim(self, field: str) -> Optional[Claim]:
        return self._claims_by_field().get(field)

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}

    def resolve_evidence(self, evidence_ids: list[str]) -> list[Evidence]:
        index = self.evidence_by_id()
        return [index[eid] for eid in evidence_ids if eid in index]

    def unaccepted_fields(self) -> list[str]:
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
        ] + [
            f"context_facts[{item.fact_id}]"
            for item in self.context_facts
            if item.review is not ReviewState.ACCEPTED
        ] + [
            f"source_geography[{item.geography_id}]"
            for item in self.source_geography
            if item.review is not ReviewState.ACCEPTED
        ]
        return open_claims + open_items

    def to_storage_document(self) -> dict:
        return self.model_dump(mode="json")
