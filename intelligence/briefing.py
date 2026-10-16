from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import CheckpointError, _write_atomically
from intelligence.contract import SCHEMA_VERSION, Department, Incident, RecordKind, ReviewState
from intelligence.copilot import (
    MIN_QUOTED_EVENTS,
    AnswerCitation,
    Asker,
    QuestionScript,
    Row,
    citation_of,
    clean,
    confidence_of,
    digit_set,
    event_block,
    rows_of,
    saved_lines,
    script_of,
    unknown_digits,
    union_of,
    value_line,
)
from intelligence.fallback import Chain
from intelligence.linking import EventLink, eligible, shown_hash
from intelligence.llm import LLMRequest, json_schema_for
from intelligence.models.base import Confidence, StrictModel
from intelligence.models.enums import ExtractionMethod
from intelligence.recurrence import (
    MAX_SHOWN_EVIDENCE,
    OLDEST,
    OPEN_STATUSES,
    RECORDED_STATUSES,
    Filters,
    IssueArea,
    IssueFamily,
    OpenFollowUp,
    RecordedResponse,
    Unit,
    areas_of,
    event_units,
)
from intelligence.trends import NOT_ASKED, Trend, TrendStatus, claim_words_in

BRIEF_PROMPT_VERSION = "brief-2"
BRIEFINGS_FILENAME = "briefings.jsonl"
BRIEF_SUMMARY_FILENAME = "briefing.json"

BRIEF_THRESHOLD = 0.8
DEFAULT_BRIEF_DAYS = 7
MAX_BRIEF_DAYS = 365
MAX_BRIEF_EVENTS = 6
MAX_BRIEF_LINES = 6
MAX_BRIEF_ACTIONS = 5

PAST_WORDS = (
    "completed",
    "was done",
    "has been done",
    "action taken",
    "was ordered",
    "was instructed",
    "carried out",
    "already repaired",
    "already cleared",
    "resolved by",
)
WHOLE_CORPUS = "the whole corpus, since no record here carries a date to count a period from"
NOTHING_READ = "no accepted incident was read, so a briefing had nothing to measure"
NO_TARGET = "names nothing it follows from"
WINDOW_BASES = ("record", "given", "named", "none")


class BriefLanguage(str, Enum):
    ENGLISH = "english"
    TAMIL = "tamil"


class BriefSection(str, Enum):
    SITUATION = "situation"
    RECURRING = "recurring"
    RISING = "rising"
    OPEN = "open"
    RECORDED = "recorded"


class ActionKind(str, Enum):
    FOLLOW_UP = "follow_up"
    VERIFY = "verify"
    COORDINATE = "coordinate"
    MONITOR = "monitor"
    OTHER = "other"


class BriefItemKind(str, Enum):
    RECURRING_ISSUE = "recurring_issue"
    MEASURED_INCREASE = "measured_increase"


class ActionOrigin(str, Enum):
    SAVED_ISSUE_FAMILY = "saved_issue_family"
    BRIEFING = "briefing"


class BriefState(str, Enum):
    ISSUED = "issued"
    QUALIFIED = "qualified"
    WITHHELD = "withheld"


SECTION_TOKENS = ", ".join(item.value for item in BriefSection)
ACTION_TOKENS = ", ".join(item.value for item in ActionKind)
LANGUAGE_TOKENS = ", ".join(item.value for item in BriefLanguage)
DEPARTMENT_TOKENS = ", ".join(item.value for item in Department if item is not Department.UNRESOLVED)


def brief_id_for(
    since: Optional[date],
    until: Optional[date],
    days: Optional[int],
    language: BriefLanguage,
    event_ids: Sequence[str],
    item_ids: Sequence[str],
) -> str:
    seed = "|".join(
        (
            since.isoformat() if since else "no-start",
            until.isoformat() if until else "no-end",
            str(days) if days is not None else "whole",
            language.value,
            *sorted(event_ids),
            "--",
            *sorted(item_ids),
        )
    )
    return "BRF-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def past_claims_in(texts: Sequence[Optional[str]]) -> list[str]:
    found: list[str] = []
    for item in texts:
        lowered = clean(item).casefold()
        for word in PAST_WORDS:
            if word in lowered and word not in found:
                found.append(word)
    return found


def resolve_token(value: Any, enum: Any, label: str, findings: list[str]) -> Optional[Any]:
    raw = clean(value)
    if not raw:
        return None
    try:
        return enum(raw)
    except ValueError:
        findings.append(f"the {label} names {raw!r}, which is not one of the tokens it was given")
        return None


def script_mismatch(texts: Sequence[Optional[str]], wanted: BriefLanguage) -> list[str]:
    found: list[str] = []
    for item in texts:
        body = clean(item)
        if not body:
            continue
        seen = script_of(body)
        if wanted is BriefLanguage.TAMIL and seen is QuestionScript.LATIN:
            found.append(body)
        elif wanted is BriefLanguage.ENGLISH and seen is QuestionScript.TAMIL:
            found.append(body)
    return found


def stray_digits(texts: Sequence[Optional[str]], allowed: set[str]) -> list[str]:
    found: list[str] = []
    for item in texts:
        for run in unknown_digits(item, allowed):
            if run not in found:
                found.append(run)
    return found


IDENTIFIERS = re.compile(r"\b(?:BRF|EVT|INC|ISS|TRD|ev)-[0-9A-Za-z_-]+")


def measured_digits(lines: Sequence[str]) -> set[str]:
    # an item identifier is printed so it can be cited, and its hex digits are not a measured number
    return digit_set("\n".join(IDENTIFIERS.sub("", line) for line in lines))


def window_note(
    basis: str, days: Optional[int], until: Optional[date], anchor_basis: str
) -> str:
    if basis == "record":
        return (
            f"{days} day(s) counted back from {until}, the newest dated record here "
            f"(its date read from {anchor_basis})"
        )
    if basis == "given":
        return f"{days} day(s) counted back from {until}, where the run was told the period ends"
    if basis == "named":
        return "the period starts where the run named it"
    return "no record here carried a date, so no window could be counted"


class LineDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section: Optional[str] = None
    body: Optional[str] = None
    events: list[int] = Field(default_factory=list)
    saved: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class ActionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Optional[str] = None
    kind: Optional[str] = None
    section: Optional[str] = None
    target_event: Optional[int] = None
    target_id: Optional[str] = None
    department: Optional[str] = None
    events: list[int] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    why: Optional[str] = None
    confidence: Optional[float] = None


class BriefDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: Optional[str] = None
    language: Optional[str] = None
    lines: list[LineDraft] = Field(default_factory=list)
    actions: list[ActionDraft] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    saved: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


BRIEF_SCHEMA = json_schema_for(BriefDraft)

BRIEF_RULES = "\n".join(
    (
        "You write one district briefing for one period from the records a run already read and the work it "
        "already saved. You are shown the counts, the windows, the places, the quotations, the saved recurring "
        "issues and the measured increases. Answer with one JSON object and nothing else.",
        "1. Every count, total, date, window, place list and status printed was measured from the records. "
        "Never write a number that is not printed above, and never present one as something you worked out.",
        "2. headline is one sentence saying what a person should take from this period. It states nothing the "
        "printed lines do not already give.",
        "3. Each line belongs to one section: " + SECTION_TOKENS + ". situation is what the period held, "
        "recurring is a saved issue that keeps happening in it, rising is a measured increase, open is a "
        "report still waiting for a department, recorded is an action or resolution the records themselves "
        "state. Write a line only for a section the briefing lists as having content, and name the events "
        "or saved items the line rests on.",
        "4. Each action is something a person could do next and names what it follows from: an event by the "
        "number printed with it, or a saved recurring issue or measured increase by the identifier printed "
        "with it. kind is one of " + ACTION_TOKENS + ": follow_up when a report states no answer yet, verify "
        "when the records say little or disagree, coordinate when more than one department is involved, "
        "monitor when an increase is measured but not yet alert-eligible, other when none of these fit.",
        "5. Nothing in these records says a suggestion was carried out. Never write that an action was done, "
        "is being done, was ordered or was instructed - it is a person's decision, and no record here holds it.",
        "6. A measured increase is alert-eligible or potential exactly as printed. Never write that a "
        "potential one is a rise a person must act on, and never turn a counted increase into a cause.",
        "7. Cite only the numbered events and the evidence identifiers printed with them. Never invent an "
        "index, an identifier, a place, a date or a source. A line or an action you cannot cite is not written.",
        "8. department takes one token from this list, and only when the records a line rests on name that "
        "department: " + DEPARTMENT_TOKENS + ". Leave it out otherwise; never assign one from the kind of "
        "problem.",
        "9. Write headline, every line body and every action in LANGUAGE. Keep every place name and every "
        "quotation exactly as the records write it, in its own script.",
        "10. When the records shown cannot support a briefing, leave lines and actions empty and say in "
        "caveats what is missing. Do not stretch a nearby fact into a finding and do not fill a gap with what "
        "districts usually see at this time of year.",
        "11. Never write that something is confirmed, proved, statistically significant, or caused by "
        "something. These records show what was reported, not what was true.",
        "12. confidence is how sure you are of what you wrote, from 0.0 to 1.0, for the briefing as a whole "
        "and for each line and each action; answer below 0.5 when guessing.",
    )
)

