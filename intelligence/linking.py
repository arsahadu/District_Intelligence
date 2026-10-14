from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import CheckpointError, _write_atomically
from intelligence.contract import (
    SCHEMA_VERSION,
    EntityType,
    Incident,
    RecordKind,
    ReviewState,
)
from intelligence.fallback import Chain, ChainExhaustedError
from intelligence.llm import LLMError, LLMRequest, json_schema_for
from intelligence.models.base import Confidence, StrictModel
from intelligence.models.enums import (
    ExtractionMethod,
    MentionRole,
    MentionType,
    ResolutionState,
    TimePrecision,
)
from intelligence.models.evidence import Evidence

LINK_PROMPT_VERSION = "link-1"
LINKS_FILENAME = "links.jsonl"
EVENTS_FILENAME = "events.json"

LINK_THRESHOLD = 0.8
DEFAULT_CONFIDENCE = 0.5
MAX_CANDIDATES_PER_INCIDENT = 5
MAX_SHOWN_EVIDENCE = 12
REPORTING_GAP = timedelta(days=7)

DATED = frozenset({TimePrecision.SECOND, TimePrecision.MINUTE, TimePrecision.HOUR, TimePrecision.DAY})
OPEN_VALUES = frozenset({"unresolved", "other", "unknown"})
OFF_EVENT_ROLES = frozenset({
    MentionRole.REPORTING_ORIGIN,
    MentionRole.MENTIONED_ONLY,
    MentionRole.INSTITUTION_NAME,
    MentionRole.ACTOR_AFFILIATION,
})


class Verdict(str, Enum):
    SAME_EVENT = "same_event"
    SEPARATE_EVENT = "separate_event"
    UNDETERMINED = "undetermined"


class LinkDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Optional[str] = None
    confidence: Optional[float] = None
    evidence_a: list[str] = Field(default_factory=list)
    evidence_b: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None


LINK_SCHEMA = json_schema_for(LinkDraft)


class LinkSide(StrictModel):
    incident_id: str
    record_id: str
    source_id: str
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    evidence_ids: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_quote_per_id(self) -> "LinkSide":
        if len(self.quotes) != len(self.evidence_ids):
            raise ValueError("a link side carries exactly one quote for each evidence id it cites")
        return self


def link_id_for(first: str, second: str) -> str:
    low, high = sorted((first, second))
    return "LNK-" + hashlib.sha256(f"{low}|{high}".encode("utf-8")).hexdigest()[:16]


class EventLink(StrictModel):
    schema_version: str = SCHEMA_VERSION
    link_id: str
    a: LinkSide
    b: LinkSide
    verdict: Verdict
    confidence: Confidence
    review: ReviewState
    method: ExtractionMethod = ExtractionMethod.LLM
    shared: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    provider: str
    model: str
    prompt_version: str
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    @model_validator(mode="after")
    def _decision_is_supported(self) -> "EventLink":
        if not self.a.incident_id < self.b.incident_id:
            raise ValueError("side a must hold the lower incident id so a pair has one spelling")
        if self.a.record_id == self.b.record_id:
            raise ValueError("a link joins two source records, never a record to itself")
        if self.link_id != link_id_for(self.a.incident_id, self.b.incident_id):
            raise ValueError(f"link id {self.link_id!r} is not the one its two incidents derive")
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a link is accepted or waits for a person; it is never left unresolved")
        if self.review is ReviewState.ACCEPTED:
            if self.findings:
                raise ValueError("an accepted link with findings is a silent acceptance")
            if self.verdict is Verdict.UNDETERMINED:
                raise ValueError("an undetermined comparison cannot be accepted")
            if self.confidence < LINK_THRESHOLD:
                raise ValueError(f"an accepted link needs confidence of at least {LINK_THRESHOLD}")
            if self.verdict is Verdict.SAME_EVENT:
                if not self.a.evidence_ids or not self.b.evidence_ids:
                    raise ValueError("an accepted same_event link cites evidence from both reports")
                if self.conflicts:
                    raise ValueError("an accepted same_event link carries no unexplained conflict")
        elif not self.findings:
            raise ValueError("a link held for review says why")
        return self

    @property
    def merges(self) -> bool:
        return self.review is ReviewState.ACCEPTED and self.verdict is Verdict.SAME_EVENT


