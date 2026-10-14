from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import CheckpointError, _write_atomically
from intelligence.contract import (
    SCHEMA_VERSION,
    Department,
    EventStatus,
    EventType,
    Incident,
    IncidentCategory,
    RecordKind,
    ReviewState,
)
from intelligence.fallback import Chain, ChainExhaustedError
from intelligence.linking import (
    DEFAULT_CONFIDENCE,
    EventLink,
    eligible,
    event_groups,
    shown_evidence,
    shown_hash,
)
from intelligence.llm import LLMError, LLMRequest, json_schema_for
from intelligence.models.base import Confidence, StrictModel
from intelligence.models.enums import (
    ExtractionMethod,
    MentionRole,
    MentionType,
    SeverityLevel,
    TimePrecision,
)

ISSUE_PROMPT_VERSION = "issue-1"
ISSUES_FILENAME = "issues.jsonl"
RECURRING_FILENAME = "recurring.json"

ISSUE_THRESHOLD = 0.8
MIN_RECURRENCE = 2
MAX_EVENTS_PER_BUCKET = 8
MAX_SHOWN_EVIDENCE = 10
MIN_COMPARISON_DAYS = 61

DATED = frozenset(
    {TimePrecision.SECOND, TimePrecision.MINUTE, TimePrecision.HOUR, TimePrecision.DAY}
)
OPEN_VALUES = frozenset({"unresolved", "other", "unknown"})
EVENT_ROLES = frozenset({MentionRole.EVENT_LOCATION, MentionRole.EVENT_CONTAINER})
RECORDED_STATUSES = frozenset({EventStatus.ACTION_TAKEN.value, EventStatus.RESOLVED.value})
OPEN_STATUSES = frozenset(
    {
        EventStatus.REPORTED.value,
        EventStatus.ONGOING.value,
        EventStatus.UNDER_INVESTIGATION.value,
    }
)
ADMIN_FIELDS = (
    ("district", MentionType.DISTRICT),
    ("taluk", MentionType.TALUK),
    ("block", MentionType.BLOCK),
    ("village", MentionType.VILLAGE),
)

SEVERITY_RANK = {
    SeverityLevel.INFO: 0,
    SeverityLevel.LOW: 1,
    SeverityLevel.MODERATE: 2,
    SeverityLevel.HIGH: 3,
    SeverityLevel.CRITICAL: 4,
}
PRECISION_RANK = {
    MentionType.DISTRICT: 1,
    MentionType.TALUK: 2,
    MentionType.BLOCK: 3,
    MentionType.VILLAGE: 4,
    MentionType.TOWN: 4,
    MentionType.LOCALITY: 5,
    MentionType.WARD: 6,
    MentionType.STREET: 7,
}
PRECISION_NAME: dict[int, str] = {}
for _mention, _rank in sorted(PRECISION_RANK.items(), key=lambda pair: pair[1]):
    PRECISION_NAME.setdefault(_rank, _mention.value)
UNDATED_KEY = "undated"

OLDEST = datetime.min.replace(tzinfo=timezone.utc)


class Filters(StrictModel):
    since: Optional[date] = None
    until: Optional[date] = None
    category: Optional[IncidentCategory] = None
    incident_type: Optional[EventType] = None
    department: Optional[Department] = None
    min_severity: Optional[SeverityLevel] = None
    place: Optional[str] = None

    def describe(self) -> str:
        parts = []
        if self.since is not None or self.until is not None:
            parts.append(
                f"from {self.since or 'the earliest record'} to {self.until or 'the latest record'}"
            )
        if self.category is not None:
            parts.append(f"category {self.category.value}")
        if self.incident_type is not None:
            parts.append(f"incident_type {self.incident_type.value}")
        if self.department is not None:
            parts.append(f"department {self.department.value}")
        if self.min_severity is not None:
            parts.append(f"severity at least {self.min_severity.value}")
        if self.place:
            parts.append(f"place containing {self.place!r}")
        return "; ".join(parts) or "no filter"

    def keeps(self, incident: Incident) -> bool:
        if self.category is not None and incident.category is not self.category:
            return False
        if self.incident_type is not None and incident.incident_type is not self.incident_type:
            return False
        if self.department is not None and incident.department is not self.department:
            return False
        if self.min_severity is not None:
            rank = SEVERITY_RANK.get(incident.severity)
            if rank is None or rank < SEVERITY_RANK[self.min_severity]:
                return False
        if self.place:
            wanted = " ".join(self.place.casefold().split())
            if not any(wanted in name for name in searchable_places(incident)):
                return False
        when, _ = incident_when(incident)
        return self.in_window(when)

    def in_window(self, when: Optional[datetime]) -> bool:
        if when is None:
            return self.since is None and self.until is None
        day = when.date()
        if self.since is not None and day < self.since:
            return False
        return not (self.until is not None and day > self.until)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _known(value: Any) -> bool:
    return getattr(value, "value", "") not in OPEN_VALUES


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def family_id_for(event_ids: Sequence[str]) -> str:
    return "ISS-" + hashlib.sha256("|".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:16]


def bucket_id_for(event_ids: Sequence[str]) -> str:
    return "BKT-" + hashlib.sha256("|".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:16]


def incident_when(incident: Incident) -> tuple[Optional[datetime], str]:
    if incident.event_time is not None and incident.event_time_precision in DATED:
        return _utc(incident.event_time), "event_time"
    if incident.provenance.declared_event_time is not None:
        return _utc(incident.provenance.declared_event_time), "declared"
    if incident.provenance.retrieved_at is not None:
        return _utc(incident.provenance.retrieved_at), "retrieved_at"
    return None, "none"