BRIEF_PROMPT_DIGEST = hashlib.sha256(
    (BRIEF_RULES + json.dumps(BRIEF_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def rules_for(language: BriefLanguage) -> str:
    return BRIEF_RULES.replace("LANGUAGE", language.value)


class BriefItemRef(StrictModel):
    kind: BriefItemKind
    item_id: str
    label: Optional[str] = None
    events: int = 0
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    alert_eligible: Optional[bool] = None

    @model_validator(mode="after")
    def _an_item_counts_what_it_lists(self) -> "BriefItemRef":
        if self.events != len(self.event_ids):
            raise ValueError(f"item {self.item_id} counts {self.events} event(s) but lists another number")
        if not self.event_ids:
            raise ValueError(f"item {self.item_id} holds no event of this period, so it says nothing about it")
        if self.kind is BriefItemKind.MEASURED_INCREASE and self.alert_eligible is None:
            raise ValueError("a measured increase states whether the numbers made it alert-eligible")
        if self.kind is BriefItemKind.RECURRING_ISSUE and self.alert_eligible is not None:
            raise ValueError("a saved recurring issue states no alert eligibility")
        return self


class BriefLine(StrictModel):
    section: BriefSection
    body: str
    item_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    review: ReviewState
    method: ExtractionMethod = ExtractionMethod.LLM

    @model_validator(mode="after")
    def _a_line_rests_on_what_was_shown(self) -> "BriefLine":
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a briefing line is used or held for a person; it is never left unresolved")
        if not self.item_ids:
            raise ValueError(
                f"a {self.section.value} line names no event, saved issue or measured increase it rests on"
            )
        if self.review is ReviewState.ACCEPTED:
            if not self.evidence_ids:
                raise ValueError(
                    f"an accepted {self.section.value} line cites no quotation printed with the records"
                )
            if self.confidence < BRIEF_THRESHOLD:
                raise ValueError(f"an accepted line needs confidence of at least {BRIEF_THRESHOLD}")
        return self


class BriefAction(StrictModel):
    action: str
    kind: Optional[ActionKind] = None
    origin: ActionOrigin
    section: Optional[BriefSection] = None
    target_kind: Optional[str] = None
    target_id: Optional[str] = None
    department: Department = Department.UNRESOLVED
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    why: Optional[str] = None
    confidence: Confidence
    review: ReviewState
    recorded: Literal[False] = False
    method: ExtractionMethod = ExtractionMethod.LLM

    @model_validator(mode="after")
    def _an_action_follows_something_and_is_never_done(self) -> "BriefAction":
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a suggested action is used or held for a person; it is never left unresolved")
        if not self.target_id or not self.target_kind:
            raise ValueError(f"the action {self.action!r} {NO_TARGET}, so it is a general instruction")
        if self.origin is ActionOrigin.BRIEFING and not self.kind:
            raise ValueError("an action this briefing drafted states which kind of next step it is")
        if self.origin is ActionOrigin.SAVED_ISSUE_FAMILY and self.target_kind != (
            BriefItemKind.RECURRING_ISSUE.value
        ):
            raise ValueError("an action carried from a saved issue follows a saved issue, not another item")
        if self.review is ReviewState.ACCEPTED:
            if not self.evidence_ids:
                raise ValueError("an accepted action cites the words in the records it follows from")
            if self.confidence < BRIEF_THRESHOLD:
                raise ValueError(f"an accepted action needs confidence of at least {BRIEF_THRESHOLD}")
        return self


class Briefing(StrictModel):
    schema_version: str = SCHEMA_VERSION
    briefing_id: str
    since: Optional[date] = None
    until: Optional[date] = None
    days_back: Optional[int] = None
    anchor: Optional[datetime] = None
    anchor_basis: str = "none"
    window_basis: str = "none"
    language: BriefLanguage = BriefLanguage.ENGLISH
    state: BriefState
    review: ReviewState
    findings: list[str] = Field(default_factory=list)
    missing: Optional[str] = None
    filters: Filters = Field(default_factory=Filters)
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0
    event_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    events: int = 0
    incidents: int = 0
    records: int = 0
    sources: int = 0
    shown_events: int = 0
    truncated_events: int = 0
    undated: int = 0
    outside_period: int = 0
    skipped_review: int = 0
    excluded_by_filter: int = 0
    status_unstated: int = 0
    areas: list[IssueArea] = Field(default_factory=list)
    geographic_precision: str = "unknown"
    open_followups: list[OpenFollowUp] = Field(default_factory=list)
    recorded_responses: list[RecordedResponse] = Field(default_factory=list)
    items: list[BriefItemRef] = Field(default_factory=list)
    named_items: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    citations: list[AnswerCitation] = Field(default_factory=list)
    headline: Optional[str] = None
    lines: list[BriefLine] = Field(default_factory=list)
    actions: list[BriefAction] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Confidence
    provider: str
    model: str
    prompt_version: str = BRIEF_PROMPT_VERSION
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    @property
    def family_ids(self) -> list[str]:
        return [item.item_id for item in self.items if item.kind is BriefItemKind.RECURRING_ISSUE]

    @property
    def trend_ids(self) -> list[str]:
        return [item.item_id for item in self.items if item.kind is BriefItemKind.MEASURED_INCREASE]

    @property
    def item_ids(self) -> list[str]:
        return [item.item_id for item in self.items]

    @property
    def quoted_events(self) -> int:
        return len({row.event_id for row in self.citations})

    def period(self) -> str:
        if self.since is None and self.until is None:
            return WHOLE_CORPUS
        return f"{self.since or 'the earliest record'}..{self.until or 'the latest record'}"

    def generated(self) -> list[Optional[str]]:
        texts: list[Optional[str]] = [self.headline]
        texts += [item.body for item in self.lines]
        texts += [item.action for item in self.actions]
        texts += [item.why for item in self.actions]
        return texts

    @model_validator(mode="after")
    def _a_briefing_states_only_what_the_period_measured(self) -> "Briefing":
        if self.days_back is not None and not 1 <= self.days_back <= MAX_BRIEF_DAYS:
            raise ValueError(f"days_back must be between 1 and {MAX_BRIEF_DAYS}")
        if self.since and self.until and self.until < self.since:
            raise ValueError("the period ends before it starts")
        if self.window_basis not in WINDOW_BASES:
            raise ValueError(
                f"the window basis {self.window_basis!r} is not one of {', '.join(WINDOW_BASES)}"
            )
        if self.days_back is not None and self.window_basis not in ("record", "given"):
            raise ValueError("a counted-back window says what its days were counted back from")
        if self.window_basis in ("record", "given") and (self.since is None or self.until is None):
            raise ValueError("a counted-back window names both of its ends")
        if self.window_basis == "record" and (self.anchor is None or self.until != self.anchor.date()):
            raise ValueError("a period counted back from a record carries that record's own date")
        if self.window_basis == "named" and self.since is None:
            raise ValueError("a period whose start the run named says which start it named")
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
        if self.sources != len(self.source_ids):
            raise ValueError(f"sources {self.sources} is not the {len(self.source_ids)} id(s) it lists")
        if self.shown_events > self.events:
            raise ValueError("a briefing cannot print more events than its period counted")
        if self.truncated_events != self.events - self.shown_events:
            raise ValueError("truncated_events is not the events left after the printed ones")
        if self.briefing_id != brief_id_for(
            self.since,
            self.until,
            self.days_back,
            self.language,
            self.event_ids,
            self.item_ids,
        ):
            raise ValueError(f"briefing id {self.briefing_id!r} is not the one its period and items derive")
        if not self.event_ids and self.state is not BriefState.WITHHELD:
            raise ValueError("a period that counted no event withholds the briefing")
        if not self.anchor and (self.since or self.until) and self.event_ids:
            raise ValueError("a briefing that names a period dates it from a record")
        counted = set(self.event_ids)
        by_id = {row.evidence_id: row for row in self.citations}
        if len({(item.kind, item.item_id) for item in self.items}) != len(self.items):
            raise ValueError("a briefing prints one reference per saved item")
        for item in self.items:
            if not set(item.event_ids) <= counted:
                raise ValueError(
                    f"item {item.item_id} lists an event outside the period this briefing counts"
                )
            outside = [key for key in item.evidence_ids if key not in by_id]
            if outside:
                raise ValueError(
                    f"item {item.item_id} cites {', '.join(outside)}, an evidence id of no incident it counts"
                )
        for name in self.named_items:
            if name not in self.item_ids:
                raise ValueError(f"the briefing names {name}, which it never printed as a saved item")
        allowed = digit_set("\n".join(self.numbers))
        for text in self.generated() + self.caveats:
            stray = stray_digits([text], allowed)
            if stray:
                raise ValueError(
                    f"{text!r} writes {', '.join(stray)}, a number no measured line of this briefing printed"
                )
        for row in self.citations:
            if row.event_id not in counted:
                raise ValueError(
                    f"a quotation cites event {row.event_id}, which this briefing did not count in its period"
                )
        for item in self.lines:
            unknown = [key for key in item.item_ids if key not in counted and key not in self.item_ids]
            if unknown:
                raise ValueError(
                    f"a {item.section.value} line names {', '.join(unknown)}, which was not printed with it"
                )
            outside = [key for key in item.evidence_ids if key not in by_id]
            if outside:
                raise ValueError(
                    f"a {item.section.value} line cites {', '.join(outside)}, which this briefing carries no "
                    "quotation for"
                )
        for item in self.actions:
            outside = [key for key in item.event_ids if key not in counted]
            if outside:
                raise ValueError(
                    f"the action {item.action!r} rests on {', '.join(outside)}, events outside this period"
                )
            unknown = [key for key in item.evidence_ids if key not in by_id]
            if unknown:
                raise ValueError(
                    f"the action {item.action!r} cites {', '.join(unknown)}, which this briefing carries no "
                    "quotation for"
                )
            if item.target_id not in counted and item.target_id not in self.item_ids:
                raise ValueError(
                    f"the action {item.action!r} follows {item.target_id!r}, which this briefing never printed"
                )
            if item.origin is ActionOrigin.SAVED_ISSUE_FAMILY and item.target_id not in self.family_ids:
                raise ValueError(
                    f"the action carried from {item.target_id} follows a saved issue of this period"
                )
        for row in self.open_followups + self.recorded_responses:
            if row.incident_id not in self.incident_ids:
                raise ValueError("a status line belongs to an incident this briefing counted")
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a briefing is issued, qualified or withheld; it is never left unresolved")
        claims = claim_words_in(self.generated() + self.caveats)
        past = past_claims_in(self.generated() + self.caveats)
        if claims and self.state is BriefState.ISSUED:
            raise ValueError("a briefing that claims " + ", ".join(claims) + " is not issued")
        if past:
            raise ValueError(
                "this briefing says something was done (" + ", ".join(past) + "), and no record here holds it"
            )
        if self.state is BriefState.ISSUED:
            if not self.lines and not self.actions:
                raise ValueError("an issued briefing says or suggests nothing")
            if self.findings:
                raise ValueError("an issued briefing carries a finding, so it is not issued")
            if self.confidence < BRIEF_THRESHOLD:
                raise ValueError(f"an issued briefing needs confidence of at least {BRIEF_THRESHOLD}")
            if self.review is not ReviewState.ACCEPTED:
                raise ValueError("an issued briefing does not wait for a person")
            if any(item.review is ReviewState.REVIEW_REQUIRED for item in self.lines + self.actions):
                raise ValueError("an issued briefing carries a line or action held for a person")
            if self.events >= MIN_QUOTED_EVENTS and self.quoted_events < MIN_QUOTED_EVENTS:
                raise ValueError(
                    f"an issued briefing quotes fewer than {MIN_QUOTED_EVENTS} of the events it counted"
                )
            wrong = script_mismatch(self.generated(), self.language)
            if wrong:
                raise ValueError(
                    f"this was asked for in {self.language.value} and wrote {wrong[0]!r} in another script"
                )
        elif self.state is BriefState.WITHHELD:
            if self.lines or self.actions:
                raise ValueError("a withheld briefing states nothing and suggests nothing")
            if self.review is not ReviewState.REVIEW_REQUIRED:
                raise ValueError("a withheld briefing waits for a person")
            if not self.findings and not self.missing:
                raise ValueError("a withheld briefing says why nothing is stated")
        elif not (self.findings or self.lines or self.actions):
            raise ValueError("a qualified briefing says what it rests on and what keeps it from being issued")
        return self


@dataclass
class Measured:
    since: Optional[date] = None
    until: Optional[date] = None
    days_back: Optional[int] = None
    anchor: Optional[datetime] = None
    anchor_basis: str = "none"
    window_basis: str = "none"
    units: list[Unit] = field(default_factory=list)
    shown: list[Unit] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    available: dict[str, Row] = field(default_factory=dict)
    areas: list[IssueArea] = field(default_factory=list)
    precision: str = "unknown"
    followups: list[OpenFollowUp] = field(default_factory=list)
    recorded: list[RecordedResponse] = field(default_factory=list)
    status_unstated: int = 0
    status_unquoted: int = 0
    undated: int = 0
    outside_period: int = 0
    skipped_review: int = 0
    excluded_by_filter: int = 0
    truncated: int = 0
    families: list[IssueFamily] = field(default_factory=list)
    trends: list[Trend] = field(default_factory=list)
    empty_reason: Optional[str] = None

    @property
    def event_ids(self) -> list[str]:
        return sorted(unit.event_id for unit in self.units)

    @property
    def incident_ids(self) -> list[str]:
        return sorted({key for unit in self.units for key in unit.incident_ids})

    @property
    def record_ids(self) -> list[str]:
        return sorted({key for unit in self.units for key in unit.record_ids})

    @property
    def source_ids(self) -> list[str]:
        return sorted({item.provenance.source_id for unit in self.units for item in unit.incidents})

    @property
    def source_urls(self) -> list[str]:
        return sorted({url for unit in self.units for url in unit.source_urls})

    @property
    def alerts(self) -> list[Trend]:
        return [item for item in self.trends if item.status is TrendStatus.ALERT_ELIGIBLE]

    @property
    def potential(self) -> list[Trend]:
        return [item for item in self.trends if item.status is TrendStatus.POTENTIAL]

    def period(self) -> str:
        if self.since is None and self.until is None:
            return WHOLE_CORPUS
        return f"{self.since or 'the earliest record'}..{self.until or 'the latest record'}"

    def in_period(self, family_or_trend: Any) -> list[str]:
        counted = set(self.event_ids)
        return [item for item in family_or_trend.event_ids if item in counted]

    def sections(self) -> list[BriefSection]:
        found = [BriefSection.SITUATION] if self.units else []
        if self.families:
            found.append(BriefSection.RECURRING)
        if self.trends:
            found.append(BriefSection.RISING)
        if self.followups:
            found.append(BriefSection.OPEN)
        if self.recorded:
            found.append(BriefSection.RECORDED)
        return found


def available_rows(units: Sequence[Unit]) -> dict[str, Row]:
    found: dict[str, Row] = {}
    for unit in units:
        for incident in unit.incidents:
            for evidence in incident.evidence:
                if evidence.evidence_id not in found:
                    found[evidence.evidence_id] = Row(unit, incident, evidence)
    return found


def status_rows(units: Sequence[Unit]) -> tuple[list[OpenFollowUp], list[RecordedResponse], int, int]:
    open_items: list[OpenFollowUp] = []
    recorded: list[RecordedResponse] = []
    unstated = 0
    unquoted = 0
    for unit in units:
        for incident in unit.incidents:
            entry = unit.statuses.get(incident.incident_id)
            if entry is None:
                unstated += 1
                continue
            value, quote, ids = entry
            if not quote:
                unquoted += 1
                continue
            provenance = incident.provenance
            row = {
                "incident_id": incident.incident_id,
                "record_id": provenance.record_id,
                "status": value,
                "quote": quote,
                "source_url": provenance.source_url,
                "retrieved_at": provenance.retrieved_at,
                "evidence_ids": list(ids),
            }
            if value in OPEN_STATUSES:
                open_items.append(OpenFollowUp(**row))
            elif value in RECORDED_STATUSES:
                recorded.append(RecordedResponse(**row))
    return open_items, recorded, unstated, unquoted


def overlap(
    units: Sequence[Unit], filters: Filters, days_back: Optional[int]
) -> tuple[Optional[date], Optional[date], Optional[datetime], str]:
    dated = [unit.when for unit in units if unit.when is not None]
    anchor = max(dated, default=None)
    basis = "none"
    if anchor is not None:
        basis = next(unit.when_basis for unit in units if unit.when == anchor)
    until = filters.until or (anchor.date() if anchor is not None else None)
    since = filters.since
    if since is None and until is not None and days_back is not None:
        since = until - timedelta(days=days_back - 1)
    return since, until, anchor, basis


def measure(
    pool: Sequence[Incident],
    links: Sequence[EventLink],
    filters: Filters,
    days_back: Optional[int],
    *,
    read: Optional[int] = None,
    skipped_review: int = 0,
    excluded_by_filter: int = 0,
    max_events: int = MAX_BRIEF_EVENTS,
    evidence_limit: int = MAX_SHOWN_EVIDENCE,
) -> Measured:
    counted_read = len(pool) if read is None else read
    broad = filters.model_copy(update={"since": None, "until": None})
    kept = [item for item in pool if broad.keeps(item)]
    units = event_units(kept, links)
    since, until, anchor, basis = overlap(units, filters, days_back)
    whole = since is None and until is None
    in_period = [
        unit
        for unit in units
        if whole
        or (
            unit.when is not None
            and (since is None or unit.day >= since)
            and (until is None or unit.day <= until)
        )
    ]
    in_period.sort(
        key=lambda unit: (unit.event_id,) if whole else (unit.when or OLDEST, unit.event_id),
        reverse=not whole,
    )
    shown = in_period[:max_events]
    counted_back = days_back is not None and filters.since is None and until is not None
    found = Measured(
        since=since,
        until=until,
        days_back=days_back if counted_back else None,
        window_basis=(
            ("given" if filters.until is not None else "record")
            if counted_back
            else ("named" if filters.since is not None else "none")
        ),
        anchor=anchor,
        anchor_basis=basis,
        units=in_period,
        shown=shown,
        rows=rows_of(shown, evidence_limit),
        skipped_review=skipped_review,
        excluded_by_filter=excluded_by_filter,
        truncated=len(in_period) - len(shown),
        undated=len([unit for unit in units if unit.when is None]),
        outside_period=len(units) - len(in_period),
    )
    found.available = available_rows(in_period)
    found.areas, found.precision = areas_of(in_period)
    found.followups, found.recorded, found.status_unstated, found.status_unquoted = status_rows(in_period)
    if not counted_read:
        found.empty_reason = NOTHING_READ
    elif not pool:
        found.empty_reason = (
            f"none of the {counted_read} accepted incident(s) the run read matches {filters.describe()}, so "
            "this period had nothing to count"
        )
    elif not kept:
        found.empty_reason = (
            f"no accepted incident matches {broad.describe()}, so there was nothing to group into events"
        )
    elif not units:
        found.empty_reason = f"the incidents matching {broad.describe()} evidence no district event"
    elif not in_period:
        found.empty_reason = (
            f"the records read hold {found.undated} event(s) with no date at all and none dated in "
            f"{found.period()}, so this period counts nothing"
        )
    return found


def set_line(label: str, values: Iterable[Any]) -> str:
    return f"{label}: {value_line(values)}"


def brief_lines(
    measured: Measured,
    families: Sequence[IssueFamily],
    trends: Sequence[Trend],
    *,
    deduplicated: bool,
    language: BriefLanguage,
) -> tuple[list[str], list[str]]:
    counted_now = len(measured.units)
    summary = [
        "Period: "
        + measured.period()
        + " - "
        + window_note(
            measured.window_basis, measured.days_back, measured.until, measured.anchor_basis
        )
        + ".",
        (
            f"The period holds {counted_now} distinct event(s) from {len(measured.incident_ids)} accepted "
            f"incident(s) in {len(measured.record_ids)} record(s) filed by {len(measured.source_ids)} "
            "publisher(s)."
            if counted_now
            else "Nothing was measured for this period: " + str(measured.empty_reason)
        ),
    ]
    if counted_now:
        summary.append(
            "Places in the period: "
            + (
                ", ".join(
                    f"{item.name} ({item.level}, {len(item.event_ids)} event(s))" for item in measured.areas
                )
                or "none the records name"
            )
            + f", at {measured.precision} precision."
        )
        summary += [
            set_line("Categories here", union_of(measured.units, "categories")),
            set_line("Event types here", union_of(measured.units, "types")),
            set_line("Departments here", union_of(measured.units, "departments")),
            set_line("Severities here", union_of(measured.units, "severities")),
        ]
    summary += [
        "Sections with content here: "
        + (", ".join(item.value for item in measured.sections()) or "none - nothing was measured"),
        f"Open with a quote: {len(measured.followups)}, action or resolution recorded: "
        f"{len(measured.recorded)}, no status evidenced: {measured.status_unstated}, a status stated with "
        f"no words to quote: {measured.status_unquoted}.",
    ]
    if measured.undated:
        summary.append(
            f"{measured.undated} distinct event(s) in these records carry no date, so they fall in no period."
        )
    if measured.outside_period:
        summary.append(
            f"{measured.outside_period} dated event(s) fall outside this period and are not counted here."
        )
    for family in families:
        summary.append(
            f"Saved recurring issue {family.family_id} counts {len(measured.in_period(family))} of its "
            f"{family.events} event(s) in this period."
        )
    for trend in trends:
        summary.append(
            f"Measured increase {trend.trend_id} counts {len(measured.in_period(trend))} of its "
            f"{trend.events} event(s) in this period."
        )
    saved = saved_lines(families, trends)
    blocks: list[str] = []
    counted = list(summary)
    for index, unit in enumerate(measured.shown, start=1):
        lines = event_block(index, unit, measured.rows)
        blocks.append("\n".join(lines))
        counted += [line for line in lines if line.startswith("date: ")]
    extra = [
        (
            f"The period counted {counted_now} distinct event(s); {len(measured.shown)} are printed here. "
            "Write from what is printed and say what you were not shown."
            if measured.truncated
            else f"All {len(measured.shown)} event(s) of the period are printed here."
        )
        if counted_now
        else "No event is printed, because the period counted none.",
        "Every count, window, place list and status above was measured by the pipeline from the records, "
        "never asked of you."
        + (
            " Repeated reports of one happening were merged into one event before counting."
            if deduplicated
            else " No link decisions were available, so two reports of one happening may be printed as two "
            "events and a count may double what happened."
        ),
        "Write headline, every line body and every action in " + language.value + ".",
    ]
    return summary + saved + blocks + extra, counted + saved + extra


def brief_request(
    measured: Measured,
    families: Sequence[IssueFamily],
    trends: Sequence[Trend],
    *,
    deduplicated: bool,
    language: BriefLanguage,
) -> tuple[LLMRequest, list[str]]:
    lines, counted = brief_lines(
        measured, families, trends, deduplicated=deduplicated, language=language
    )
    request = LLMRequest(
        system=rules_for(language),
        user="\n\n".join(lines),
        json_schema=BRIEF_SCHEMA,
        meta={"prompt_version": BRIEF_PROMPT_VERSION, "period": measured.period()},
    )
    return request, counted


def resolve_events(
    raw: Sequence[int], by_index: dict[int, Unit], findings: list[str], label: str
) -> list[str]:
    chosen: list[str] = []
    for item in raw:
        unit = by_index.get(item)
        if unit is None:
            findings.append(f"the {label} names event {item}, which was not printed for it")
            continue
        if unit.event_id not in chosen:
            chosen.append(unit.event_id)
    return chosen


def resolve_saved(raw: Sequence[str], refs: Sequence[BriefItemRef], findings: list[str], label: str) -> list[str]:
    known = {item.item_id for item in refs}
    chosen: list[str] = []
    for item in raw:
        token_text = clean(item)
        if token_text not in known:
            findings.append(
                f"the {label} names {token_text!r}, which is neither a saved recurring issue nor a measured "
                "increase printed with it"
            )
            continue
        if token_text not in chosen:
            chosen.append(token_text)
    return chosen


def resolve_evidence(
    raw: Sequence[str], printed: dict[str, Row], findings: list[str], label: str
) -> list[str]:
    chosen: list[str] = []
    for item in raw:
        evidence_id = clean(item)
        if evidence_id not in printed:
            findings.append(
                f"the {label} cites {evidence_id!r}, which is not one of the quotations printed with the "
                "records it was shown"
            )
            continue
        if evidence_id not in chosen:
            chosen.append(evidence_id)
    return chosen


def item_refs(
    families: Sequence[IssueFamily],
    trends: Sequence[Trend],
    measured: Measured,
) -> list[BriefItemRef]:
    refs: list[BriefItemRef] = []
    for family in families:
        inside = measured.in_period(family)
        if not inside:
            continue
        wanted = set(inside)
        evidence = sorted(
            {
                key
                for reference in family.references
                if reference.event_id in wanted
                for key in reference.evidence_ids
                if key in measured.available
            }
        )
        refs.append(
            BriefItemRef(
                kind=BriefItemKind.RECURRING_ISSUE,
                item_id=family.family_id,
                label=clean(family.label) or None,
                events=len(inside),
                event_ids=inside,
                evidence_ids=evidence,
            )
        )
    for trend in trends:
        inside = measured.in_period(trend)
        if not inside:
            continue
        wanted = set(inside)
        evidence = sorted(
            {
                key
                for reference in trend.references
                if reference.event_id in wanted
                for key in reference.evidence_ids
                if key in measured.available
            }
        )
        refs.append(
            BriefItemRef(
                kind=BriefItemKind.MEASURED_INCREASE,
                item_id=trend.trend_id,
                label=f"{trend.dimension.value}={trend.value}",
                events=len(inside),
                event_ids=inside,
                evidence_ids=evidence,
                alert_eligible=trend.status is TrendStatus.ALERT_ELIGIBLE,
            )
        )
    return refs


def carried_actions(
    families: Sequence[IssueFamily],
    refs: Sequence[BriefItemRef],
    measured: Measured,
    findings: list[str],
    allowed: set[str],
) -> list[BriefAction]:
    by_id = {item.item_id: item for item in refs}
    found: list[BriefAction] = []
    for family in families:
        ref = by_id.get(family.family_id)
        if ref is None:
            continue
        inside = set(ref.event_ids)
        for item in family.suggested:
            evidence = [key for key in item.evidence_ids if key in measured.available]
            events = sorted(set(item.event_ids) & inside)
            if not evidence:
                findings.append(
                    f"the action the saved issue {family.family_id} offered cites no quotation this briefing "
                    "counts, so it is not carried"
                )
                continue
            if not events:
                findings.append(
                    f"the action the saved issue {family.family_id} offered rests on no event of this period, "
                    "so it is not carried"
                )
                continue
            if past_claims_in([item.action]) or stray_digits([item.action], allowed):
                findings.append(
                    f"the action the saved issue {family.family_id} offered says something was done or writes "
                    "an unmeasured number, so it is not carried"
                )
                continue
            found.append(
                BriefAction(
                    action=item.action,
                    origin=ActionOrigin.SAVED_ISSUE_FAMILY,
                    section=BriefSection.RECURRING,
                    target_kind=BriefItemKind.RECURRING_ISSUE.value,
                    target_id=family.family_id,
                    department=item.department,
                    event_ids=events,
                    evidence_ids=evidence,
                    confidence=item.confidence,
                    review=item.review,
                    method=item.method,
                )
            )
    return found


def build_briefing(
    measured: Measured,
    draft: Optional[BriefDraft],
    *,
    refs: Sequence[BriefItemRef],
    carried: Sequence[BriefAction],
    filters: Filters,
    language: BriefLanguage,
    counted: Sequence[str],
    extras: Sequence[str],
    deduplicated: bool,
    links: int,
    undecided_links: int,
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
) -> Briefing:
    findings = [clean(item) for item in extras if clean(item)]
    allowed = measured_digits(counted)
    by_index = {index: unit for index, unit in enumerate(measured.shown, start=1)}
    units_by_id = {unit.event_id: unit for unit in measured.units}
    printed = {row.evidence.evidence_id: row for row in measured.rows}
    headline: Optional[str] = None
    caveats: list[str] = []
    named_items: list[str] = []
    lines: list[BriefLine] = []
    actions: list[BriefAction] = list(carried)
    cited: list[str] = [key for item in refs for key in item.evidence_ids]
    cited += [key for item in carried for key in item.evidence_ids]
    confidence = 0.0
    asked = draft is not None
    if draft is None:
        findings.append(
            "no briefing was asked, so nothing here is claimed beyond what the records measured"
        )
    else:
        confidence = confidence_of(draft.confidence, findings, "briefing")
        said = resolve_token(draft.language, BriefLanguage, "briefing", findings)
        if said is not None and said is not language:
            findings.append(
                f"the briefing answered in {said.value} although {language.value} was asked for it"
            )
        raw_headline = clean(draft.headline) or None
        stray = stray_digits([raw_headline], allowed)
        said_done = past_claims_in([raw_headline])
        if stray:
            findings.append(
                f"the headline writes {', '.join(stray)}, a number no measured line printed, so it is left out"
            )
        elif said_done:
            findings.append(
                f"the headline {raw_headline!r} says something was done ({', '.join(said_done)}), so it is left "
                "out"
            )
        else:
            headline = raw_headline
        caveats = [clean(item) for item in draft.caveats if clean(item)]
        dropped: list[str] = []
        for item in caveats:
            stray = stray_digits([item], allowed)
            said_done = past_claims_in([item])
            if stray:
                findings.append(
                    f"a caveat writes {', '.join(stray)}, a number no measured line printed, so it is left out"
                )
                dropped.append(item)
            elif said_done:
                findings.append(
                    f"the caveat {item!r} says something was done ({', '.join(said_done)}), so it is left out"
                )
                dropped.append(item)
        caveats = [item for item in caveats if item not in dropped]
        named_items = resolve_saved(draft.saved, refs, findings, "briefing")
        usable = measured.sections()
        if len(draft.lines) > MAX_BRIEF_LINES:
            findings.append(
                f"the briefing offers more than {MAX_BRIEF_LINES} lines, so only the first "
                f"{MAX_BRIEF_LINES} are kept"
            )
        for raw in draft.lines[:MAX_BRIEF_LINES]:
            body = clean(raw.body)
            if not body:
                findings.append("a briefing line came back with no words in it")
                continue
            section = resolve_token(raw.section, BriefSection, "briefing line", findings)
            if section is None:
                continue
            if section not in usable:
                findings.append(
                    f"a {section.value} line was written although nothing was measured for that section, so "
                    "it is left out"
                )
                continue
            items = resolve_events(raw.events, by_index, findings, f"{section.value} line")
            items += [
                key for key in resolve_saved(raw.saved, refs, findings, f"{section.value} line")
                if key not in items
            ]
            if not items:
                findings.append(f"the {section.value} line {NO_TARGET}, so it is left out")
                continue
            evidence = resolve_evidence(raw.evidence, printed, findings, f"{section.value} line")
            if stray_digits([body], allowed):
                findings.append(
                    f"a {section.value} line writes {', '.join(stray_digits([body], allowed))}, a number no "
                    "measured line printed, so it is left out"
                )
                continue
            said_done = past_claims_in([body])
            if said_done:
                findings.append(
                    f"the {section.value} line {body!r} says something was done ({', '.join(said_done)}), so it "
                    "is left out"
                )
                continue
            words = confidence_of(raw.confidence, findings, f"{section.value} line")
            lines.append(
                BriefLine(
                    section=section,
                    body=body,
                    item_ids=items,
                    evidence_ids=evidence,
                    confidence=words,
                    review=(
                        ReviewState.ACCEPTED
                        if evidence and words >= BRIEF_THRESHOLD
                        else ReviewState.REVIEW_REQUIRED
                    ),
                )
            )
            cited += evidence
        if len(draft.actions) > MAX_BRIEF_ACTIONS:
            findings.append(
                f"the briefing offers more than {MAX_BRIEF_ACTIONS} actions, so only the first "
                f"{MAX_BRIEF_ACTIONS} are kept"
            )
        for raw in draft.actions[:MAX_BRIEF_ACTIONS]:
            text = clean(raw.action)
            if not text:
                findings.append("a suggested action came back with no words in it")
                continue
            kind = resolve_token(raw.kind, ActionKind, "suggested action", findings)
            if kind is None:
                findings.append(f"the action {text!r} names no kind of next step, so it is left out")
                continue
            section = resolve_token(raw.section, BriefSection, "suggested action", findings)
            events = resolve_events(raw.events, by_index, findings, f"{kind.value} action")
            evidence = resolve_evidence(raw.evidence, printed, findings, f"{kind.value} action")
            why = clean(raw.why) or None
            target_kind: Optional[str] = None
            target_id: Optional[str] = None
            if raw.target_event is not None:
                unit = by_index.get(raw.target_event)
                if unit is None:
                    findings.append(
                        f"a {kind.value} action follows event {raw.target_event}, which was not printed for it"
                    )
                    continue
                target_kind, target_id = "event", unit.event_id
                if unit.event_id not in events:
                    events.append(unit.event_id)
            elif clean(raw.target_id):
                chosen = resolve_saved([raw.target_id], refs, findings, f"{kind.value} action")
                if not chosen:
                    continue
                ref = next(item for item in refs if item.item_id == chosen[0])
                target_kind, target_id = ref.kind.value, ref.item_id
                events = sorted(set(events) | set(ref.event_ids))
                if not evidence:
                    evidence = [key for key in ref.evidence_ids if key in printed]
            else:
                findings.append(f"the action {text!r} {NO_TARGET}, so it is left out")
                continue
            if stray_digits([text, why], allowed):
                findings.append(
                    f"a {kind.value} action writes {', '.join(stray_digits([text, why], allowed))}, a number "
                    "no measured line printed, so it is left out"
                )
                continue
            past = past_claims_in([text, why])
            if past:
                findings.append(
                    f"the action {text!r} says something was done ({', '.join(past)}), so it is left out"
                )
                continue
            named = resolve_token(raw.department, Department, "suggested action", findings)
            if named is not None:
                owners = {
                    value
                    for key in events
                    if key in units_by_id
                    for value in units_by_id[key].departments
                }
                if named not in owners:
                    findings.append(
                        f"the action {text!r} names {named.value}, a department no record of the events it "
                        "rests on names, so none is assigned"
                    )
                    named = None
            wrong = script_mismatch([text, why], language)
            if wrong:
                findings.append(
                    f"the action {text!r} is written in another script than the {language.value} asked for"
                )
            words = confidence_of(raw.confidence, findings, f"{kind.value} action")
            actions.append(
                BriefAction(
                    action=text,
                    kind=kind,
                    origin=ActionOrigin.BRIEFING,
                    section=section,
                    target_kind=target_kind,
                    target_id=target_id,
                    department=named or Department.UNRESOLVED,
                    event_ids=sorted(events),
                    evidence_ids=evidence,
                    why=why,
                    confidence=words,
                    review=(
                        ReviewState.ACCEPTED
                        if evidence and not wrong and words >= BRIEF_THRESHOLD
                        else ReviewState.REVIEW_REQUIRED
                    ),
                )
            )
            cited += evidence
        wrong = script_mismatch([headline] + [item.body for item in lines], language)
        if wrong:
            findings.append(
                f"this was asked for in {language.value} and wrote {wrong[0]!r} in another script"
            )
        claims = claim_words_in([headline] + caveats + [item.body for item in lines])
        if claims:
            findings.append("the briefing claims " + ", ".join(claims))
    citations = [
        citation_of(measured.available[key])
        for key in sorted(set(cited) & set(measured.available))
    ]
    quoted = len({row.event_id for row in citations})
    counted_now = len(measured.units)
    if asked and (lines or actions) and counted_now >= MIN_QUOTED_EVENTS:
        if quoted < MIN_QUOTED_EVENTS:
            findings.append(
                f"the briefing quotes {quoted} of the {counted_now} event(s) it counted, below the "
                f"{MIN_QUOTED_EVENTS} one briefing leans on"
            )
    held = any(item.review is ReviewState.REVIEW_REQUIRED for item in lines + actions)
    if asked and confidence < BRIEF_THRESHOLD:
        held = True
    if not lines and not actions:
        state = BriefState.WITHHELD
        review = ReviewState.REVIEW_REQUIRED
        if measured.empty_reason is None:
            findings.append("nothing was said or suggested that carried its own citation")
    elif findings:
        state = BriefState.QUALIFIED
        review = ReviewState.REVIEW_REQUIRED if held else ReviewState.ACCEPTED
    elif held:
        state = BriefState.QUALIFIED
        review = ReviewState.REVIEW_REQUIRED
        findings.append(
            "the briefing rests on a line or an action a person has not confirmed, so it is not a finding of "
            "this pipeline"
        )
    else:
        state = BriefState.ISSUED
        review = ReviewState.ACCEPTED
    if state is BriefState.ISSUED and measured.truncated:
        findings.append(
            f"the period counted {len(measured.event_ids)} event(s) and printed {len(measured.shown)}, so "
            "this briefing names fewer events than it counted"
        )
        state = BriefState.QUALIFIED
        review = ReviewState.REVIEW_REQUIRED if held else ReviewState.ACCEPTED
    return Briefing(
        briefing_id=brief_id_for(
            measured.since,
            measured.until,
            measured.days_back,
            language,
            measured.event_ids,
            [item.item_id for item in refs],
        ),
        since=measured.since,
        until=measured.until,
        days_back=measured.days_back,
        anchor=measured.anchor,
        anchor_basis=measured.anchor_basis,
        window_basis=measured.window_basis,
        language=language,
        state=state,
        review=review,
        findings=findings,
        missing=(
            measured.empty_reason
            if state is BriefState.WITHHELD
            else None
        ),
        filters=filters,
        deduplicated=deduplicated,
        links=links,
        undecided_links=undecided_links,
        event_ids=measured.event_ids,
        incident_ids=measured.incident_ids,
        record_ids=measured.record_ids,
        source_ids=measured.source_ids,
        source_urls=measured.source_urls,
        events=len(measured.event_ids),
        incidents=len(measured.incident_ids),
        records=len(measured.record_ids),
        sources=len(measured.source_ids),
        shown_events=len(measured.shown),
        truncated_events=measured.truncated,
        undated=measured.undated,
        outside_period=measured.outside_period,
        skipped_review=measured.skipped_review,
        excluded_by_filter=measured.excluded_by_filter,
        status_unstated=measured.status_unstated,
        areas=measured.areas,
        geographic_precision=measured.precision,
        open_followups=measured.followups,
        recorded_responses=measured.recorded,
        items=list(refs),
        named_items=named_items,
        numbers=sorted(allowed, key=lambda item: int(item)),
        citations=citations,
        headline=headline,
        lines=lines,
        actions=actions,
        caveats=caveats,
        confidence=confidence,
        provider=provider or (NOT_ASKED if not asked else "unknown"),
        model=model or (NOT_ASKED if not asked else "unknown"),
        prompt_digest=BRIEF_PROMPT_DIGEST,
        shown_hash=shown,
        decided_at=decided_at,
    )


@dataclass
class BriefReport:
    period: str = ""
    days_back: Optional[int] = None
    language: BriefLanguage = BriefLanguage.ENGLISH
    incidents: int = 0
    records: int = 0
    sources: int = 0
    units: int = 0
    undated: int = 0
    outside_period: int = 0
    skipped_review: int = 0
    excluded_by_filter: int = 0
    families: int = 0
    trends: int = 0
    alerts: int = 0
    open_followups: int = 0
    recorded: int = 0
    status_unstated: int = 0
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0
    asked: int = 0
    reused: int = 0
    truncated: int = 0
    briefings: list[Briefing] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None

    @property
    def issued(self) -> list[Briefing]:
        return [item for item in self.briefings if item.state is BriefState.ISSUED]

    @property
    def qualified(self) -> list[Briefing]:
        return [item for item in self.briefings if item.state is BriefState.QUALIFIED]

    @property
    def withheld(self) -> list[Briefing]:
        return [item for item in self.briefings if item.state is BriefState.WITHHELD]

    @property
    def actions(self) -> list[BriefAction]:
        return [item for brief in self.briefings for item in brief.actions]

    def counts(self) -> dict[str, Any]:
        return {
            "Period": self.period,
            "Accepted incidents": self.incidents,
            "Records": self.records,
            "Publishers": self.sources,
            "Distinct events": self.units,
            "Events with no date": self.undated,
            "Events outside the period": self.outside_period,
            "Left out (waiting for a person)": self.skipped_review,
            "Left out (outside the filters)": self.excluded_by_filter,
            "Saved recurring issues in the period": self.families,
            "Measured increases in the period": self.trends,
            "Alert-eligible increases": self.alerts,
            "Open with a quote": self.open_followups,
            "Action or resolution recorded": self.recorded,
            "No status evidenced": self.status_unstated,
            "Briefing drafted": self.asked,
            "Briefing reused": self.reused,
            "Events beyond one request": self.truncated,
            "Suggested actions": len(self.actions),
            "Issued": len(self.issued),
            "Qualified": len(self.qualified),
            "Withheld": len(self.withheld),
        }

    def limitations(self) -> list[str]:
        lines: list[str] = []
        if not self.incidents:
            lines.append(NOTHING_READ)
        if not self.deduplicated:
            lines.append(
                "no link decisions were available, so each accepted incident was counted as its own event "
                "and repeated reports of one happening count separately"
            )
        if self.undecided_links:
            lines.append(
                f"{self.undecided_links} link decision(s) wait for a person, so two happenings that may be "
                "one were counted as two"
            )
        if self.skipped_review:
            lines.append(
                f"{self.skipped_review} incident(s) waiting for a person were left out of every count"
            )
        if self.excluded_by_filter:
            lines.append(f"{self.excluded_by_filter} incident(s) fell outside the filters used")
        if self.undated:
            lines.append(
                f"{self.undated} distinct event(s) carry no date the record gave, so they fall in no period "
                "and in no count"
            )
        if self.outside_period:
            lines.append(
                f"{self.outside_period} dated event(s) fall outside the period, so they are not absent from "
                "the district - only from this briefing"
            )
        if not self.families:
            lines.append(
                "no saved recurring issue overlapped the period, so what keeps happening was counted from "
                "the events rather than from a grouping"
            )
        if not self.trends:
            lines.append("no measured increase overlapped the period, so nothing rising could be named in it")
        if self.truncated:
            lines.append(
                f"{self.truncated} event(s) fall in the period but were beyond the {MAX_BRIEF_EVENTS} printed "
                "in one request, so a briefing names fewer events than it counted"
            )
        if self.asked:
            lines.append(
                "a suggested action is what a person could do next; nothing in these records says any of it "
                "was done, ordered or agreed"
            )
        if self.failed:
            lines.append(f"{len(self.failed)} briefing request(s) got no usable answer")
        if self.stop_reason:
            lines.append(f"the chain stopped drafting: {self.stop_reason}")
        lines.append(
            "this briefing covers only the records this run read, so an empty period says the records hold "
            "nothing for it, not that the district saw nothing"
        )
        return lines


def overlapped(
    families: Sequence[IssueFamily], trends: Sequence[Trend], counted: set[str]
) -> tuple[list[IssueFamily], list[Trend]]:
    picked = [family for family in families if set(family.event_ids) & counted]
    rising = [trend for trend in trends if set(trend.event_ids) & counted]
    return picked, rising


def district_briefing(
    incidents: Iterable[Incident],
    *,
    chain_factory: Callable[[], Chain],
    filters: Optional[Filters] = None,
    days_back: int = DEFAULT_BRIEF_DAYS,
    language: BriefLanguage = BriefLanguage.ENGLISH,
    links: Sequence[EventLink] = (),
    families: Sequence[IssueFamily] = (),
    measured: Sequence[Trend] = (),
    store: Optional[BriefStore] = None,
    now: Optional[datetime] = None,
) -> BriefReport:
    every = list(incidents)
    filed = [item for item in every if item.record_kind is RecordKind.INCIDENT]
    applied = filters or Filters()
    pool = eligible(filed)
    kept = [item for item in pool if applied.keeps(item)]
    report = BriefReport(
        language=language,
        days_back=days_back,
        incidents=len(kept),
        records=len({item.provenance.record_id for item in kept}),
        sources=len({item.provenance.source_id for item in kept}),
        skipped_review=len([item for item in filed if item.review_required]),
        excluded_by_filter=len(pool) - len(kept),
        deduplicated=bool(links),
        links=len(links),
        undecided_links=len([link for link in links if link.review is ReviewState.REVIEW_REQUIRED]),
    )
    counts = measure(
        kept,
        links,
        applied,
        days_back,
        read=len(pool),
        skipped_review=report.skipped_review,
        excluded_by_filter=report.excluded_by_filter,
    )
    report.period = counts.period()
    report.units = len(counts.units)
    report.undated = counts.undated
    report.outside_period = counts.outside_period
    report.open_followups = len(counts.followups)
    report.recorded = len(counts.recorded)
    report.status_unstated = counts.status_unstated
    report.truncated = counts.truncated
    picked, rising = overlapped(
        [family for family in families if family.review is ReviewState.ACCEPTED],
        [trend for trend in measured if trend.review is ReviewState.ACCEPTED],
        set(counts.event_ids),
    )
    counts.families = picked
    counts.trends = rising
    report.families = len(picked)
    report.trends = len(rising)
    report.alerts = len(counts.alerts)
    refs = item_refs(picked, rising, counts)
    decided_at = now or datetime.now(timezone.utc)
    request, counted = brief_request(
        counts, picked, rising, deduplicated=report.deduplicated, language=language
    )
    findings: list[str] = []
    carried = carried_actions(picked, refs, counts, findings, measured_digits(counted))
    shown = shown_hash(request)
    briefing_id = brief_id_for(
        counts.since,
        counts.until,
        counts.days_back,
        language,
        counts.event_ids,
        [item.item_id for item in refs],
    )
    saved = store.reusable(briefing_id, shown) if store is not None else None
    if saved is not None:
        report.briefings = [saved]
        report.reused = 1
        return report
    draft: Optional[BriefDraft] = None
    provider = model = ""
    asker = Asker(chain_factory)
    if counts.empty_reason is None:
        outcome = asker.draft(request, BriefDraft, "briefing")
        if outcome.draft is not None:
            draft = outcome.draft
            provider, model = outcome.provider, outcome.model
            report.asked = 1
        elif outcome.failed:
            report.failed.append(f"{briefing_id}: {outcome.note}")
        if asker.stop_reason:
            report.stop_reason = asker.stop_reason
    report.briefings = [
        build_briefing(
            counts,
            draft,
            refs=refs,
            carried=carried,
            filters=applied,
            language=language,
            counted=counted,
            extras=findings,
            deduplicated=report.deduplicated,
            links=report.links,
            undecided_links=report.undecided_links,
            provider=provider,
            model=model,
            shown=shown,
            decided_at=decided_at,
        )
    ]
    if store is not None:
        store.save(report)
    return report


class BriefStore:
    def __init__(self, directory: Any) -> None:
        self.directory = Path(str(directory))
        self.briefs_path = self.directory / BRIEFINGS_FILENAME
        self.summary_path = self.directory / BRIEF_SUMMARY_FILENAME
        self.by_id: dict[str, Briefing] = {}

    def load(self) -> "BriefStore":
        if not self.briefs_path.exists():
            return self
        try:
            text = self.briefs_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{self.briefs_path} could not be read: {error}") from error
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                brief = Briefing.model_validate_json(line)
            except ValidationError as error:
                problem = error.errors()[0].get("msg", "invalid")
                raise CheckpointError(
                    f"{self.briefs_path} line {number} is not a briefing this pipeline wrote "
                    f"({problem}); the saved briefings were not changed"
                ) from error
            self.by_id[brief.briefing_id] = brief
        return self

    def reusable(self, briefing_id: str, shown: str) -> Optional[Briefing]:
        found = self.by_id.get(briefing_id)
        if found is None:
            return None
        if (
            found.schema_version != SCHEMA_VERSION
            or found.prompt_digest != BRIEF_PROMPT_DIGEST
            or found.shown_hash != shown
        ):
            return None
        return found

    def save(self, report: BriefReport) -> None:
        for brief in report.briefings:
            self.by_id[brief.briefing_id] = brief
        lines = [
            json.dumps(
                self.by_id[key].model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
            + "\n"
            for key in sorted(self.by_id)
        ]
        _write_atomically(self.briefs_path, "".join(lines))
        summary = {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": BRIEF_PROMPT_VERSION,
            "prompt_digest": BRIEF_PROMPT_DIGEST,
            "period": report.period,
            "days_back": report.days_back,
            "language": report.language.value,
            "counts": report.counts(),
            "limitations": report.limitations(),
            "briefings": [
                {
                    "briefing": brief.model_dump(mode="json"),
                    "document": "\n".join(briefing_document(brief)),
                }
                for brief in report.briefings
            ],
        }
        _write_atomically(
            self.summary_path,
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def briefing_document(brief: Briefing) -> list[str]:
    lines = [
        f"District briefing {brief.briefing_id}",
        "Period: "
        + brief.period()
        + " ("
        + window_note(brief.window_basis, brief.days_back, brief.until, brief.anchor_basis)
        + ")",
        f"Written in {brief.language.value}; place names and quotations stay as the records write them.",
        f"{brief.events} distinct event(s) from {brief.incidents} accepted incident(s) in {brief.records} "
        f"record(s) filed by {brief.sources} publisher(s), at {brief.geographic_precision} precision.",
    ]
    if brief.areas:
        lines.append(
            "Places: "
            + ", ".join(
                f"{item.name} ({item.level}, {len(item.event_ids)} event(s))" for item in brief.areas
            )
        )
    if brief.headline:
        lines.append(f"Headline: {brief.headline}")
    for item in brief.lines:
        lines.append(f"[{item.section.value}] {item.body}")
    for item in brief.items:
        state = (
            ""
            if item.alert_eligible is None
            else " (alert-eligible)"
            if item.alert_eligible
            else " (not yet alert-eligible)"
        )
        lines.append(
            f"{item.kind.value.replace('_', ' ')} {item.item_id}: {item.events} event(s) of this period"
            + (f" - {item.label}" if item.label else "")
            + state
        )
    if brief.open_followups:
        lines.append(f"{len(brief.open_followups)} report(s) still state no answer:")
        lines += [f"  {row.incident_id} says {row.status}: {row.quote}" for row in brief.open_followups]
    if brief.recorded_responses:
        lines.append(f"{len(brief.recorded_responses)} response(s) the records themselves state:")
        lines += [f"  {row.incident_id}: {row.quote}" for row in brief.recorded_responses]
    lines.append("Suggested actions - each is a person's decision, never recorded as done here:")
    if not brief.actions:
        lines.append("  none carried a citation, so none is offered")
    for item in brief.actions:
        who = "" if item.department is Department.UNRESOLVED else f" ({item.department.value})"
        kind = item.kind.value if item.kind else "as the saved issue wrote it"
        lines.append(
            f"  [{kind}] {item.action}{who} - follows {item.target_kind} {item.target_id}, "
            f"{item.review.value}"
        )
    for line in brief.caveats:
        lines.append(f"caveat: {line}")
    for line in brief.findings:
        lines.append(f"finding: {line}")
    lines.append(
        f"{brief.state.value} (confidence {brief.confidence:.2f}, {brief.review.value}, "
        f"{brief.provider}/{brief.model})"
    )
    return lines


def print_briefings(report: BriefReport, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream or sys.stdout
    print(
        f"District briefing ({BRIEF_PROMPT_VERSION}, {BRIEF_PROMPT_DIGEST}, incident contract "
        f"{SCHEMA_VERSION})",
        file=out,
    )
    for label, value in report.counts().items():
        print(f"  {label}: {value}", file=out)
    if report.stop_reason:
        print(f"  Stopped: {report.stop_reason}", file=out)
    for line in report.failed:
        print(f"  failed {line}", file=out)
    for brief in report.briefings:
        print(
            f"  {brief.briefing_id} {brief.period()}: {brief.events} event(s) from {brief.incidents} "
            f"incident(s) in {brief.records} record(s), {len(brief.lines)} line(s), "
            f"{len(brief.actions)} suggested action(s), {len(brief.citations)} quotation(s) over "
            f"{brief.quoted_events} event(s), {brief.state.value} (confidence "
            f"{brief.confidence:.2f}, {brief.review.value})",
            file=out,
        )
        if brief.headline:
            print(f"     headline: {brief.headline}", file=out)
        for item in brief.lines:
            print(f"     [{item.section.value}] {item.body}", file=out)
        for item in brief.actions:
            kind = item.kind.value if item.kind else "as the saved issue wrote it"
            print(
                f"     suggested ({kind}, {item.origin.value}): {item.action} - follows {item.target_kind} "
                f"{item.target_id} (a person's decision, never recorded as done)",
                file=out,
            )
        if not detailed:
            continue
        for line in briefing_document(brief):
            print(f"     {line}", file=out)
    for line in report.limitations():
        print(f"  limitation: {line}", file=out)


__all__ = [
    "ACTION_TOKENS",
    "BRIEFINGS_FILENAME",
    "BRIEF_PROMPT_DIGEST",
    "BRIEF_PROMPT_VERSION",
    "BRIEF_RULES",
    "BRIEF_SCHEMA",
    "BRIEF_SUMMARY_FILENAME",
    "BRIEF_THRESHOLD",
    "DEPARTMENT_TOKENS",
    "DEFAULT_BRIEF_DAYS",
    "IDENTIFIERS",
    "LANGUAGE_TOKENS",
    "MAX_BRIEF_ACTIONS",
    "MAX_BRIEF_DAYS",
    "MAX_BRIEF_EVENTS",
    "MAX_BRIEF_LINES",
    "NO_TARGET",
    "NOTHING_READ",
    "PAST_WORDS",
    "SECTION_TOKENS",
    "WHOLE_CORPUS",
    "WINDOW_BASES",
    "ActionDraft",
    "ActionKind",
    "ActionOrigin",
    "BriefAction",
    "BriefDraft",
    "BriefItemKind",
    "BriefItemRef",
    "BriefLanguage",
    "BriefLine",
    "BriefReport",
    "BriefSection",
    "BriefState",
    "BriefStore",
    "Briefing",
    "LineDraft",
    "Measured",
    "available_rows",
    "brief_id_for",
    "brief_lines",
    "brief_request",
    "briefing_document",
    "build_briefing",
    "carried_actions",
    "district_briefing",
    "item_refs",
    "measure",
    "measured_digits",
    "overlap",
    "overlapped",
    "past_claims_in",
    "print_briefings",
    "resolve_events",
    "resolve_evidence",
    "resolve_saved",
    "resolve_token",
    "rules_for",
    "script_mismatch",
    "status_rows",
    "stray_digits",
    "window_note",
]