class EventGroup(StrictModel):
    schema_version: str = SCHEMA_VERSION
    event_id: str
    incident_ids: list[str]
    record_ids: list[str]
    source_urls: list[str] = Field(default_factory=list)
    link_ids: list[str] = Field(default_factory=list)
    review: ReviewState = ReviewState.ACCEPTED
    findings: list[str] = Field(default_factory=list)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def eligible(incidents: Iterable[Incident]) -> list[Incident]:
    kept: dict[str, Incident] = {}
    for incident in incidents:
        if incident.record_kind is not RecordKind.INCIDENT or incident.review_required:
            continue
        kept.setdefault(incident.incident_id, incident)
    return [kept[key] for key in sorted(kept)]


def reported_at(incident: Incident) -> Optional[datetime]:
    provenance = incident.provenance
    return _utc(provenance.declared_event_time or provenance.retrieved_at or incident.event_time)


def _name(text: Optional[str], normalized: Optional[str]) -> str:
    return " ".join((normalized or text or "").casefold().split())


def place_names(incident: Incident) -> set[str]:
    provenance = incident.provenance
    broad = {_name(provenance.district_hint, None), _name(provenance.state_hint, None)}
    names = set()
    for location in incident.locations:
        if location.review is not ReviewState.ACCEPTED or location.location_type is MentionType.DISTRICT:
            continue
        if location.role in OFF_EVENT_ROLES:
            continue
        name = _name(location.text, location.normalized_name)
        if name and name not in broad and name != _name(location.district, None):
            names.add(name)
    return names


def people(incident: Incident) -> set[str]:
    return {
        _name(entity.text, entity.normalized_name)
        for entity in incident.entities
        if entity.review is ReviewState.ACCEPTED and entity.entity_type is EntityType.PERSON
    } - {""}


def _known(value: Enum) -> bool:
    return value.value not in OPEN_VALUES


def shared_signals(a: Incident, b: Incident) -> list[str]:
    found = []
    if _known(a.incident_type) and a.incident_type is b.incident_type:
        found.append(f"incident_type {a.incident_type.value}")
    if _known(a.category) and a.category is b.category:
        found.append(f"category {a.category.value}")
    found += [f"place {name}" for name in sorted(place_names(a) & place_names(b))]
    found += [f"person {name}" for name in sorted(people(a) & people(b))]
    return found


def _resolved_places(incident: Incident) -> set[str]:
    return {
        location.canonical_location_id
        for location in incident.locations
        if location.resolution_state is ResolutionState.RESOLVED and location.canonical_location_id
    }


def conflicts(a: Incident, b: Incident) -> list[str]:
    found = []
    first, second = _utc(a.event_time), _utc(b.event_time)
    if (
        first is not None
        and second is not None
        and a.event_time_precision in DATED
        and b.event_time_precision in DATED
        and first.date() != second.date()
    ):
        found.append(f"event_time {first.date().isoformat()} vs {second.date().isoformat()}")
    if _known(a.incident_type) and _known(b.incident_type) and a.incident_type is not b.incident_type:
        found.append(f"incident_type {a.incident_type.value} vs {b.incident_type.value}")
    places_a, places_b = _resolved_places(a), _resolved_places(b)
    if places_a and places_b and not places_a & places_b:
        found.append(
            "resolved places " + ", ".join(sorted(places_a)) + " vs " + ", ".join(sorted(places_b))
        )
    return found


@dataclass(frozen=True)
class Pair:
    a: Incident
    b: Incident
    shared: tuple[str, ...]
    conflicts: tuple[str, ...]
    gap: Optional[timedelta]

    @property
    def link_id(self) -> str:
        return link_id_for(self.a.incident_id, self.b.incident_id)

    def rank(self) -> tuple:
        return (-len(self.shared), self.gap if self.gap is not None else timedelta.max, self.link_id)