def area_entries(incident: Incident) -> list[tuple[str, MentionType, list[str]]]:
    found: list[tuple[str, MentionType, list[str]]] = []
    for location in incident.locations:
        if location.review is not ReviewState.ACCEPTED or location.role not in EVENT_ROLES:
            continue
        name = _text(location.normalized_name) or _text(location.text)
        if not name:
            continue
        entry = (name, location.location_type, list(location.evidence_ids))
        if entry not in found:
            found.append(entry)
        for level, kind in ADMIN_FIELDS:
            stated = _text(getattr(location, level, None))
            if not stated or _fold(stated) == _fold(name):
                continue
            parent = (stated, kind, list(location.evidence_ids))
            if parent not in found:
                found.append(parent)
    return found


def searchable_places(incident: Incident) -> set[str]:
    provenance = incident.provenance
    names = {_fold(name) for name, _kind, _ids in area_entries(incident)}
    for location in incident.locations:
        if location.review is not ReviewState.ACCEPTED:
            continue
        names |= {_fold(location.text), _fold(location.normalized_name)}
        names |= {_fold(getattr(location, level, None)) for level, _kind in ADMIN_FIELDS}
    names.add(_fold(provenance.district_hint))
    names.add(_fold(provenance.state_hint))
    return {name for name in names if name}


@dataclass
class Unit:
    event_id: str
    incidents: list[Incident]
    when: Optional[datetime]
    when_basis: str
    areas: list[tuple[str, MentionType, list[str]]]
    types: set[EventType]
    categories: set[IncidentCategory]
    departments: set[Department]
    severities: set[SeverityLevel]
    statuses: dict[str, tuple[str, str, list[str]]]

    @property
    def incident_ids(self) -> list[str]:
        return [incident.incident_id for incident in self.incidents]

    @property
    def record_ids(self) -> list[str]:
        return sorted({incident.provenance.record_id for incident in self.incidents})

    @property
    def source_urls(self) -> list[str]:
        return sorted({url for incident in self.incidents if (url := incident.provenance.source_url)})

    @property
    def day(self) -> Optional[date]:
        return self.when.date() if self.when is not None else None

    @property
    def keys(self) -> set[tuple[str, str]]:
        found = {(kind.value, _fold(name)) for name, kind, _ids in self.areas}
        found |= {("district", _fold(incident.provenance.district_hint)) for incident in self.incidents}
        return {(level, name) for level, name in found if name}

    @property
    def precision(self) -> int:
        return max((PRECISION_RANK.get(kind, 0) for _name, kind, _ids in self.areas), default=0)


def _quote_for(incident: Incident, evidence_ids: Sequence[str]) -> str:
    index = incident.evidence_by_id()
    for eid in evidence_ids:
        item = index.get(eid)
        if item is not None and item.quote:
            return item.quote
    return ""


def event_units(pool: Sequence[Incident], links: Sequence[EventLink]) -> list[Unit]:
    by_id = {incident.incident_id: incident for incident in pool}
    units: list[Unit] = []
    for group in event_groups(pool, links):
        members = [by_id[key] for key in group.incident_ids if key in by_id]
        if not members:
            continue
        stamps = [pair for pair in (incident_when(item) for item in members) if pair[0] is not None]
        when, basis = min(stamps, key=lambda pair: pair[0]) if stamps else (None, "none")
        areas: list[tuple[str, MentionType, list[str]]] = []
        statuses: dict[str, tuple[str, str, list[str]]] = {}
        for incident in members:
            for entry in area_entries(incident):
                if entry not in areas:
                    areas.append(entry)
            claim = incident.claim("event_status")
            if claim is None or claim.review is not ReviewState.ACCEPTED:
                continue
            if not _known(incident.event_status):
                continue
            statuses[incident.incident_id] = (
                incident.event_status.value,
                _quote_for(incident, claim.evidence_ids),
                list(claim.evidence_ids),
            )
        units.append(
            Unit(
                event_id=group.event_id,
                incidents=members,
                when=when,
                when_basis=basis,
                areas=areas,
                types={item.incident_type for item in members if _known(item.incident_type)},
                categories={item.category for item in members if _known(item.category)},
                departments={item.department for item in members if _known(item.department)},
                severities={item.severity for item in members if _known(item.severity)},
                statuses=statuses,
            )
        )
    return sorted(units, key=lambda unit: unit.event_id)


def shares_problem(a: Unit, b: Unit) -> bool:
    return bool(a.types & b.types) or bool(a.categories & b.categories)


def connects(a: Unit, b: Unit) -> list[str]:
    if not (a.keys & b.keys) or not shares_problem(a, b):
        return []
    found = [f"place {name} ({level})" for level, name in sorted(a.keys & b.keys)]
    found += [f"incident_type {item.value}" for item in sorted(a.types & b.types, key=lambda x: x.value)]
    found += [
        f"category {item.value}" for item in sorted(a.categories & b.categories, key=lambda x: x.value)
    ]
    return found


@dataclass
class Bucket:
    bucket_id: str
    units: tuple[Unit, ...]
    signals: tuple[str, ...]
    shown: tuple[Unit, ...] = ()
    truncated: int = 0

    def __post_init__(self) -> None:
        if not self.shown:
            self.shown = self.units