def candidate_pairs(pool: Sequence[Incident], *, per_incident: int = MAX_CANDIDATES_PER_INCIDENT) -> list[Pair]:
    ordered = sorted(pool, key=lambda incident: incident.incident_id)
    every: list[Pair] = []
    for index, a in enumerate(ordered):
        for b in ordered[index + 1:]:
            if a.provenance.record_id == b.provenance.record_id:
                continue
            shared = shared_signals(a, b)
            if not shared:
                continue
            first, second = reported_at(a), reported_at(b)
            gap = abs(first - second) if first is not None and second is not None else None
            if gap is not None and gap > REPORTING_GAP:
                continue
            every.append(Pair(a, b, tuple(shared), tuple(conflicts(a, b)), gap))
    kept: dict[str, Pair] = {}
    by_incident: dict[str, list[Pair]] = {}
    for pair in every:
        by_incident.setdefault(pair.a.incident_id, []).append(pair)
        by_incident.setdefault(pair.b.incident_id, []).append(pair)
    for pairs in by_incident.values():
        for pair in sorted(pairs, key=Pair.rank)[:per_incident]:
            kept[pair.link_id] = pair
    return [kept[key] for key in sorted(kept)]


def shown_evidence(incident: Incident) -> list[Evidence]:
    cited: list[str] = []
    for item in [*incident.claims, *incident.locations, *incident.entities]:
        if item.review is ReviewState.ACCEPTED:
            cited += [eid for eid in item.evidence_ids if eid not in cited]
    index = incident.evidence_by_id()
    return [index[eid] for eid in cited if eid in index and index[eid].quote][:MAX_SHOWN_EVIDENCE]


def _stamp(value: Optional[datetime]) -> str:
    return value.isoformat() if value is not None else "not stated"


def report_block(label: str, incident: Incident) -> str:
    provenance = incident.provenance
    when = "not stated"
    if incident.event_time is not None:
        when = f"{incident.event_time.isoformat()} ({incident.event_time_precision.value})"
    places = sorted(
        location.normalized_name or location.text
        for location in incident.locations
        if location.review is ReviewState.ACCEPTED
    )
    actors = sorted(
        entity.normalized_name or entity.text
        for entity in incident.entities
        if entity.review is ReviewState.ACCEPTED
    )
    lines = [
        f"Report {label}",
        f"source: {provenance.source_id} ({provenance.source_type}) {provenance.source_url or 'no url'}",
        f"published: {_stamp(provenance.declared_event_time)}",
        f"title: {incident.title or 'none'}",
        f"description: {incident.description or 'none'}",
        f"incident_type: {incident.incident_type.value}",
        f"category: {incident.category.value}",
        f"event_time: {when}",
        f"places: {', '.join(places) or 'none named'}",
        f"people and bodies: {', '.join(actors) or 'none named'}",
        "evidence:",
    ]
    lines += [f"[{item.evidence_id}] {item.field}: {item.quote}" for item in shown_evidence(incident)]
    return "\n".join(lines)


LINK_RULES = "\n".join(
    (
        "You compare two district reports and decide whether they describe the same real-world event. "
        "Answer with one JSON object and nothing else.",
        "1. verdict is same_event when both reports describe one happening at one place and time, "
        "including a follow-up or update of it; separate_event when they describe different happenings, "
        "even of the same kind; undetermined when the reports do not say enough to tell.",
        "2. Sharing an incident type, a district, a village, a department or a category never makes two "
        "reports one event: two floods, accidents or protests in the same place on different days are "
        "separate events.",
        "3. Compare by meaning, not wording: a Tamil report and an English report, or two different "
        "headlines, can describe one event.",
        "4. List in conflicts every difference that argues against one event - another date, place, toll "
        "or people involved - including any the pipeline found; never answer same_event while a conflict "
        "stays unexplained.",
        "5. For same_event cite in evidence_a and evidence_b the ids of the quotes in each report that "
        "show it is the same happening; cite only ids printed in square brackets and never invent one.",
        "6. confidence is how sure you are of the verdict, from 0.0 to 1.0; if you are guessing, answer "
        "undetermined.",
        "7. explanation says in one or two sentences what decided it.",
    )
)