def candidate_buckets(units: Sequence[Unit]) -> list[Bucket]:
    parent = {unit.event_id: unit.event_id for unit in units}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for index, a in enumerate(units):
        for b in units[index + 1:]:
            if not connects(a, b):
                continue
            low, high = sorted((root(a.event_id), root(b.event_id)))
            parent[high] = low
    members: dict[str, list[Unit]] = {}
    for unit in units:
        members.setdefault(root(unit.event_id), []).append(unit)

    buckets: list[Bucket] = []
    for group in members.values():
        if len(group) < MIN_RECURRENCE:
            continue
        ordered = sorted(group, key=lambda unit: (unit.when or OLDEST, unit.event_id), reverse=True)
        shown = tuple(ordered[:MAX_EVENTS_PER_BUCKET])
        signals: set[str] = set()
        for index, a in enumerate(shown):
            for b in shown[index + 1:]:
                signals.update(connects(a, b))
        buckets.append(
            Bucket(
                bucket_id=bucket_id_for([unit.event_id for unit in shown]),
                units=tuple(ordered),
                shown=shown,
                signals=tuple(sorted(signals)),
                truncated=len(ordered) - len(shown),
            )
        )
    return sorted(buckets, key=lambda bucket: bucket.bucket_id)


def shown_units(bucket: Bucket) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    seen: set[str] = set()
    for unit in bucket.shown:
        own = 0
        for incident in unit.incidents:
            for item in shown_evidence(incident):
                if not item.quote or item.evidence_id in seen:
                    continue
                seen.add(item.evidence_id)
                own += 1
                rows.append(
                    (
                        item.evidence_id,
                        unit.event_id,
                        incident.provenance.record_id,
                        item.field,
                        item.quote,
                    )
                )
                if own >= MAX_SHOWN_EVIDENCE:
                    break
            if own >= MAX_SHOWN_EVIDENCE:
                break
    return rows


class FamilyDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: Optional[str] = None
    description: Optional[str] = None
    events: list[int] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    department: Optional[str] = None
    action: Optional[str] = None
    why_separate: list[str] = Field(default_factory=list)


class GroupDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    families: list[FamilyDraft] = Field(default_factory=list)
    alone: list[int] = Field(default_factory=list)
    explanation: Optional[str] = None


ISSUE_SCHEMA = json_schema_for(GroupDraft)

DEPARTMENT_TOKENS = ", ".join(item.value for item in Department if item is not Department.UNRESOLVED)

ISSUE_RULES = "\n".join(
    (
        "You are shown distinct district events. Repeated articles about one happening are already merged, "
        "so each event here happened once. Group them into recurring issue families. Answer with one JSON "
        "object and nothing else.",
        "1. Group by what the problem is, not by the words a report used: two differently worded events, or "
        "one in Tamil and one in English, belong together when the same thing keeps happening.",
        "2. Sharing a place, a district, a category or a department never makes one family. A flood and a "
        "road accident in one village are two families, and two events are never one family merely because "
        "they happened near each other or in the same kind of record.",
        "3. Different kinds of happening stay apart unless the events themselves say the problem is one "
        "problem: a drinking-water shortage, a canal breach and a polluted well are three families even "
        "when every report names the same ward.",
        "4. A family needs at least two events. One event is a single incident, never recurrence: list any "
        "event you cannot place in a family of two or more in alone.",
        "5. label names the problem in a few words and description says in one sentence what keeps "
        "happening and where. Use the events' own words for what is named, and never add a place, a number, "
        "a date or a toll the events do not carry.",
        "6. Cite in evidence_ids only ids printed in square brackets, from the events in that family, at "
        "least one quote from two different events. Never invent an id and never cite an event you were not "
        "shown.",
        "7. department is which district body owns the problem, one of: " + DEPARTMENT_TOKENS +
        ". Leave it null when the events do not say.",
        "8. action is a suggestion for that department, phrased as what could be done next. It is not a "
        "record: never state or imply that an inspection, repair, payment, hearing or closure already "
        "happened, is scheduled, or was ordered.",
        "9. confidence is how sure you are of the grouping, from 0.0 to 1.0; when you are guessing, answer "
        "below 0.5.",
        "10. why_separate names any pair of events you grouped although they look like different problems.",
        "11. explanation says in one or two sentences what you grouped and what you left alone.",
    )
)

ISSUE_PROMPT_DIGEST = hashlib.sha256(
    (ISSUE_RULES + json.dumps(ISSUE_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def _event_block(index: int, unit: Unit, rows: Sequence[tuple[str, str, str, str, str]]) -> str:
    lines = [f"Event {index} ({unit.event_id})"]
    lines.append(f"date: {unit.day.isoformat() if unit.day else UNDATED_KEY} (from {unit.when_basis})")
    lines.append("incidents: " + ", ".join(unit.incident_ids) + " | records: " + ", ".join(unit.record_ids))
    lines.append("sources: " + (", ".join(unit.source_urls) or "none named"))
    lines.append("incident_type: " + (", ".join(sorted(i.value for i in unit.types)) or "not stated"))
    lines.append("category: " + (", ".join(sorted(i.value for i in unit.categories)) or "not stated"))
    lines.append("department: " + (", ".join(sorted(i.value for i in unit.departments)) or "not stated"))
    lines.append("severity: " + (", ".join(sorted(i.value for i in unit.severities)) or "not stated"))
    lines.append(
        "places: "
        + (", ".join(f"{name} ({kind.value})" for name, kind, _ids in unit.areas) or "none named")
    )
    statuses = [f"{key}: {value[0]}" for key, value in sorted(unit.statuses.items())]
    lines.append("recorded status: " + ("; ".join(statuses) if statuses else "none recorded"))
    lines.append("evidence:")
    lines += [
        f"[{eid}] {record} {where}: {quote}"
        for eid, event, record, where, quote in rows
        if event == unit.event_id
    ]
    return "\n".join(lines)


def group_request(bucket: Bucket) -> LLMRequest:
    rows = shown_units(bucket)
    user = "\n\n".join(
        _event_block(index, unit, rows) for index, unit in enumerate(bucket.shown, start=1)
    )
    extra = [
        "The pipeline found these shared signals: " + ("; ".join(bucket.signals) or "none"),
        f"These {len(bucket.shown)} events are already distinct happenings. Each one shares a place and a "
        "kind of problem with at least one other, which is why they are shown together - that is not a "
        "reason to file them as one issue.",
    ]
    if bucket.truncated:
        extra.append(
            f"{bucket.truncated} further event(s) share these signals but were not shown to you; group only "
            "what is printed."
        )
    return LLMRequest(
        system=ISSUE_RULES,
        user=user + "\n\n" + "\n".join(extra),
        json_schema=ISSUE_SCHEMA,
        meta={"bucket_id": bucket.bucket_id, "prompt_version": ISSUE_PROMPT_VERSION},
    )


class IssueArea(StrictModel):
    level: str
    name: str
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class EventReference(StrictModel):
    event_id: str
    incident_id: str
    record_id: str
    source_id: str
    source_url: Optional[str] = None
    when: Optional[datetime] = None
    when_basis: str = "none"
    evidence_ids: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_quote_per_id(self) -> "EventReference":
        if len(self.quotes) != len(self.evidence_ids):
            raise ValueError("an event reference carries one quote for each evidence id it cites")
        return self


class RecordedResponse(StrictModel):
    incident_id: str
    record_id: str
    status: str
    quote: str
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _a_record_is_quoted(self) -> "RecordedResponse":
        if not self.quote:
            raise ValueError(
                f"a recorded response for {self.incident_id} carries no words from its record"
            )
        return self


class OpenFollowUp(StrictModel):
    incident_id: str
    record_id: str
    status: str
    quote: str
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _a_followup_is_quoted(self) -> "OpenFollowUp":
        if not self.quote:
            raise ValueError(
                f"an open follow-up for {self.incident_id} carries no words from its record"
            )
        return self


class SuggestedAction(StrictModel):
    action: str
    department: Department = Department.UNRESOLVED
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    review: ReviewState
    recorded: Literal[False] = False
    method: ExtractionMethod = ExtractionMethod.LLM


class PeriodCount(StrictModel):
    since: Optional[date] = None
    until: Optional[date] = None
    events: int = 0
    incidents: int = 0


class IssueFamily(StrictModel):
    schema_version: str = SCHEMA_VERSION
    family_id: str
    bucket_id: str
    label: Optional[str] = None
    description: Optional[str] = None
    event_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    events: int = 0
    incidents: int = 0
    records: int = 0
    references: list[EventReference] = Field(default_factory=list)
    areas: list[IssueArea] = Field(default_factory=list)
    geographic_precision: str = "unknown"
    first_event: Optional[date] = None
    last_event: Optional[date] = None
    span_days: Optional[int] = None
    per_month: dict[str, int] = Field(default_factory=dict)
    recent: Optional[PeriodCount] = None
    previous: Optional[PeriodCount] = None
    change: Optional[int] = None
    comparison: Optional[str] = None
    severities: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    incident_types: list[str] = Field(default_factory=list)
    recorded: list[RecordedResponse] = Field(default_factory=list)
    followups: list[OpenFollowUp] = Field(default_factory=list)
    suggested: list[SuggestedAction] = Field(default_factory=list)
    confidence: Confidence
    review: ReviewState
    method: ExtractionMethod = ExtractionMethod.LLM
    findings: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    @model_validator(mode="after")
    def _recurrence_is_counted_not_claimed(self) -> "IssueFamily":
        if self.events != len(self.event_ids):
            raise ValueError(f"events {self.events} is not the {len(self.event_ids)} event id(s) it lists")
        if self.incidents != len(self.incident_ids):
            raise ValueError(
                f"incidents {self.incidents} is not the {len(self.incident_ids)} incident id(s) it lists"
            )
        if self.records != len(self.record_ids):
            raise ValueError(
                f"records {self.records} is not the {len(self.record_ids)} record id(s) it lists"
            )
        if self.family_id != family_id_for(self.event_ids):
            raise ValueError(f"family id {self.family_id!r} is not the one its event ids derive")
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a family is accepted or waits for a person; it is never left unresolved")
        if len(self.event_ids) < MIN_RECURRENCE:
            raise ValueError(
                f"one distinct event establishes no recurrence; a family needs at least {MIN_RECURRENCE}"
            )
        if len(self.references) != self.incidents:
            raise ValueError("every incident a family counts carries one reference with its own source")
        if self.sum_of_months() != self.events:
            raise ValueError("per_month counts must add up to the events the family holds")
        if self.first_event and self.last_event and self.last_event < self.first_event:
            raise ValueError("last_event is before first_event")
        if self.first_event and self.span_days != (self.last_event - self.first_event).days:
            raise ValueError("span_days is not the days between first_event and last_event")
        if self.review is ReviewState.ACCEPTED:
            if self.findings:
                raise ValueError("an accepted family with findings is a silent acceptance")
            if not self.label or not self.description:
                raise ValueError("an accepted family names the problem and says what keeps happening")
            if self.confidence < ISSUE_THRESHOLD:
                raise ValueError(f"an accepted family needs confidence of at least {ISSUE_THRESHOLD}")
            cited = len({item.event_id for item in self.references if item.evidence_ids})
            if cited < MIN_RECURRENCE:
                raise ValueError("an accepted family cites a quote from at least two of its events")
        elif not self.findings:
            raise ValueError("a family held for review says why")
        return self

    def sum_of_months(self) -> int:
        return sum(self.per_month.values())


def areas_of(units: Sequence[Unit]) -> tuple[list[IssueArea], str]:
    found: dict[tuple[str, str], IssueArea] = {}
    for unit in units:
        for name, kind, ids in unit.areas:
            area = found.setdefault((kind.value, name), IssueArea(level=kind.value, name=name))
            if unit.event_id not in area.event_ids:
                area.event_ids.append(unit.event_id)
            area.evidence_ids += [eid for eid in ids if eid not in area.evidence_ids]
    ranks = [unit.precision for unit in units]
    shared = min(ranks) if ranks else 0
    return sorted(found.values(), key=lambda area: (area.level, area.name)), PRECISION_NAME.get(
        shared, "unknown"
    )


def _distribution(units: Sequence[Unit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in units:
        key = unit.day.strftime("%Y-%m") if unit.day is not None else UNDATED_KEY
        counts[key] = counts.get(key, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def _linked(units: Sequence[Unit]) -> bool:
    seen = {units[0].event_id}
    queue = [units[0]]
    while queue:
        current = queue.pop()
        for other in units:
            if other.event_id in seen or not connects(current, other):
                continue
            seen.add(other.event_id)
            queue.append(other)
    return len(seen) == len(units)


def _comparison(
    units: Sequence[Unit], filters: Filters
) -> tuple[Optional[PeriodCount], Optional[PeriodCount], Optional[int], Optional[str]]:
    dated = [unit for unit in units if unit.when is not None]
    if len(dated) < 2:
        return None, None, None, "fewer than two of its events carry a date, so nothing was measured over time"
    lo = min(unit.when for unit in dated)
    hi = max(unit.when for unit in dated)
    if filters.since is not None:
        lo = max(lo, datetime.combine(filters.since, time.min, tzinfo=timezone.utc))
    if filters.until is not None:
        hi = min(hi, datetime.combine(filters.until, time.max, tzinfo=timezone.utc))
    days = (hi - lo).days
    if days < MIN_COMPARISON_DAYS:
        return None, None, None, (
            f"its events span {days} day(s), shorter than the {MIN_COMPARISON_DAYS} days one period needs "
            "to be compared with another"
        )
    middle = lo + timedelta(days=days // 2)
    recent = [unit for unit in dated if unit.when >= middle]
    previous = [unit for unit in dated if unit.when < middle]
    if not recent or not previous:
        return None, None, None, (
            "every dated event falls in one half of the period, so there is no other half to compare with"
        )

    def window(items: Sequence[Unit], start: datetime, end: datetime) -> PeriodCount:
        return PeriodCount(
            since=start.date(),
            until=end.date(),
            events=len(items),
            incidents=sum(len(item.incidents) for item in items),
        )

    recent_window = window(recent, middle, hi)
    previous_window = window(previous, lo, middle)
    return recent_window, previous_window, recent_window.events - previous_window.events, None


@dataclass
class Grouped:
    families: list[IssueFamily] = field(default_factory=list)
    alone: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


def _indexes(raw: Sequence[Any], total: int, findings: list[str], label: str) -> list[int]:
    kept: list[int] = []
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            findings.append(f"{label} named event {item!r}, which is not a printed number")
            continue
        if not 1 <= index <= total:
            findings.append(f"{label} named event {index}, which was not shown to the model")
            continue
        if index in kept:
            findings.append(f"{label} named event {index} twice")
            continue
        kept.append(index)
    return kept


def _union(items: Sequence[set[Any]]) -> set[Any]:
    joined: set[Any] = set()
    for item in items:
        joined |= item
    return joined


def assess_bucket(
    bucket: Bucket,
    draft: GroupDraft,
    *,
    filters: Filters,
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
) -> Grouped:
    result = Grouped()
    rows = shown_units(bucket)
    owners = {eid: event for eid, event, _record, _field, _quote in rows}
    by_index = {index: unit for index, unit in enumerate(bucket.shown, start=1)}
    taken: dict[int, int] = {}
    filed: set[str] = set()
    for number, family in enumerate(draft.families, start=1):
        findings: list[str] = []
        label = _text(family.label) or None
        description = _text(family.description) or None
        indexes = _indexes(family.events, len(by_index), findings, f"family {number}")
        picked: list[int] = []
        for index in indexes:
            if index in taken:
                findings.append(
                    f"family {number} reuses event {index}, which family {taken[index]} already grouped"
                )
                continue
            taken[index] = number
            picked.append(index)
        units = [by_index[index] for index in picked if index in by_index]
        if len(units) < MIN_RECURRENCE:
            result.alone += [unit.event_id for unit in units]
            result.findings.append(
                f"family {number} named {len(units)} distinct event(s), which establishes no recurrence, "
                "so no issue was filed"
            )
            continue
        cited: list[str] = []
        for item in family.evidence_ids:
            eid = str(item).strip().strip("[]")
            if not eid or eid in cited:
                continue
            if eid in owners:
                cited.append(eid)
            else:
                findings.append(
                    f"family {number} cites evidence {eid!r}, which was not shown to the model"
                )
        if len({owners[eid] for eid in cited}) < MIN_RECURRENCE:
            findings.append("a family needs a quote from at least two of the events it groups")
        if not _linked(units):
            findings.append(
                "two of its events share neither a place nor a kind of problem with each other, so the "
                "family holds them only through a third event"
            )
        categories = _union([unit.categories for unit in units])
        if len(categories) > 1:
            findings.append(
                "one family spans categories " + ", ".join(sorted(item.value for item in categories))
            )
        if bucket.truncated:
            findings.append(
                f"{bucket.truncated} further event(s) share these signals but were not shown, so this issue "
                "may be smaller than the problem"
            )
        differences = [_text(item) for item in family.why_separate if _text(item)]
        if differences:
            findings.append("the model itself listed differences: " + "; ".join(differences))
        if label is None or description is None:
            findings.append("the model named no problem or did not say what keeps happening")
        confidence = family.confidence
        if confidence is None or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            findings.append(f"the model gave confidence {confidence!r}; {DEFAULT_CONFIDENCE} was used")
            confidence = DEFAULT_CONFIDENCE
        if confidence < ISSUE_THRESHOLD:
            findings.append(f"confidence {confidence:.2f} is below {ISSUE_THRESHOLD}")
        department = Department.UNRESOLVED
        if _text(family.department):
            try:
                department = Department(_text(family.department).casefold())
            except ValueError:
                findings.append(
                    f"the model named department {family.department!r}, which is not one the contract has"
                )
        units = sorted(units, key=lambda unit: (unit.when or OLDEST, unit.event_id))
        event_ids = sorted(unit.event_id for unit in units)
        incident_ids = sorted({key for unit in units for key in unit.incident_ids})
        record_ids = sorted({key for unit in units for key in unit.record_ids})
        source_urls = sorted({url for unit in units for url in unit.source_urls})
        references = []
        for unit in units:
            for incident in sorted(unit.incidents, key=lambda item: item.incident_id):
                index = incident.evidence_by_id()
                ids = [eid for eid in cited if eid in index]
                references.append(
                    EventReference(
                        event_id=unit.event_id,
                        incident_id=incident.incident_id,
                        record_id=incident.provenance.record_id,
                        source_id=incident.provenance.source_id,
                        source_url=incident.provenance.source_url,
                        when=unit.when,
                        when_basis=unit.when_basis,
                        evidence_ids=ids,
                        quotes=[index[eid].quote for eid in ids],
                    )
                )
        recorded: list[RecordedResponse] = []
        followups: list[OpenFollowUp] = []
        for unit in units:
            for incident in sorted(unit.incidents, key=lambda item: item.incident_id):
                status = unit.statuses.get(incident.incident_id)
                if status is None:
                    continue
                value, quote, claim_ids = status
                if not quote:
                    continue
                row = {
                    "incident_id": incident.incident_id,
                    "record_id": incident.provenance.record_id,
                    "status": value,
                    "quote": quote,
                    "source_url": incident.provenance.source_url,
                    "retrieved_at": incident.provenance.retrieved_at,
                    "evidence_ids": [eid for eid in claim_ids if eid in owners],
                }
                if value in RECORDED_STATUSES:
                    recorded.append(RecordedResponse(**row))
                elif value in OPEN_STATUSES:
                    followups.append(OpenFollowUp(**row))
        suggested: list[SuggestedAction] = []
        action = _text(family.action)
        if action:
            suggested.append(
                SuggestedAction(
                    action=action,
                    department=department,
                    event_ids=event_ids,
                    evidence_ids=cited,
                    confidence=confidence,
                    review=ReviewState.REVIEW_REQUIRED if findings else ReviewState.ACCEPTED,
                )
            )
        days = [unit.day for unit in units if unit.day is not None]
        areas, precision = areas_of(units)
        recent, previous, change, comparison = _comparison(units, filters)
        result.families.append(
            IssueFamily(
                family_id=family_id_for(event_ids),
                bucket_id=bucket.bucket_id,
                label=label,
                description=description,
                event_ids=event_ids,
                incident_ids=incident_ids,
                record_ids=record_ids,
                source_urls=source_urls,
                events=len(event_ids),
                incidents=len(incident_ids),
                records=len(record_ids),
                references=references,
                areas=areas,
                geographic_precision=precision,
                first_event=min(days) if days else None,
                last_event=max(days) if days else None,
                span_days=(max(days) - min(days)).days if days else None,
                per_month=_distribution(units),
                recent=recent,
                previous=previous,
                change=change,
                comparison=comparison,
                severities=sorted(item.value for item in _union([unit.severities for unit in units])),
                categories=sorted(item.value for item in categories),
                incident_types=sorted(
                    item.value for item in _union([unit.types for unit in units])
                ),
                recorded=recorded,
                followups=followups,
                suggested=suggested,
                confidence=confidence,
                review=ReviewState.REVIEW_REQUIRED if findings else ReviewState.ACCEPTED,
                findings=findings,
                provider=provider or "unknown",
                model=model or "unknown",
                prompt_version=ISSUE_PROMPT_VERSION,
                prompt_digest=ISSUE_PROMPT_DIGEST,
                shown_hash=shown,
                decided_at=decided_at,
            )
        )
        filed.update(event_ids)
    for index, unit in by_index.items():
        if unit.event_id not in filed:
            result.alone.append(unit.event_id)
    result.alone = sorted(set(result.alone))
    return result


@dataclass
class IssueReport:
    filters: Filters = field(default_factory=Filters)
    eligible: int = 0
    skipped_review: int = 0
    excluded_by_filter: int = 0
    units: int = 0
    undated: int = 0
    buckets: int = 0
    truncated: int = 0
    asked: int = 0
    reused: int = 0
    families: list[IssueFamily] = field(default_factory=list)
    alone: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_grouped: int = 0
    stop_reason: Optional[str] = None
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0

    @property
    def accepted(self) -> list[IssueFamily]:
        return [family for family in self.families if family.review is ReviewState.ACCEPTED]

    @property
    def review_families(self) -> list[IssueFamily]:
        return [family for family in self.families if family.review is ReviewState.REVIEW_REQUIRED]

    def limitations(self) -> list[str]:
        lines = []
        if not self.deduplicated:
            lines.append(
                "no link decisions were available, so each accepted incident was counted as its own event "
                "and repeated reports of one happening count separately here"
            )
        if self.undecided_links:
            lines.append(
                f"{self.undecided_links} link decision(s) wait for a person, so two happenings that may be "
                "one are counted as two"
            )
        if self.skipped_review:
            lines.append(
                f"{self.skipped_review} incident(s) waiting for a person were left out of every count"
            )
        if self.excluded_by_filter:
            lines.append(
                f"{self.excluded_by_filter} incident(s) fell outside the filters used "
                f"({self.filters.describe()})"
            )
        if self.undated:
            lines.append(
                f"{self.undated} distinct event(s) carry no date the record or the reading gave, so they "
                "are outside the period and its counts"
            )
        if self.truncated:
            lines.append(
                f"{self.truncated} event(s) shared a signal but were beyond the {MAX_EVENTS_PER_BUCKET} "
                "shown in one request, so a filed issue may be smaller than the problem"
            )
        if self.not_grouped:
            lines.append(f"{self.not_grouped} candidate group(s) were never asked about")
        if self.failed:
            lines.append(f"{len(self.failed)} candidate group(s) got no usable answer")
        if self.alone:
            lines.append(
                f"{len(self.alone)} distinct event(s) matched nothing else, which says nothing about records "
                "this run did not read"
            )
        lines.append(
            "a suggested action is generated text beside the events that support it, never a record of "
            "work assigned, done or acknowledged"
        )
        return lines

    def counts(self) -> dict[str, Any]:
        return {
            "Filters": self.filters.describe(),
            "Eligible incidents": self.eligible,
            "Left out (waiting for a person)": self.skipped_review,
            "Left out (outside the filters)": self.excluded_by_filter,
            "Distinct events counted": self.units,
            "Events with no date": self.undated,
            "Candidate groups": self.buckets,
            "Grouped now": self.asked,
            "Reused decisions": self.reused,
            "Not grouped": self.not_grouped,
            "Recurring issues": len(self.families),
            "Accepted recurring issues": len(self.accepted),
            "Issues for review": len(self.review_families),
            "Events matching nothing else": len(self.alone),
        }


def recurring_issues(
    incidents: Iterable[Incident],
    *,
    chain_factory: Callable[[], Chain],
    filters: Optional[Filters] = None,
    store: Optional[IssueStore] = None,
    links: Sequence[EventLink] = (),
    now: Optional[datetime] = None,
) -> IssueReport:
    applied = filters or Filters()
    candidates = [
        incident for incident in incidents if incident.record_kind is RecordKind.INCIDENT
    ]
    kept = eligible(candidates)
    report = IssueReport(
        filters=applied,
        skipped_review=len([item for item in candidates if item.review_required]),
        deduplicated=bool(links),
        links=len(links),
        undecided_links=len(
            [link for link in links if link.review is ReviewState.REVIEW_REQUIRED]
        ),
    )
    inside = [incident for incident in kept if applied.keeps(incident)]
    report.eligible = len(kept)
    report.excluded_by_filter = len(kept) - len(inside)
    units = event_units(inside, links)
    report.undated = len([unit for unit in units if unit.when is None])
    dated = [unit for unit in units if unit.when is not None and applied.in_window(unit.when)]
    report.units = len(dated)
    buckets = candidate_buckets(dated)
    report.buckets = len(buckets)
    report.truncated = sum(bucket.truncated for bucket in buckets)
    decided_at = now or datetime.now(timezone.utc)
    chain: Optional[Chain] = None
    for bucket in buckets:
        request = group_request(bucket)
        shown = shown_hash(request)
        found = store.reusable(bucket.bucket_id, shown) if store is not None else None
        if found is not None:
            report.families += found
            report.reused += 1
            grouped = {key for family in found for key in family.event_ids}
            report.alone += [unit.event_id for unit in bucket.shown if unit.event_id not in grouped]
            continue
        if report.stop_reason is not None:
            report.not_grouped += 1
            continue
        if chain is None:
            try:
                chain = chain_factory()
            except LLMError as error:
                report.stop_reason = str(error)
                report.not_grouped += 1
                continue
        try:
            answered = chain.read(lambda provider: provider.generate_structured(request))
        except ChainExhaustedError as error:
            report.stop_reason = str(error)
            report.not_grouped += 1
            continue
        except LLMError as error:
            report.failed.append(f"{bucket.bucket_id}: {error}")
            continue
        report.asked += 1
        try:
            draft = GroupDraft.model_validate(answered.result.payload)
        except ValidationError as error:
            report.failed.append(
                f"{bucket.bucket_id}: the answer is not a grouping "
                f"({error.error_count()} problem(s))"
            )
            continue
        provider, model = answered.identity
        grouped = assess_bucket(
            bucket,
            draft,
            filters=applied,
            provider=provider,
            model=model,
            shown=shown,
            decided_at=decided_at,
        )
        report.families += grouped.families
        report.alone += grouped.alone
        report.findings += grouped.findings
    report.alone = sorted(set(report.alone))
    if store is not None:
        store.save(report)
    return report


class IssueStore:
    def __init__(self, directory: Any) -> None:
        self.directory = Path(str(directory))
        self.issues_path = self.directory / ISSUES_FILENAME
        self.summary_path = self.directory / RECURRING_FILENAME
        self.buckets: dict[str, list[IssueFamily]] = {}

    def load(self) -> "IssueStore":
        if not self.issues_path.exists():
            return self
        try:
            text = self.issues_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{self.issues_path} could not be read: {error}") from error
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                family = IssueFamily.model_validate_json(line)
            except ValidationError as error:
                problem = error.errors()[0].get("msg", "invalid")
                raise CheckpointError(
                    f"{self.issues_path} line {number} is not a grouping this pipeline wrote "
                    f"({problem}); the saved groupings were not changed"
                ) from error
            self.buckets.setdefault(family.bucket_id, []).append(family)
        return self

    def reusable(self, bucket_id: str, shown: str) -> Optional[list[IssueFamily]]:
        found = self.buckets.get(bucket_id)
        if not found:
            return None
        for family in found:
            if (
                family.schema_version != SCHEMA_VERSION
                or family.prompt_digest != ISSUE_PROMPT_DIGEST
                or family.shown_hash != shown
            ):
                return None
        return found

    def save(self, report: IssueReport) -> None:
        fresh: dict[str, list[IssueFamily]] = {}
        for family in report.families:
            fresh.setdefault(family.bucket_id, []).append(family)
        for bucket_id, families in fresh.items():
            self.buckets[bucket_id] = sorted(families, key=lambda item: item.family_id)
        every = [family for key in sorted(self.buckets) for family in self.buckets[key]]
        lines = [
            json.dumps(family.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            for family in every
        ]
        _write_atomically(self.issues_path, "".join(lines))
        summary = {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": ISSUE_PROMPT_VERSION,
            "prompt_digest": ISSUE_PROMPT_DIGEST,
            "filters": report.filters.model_dump(mode="json"),
            "counts": report.counts(),
            "limitations": report.limitations(),
            "issues": [family.model_dump(mode="json") for family in report.families],
        }
        _write_atomically(
            self.summary_path, json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )


def print_issues(report: IssueReport, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream or sys.stdout
    print(
        f"Recurring issues ({ISSUE_PROMPT_VERSION}, {ISSUE_PROMPT_DIGEST}, incident contract "
        f"{SCHEMA_VERSION})",
        file=out,
    )
    for label, value in report.counts().items():
        print(f"  {label}: {value}", file=out)
    if report.stop_reason:
        print(f"  Stopped: {report.stop_reason}", file=out)
    for line in report.failed:
        print(f"  failed {line}", file=out)
    for line in report.findings:
        print(f"  refused {line}", file=out)
    for family in report.families:
        span = "?" if family.span_days is None else str(family.span_days)
        line = (
            f"  {family.family_id} {family.label or 'unlabelled'}: {family.events} distinct event(s) over "
            f"{span} day(s), {family.incidents} incident(s) from {family.records} record(s), at "
            f"{family.geographic_precision} precision, confidence {family.confidence:.2f}, "
            f"{family.review.value}"
        )
        if family.findings:
            line += " | " + "; ".join(family.findings)
        print(line, file=out)
        if not detailed:
            continue
        print(f"    {family.description or 'no description'}", file=out)
        print(
            "    areas: "
            + (
                ", ".join(f"{area.name} ({area.level})" for area in family.areas)
                or "none named by the records"
            ),
            file=out,
        )
        print(
            "    per month: "
            + (", ".join(f"{key}={value}" for key, value in family.per_month.items()) or "none"),
            file=out,
        )
        if family.change is not None:
            print(
                f"    change: {family.change:+d} event(s), {family.previous.events} in "
                f"{family.previous.since}..{family.previous.until} then {family.recent.events} in "
                f"{family.recent.since}..{family.recent.until}",
                file=out,
            )
        elif family.comparison:
            print(f"    change: not compared - {family.comparison}", file=out)
        for row in family.recorded:
            print(f"    recorded by {row.record_id}: {row.status} - {row.quote}", file=out)
        for row in family.followups:
            print(f"    open follow-up in {row.record_id}: {row.status} - {row.quote}", file=out)
        for suggestion in family.suggested:
            print(
                f"    suggested for {suggestion.department.value}: {suggestion.action} "
                "(suggested, not recorded)",
                file=out,
            )
    if report.alone:
        print("  alone: " + ", ".join(report.alone), file=out)
    for line in report.limitations():
        print(f"  limitation: {line}", file=out)


__all__ = [
    "Bucket",
    "DEPARTMENT_TOKENS",
    "EventReference",
    "FamilyDraft",
    "Filters",
    "Grouped",
    "ISSUES_FILENAME",
    "ISSUE_PROMPT_DIGEST",
    "ISSUE_PROMPT_VERSION",
    "ISSUE_RULES",
    "ISSUE_SCHEMA",
    "ISSUE_THRESHOLD",
    "IssueArea",
    "IssueFamily",
    "IssueReport",
    "IssueStore",
    "MAX_EVENTS_PER_BUCKET",
    "MAX_SHOWN_EVIDENCE",
    "MIN_COMPARISON_DAYS",
    "MIN_RECURRENCE",
    "OpenFollowUp",
    "PeriodCount",
    "RECURRING_FILENAME",
    "RecordedResponse",
    "SuggestedAction",
    "Unit",
    "assess_bucket",
    "area_entries",
    "areas_of",
    "bucket_id_for",
    "candidate_buckets",
    "connects",
    "event_units",
    "family_id_for",
    "group_request",
    "incident_when",
    "print_issues",
    "recurring_issues",
    "searchable_places",
    "shown_units",
]