LINK_PROMPT_DIGEST = hashlib.sha256(
    (LINK_RULES + json.dumps(LINK_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def comparison_request(pair: Pair) -> LLMRequest:
    user = "\n\n".join(
        (
            report_block("A", pair.a),
            report_block("B", pair.b),
            "The pipeline found these shared signals: " + "; ".join(pair.shared),
            "The pipeline found these conflicts: " + ("; ".join(pair.conflicts) or "none"),
        )
    )
    return LLMRequest(
        system=LINK_RULES,
        user=user,
        json_schema=LINK_SCHEMA,
        meta={"link_id": pair.link_id, "prompt_version": LINK_PROMPT_VERSION},
    )


def shown_hash(request: LLMRequest) -> str:
    return hashlib.sha256((request.system + "\n" + request.user).encode("utf-8")).hexdigest()


def _side(incident: Incident, cited: Sequence[str]) -> LinkSide:
    index = incident.evidence_by_id()
    provenance = incident.provenance
    return LinkSide(
        incident_id=incident.incident_id,
        record_id=provenance.record_id,
        source_id=provenance.source_id,
        source_url=provenance.source_url,
        published_at=provenance.declared_event_time,
        evidence_ids=list(cited),
        quotes=[index[eid].quote for eid in cited],
    )


def _cited(raw: Sequence[str], shown: set[str], label: str, findings: list[str]) -> list[str]:
    kept: list[str] = []
    for item in raw:
        eid = str(item).strip().strip("[]")
        if not eid or eid in kept:
            continue
        if eid in shown:
            kept.append(eid)
        else:
            findings.append(f"report {label} cites evidence {eid!r}, which was not shown to the model")
    return kept


def decide(
    pair: Pair,
    draft: LinkDraft,
    *,
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
) -> EventLink:
    findings: list[str] = []
    try:
        verdict = Verdict(str(draft.verdict or "").strip())
    except ValueError:
        verdict = Verdict.UNDETERMINED
        findings.append(
            f"the model answered verdict {draft.verdict!r}, which is not one of "
            + ", ".join(item.value for item in Verdict)
        )
    confidence = draft.confidence
    if confidence is None or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        findings.append(f"the model gave confidence {confidence!r}; {DEFAULT_CONFIDENCE} was used")
        confidence = DEFAULT_CONFIDENCE
    cited_a = _cited(draft.evidence_a, {e.evidence_id for e in shown_evidence(pair.a)}, "A", findings)
    cited_b = _cited(draft.evidence_b, {e.evidence_id for e in shown_evidence(pair.b)}, "B", findings)
    stated = [text.strip() for text in draft.conflicts if str(text).strip()]
    every_conflict = list(dict.fromkeys([*pair.conflicts, *stated]))
    if verdict is Verdict.UNDETERMINED:
        findings.append("the model could not tell whether the reports describe one event")
    if verdict is Verdict.SAME_EVENT:
        if not cited_a or not cited_b:
            findings.append("same_event needs a quote cited from each report")
        if pair.conflicts:
            findings.append("same_event while the pipeline found: " + "; ".join(pair.conflicts))
        if stated:
            findings.append("same_event while the model itself listed: " + "; ".join(stated))
    if confidence < LINK_THRESHOLD:
        findings.append(f"confidence {confidence:.2f} is below {LINK_THRESHOLD}")
    explanation = (draft.explanation or "").strip() or None
    return EventLink(
        link_id=pair.link_id,
        a=_side(pair.a, cited_a),
        b=_side(pair.b, cited_b),
        verdict=verdict,
        confidence=confidence,
        review=ReviewState.REVIEW_REQUIRED if findings else ReviewState.ACCEPTED,
        shared=list(pair.shared),
        conflicts=every_conflict,
        findings=findings,
        explanation=explanation,
        provider=provider or "unknown",
        model=model or "unknown",
        prompt_version=LINK_PROMPT_VERSION,
        prompt_digest=LINK_PROMPT_DIGEST,
        shown_hash=shown,
        decided_at=decided_at,
    )


def event_id_for(incident_ids: Sequence[str]) -> str:
    return "EVT-" + hashlib.sha256("|".join(sorted(incident_ids)).encode("utf-8")).hexdigest()[:16]


def event_groups(pool: Sequence[Incident], links: Sequence[EventLink]) -> list[EventGroup]:
    by_id = {incident.incident_id: incident for incident in pool}
    parent = {key: key for key in by_id}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    inside = [link for link in links if link.a.incident_id in by_id and link.b.incident_id in by_id]
    for link in inside:
        if link.merges:
            low, high = sorted((root(link.a.incident_id), root(link.b.incident_id)))
            parent[high] = low
    members: dict[str, list[str]] = {}
    for key in sorted(by_id):
        members.setdefault(root(key), []).append(key)

    groups = []
    for ids in members.values():
        group = set(ids)
        own = [link for link in inside if link.a.incident_id in group and link.b.incident_id in group]
        findings = [
            f"{link.link_id} judged {link.a.incident_id} and {link.b.incident_id} separate events, "
            "yet other accepted links join them"
            for link in own
            if link.review is ReviewState.ACCEPTED and link.verdict is Verdict.SEPARATE_EVENT
        ]
        provenance = [by_id[key].provenance for key in ids]
        groups.append(
            EventGroup(
                event_id=event_id_for(ids),
                incident_ids=ids,
                record_ids=sorted({item.record_id for item in provenance}),
                source_urls=sorted({item.source_url for item in provenance if item.source_url}),
                link_ids=sorted(link.link_id for link in own if link.merges),
                review=ReviewState.REVIEW_REQUIRED if findings else ReviewState.ACCEPTED,
                findings=findings,
            )
        )
    return groups


class LinkStore:
    def __init__(self, directory: Any) -> None:
        self.directory = Path(str(directory))
        self.links_path = self.directory / LINKS_FILENAME
        self.events_path = self.directory / EVENTS_FILENAME
        self.links: dict[str, EventLink] = {}

    def load(self) -> "LinkStore":
        if not self.links_path.exists():
            return self
        try:
            text = self.links_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{self.links_path} could not be read: {error}") from error
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                link = EventLink.model_validate_json(line)
            except ValidationError as error:
                problem = error.errors()[0].get("msg", "invalid")
                raise CheckpointError(
                    f"{self.links_path} line {number} is not a link this pipeline wrote ({problem}); "
                    "the saved links were not changed"
                ) from error
            self.links[link.link_id] = link
        return self

    def reusable(self, link_id: str, shown: str) -> Optional[EventLink]:
        found = self.links.get(link_id)
        if (
            found is None
            or found.schema_version != SCHEMA_VERSION
            or found.shown_hash != shown
            or found.prompt_digest != LINK_PROMPT_DIGEST
        ):
            return None
        return found

    def save(self, links: Sequence[EventLink], groups: Sequence[EventGroup]) -> None:
        for link in links:
            self.links[link.link_id] = link
        lines = [
            json.dumps(self.links[key].model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            for key in sorted(self.links)
        ]
        _write_atomically(self.links_path, "".join(lines))
        events = {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": LINK_PROMPT_VERSION,
            "prompt_digest": LINK_PROMPT_DIGEST,
            "events": [group.model_dump(mode="json") for group in groups],
        }
        _write_atomically(
            self.events_path, json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )


@dataclass
class LinkReport:
    eligible: int = 0
    candidates: int = 0
    links: list[EventLink] = field(default_factory=list)
    groups: list[EventGroup] = field(default_factory=list)
    asked: int = 0
    reused: int = 0
    failed: list[str] = field(default_factory=list)
    not_compared: int = 0
    stop_reason: Optional[str] = None

    @property
    def distinct_events(self) -> int:
        return len(self.groups)

    @property
    def review_links(self) -> list[EventLink]:
        return [link for link in self.links if link.review is ReviewState.REVIEW_REQUIRED]

    @property
    def undecided(self) -> int:
        return len(self.review_links) + len(self.failed) + self.not_compared

    def counts(self) -> dict[str, Any]:
        accepted = [link for link in self.links if link.review is ReviewState.ACCEPTED]
        events = str(self.distinct_events)
        if self.undecided:
            events = f"at most {events} ({self.undecided} candidate pair(s) undecided)"
        return {
            "Eligible incidents": self.eligible,
            "Candidate pairs": self.candidates,
            "Compared now": self.asked,
            "Reused decisions": self.reused,
            "Failed comparisons": len(self.failed),
            "Not compared": self.not_compared,
            "Same event (accepted)": len([link for link in accepted if link.verdict is Verdict.SAME_EVENT]),
            "Separate events (accepted)": len(
                [link for link in accepted if link.verdict is Verdict.SEPARATE_EVENT]
            ),
            "Links for review": len(self.review_links),
            "Distinct events": events,
        }


def link_incidents(
    incidents: Iterable[Incident],
    *,
    chain_factory: Callable[[], Chain],
    store: Optional[LinkStore] = None,
    now: Optional[datetime] = None,
) -> LinkReport:
    pool = eligible(incidents)
    pairs = candidate_pairs(pool)
    report = LinkReport(eligible=len(pool), candidates=len(pairs))
    decided_at = now or datetime.now(timezone.utc)
    chain: Optional[Chain] = None
    for pair in pairs:
        request = comparison_request(pair)
        shown = shown_hash(request)
        found = store.reusable(pair.link_id, shown) if store is not None else None
        if found is not None:
            report.links.append(found)
            report.reused += 1
            continue
        if report.stop_reason is not None:
            report.not_compared += 1
            continue
        if chain is None:
            try:
                chain = chain_factory()
            except LLMError as error:
                report.stop_reason = str(error)
                report.not_compared += 1
                continue
        try:
            answered = chain.read(lambda provider: provider.generate_structured(request))
        except ChainExhaustedError as error:
            report.stop_reason = str(error)
            report.not_compared += 1
            continue
        except LLMError as error:
            report.failed.append(f"{pair.link_id}: {error}")
            continue
        report.asked += 1
        try:
            draft = LinkDraft.model_validate(answered.result.payload)
        except ValidationError as error:
            report.failed.append(f"{pair.link_id}: the answer is not a comparison ({error.error_count()} problem(s))")
            continue
        provider, model = answered.identity
        report.links.append(
            decide(pair, draft, provider=provider, model=model, shown=shown, decided_at=decided_at)
        )
    report.groups = event_groups(pool, report.links)
    if store is not None:
        store.save(report.links, report.groups)
    return report


def print_links(report: LinkReport, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream or sys.stdout
    print(
        f"Event linking ({LINK_PROMPT_VERSION}, {LINK_PROMPT_DIGEST}, incident contract "
        f"{SCHEMA_VERSION})",
        file=out,
    )
    for label, value in report.counts().items():
        print(f"  {label}: {value}", file=out)
    if report.stop_reason:
        print(f"  Stopped: {report.stop_reason}", file=out)
    for line in report.failed:
        print(f"  failed {line}", file=out)
    for link in report.links:
        line = (
            f"  {link.link_id} {link.a.incident_id} <-> {link.b.incident_id}: {link.verdict.value} "
            f"{link.confidence:.2f} {link.review.value}"
        )
        if link.findings:
            line += " | " + "; ".join(link.findings)
        print(line, file=out)
    for group in report.groups:
        if len(group.incident_ids) < 2 and not detailed:
            continue
        line = f"  {group.event_id}: {', '.join(group.incident_ids)} ({group.review.value})"
        if group.findings:
            line += " | " + "; ".join(group.findings)
        print(line, file=out)
        if detailed:
            for url in group.source_urls:
                print(f"    {url}", file=out)


__all__ = [
    "DEFAULT_CONFIDENCE",
    "EVENTS_FILENAME",
    "EventGroup",
    "EventLink",
    "LINKS_FILENAME",
    "LINK_PROMPT_DIGEST",
    "LINK_PROMPT_VERSION",
    "LINK_RULES",
    "LINK_THRESHOLD",
    "LinkDraft",
    "LinkReport",
    "LinkSide",
    "LinkStore",
    "MAX_CANDIDATES_PER_INCIDENT",
    "Pair",
    "REPORTING_GAP",
    "Verdict",
    "candidate_pairs",
    "comparison_request",
    "conflicts",
    "decide",
    "eligible",
    "event_groups",
    "event_id_for",
    "link_id_for",
    "link_incidents",
    "print_links",
    "shared_signals",
    "shown_evidence",
]
