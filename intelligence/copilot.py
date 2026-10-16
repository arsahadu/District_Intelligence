from __future__ import annotations

import hashlib
import json
import math
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import CheckpointError, _write_atomically
from intelligence.contract import (
    SCHEMA_VERSION,
    Department,
    EventStatus,
    EventType,
    Evidence,
    Incident,
    IncidentCategory,
    PriorityLevel,
    RecordKind,
    ReviewState,
)
from intelligence.fallback import Chain, ChainExhaustedError
from intelligence.linking import (
    DEFAULT_CONFIDENCE,
    EventLink,
    eligible,
    shown_evidence,
    shown_hash,
)
from intelligence.llm import LLMError, LLMRequest, json_schema_for
from intelligence.models.base import Confidence, StrictModel
from intelligence.models.enums import ExtractionMethod, Modality, SeverityLevel
from intelligence.recurrence import (
    MAX_SHOWN_EVIDENCE,
    OLDEST,
    OPEN_STATUSES,
    RECORDED_STATUSES,
    Filters,
    IssueArea,
    IssueFamily,
    OpenFollowUp,
    PeriodCount,
    RecordedResponse,
    Unit,
    areas_of,
    event_units,
    searchable_places,
)
from intelligence.trends import NOT_ASKED, Trend, claim_words_in

COPILOT_PROMPT_VERSION = "copilot-6"
ANSWER_PROMPT_VERSION = "answer-1"
ANSWERS_FILENAME = "answers.jsonl"
COPILOT_SUMMARY_FILENAME = "copilot.json"

ASK_THRESHOLD = 0.8
MAX_QUESTIONS_PER_RUN = 6
MAX_EVENTS_SHOWN = 8
MAX_STATEMENTS = 6
MAX_PLACES_PRINTED = 60
MIN_QUOTED_EVENTS = 2
MAX_DAYS_BACK = 365

NO_CORPUS = "no accepted incident was read, so the question was never searched"
NOT_A_RECORD_QUESTION = (
    "the question asks nothing these records were filed to answer, so no answer was sought from them"
)

TAMIL_LOW = 0x0B80
TAMIL_HIGH = 0x0BFF


class QuestionIntent(str, Enum):
    REPEATED_AREAS = "repeated_areas"
    OPEN_FOLLOWUPS = "open_followups"
    PERIOD_CHANGE = "period_change"
    SUPPORTING_EVIDENCE = "supporting_evidence"
    DEPARTMENT_LOAD = "department_load"
    COUNTING = "counting"
    GENERAL = "general"
    OTHER = "other"


INTENT_TOKENS = ", ".join(item.value for item in QuestionIntent)
CATEGORY_TOKENS = ", ".join(item.value for item in IncidentCategory)
TYPE_TOKENS = ", ".join(item.value for item in EventType)
DEPARTMENT_TOKENS = ", ".join(item.value for item in Department)
SEVERITY_TOKENS = ", ".join(item.value for item in SeverityLevel)
PRIORITY_TOKENS = ", ".join(item.value for item in PriorityLevel)
STATUS_TOKENS = ", ".join(item.value for item in EventStatus)


class QuestionScript(str, Enum):
    TAMIL = "tamil"
    LATIN = "latin"
    MIXED = "mixed"
    OTHER = "other"


class StatementKind(str, Enum):
    SOURCE_FACT = "source_fact"
    CALCULATED = "calculated"
    POSSIBLE_EXPLANATION = "possible_explanation"
    RECOMMENDATION = "recommendation"


KIND_TOKENS = ", ".join(item.value for item in StatementKind)


class AnswerState(str, Enum):
    GROUNDED = "grounded"
    QUALIFIED = "qualified"
    NOT_ANSWERABLE = "not_answerable"


def clean(value: Any) -> str:
    return str(value or "").strip()


def norm(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


def script_of(text: str) -> QuestionScript:
    tamil = any(TAMIL_LOW <= ord(char) <= TAMIL_HIGH for char in text)
    latin = any("a" <= char.lower() <= "z" for char in text)
    if tamil and latin:
        return QuestionScript.MIXED
    if tamil:
        return QuestionScript.TAMIL
    if latin:
        return QuestionScript.LATIN
    return QuestionScript.OTHER


def digit_runs(text: Any) -> list[str]:
    runs: list[str] = []
    current: list[str] = []
    for char in str(text or ""):
        value = unicodedata.decimal(char, None)
        if value is None:
            if current:
                runs.append(str(int("".join(current))))
                current = []
            continue
        current.append(str(value))
    if current:
        runs.append(str(int("".join(current))))
    return runs


def digit_set(text: str) -> set[str]:
    return set(digit_runs(text))


def unknown_digits(text: Any, allowed: set[str]) -> list[str]:
    seen: list[str] = []
    for run in digit_runs(text):
        if run not in allowed and run not in seen:
            seen.append(run)
    return seen


def token(value: Any, enum: Any, label: str, findings: list[str]) -> Optional[Any]:
    raw = clean(value)
    if not raw:
        return None
    try:
        return enum(raw)
    except ValueError:
        findings.append(f"the search named {label} {raw!r}, which the contract does not have")
        return None


def answer_id_for(question: str) -> str:
    return "ANS-" + hashlib.sha256(norm(question).encode("utf-8")).hexdigest()[:16]


def unit_names(unit: Unit) -> set[str]:
    found: set[str] = set()
    for incident in unit.incidents:
        found |= {norm(name) for name in searchable_places(incident) if name}
    return found


def place_vocabulary(pool: Sequence[Incident]) -> list[str]:
    names: set[str] = set()
    for incident in pool:
        names |= {norm(name) for name in searchable_places(incident) if name}
    return sorted(names)


def place_matches(query: str, vocabulary: Iterable[str]) -> bool:
    wanted = norm(query)
    return bool(wanted) and any(wanted in name or name == wanted for name in vocabulary)


ASK_RULES = "\n".join(
    (
        "You read one question about district records and say how to look for its answer. You are not shown "
        "the records themselves here. Answer with one JSON object and nothing else.",
        "1. The question may be Tamil, English or a mix of both. Read it as it is written; never answer it, "
        "only set the search.",
        "2. intent is one of: " + INTENT_TOKENS + ". Choose repeated_areas when the question asks where the "
        "same problem keeps happening, open_followups when it asks what still needs action, period_change "
        "when it compares one period with another, supporting_evidence when it asks which reports back a "
        "claim, department_load when it names a department, counting when it asks how many or which, general "
        "when the question asks nothing about what these records would carry, whatever language it is "
        "written in - a greeting, a courtesy, what you yourself are or can do, or a fact about the wider "
        "world such as how many countries there are or who holds a national office - and other when none of "
        "these fit. A question about how things stand in this district, its situation, what is going on here "
        "or what needs attention is about these records and never general: choose other, leave places out and "
        "let the whole corpus be searched.",
        "3. places: copy the place wording the question uses. Only add a place the question names, and never "
        "add a district, taluk or ward it leaves out.",
        "4. category, incident_type, department, min_severity and min_priority take one token from the lists "
        "printed below. "
        "Leave one out when the question does not ask for it, and never infer a department from a kind of "
        "problem.",
        "5. days_back is how many days of records the question needs, counted back from the newest dated "
        "record the pipeline holds. Use 7 for a week, 30 for a month, 90 for a quarter. Never write a date "
        "yourself, and leave days_back out when the question names no period.",
        "6. compare is true only when the question asks what changed between one period and the one before "
        "it. Set days_back too, or the comparison has no length.",
        "7. understood is one short sentence in the question's own language saying what you searched for. It "
        "is shown back to the person who asked, so it must not promise more than the fields above do.",
        "8. Leave a field out rather than filling it with something the question did not say. A field you "
        "guess narrows the search wrongly.",
        "9. confidence is how sure you are of the search you set, from 0.0 to 1.0; answer below 0.5 when "
        "guessing.",
        "10. An earlier question from the same conversation is printed above only when there is one. This "
        "question may then be nothing but a request about its answer - more detail, the same answer in "
        "another language, a longer or shorter period, one part of it, or what to do about it. When it is, "
        "set follow_up true and read the two questions as one: set every field above from the earlier "
        "question, changing only what this one changes. When it asks about something else, or greets, or is "
        "a question of general knowledge, leave follow_up out.",
    )
)


class InterpretationDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Optional[str] = None
    places: list[str] = Field(default_factory=list)
    category: Optional[str] = None
    incident_type: Optional[str] = None
    department: Optional[str] = None
    min_severity: Optional[str] = None
    min_priority: Optional[str] = None
    days_back: Optional[int] = None
    compare: Optional[bool] = None
    understood: Optional[str] = None
    follow_up: Optional[bool] = None
    confidence: Optional[float] = None


class StatementDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: Optional[str] = None
    kind: Optional[str] = None
    events: list[int] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: Optional[str] = None
    statements: list[StatementDraft] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    events: list[int] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    saved: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None


ASK_SCHEMA = json_schema_for(InterpretationDraft)
ANSWER_SCHEMA = json_schema_for(AnswerDraft)

COPILOT_PROMPT_DIGEST = hashlib.sha256(
    (ASK_RULES + json.dumps(ASK_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]

ANSWER_RULES = "\n".join(
    (
        "You are shown the district records one question was answered from: the distinct events retrieved, "
        "the counts and windows the pipeline measured over them, and the quotations on file. Answer with one "
        "JSON object and nothing else.",
        "1. Every count, total, date, window, place list and status printed was measured from the records. "
        "Never write a number that is not printed above, and never restate a measured count as something you "
        "worked out.",
        "2. Each statement carries a kind: " + KIND_TOKENS + ". A source_fact is what a record says and must "
        "cite the numbered evidence printed with it. A calculated statement is a count, comparison or list "
        "the measured lines above already give. A possible_explanation is your reading of those numbers - say "
        "may, could or fits, never that it is so. A recommendation is what a person could do next; nothing "
        "in these records says it was done, so never write that it has been.",
        "3. Cite only numbered events and evidence identifiers printed below, and name a saved recurring "
        "issue or a measured increase only by the identifier printed with it. Never invent an index, an "
        "identifier, a place, a date or a source. A statement you cannot cite is not an answer.",
        "4. Name at least two of the printed events when the answer rests on more than one happening.",
        "5. When the records shown cannot answer the question, leave statements empty and say in caveats what "
        "is missing. Do not stretch a nearby fact into an answer, and do not fill a gap with what districts "
        "usually see at this time of year.",
        "6. Answer in the language the question is written in - Tamil, English, or Tamil written in Latin "
        "letters - and for a question that mixes two, the language its main words are in. Keep every place "
        "name and quotation exactly as the records write it.",
        "7. headline answers the question in one sentence. caveats says what limits this answer: a window the "
        "run read few records for, events with no date, reports that may describe one happening.",
        "8. Never write that something is confirmed, proved, statistically significant, or caused by "
        "something. These records show what was reported, not what was true.",
        "9. confidence is how sure you are of what you wrote, from 0.0 to 1.0; answer below 0.5 when guessing.",
    )
)

ANSWER_PROMPT_DIGEST = hashlib.sha256(
    (ANSWER_RULES + json.dumps(ANSWER_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


class Interpretation(StrictModel):
    intent: QuestionIntent = QuestionIntent.OTHER
    question: str
    script: QuestionScript = QuestionScript.OTHER
    filters: Filters = Field(default_factory=Filters)
    days_back: Optional[int] = None
    compare: bool = False
    places_asked: list[str] = Field(default_factory=list)
    places_matched: list[str] = Field(default_factory=list)
    understood: Optional[str] = None
    follow_up: bool = False
    findings: list[str] = Field(default_factory=list)
    confidence: Confidence
    review: ReviewState
    method: ExtractionMethod = ExtractionMethod.LLM
    provider: str
    model: str
    prompt_version: str = COPILOT_PROMPT_VERSION
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    def describe(self) -> str:
        parts = [self.filters.describe()]
        if self.places_asked:
            parts.append("place matching any of " + ", ".join(repr(item) for item in self.places_asked))
        return "; ".join(part for part in parts if part and part != "no filter") or "the whole corpus"

    @model_validator(mode="after")
    def _an_interpretation_sets_a_search_not_an_answer(self) -> "Interpretation":
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("an interpretation is used or held for a person; it is never left unresolved")
        if self.days_back is not None and not 1 <= self.days_back <= MAX_DAYS_BACK:
            raise ValueError(f"days_back must be between 1 and {MAX_DAYS_BACK}")
        if self.compare and self.days_back is None:
            raise ValueError("a comparison needs a window length before it can compare two of them")
        if self.filters.place is not None:
            raise ValueError(
                "places are matched against the names the records themselves carry, never through a "
                "filter string"
            )
        windowed = self.filters.since is not None or self.filters.until is not None
        if self.days_back is not None and not windowed:
            raise ValueError("a window in days must be anchored on the dates the records carry")
        if windowed and self.days_back is None:
            raise ValueError("a date window here can only come from a window counted in days")
        if self.review is ReviewState.ACCEPTED:
            if self.findings:
                raise ValueError("an accepted interpretation with findings is a silent guess")
            if not self.understood:
                raise ValueError("an accepted interpretation says back what it searched for")
            if self.confidence < ASK_THRESHOLD:
                raise ValueError(f"an accepted interpretation needs confidence of at least {ASK_THRESHOLD}")
            if self.method is not ExtractionMethod.LLM:
                raise ValueError("only a question that was read by a model can be accepted as understood")
            unmatched = [item for item in self.places_asked if norm(item) not in self.places_matched]
            if unmatched:
                raise ValueError(
                    "the search names places no record here carries: "
                    + ", ".join(repr(item) for item in unmatched)
                )
        elif not self.findings and self.method is ExtractionMethod.LLM:
            raise ValueError("an interpretation held for a person says why")
        return self


class AnswerCitation(StrictModel):
    event_id: str
    incident_id: str
    record_id: str
    source_id: str
    source_url: Optional[str] = None
    evidence_id: str
    field: str
    quote: str
    when: Optional[datetime] = None
    when_basis: str = "none"
    modality: Modality = Modality.TEXT
    method: ExtractionMethod = ExtractionMethod.LLM


class AnswerStatement(StrictModel):
    text: str
    kind: StatementKind
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    review: ReviewState
    method: ExtractionMethod = ExtractionMethod.LLM

    @model_validator(mode="after")
    def _a_statement_is_the_kind_it_claims(self) -> "AnswerStatement":
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a statement is used or held for a person; it is never left unresolved")
        if self.kind is StatementKind.SOURCE_FACT and not self.evidence_ids:
            raise ValueError(
                "a source fact cites the words it rests on or it is a claim about the district with no "
                "record behind it"
            )
        if self.kind is StatementKind.CALCULATED and not self.event_ids:
            raise ValueError("a calculated statement names the events its numbers were counted over")
        if self.review is ReviewState.ACCEPTED:
            if self.kind not in (StatementKind.SOURCE_FACT, StatementKind.CALCULATED):
                raise ValueError(
                    f"a {self.kind.value} is offered for a person to weigh, never accepted as a finding"
                )
            if self.confidence < ASK_THRESHOLD:
                raise ValueError(f"an accepted statement needs confidence of at least {ASK_THRESHOLD}")
        return self


class AnswerExplanation(StrictModel):
    headline: Optional[str] = None
    caveats: list[str] = Field(default_factory=list)
    cited_event_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    method: ExtractionMethod = ExtractionMethod.LLM
    provider: str
    model: str
    prompt_version: str = ANSWER_PROMPT_VERSION
    prompt_digest: str
    shown_hash: str
    decided_at: datetime


class Answer(StrictModel):
    schema_version: str = SCHEMA_VERSION
    answer_id: str
    question: str
    script: QuestionScript
    intent: QuestionIntent
    state: AnswerState
    review: ReviewState
    findings: list[str] = Field(default_factory=list)
    missing: Optional[str] = None
    interpretation: Interpretation
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
    status_unstated: int = 0
    areas: list[IssueArea] = Field(default_factory=list)
    geographic_precision: str = "unknown"
    recent: Optional[PeriodCount] = None
    previous: Optional[PeriodCount] = None
    change: Optional[int] = None
    comparison: Optional[str] = None
    only_in_recent: list[str] = Field(default_factory=list)
    only_in_previous: list[str] = Field(default_factory=list)
    followups: list[OpenFollowUp] = Field(default_factory=list)
    recorded: list[RecordedResponse] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    citations: list[AnswerCitation] = Field(default_factory=list)
    statements: list[AnswerStatement] = Field(default_factory=list)
    families_cited: list[str] = Field(default_factory=list)
    trends_cited: list[str] = Field(default_factory=list)
    explanation: Optional[AnswerExplanation] = None
    provider: str
    model: str
    prompt_version: str = ANSWER_PROMPT_VERSION
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    @property
    def quoted_events(self) -> int:
        return len({item.event_id for item in self.citations})

    @model_validator(mode="after")
    def _an_answer_claims_only_what_the_records_say(self) -> "Answer":
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
            raise ValueError(
                f"sources {self.sources} is not the {len(self.source_ids)} publisher id(s) it lists"
            )
        if self.answer_id != answer_id_for(self.question):
            raise ValueError(f"answer id {self.answer_id!r} is not the one its question derives")
        if self.interpretation.question != self.question:
            raise ValueError("the answer's search was set for a different question than the answer records")
        if self.script is not self.interpretation.script:
            raise ValueError("the answer and its search disagree about the script the question used")
        if self.intent is not self.interpretation.intent:
            raise ValueError("the answer and its search disagree about what was asked")
        if self.shown_events > self.events:
            raise ValueError("an answer cannot print more events than it counted")
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("an answer is used, qualified or refused; it is never left unresolved")
        if self.recent is not None:
            if self.recent.events != self.events:
                raise ValueError("the window counted a different number of events than the answer lists")
            if self.recent.incidents != self.incidents:
                raise ValueError("the window counted a different number of incidents than the answer lists")
        elif self.previous is not None or self.change is not None:
            raise ValueError("a comparison with no window of its own compares nothing")
        if self.previous is not None and self.change != self.events - self.previous.events:
            raise ValueError("the change is not the recent window minus the window before it")
        for item in self.followups:
            if item.incident_id not in self.incident_ids:
                raise ValueError("a follow-up names an incident this answer did not retrieve")
        for item in self.recorded:
            if item.incident_id not in self.incident_ids:
                raise ValueError("a recorded response names an incident this answer did not retrieve")
        for item in self.citations:
            if item.incident_id not in self.incident_ids:
                raise ValueError("a quotation cites an incident this answer did not retrieve")
        if len({item.evidence_id for item in self.citations}) != len(self.citations):
            raise ValueError("a quotation is listed twice")
        cited = {item.event_id for item in self.citations}
        outside = sorted({event for item in self.statements for event in item.event_ids} - set(self.event_ids))
        if outside:
            raise ValueError("a statement names events that were never retrieved: " + ", ".join(outside))
        printed = {item.evidence_id for item in self.citations}
        invented = sorted(
            {eid for item in self.statements for eid in item.evidence_ids} - printed
        )
        if invented:
            raise ValueError(
                "a statement cites evidence this answer carries no quotation for: " + ", ".join(invented)
            )
        if self.explanation is not None:
            stray = sorted(set(self.explanation.cited_event_ids) - set(self.event_ids))
            if stray:
                raise ValueError("the answer cites events that were never retrieved: " + ", ".join(stray))
            extra = sorted(set(self.explanation.cited_evidence_ids) - printed)
            if extra:
                raise ValueError("the answer cites evidence with no quotation: " + ", ".join(extra))
            if self.provider == NOT_ASKED:
                raise ValueError("an answer with an explanation names the provider that gave it")
        elif self.provider != NOT_ASKED:
            raise ValueError("an answer with no explanation says no answer was asked")
        allowed = set(self.numbers)
        for item in self.statements:
            if item.kind is not StatementKind.CALCULATED:
                continue
            stray = [run for run in digit_runs(item.text) if run not in allowed]
            if stray:
                raise ValueError(
                    "a calculated statement says "
                    + ", ".join(stray)
                    + ", which no measured line printed for this question carries"
                )
        generated: list[Optional[str]] = [self.interpretation.understood]
        generated += [item.text for item in self.statements]
        if self.explanation is not None:
            generated += [self.explanation.headline, *self.explanation.caveats]
        claims = claim_words_in(generated)
        if self.state is AnswerState.GROUNDED:
            if self.findings:
                raise ValueError("a grounded answer carrying findings is a silent qualification")
            if self.review is not ReviewState.ACCEPTED:
                raise ValueError("a grounded answer is one a person has not been asked to confirm")
            if not self.statements or self.explanation is None:
                raise ValueError("a grounded answer states what the records answer")
            if any(item.review is not ReviewState.ACCEPTED for item in self.statements):
                raise ValueError("a grounded answer holds no statement for a person")
            if self.explanation.confidence < ASK_THRESHOLD:
                raise ValueError(f"a grounded answer needs confidence of at least {ASK_THRESHOLD}")
            if claims:
                raise ValueError(
                    "a grounded answer uses " + ", ".join(claims) + ", which these records never establish"
                )
            if self.events >= MIN_QUOTED_EVENTS and self.quoted_events < MIN_QUOTED_EVENTS:
                raise ValueError(
                    f"a grounded answer quotes fewer than {MIN_QUOTED_EVENTS} of the events it counted"
                )
            if not self.citations:
                raise ValueError("a grounded answer carries no quotation")
        elif self.state is AnswerState.QUALIFIED:
            if not self.findings:
                raise ValueError("a qualified answer says what qualifies it")
            if not self.statements:
                raise ValueError("an answer with nothing stated answers nothing; refuse it instead")
            if self.missing is not None:
                raise ValueError("a qualified answer is not one the records cannot answer")
        else:
            if self.statements:
                raise ValueError("an answer the records cannot support still states something")
            if not self.missing:
                raise ValueError("a refused answer says what these records could not answer and why")
            if self.review is ReviewState.ACCEPTED:
                raise ValueError("a refusal is a person's decision to make, never an accepted finding")
        if self.statements and self.explanation is None:
            raise ValueError("statements without an answer asked for cannot exist")
        return self


@dataclass
class Row:
    unit: Unit
    incident: Incident
    evidence: Evidence

    @property
    def event_id(self) -> str:
        return self.unit.event_id

    @property
    def record_id(self) -> str:
        return self.incident.provenance.record_id


def rows_of(units: Sequence[Unit], limit: int) -> list[Row]:
    rows: list[Row] = []
    seen: set[str] = set()
    for unit in units:
        own = 0
        for incident in unit.incidents:
            for item in shown_evidence(incident):
                if not item.quote or item.evidence_id in seen:
                    continue
                seen.add(item.evidence_id)
                own += 1
                rows.append(Row(unit, incident, item))
                if own >= limit:
                    break
            if own >= limit:
                break
    return rows


def value_line(values: Iterable[Any]) -> str:
    named = sorted({clean(getattr(item, "value", item)) for item in values if clean(getattr(item, "value", item))})
    return ", ".join(named) or "none"


def union_of(units: Sequence[Unit], name: str) -> set[Any]:
    found: set[Any] = set()
    for unit in units:
        found |= getattr(unit, name)
    return found


def status_values(units: Sequence[Unit]) -> set[str]:
    return {value for unit in units for value, _quote, _ids in unit.statuses.values()}


def unit_matches(unit: Unit, wanted: Sequence[str]) -> bool:
    names = unit_names(unit)
    return any(query in name for query in wanted for name in names)


@dataclass
class Corpus:
    pool: list[Incident] = field(default_factory=list)
    skipped_review: int = 0
    excluded_by_filter: int = 0
    units: list[Unit] = field(default_factory=list)
    vocabulary: list[str] = field(default_factory=list)
    earliest: Optional[datetime] = None
    latest: Optional[datetime] = None
    latest_basis: str = "none"
    undated: int = 0
    categories: set[IncidentCategory] = field(default_factory=set)
    types: set[EventType] = field(default_factory=set)
    departments: set[Department] = field(default_factory=set)
    severities: set[SeverityLevel] = field(default_factory=set)
    priorities: set[PriorityLevel] = field(default_factory=set)
    statuses: set[str] = field(default_factory=set)

    @property
    def incidents(self) -> int:
        return len(self.pool)

    @property
    def records(self) -> int:
        return len({incident.provenance.record_id for incident in self.pool})

    @property
    def sources(self) -> int:
        return len({incident.provenance.source_id for incident in self.pool})

    def window(self) -> str:
        if self.earliest is None or self.latest is None:
            return "no record here carries a date a search can count days back from"
        return f"{self.earliest.date()}..{self.latest.date()} (newest from {self.latest_basis})"


def corpus_of(pool: Sequence[Incident], links: Sequence[EventLink]) -> Corpus:
    units = event_units(pool, links)
    dated = [unit.when for unit in units if unit.when is not None]
    latest = max(dated, default=None)
    basis = "none"
    if latest is not None:
        basis = next(unit.when_basis for unit in units if unit.when == latest)
    return Corpus(
        pool=list(pool),
        units=units,
        vocabulary=place_vocabulary(pool),
        earliest=min(dated, default=None),
        latest=latest,
        latest_basis=basis,
        undated=len([unit for unit in units if unit.when is None]),
        categories=union_of(units, "categories"),
        types=union_of(units, "types"),
        departments=union_of(units, "departments"),
        severities=union_of(units, "severities"),
        priorities=union_of(units, "priorities"),
        statuses=status_values(units),
    )


@dataclass
class Retrieved:
    units: list[Unit] = field(default_factory=list)
    shown: list[Unit] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    areas: list[IssueArea] = field(default_factory=list)
    precision: str = "unknown"
    recent: Optional[PeriodCount] = None
    previous: Optional[PeriodCount] = None
    change: Optional[int] = None
    comparison: Optional[str] = None
    only_in_recent: list[str] = field(default_factory=list)
    only_in_previous: list[str] = field(default_factory=list)
    followups: list[OpenFollowUp] = field(default_factory=list)
    recorded: list[RecordedResponse] = field(default_factory=list)
    status_unstated: int = 0
    status_unquoted: int = 0
    undated: int = 0
    outside_window: int = 0
    truncated: int = 0
    anchor: Optional[datetime] = None
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
    def quoted_events(self) -> int:
        return len({row.event_id for row in self.rows})


def window_counts(units: Sequence[Unit], since: Optional[date], until: Optional[date]) -> PeriodCount:
    return PeriodCount(
        since=since,
        until=until,
        events=len(units),
        incidents=sum(len(unit.incidents) for unit in units),
    )


def area_names(units: Sequence[Unit]) -> set[str]:
    found: set[str] = set()
    for unit in units:
        found |= {norm(name) for name, _kind, _ids in unit.areas if clean(name)}
    return found


def search(
    corpus: Corpus,
    interpretation: Interpretation,
    links: Sequence[EventLink],
    *,
    max_events: int = MAX_EVENTS_SHOWN,
    evidence_limit: int = MAX_SHOWN_EVIDENCE,
) -> Retrieved:
    applied = interpretation.filters
    broad = applied.model_copy(update={"since": None, "until": None})
    kept = [item for item in corpus.pool if broad.keeps(item)]
    units = event_units(kept, links)
    wanted = interpretation.places_matched or interpretation.places_asked
    if wanted:
        units = [unit for unit in units if unit_matches(unit, [norm(item) for item in wanted])]
    dated = [unit for unit in units if unit.when is not None]
    anchor = max((unit.when for unit in dated), default=None)
    ordered = [unit for unit in units if applied.in_window(unit.when)]
    ordered.sort(key=lambda unit: (unit.when or OLDEST, unit.event_id), reverse=True)
    shown = ordered[:max_events]
    found = Retrieved(
        units=ordered,
        shown=shown,
        rows=rows_of(shown, evidence_limit),
        truncated=len(ordered) - len(shown),
        undated=len([unit for unit in units if unit.when is None]),
        anchor=anchor,
    )
    found.outside_window = len(units) - len(ordered)
    found.areas, found.precision = areas_of(ordered)
    days = interpretation.days_back
    since = applied.since
    until = applied.until
    found.recent = window_counts(ordered, since, until)
    if interpretation.compare and days is not None and anchor is not None:
        low = anchor.date() - timedelta(days=2 * days - 1)
        high = anchor.date() - timedelta(days=days)
        before = [unit for unit in dated if low <= unit.day <= high]
        found.previous = window_counts(before, low, high)
        found.change = len(ordered) - len(before)
        found.only_in_recent = sorted(area_names(ordered) - area_names(before))
        found.only_in_previous = sorted(area_names(before) - area_names(ordered))
        found.comparison = (
            f"{found.recent.events} distinct event(s) in {since}..{until} against {found.previous.events} "
            f"in {low}..{high}, a change of {found.change:+d} event(s)"
        )
    for unit in ordered:
        for incident in unit.incidents:
            entry = unit.statuses.get(incident.incident_id)
            if entry is None:
                found.status_unstated += 1
                continue
            value, quote, ids = entry
            if not quote:
                found.status_unquoted += 1
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
                found.followups.append(OpenFollowUp(**row))
            elif value in RECORDED_STATUSES:
                found.recorded.append(RecordedResponse(**row))
    if not corpus.pool:
        found.empty_reason = NO_CORPUS
    elif not kept:
        found.empty_reason = (
            f"no accepted incident matches {broad.describe()}, so there was nothing to group into events"
        )
    elif wanted and not units:
        found.empty_reason = (
            "none of the records here names the place the question asks about ("
            + ", ".join(repr(item) for item in wanted)
            + "), so the search matched nothing"
        )
    elif not units:
        found.empty_reason = f"the incidents matching {broad.describe()} evidence no district event"
    elif not ordered and days is not None:
        found.empty_reason = (
            f"{found.undated} event(s) match the rest of the search but carry no date, and the question asks "
            f"for the {days} days counted back from {anchor.date() if anchor else 'the newest dated record'}"
        )
    elif not ordered:
        found.empty_reason = "the search matched no distinct event in the records read"
    return found


def ask_lines(
    question: str,
    script: QuestionScript,
    corpus: Corpus,
    families: Sequence[IssueFamily],
    measured: Sequence[Trend],
    previous: Optional[str] = None,
) -> list[str]:
    named = corpus.vocabulary
    shown_names = named[:MAX_PLACES_PRINTED]
    lines = [
        f"Question: {question}",
        *(
            [f"Earlier in this conversation, the same person asked: {clean(previous)}"]
            if clean(previous)
            else []
        ),
        f"The question uses {script.value} characters.",
        f"The records read hold {len(corpus.units)} distinct event(s) from {corpus.incidents} accepted "
        f"incident(s) in {corpus.records} record(s) filed by {corpus.sources} publisher(s).",
        f"They are dated {corpus.window()}; {corpus.undated} event(s) carry no date at all.",
        "Places these records name, as they write them"
        + (
            f" (first {len(shown_names)} of {len(named)}): " + ", ".join(shown_names)
            if len(named) > len(shown_names)
            else ": " + (", ".join(shown_names) or "none")
        ),
        "categories here: " + value_line(corpus.categories),
        "event types here: " + value_line(corpus.types),
        "departments here: " + value_line(corpus.departments),
        "severities here: " + value_line(corpus.severities),
        "priorities here: " + value_line(corpus.priorities),
        "statuses here: " + (", ".join(sorted(corpus.statuses)) or "none"),
    ]
    if families:
        lines.append(
            "recurring issues already grouped from these records: "
            + "; ".join(
                f"{family.family_id}: {clean(family.label) or 'unnamed'}"
                for family in families[:MAX_EVENTS_SHOWN]
            )
        )
    if measured:
        lines.append(
            "increases already measured over these records: "
            + "; ".join(
                f"{trend.trend_id}: {trend.dimension.value}={trend.value} "
                f"{trend.events} event(s) against a baseline of {trend.arithmetic.baseline_mean:.2f}"
                for trend in measured[:MAX_EVENTS_SHOWN]
            )
        )
    lines += [
        "Set the search with these fields only, each taking one token from the lists below: "
        f"category: {CATEGORY_TOKENS}; incident_type: {TYPE_TOKENS}; department: {DEPARTMENT_TOKENS}; "
        f"min_severity: {SEVERITY_TOKENS}; min_priority: {PRIORITY_TOKENS}.",
        "No record content is printed here: set how to look, do not answer.",
    ]
    return lines


def interpret_request(
    question: str,
    script: QuestionScript,
    corpus: Corpus,
    families: Sequence[IssueFamily],
    measured: Sequence[Trend],
    previous: Optional[str] = None,
) -> LLMRequest:
    return LLMRequest(
        system=ASK_RULES,
        user="\n".join(ask_lines(question, script, corpus, families, measured, previous)),
        json_schema=ASK_SCHEMA,
        meta={"answer_id": answer_id_for(question), "prompt_version": COPILOT_PROMPT_VERSION},
    )


def event_block(index: int, unit: Unit, rows: Sequence[Row]) -> list[str]:
    own = [row for row in rows if row.event_id == unit.event_id]
    lines = [f"Event {index} ({unit.event_id})"]
    lines.append(
        f"date: {unit.day.isoformat() if unit.day else 'no date'} (from {unit.when_basis})"
    )
    lines.append("incidents: " + ", ".join(unit.incident_ids) + " | records: " + ", ".join(unit.record_ids))
    lines.append("sources: " + (", ".join(unit.source_urls) or "none named"))
    lines.append("incident_type: " + (value_line(unit.types) if unit.types else "not stated"))
    lines.append("category: " + (value_line(unit.categories) if unit.categories else "not stated"))
    lines.append("department: " + (value_line(unit.departments) if unit.departments else "not stated"))
    lines.append("severity: " + (value_line(unit.severities) if unit.severities else "not stated"))
    lines.append("priority: " + (value_line(unit.priorities) if unit.priorities else "not stated"))
    lines.append(
        "places: "
        + (", ".join(f"{name} ({kind.value})" for name, kind, _ids in unit.areas) or "none named")
    )
    statuses = [f"{key}: {value[0]}" for key, value in sorted(unit.statuses.items())]
    lines.append("recorded status: " + ("; ".join(statuses) if statuses else "none recorded"))
    lines.append("evidence:")
    lines += [f"[{row.evidence.evidence_id}] {row.record_id} {row.evidence.field}: {row.evidence.quote}" for row in own]
    return lines


def saved_lines(families: Sequence[IssueFamily], measured: Sequence[Trend]) -> list[str]:
    lines: list[str] = []
    for family in families[:MAX_EVENTS_SHOWN]:
        parts = [
            f"Saved recurring issue {family.family_id}: {clean(family.label) or 'unnamed'}",
            clean(family.description) or "no description",
            f"{family.events} distinct event(s) from {family.first_event or 'undated'} to "
            f"{family.last_event or 'undated'} at "
            + (", ".join(f"{area.name} ({area.level})" for area in family.areas) or "no named place"),
        ]
        if family.comparison:
            parts.append(family.comparison)
        lines.append(" | ".join(parts))
    for trend in measured[:MAX_EVENTS_SHOWN]:
        recent = trend.arithmetic.recent
        lines.append(
            f"Measured increase {trend.trend_id}: {trend.dimension.value}={trend.value} holds "
            f"{trend.events} distinct event(s) in {recent.since}..{recent.until} against a baseline average "
            f"of {trend.arithmetic.baseline_mean:.2f}, so it is {trend.status.value.replace('_', ' ')}"
        )
    return lines


def answer_lines(
    question: str,
    interpretation: Interpretation,
    retrieved: Retrieved,
    families: Sequence[IssueFamily],
    measured: Sequence[Trend],
    *,
    deduplicated: bool,
) -> tuple[list[str], list[str]]:
    summary = [
        f"Question: {question}",
        "What was searched: " + interpretation.describe(),
        (
            f"The search matched {retrieved.recent.events if retrieved.recent else len(retrieved.units)} "
            f"distinct event(s) from {len(retrieved.incident_ids)} incident(s) in "
            f"{len(retrieved.record_ids)} record(s) filed by {len(retrieved.source_ids)} publisher(s)."
            if retrieved.units
            else "The search matched nothing."
        ),
    ]
    if retrieved.recent is not None and retrieved.recent.since is not None:
        summary.append(
            f"Window: {retrieved.recent.since}..{retrieved.recent.until}, counted back from the newest "
            f"dated record ({retrieved.anchor.date() if retrieved.anchor else 'none'})."
        )
    else:
        summary.append("No date window was asked for, so the whole corpus of matched records is in scope.")
    if retrieved.comparison:
        summary.append("Comparison: " + retrieved.comparison)
        summary.append(
            "Places only in the recent window: "
            + (", ".join(retrieved.only_in_recent) or "none")
            + "; only in the window before: " + (", ".join(retrieved.only_in_previous) or "none")
        )
    elif interpretation.compare:
        summary.append(
            "The question compares two periods but the records hold no dated events to build both windows "
            "from, so nothing was compared."
        )
    if retrieved.undated:
        summary.append(
            f"{retrieved.undated} event(s) matched the rest of the search but carry no date"
            + (", so they fall in no window" if interpretation.days_back is not None else "")
        )
    area_line = ", ".join(
        f"{area.name} ({area.level}, {len(area.event_ids)} event(s))" for area in retrieved.areas
    )
    summary.append(
        "Places among the matched events: "
        + (area_line or "none named")
        + f", at {retrieved.precision} precision."
    )
    summary.append(
        f"Statuses among the matched incidents: {len(retrieved.followups)} still open and quoted, "
        f"{len(retrieved.recorded)} carry a recorded action or resolution, {retrieved.status_unstated} state "
        f"no status the record evidenced, {retrieved.status_unquoted} state one with no words to quote."
    )
    saved = saved_lines(families, measured)
    blocks: list[str] = []
    counted = list(summary)
    for index, unit in enumerate(retrieved.shown, start=1):
        lines = event_block(index, unit, retrieved.rows)
        blocks.append("\n".join(lines))
        counted += [line for line in lines if line.startswith("date: ")]
    extra = [
        f"The search matched {len(retrieved.units)} distinct event(s); {len(retrieved.shown)} are printed "
        "here. Answer from what is printed and say what you were not shown."
        if retrieved.truncated
        else f"All {len(retrieved.shown)} matched event(s) are printed here.",
        "Every count, window, place list and status above was measured by the pipeline from the records, "
        "never asked of you."
        + (
            " Repeated reports of one happening were merged into one event before counting."
            if deduplicated
            else " No link decisions were available, so two reports of one happening may be printed as two "
            "events and a count may double what happened."
        ),
    ]
    return summary + saved + blocks + extra, counted + saved + extra


def answer_request(
    question: str,
    interpretation: Interpretation,
    retrieved: Retrieved,
    families: Sequence[IssueFamily],
    measured: Sequence[Trend],
    *,
    deduplicated: bool,
) -> tuple[LLMRequest, list[str]]:
    lines, counted = answer_lines(
        question, interpretation, retrieved, families, measured, deduplicated=deduplicated
    )
    request = LLMRequest(
        system=ANSWER_RULES,
        user="\n\n".join(lines),
        json_schema=ANSWER_SCHEMA,
        meta={"answer_id": answer_id_for(question), "prompt_version": ANSWER_PROMPT_VERSION},
    )
    return request, counted


def confidence_of(value: Optional[float], findings: list[str], label: str) -> float:
    if value is None or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        findings.append(f"the {label} gave confidence {value!r}; {DEFAULT_CONFIDENCE} was used")
        return DEFAULT_CONFIDENCE
    return value


def build_interpretation(
    question: str,
    script: QuestionScript,
    draft: Optional[InterpretationDraft],
    *,
    vocabulary: Sequence[str],
    anchor: Optional[datetime],
    extras: Sequence[str],
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
) -> Interpretation:
    findings = [clean(item) for item in extras if clean(item)]
    intent = QuestionIntent.OTHER
    places: list[str] = []
    matched: list[str] = []
    days_back: Optional[int] = None
    compare = False
    understood: Optional[str] = None
    confidence = 0.0
    category: Optional[IncidentCategory] = None
    incident_type: Optional[EventType] = None
    department: Optional[Department] = None
    min_severity: Optional[SeverityLevel] = None
    min_priority: Optional[PriorityLevel] = None
    method = ExtractionMethod.UNRESOLVED
    if draft is None:
        findings.append("the question was never read into a search, so nothing was retrieved for it")
    else:
        method = ExtractionMethod.LLM
        raw = clean(draft.intent)
        if not raw:
            findings.append("the search offered no intent, so it was treated as an open question")
        else:
            try:
                intent = QuestionIntent(raw)
            except ValueError:
                findings.append(f"the search named intent {raw!r}, which is not one of the tokens")
        for item in draft.places:
            text = clean(item)
            if not text or text in places:
                continue
            places.append(text)
            if place_matches(text, vocabulary):
                matched.append(norm(text))
            else:
                findings.append(
                    f"no record here names the place {text!r} the search asked for, so nothing matches it"
                )
        category = token(draft.category, IncidentCategory, "category", findings)
        incident_type = token(draft.incident_type, EventType, "incident_type", findings)
        department = token(draft.department, Department, "department", findings)
        min_severity = token(draft.min_severity, SeverityLevel, "min_severity", findings)
        min_priority = token(draft.min_priority, PriorityLevel, "min_priority", findings)
        if draft.days_back is not None:
            if not 1 <= draft.days_back <= MAX_DAYS_BACK:
                findings.append(
                    f"the search asked for {draft.days_back} day(s), which is not between 1 and "
                    f"{MAX_DAYS_BACK}, so no window was applied"
                )
            elif anchor is None:
                findings.append(
                    f"the search asked for the last {draft.days_back} day(s) but no record here carries a "
                    "date to count back from, so no window was applied"
                )
            else:
                days_back = draft.days_back
        compare = bool(draft.compare)
        if compare and days_back is None:
            findings.append(
                "the search asks for a comparison with no window length to compare, so nothing was compared"
            )
            compare = False
        understood = clean(draft.understood) or None
        if not understood:
            findings.append("the search does not say back what it looked for in the question's own words")
        confidence = confidence_of(draft.confidence, findings, "reading of the question")
        if confidence < ASK_THRESHOLD:
            findings.append(
                f"the question was read with confidence {confidence:.2f}, below the {ASK_THRESHOLD} an "
                "unattended search needs"
            )
    since = until = None
    if days_back is not None and anchor is not None:
        until = anchor.date()
        since = until - timedelta(days=days_back - 1)
    review = ReviewState.ACCEPTED
    if method is not ExtractionMethod.LLM or findings or confidence < ASK_THRESHOLD:
        review = ReviewState.REVIEW_REQUIRED
    return Interpretation(
        intent=intent,
        question=question,
        script=script,
        filters=Filters(
            since=since,
            until=until,
            category=category,
            incident_type=incident_type,
            department=department,
            min_severity=min_severity,
            min_priority=min_priority,
        ),
        days_back=days_back,
        compare=compare,
        places_asked=places,
        places_matched=matched,
        understood=understood,
        follow_up=bool(draft is not None and draft.follow_up),
        findings=findings,
        confidence=confidence,
        review=review,
        method=method,
        provider=provider or NOT_ASKED,
        model=model or NOT_ASKED,
        prompt_digest=COPILOT_PROMPT_DIGEST,
        shown_hash=shown,
        decided_at=decided_at,
    )


def picked_events(
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


def picked_evidence(
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


def citation_of(row: Row) -> AnswerCitation:
    provenance = row.incident.provenance
    return AnswerCitation(
        event_id=row.unit.event_id,
        incident_id=row.incident.incident_id,
        record_id=provenance.record_id,
        source_id=provenance.source_id,
        source_url=provenance.source_url,
        evidence_id=row.evidence.evidence_id,
        field=row.evidence.field,
        quote=str(row.evidence.quote),
        when=row.unit.when,
        when_basis=row.unit.when_basis,
        modality=row.evidence.modality,
        method=row.evidence.method,
    )


def build_answer(
    question: str,
    script: QuestionScript,
    interpretation: Interpretation,
    retrieved: Retrieved,
    draft: Optional[AnswerDraft],
    *,
    counted: Sequence[str],
    extras: Sequence[str],
    family_ids: Sequence[str],
    trend_ids: Sequence[str],
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
    missing: Optional[str] = None,
) -> Answer:
    findings = [clean(item) for item in extras if clean(item)]
    findings += list(interpretation.findings)
    by_index = {index: unit for index, unit in enumerate(retrieved.shown, start=1)}
    printed = {row.evidence.evidence_id: row for row in retrieved.rows}
    allowed = digit_set("\n".join(counted))
    statements: list[AnswerStatement] = []
    cited_events: list[str] = []
    cited_evidence: list[str] = []
    families_cited: list[str] = []
    trends_cited: list[str] = []
    headline: Optional[str] = None
    caveats: list[str] = []
    confidence = 0.0
    if draft is None:
        findings.append("no answer was asked, so nothing is claimed here")
    else:
        headline = clean(draft.headline) or None
        caveats = [clean(item) for item in draft.caveats if clean(item)]
        confidence = confidence_of(draft.confidence, findings, "answer")
        cited_events = picked_events(draft.events, by_index, findings, "answer")
        cited_evidence = picked_evidence(draft.evidence, printed, findings, "answer")
        for item in draft.saved:
            token_text = clean(item)
            if token_text in family_ids:
                if token_text not in families_cited:
                    families_cited.append(token_text)
            elif token_text in trend_ids:
                if token_text not in trends_cited:
                    trends_cited.append(token_text)
            else:
                findings.append(
                    f"the answer cites {token_text!r}, which is neither a saved recurring issue nor a "
                    "measured increase it was shown"
                )
        if len(draft.statements) > MAX_STATEMENTS:
            findings.append(
                f"the answer offers more than {MAX_STATEMENTS} statements, so only the first "
                f"{MAX_STATEMENTS} are kept"
            )
        for raw in draft.statements[:MAX_STATEMENTS]:
            text = clean(raw.text)
            if not text:
                findings.append("a statement came back with no words in it")
                continue
            kind_token = clean(raw.kind)
            try:
                kind = StatementKind(kind_token)
            except ValueError:
                findings.append(
                    f"a statement is typed {kind_token!r}, which is not one of: {KIND_TOKENS}"
                )
                continue
            events = picked_events(raw.events, by_index, findings, f"{kind.value} statement")
            evidence = picked_evidence(raw.evidence, printed, findings, f"{kind.value} statement")
            if kind is StatementKind.SOURCE_FACT and not evidence:
                findings.append(
                    f"a source fact says {text!r} without citing a quotation printed with it, so it is not "
                    "on file"
                )
                continue
            if kind is StatementKind.CALCULATED:
                if not events:
                    findings.append(
                        f"a calculated statement says {text!r} without naming the events its numbers were "
                        "counted over"
                    )
                    continue
                stray = unknown_digits(text, allowed)
                if stray:
                    findings.append(
                        "a calculated statement says "
                        + ", ".join(stray)
                        + ", which no measured line printed for this question carries, so it was left out"
                    )
                    continue
            statement_confidence = confidence_of(raw.confidence, findings, f"{kind.value} statement")
            review = ReviewState.REVIEW_REQUIRED
            if kind in (StatementKind.SOURCE_FACT, StatementKind.CALCULATED):
                if statement_confidence >= ASK_THRESHOLD:
                    review = ReviewState.ACCEPTED
            statements.append(
                AnswerStatement(
                    text=text,
                    kind=kind,
                    event_ids=events,
                    evidence_ids=evidence,
                    confidence=statement_confidence,
                    review=review,
                )
            )
    wanted = set(cited_evidence) | {eid for item in statements for eid in item.evidence_ids}
    citations = [citation_of(row) for row in retrieved.rows if row.evidence.evidence_id in wanted]
    if citations and len({item.event_id for item in citations}) < min(
        MIN_QUOTED_EVENTS, len(retrieved.units)
    ):
        findings.append(
            f"the answer quotes {len({item.event_id for item in citations})} of the "
            f"{len(retrieved.units)} event(s) it counted, below the {MIN_QUOTED_EVENTS} one answer leans on"
        )
    generated: list[Optional[str]] = [interpretation.understood, headline, *caveats]
    generated += [item.text for item in statements]
    findings += [
        f"the answer says {word!r}, which a set of reports never establishes"
        for word in claim_words_in(generated)
    ]
    if draft is None:
        provider = model = NOT_ASKED
        explanation: Optional[AnswerExplanation] = None
    else:
        explanation = AnswerExplanation(
            headline=headline,
            caveats=caveats,
            cited_event_ids=cited_events,
            cited_evidence_ids=cited_evidence,
            confidence=confidence,
            provider=provider or "unknown",
            model=model or "unknown",
            prompt_digest=ANSWER_PROMPT_DIGEST,
            shown_hash=shown,
            decided_at=decided_at,
        )
    held = any(item.review is ReviewState.REVIEW_REQUIRED for item in statements)
    if explanation is not None and explanation.confidence < ASK_THRESHOLD:
        held = True
    if interpretation.review is ReviewState.REVIEW_REQUIRED:
        held = True
    if not statements:
        state = AnswerState.NOT_ANSWERABLE
        review = ReviewState.REVIEW_REQUIRED
        if not missing:
            missing = (
                "the records searched for this question produced no statement that carried its own citation, "
                "so nothing is claimed here"
            )
    elif findings:
        state = AnswerState.QUALIFIED
        review = ReviewState.REVIEW_REQUIRED if held else ReviewState.ACCEPTED
        missing = None
    elif held:
        state = AnswerState.QUALIFIED
        review = ReviewState.REVIEW_REQUIRED
        findings.append(
            "the answer rests on a reading or a suggestion a person has not confirmed, so it is not a "
            "finding of this pipeline"
        )
        missing = None
    else:
        state = AnswerState.GROUNDED
        review = ReviewState.ACCEPTED
        missing = None
    return Answer(
        answer_id=answer_id_for(question),
        question=question,
        script=script,
        intent=interpretation.intent,
        state=state,
        review=review,
        findings=findings,
        missing=missing,
        interpretation=interpretation,
        event_ids=retrieved.event_ids,
        incident_ids=retrieved.incident_ids,
        record_ids=retrieved.record_ids,
        source_ids=retrieved.source_ids,
        source_urls=retrieved.source_urls,
        events=len(retrieved.units),
        incidents=len(retrieved.incident_ids),
        records=len(retrieved.record_ids),
        sources=len(retrieved.source_ids),
        shown_events=len(retrieved.shown),
        truncated_events=retrieved.truncated,
        undated=retrieved.undated,
        status_unstated=retrieved.status_unstated,
        areas=retrieved.areas,
        geographic_precision=retrieved.precision,
        recent=retrieved.recent,
        previous=retrieved.previous,
        change=retrieved.change,
        comparison=retrieved.comparison,
        only_in_recent=retrieved.only_in_recent,
        only_in_previous=retrieved.only_in_previous,
        followups=retrieved.followups,
        recorded=retrieved.recorded,
        numbers=sorted(allowed, key=lambda item: int(item)),
        citations=citations,
        statements=statements,
        families_cited=families_cited,
        trends_cited=trends_cited,
        explanation=explanation,
        provider=provider,
        model=model,
        prompt_digest=ANSWER_PROMPT_DIGEST,
        shown_hash=shown,
        decided_at=decided_at,
    )


@dataclass
class Drafted:
    draft: Optional[Any] = None
    provider: str = ""
    model: str = ""
    note: Optional[str] = None
    failed: bool = False
    asked: bool = False


class Asker:
    def __init__(self, chain_factory: Callable[[], Chain]) -> None:
        self.chain_factory = chain_factory
        self.chain: Optional[Chain] = None
        self.stop_reason: Optional[str] = None

    def draft(self, request: LLMRequest, schema: Any, label: str) -> Drafted:
        if self.stop_reason is not None:
            return Drafted(note=f"nothing was asked: {self.stop_reason}")
        if self.chain is None:
            try:
                self.chain = self.chain_factory()
            except LLMError as error:
                self.stop_reason = str(error)
                return Drafted(note=f"no provider could be reached: {error}", failed=True)
        try:
            answered = self.chain.read(lambda provider_: provider_.generate_structured(request))
        except ChainExhaustedError as error:
            self.stop_reason = str(error)
            return Drafted(note=f"no provider is left to ask: {error}")
        except LLMError as error:
            return Drafted(note=f"no usable {label} came back: {error}", failed=True)
        try:
            draft = schema.model_validate(answered.result.payload)
        except ValidationError as error:
            return Drafted(
                note=f"the {label} is not shaped like one: {error.error_count()} problem(s)", failed=True
            )
        provider, model = answered.identity
        return Drafted(draft=draft, provider=provider, model=model, asked=True)


@dataclass
class AskReport:
    questions: list[str] = field(default_factory=list)
    incidents: int = 0
    records: int = 0
    sources: int = 0
    units: int = 0
    undated: int = 0
    skipped_review: int = 0
    families: int = 0
    measured: int = 0
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0
    read: int = 0
    reused_read: int = 0
    asked: int = 0
    reused: int = 0
    not_asked: int = 0
    truncated: int = 0
    max_requests: int = MAX_QUESTIONS_PER_RUN
    answers: list[Answer] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None

    @property
    def grounded(self) -> list[Answer]:
        return [item for item in self.answers if item.state is AnswerState.GROUNDED]

    @property
    def qualified(self) -> list[Answer]:
        return [item for item in self.answers if item.state is AnswerState.QUALIFIED]

    @property
    def refused(self) -> list[Answer]:
        return [item for item in self.answers if item.state is AnswerState.NOT_ANSWERABLE]

    def limitations(self) -> list[str]:
        lines: list[str] = []
        if not self.incidents:
            lines.append(NO_CORPUS)
        if not self.deduplicated:
            lines.append(
                "no link decisions were available, so each accepted incident was searched as its own event "
                "and repeated reports of one happening count separately"
            )
        if self.undecided_links:
            lines.append(
                f"{self.undecided_links} link decision(s) wait for a person, so two happenings that may be "
                "one were searched as two"
            )
        if self.skipped_review:
            lines.append(
                f"{self.skipped_review} incident(s) waiting for a person were left out of every search"
            )
        if self.undated:
            lines.append(
                f"{self.undated} distinct event(s) carry no date the record gave, so they fall in no window "
                "and answer no question about a period"
            )
        if not self.families:
            lines.append(
                "no saved recurring issue was available, so a question about repetition was answered from "
                "the events the records evidence rather than from a grouping"
            )
        if not self.measured:
            lines.append(
                "no measured increase was available, so a question about what changed was answered from the "
                "two windows counted here, not from a trend the pipeline certified"
            )
        if self.not_asked:
            lines.append(
                f"{self.not_asked} question(s) were never put to a provider, so their answers say only what "
                "the search returned"
            )
        if self.truncated:
            lines.append(
                f"{self.truncated} event(s) matched a search but fell beyond the {MAX_EVENTS_SHOWN} printed "
                "in one request, so an answer may rest on fewer events than it counted"
            )
        if self.failed:
            lines.append(f"{len(self.failed)} request(s) got no usable answer")
        if self.stop_reason:
            lines.append(f"the chain stopped answering: {self.stop_reason}")
        lines.append(
            "counts, windows, places and statuses are measured from the records; a possible explanation or "
            "a recommendation beside them is a model's reading of the same numbers, never a finding"
        )
        lines.append(
            "a place is matched by the wording the records themselves use, so a question naming it in "
            "another script or a different transliteration matches nothing and says so rather than guessing"
        )
        lines.append(
            "these answers cover only the records this run read, so an empty result says the records hold "
            "nothing, not that the district saw nothing"
        )
        return lines

    def counts(self) -> dict[str, Any]:
        return {
            "Questions": len(self.questions),
            "Accepted incidents": self.incidents,
            "Records": self.records,
            "Publishers": self.sources,
            "Distinct events": self.units,
            "Events with no date": self.undated,
            "Left out (waiting for a person)": self.skipped_review,
            "Saved recurring issues": self.families,
            "Measured increases": self.measured,
            "Questions read": self.read,
            "Reads reused": self.reused_read,
            "Answers asked": self.asked,
            "Answers reused": self.reused,
            "Answers not asked for": self.not_asked,
            "Events beyond one request": self.truncated,
            "Grounded answers": len(self.grounded),
            "Qualified answers": len(self.qualified),
            "Questions these records cannot answer": len(self.refused),
        }


def ask_copilot(
    incidents: Iterable[Incident],
    questions: Sequence[str],
    *,
    chain_factory: Callable[[], Chain],
    links: Sequence[EventLink] = (),
    families: Sequence[IssueFamily] = (),
    measured: Sequence[Trend] = (),
    store: Optional[AnswerStore] = None,
    max_requests: int = MAX_QUESTIONS_PER_RUN,
    now: Optional[datetime] = None,
    previous: Optional[str] = None,
) -> AskReport:
    every = list(incidents)
    filed = [item for item in every if item.record_kind is RecordKind.INCIDENT]
    pool = eligible(filed)
    corpus = corpus_of(pool, links)
    unique: list[str] = []
    seen: set[str] = set()
    for item in questions:
        text = clean(item)
        key = norm(text)
        if not text or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    grouped = [family for family in families if family.review is ReviewState.ACCEPTED]
    report = AskReport(
        questions=unique,
        incidents=corpus.incidents,
        records=corpus.records,
        sources=corpus.sources,
        units=len(corpus.units),
        undated=corpus.undated,
        skipped_review=len([item for item in filed if item.review_required]),
        families=len(grouped),
        measured=len(measured),
        deduplicated=bool(links),
        links=len(links),
        undecided_links=len(
            [link for link in links if link.review is ReviewState.REVIEW_REQUIRED]
        ),
        max_requests=max_requests,
    )
    asker = Asker(chain_factory)
    decided_at = now or datetime.now(timezone.utc)
    family_ids = [family.family_id for family in grouped[:MAX_EVENTS_SHOWN]]
    trend_ids = [trend.trend_id for trend in measured[:MAX_EVENTS_SHOWN]]
    for question in unique:
        answer_id = answer_id_for(question)
        script = script_of(question)
        ask_request = interpret_request(
            question, script, corpus, grouped, measured, previous if question is unique[0] else None
        )
        ask_shown = shown_hash(ask_request)
        read = store.reusable_read(answer_id, ask_shown) if store is not None else None
        if read is not None:
            interpretation = read
            report.reused_read += 1
            missing = None
        else:
            extras: list[str] = []
            draft = None
            provider = model = ""
            if not corpus.pool:
                extras.append(NO_CORPUS)
                missing = NO_CORPUS
                report.not_asked += 1
            elif report.read >= max_requests:
                extras.append(
                    f"the question was not read - this run reads at most {max_requests} question(s)"
                )
                missing = extras[-1]
                report.not_asked += 1
            else:
                got = asker.draft(ask_request, InterpretationDraft, "question")
                if got.note:
                    extras.append(got.note)
                if got.failed and got.note:
                    report.failed.append(f"{answer_id}: {got.note}")
                if got.asked:
                    report.read += 1
                draft = got.draft
                provider, model = got.provider, got.model
                missing = None if draft is not None else extras[0]
            interpretation = build_interpretation(
                question,
                script,
                draft,
                vocabulary=corpus.vocabulary,
                anchor=corpus.latest,
                extras=extras,
                provider=provider,
                model=model,
                shown=ask_shown,
                decided_at=decided_at,
            )
        general = interpretation.intent is QuestionIntent.GENERAL
        if general:
            retrieved = Retrieved()
            # An empty request hashes stable, so a saved general refusal is reused like any other answer.
            answer_req, counted = LLMRequest(system="", user=""), []
        else:
            retrieved = search(corpus, interpretation, links)
            answer_req, counted = answer_request(
                question, interpretation, retrieved, grouped, measured, deduplicated=report.deduplicated
            )
        answer_shown = shown_hash(answer_req)
        report.truncated += retrieved.truncated
        saved = store.reusable(answer_id, ask_shown, answer_shown) if store is not None else None
        if saved is not None:
            report.answers.append(saved)
            report.reused += 1
            continue
        extras = []
        draft = None
        provider = model = ""
        if general:
            extras.append(NOT_A_RECORD_QUESTION)
            missing = NOT_A_RECORD_QUESTION
        elif interpretation.method is not ExtractionMethod.LLM:
            extras.append("the question was never read into a search, so no answer was sought for it")
        elif retrieved.empty_reason:
            extras.append(retrieved.empty_reason)
            missing = retrieved.empty_reason
            report.not_asked += 1
        elif not retrieved.shown:
            extras.append("the search matched events but none carries words to print, so none was shown")
            missing = extras[-1]
            report.not_asked += 1
        elif report.asked >= max_requests:
            extras.append(
                f"no answer was asked - this run answers at most {max_requests} question(s)"
            )
            missing = extras[-1]
            report.not_asked += 1
        else:
            got = asker.draft(answer_req, AnswerDraft, "answer")
            if got.note:
                extras.append(got.note)
            if got.failed and got.note:
                report.failed.append(f"{answer_id}: {got.note}")
            if got.asked:
                report.asked += 1
            draft = got.draft
            provider, model = got.provider, got.model
        report.answers.append(
            build_answer(
                question,
                script,
                interpretation,
                retrieved,
                draft,
                counted=counted,
                extras=extras,
                family_ids=family_ids,
                trend_ids=trend_ids,
                provider=provider,
                model=model,
                shown=answer_shown,
                decided_at=decided_at,
                missing=missing,
            )
        )
    report.stop_reason = asker.stop_reason
    if store is not None:
        store.save(report)
    return report


class AnswerStore:
    def __init__(self, directory: Any) -> None:
        self.directory = Path(str(directory))
        self.answers_path = self.directory / ANSWERS_FILENAME
        self.summary_path = self.directory / COPILOT_SUMMARY_FILENAME
        self.by_id: dict[str, Answer] = {}

    def load(self) -> "AnswerStore":
        if not self.answers_path.exists():
            return self
        try:
            text = self.answers_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{self.answers_path} could not be read: {error}") from error
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                answer = Answer.model_validate_json(line)
            except ValidationError as error:
                problem = error.errors()[0].get("msg", "invalid")
                raise CheckpointError(
                    f"{self.answers_path} line {number} is not an answer this pipeline wrote "
                    f"({problem}); the saved answers were not changed"
                ) from error
            self.by_id[answer.answer_id] = answer
        return self

    def reusable_read(self, answer_id: str, ask: str) -> Optional[Interpretation]:
        found = self.by_id.get(answer_id)
        if found is None:
            return None
        read = found.interpretation
        if read.prompt_digest != COPILOT_PROMPT_DIGEST or read.shown_hash != ask:
            return None
        return read

    def reusable(self, answer_id: str, ask: str, answered: str) -> Optional[Answer]:
        found = self.by_id.get(answer_id)
        if found is None or self.reusable_read(answer_id, ask) is None:
            return None
        if (
            found.schema_version != SCHEMA_VERSION
            or found.prompt_version != ANSWER_PROMPT_VERSION
            or found.prompt_digest != ANSWER_PROMPT_DIGEST
            or found.shown_hash != answered
        ):
            return None
        return found

    def save(self, report: AskReport) -> None:
        for answer in report.answers:
            self.by_id[answer.answer_id] = answer
        lines = [
            json.dumps(
                self.by_id[key].model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
            + "\n"
            for key in sorted(self.by_id)
        ]
        _write_atomically(self.answers_path, "".join(lines))
        summary = {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": COPILOT_PROMPT_VERSION,
            "prompt_digest": COPILOT_PROMPT_DIGEST,
            "answer_prompt_version": ANSWER_PROMPT_VERSION,
            "answer_prompt_digest": ANSWER_PROMPT_DIGEST,
            "counts": report.counts(),
            "limitations": report.limitations(),
            "answers": [answer.model_dump(mode="json") for answer in report.answers],
        }
        _write_atomically(
            self.summary_path,
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def print_answers(report: AskReport, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream or sys.stdout
    print(
        f"Collector Copilot ({COPILOT_PROMPT_VERSION} + {ANSWER_PROMPT_VERSION}, "
        f"{COPILOT_PROMPT_DIGEST}/{ANSWER_PROMPT_DIGEST}, incident contract {SCHEMA_VERSION})",
        file=out,
    )
    for label, value in report.counts().items():
        print(f"  {label}: {value}", file=out)
    if report.stop_reason:
        print(f"  Stopped: {report.stop_reason}", file=out)
    for line in report.failed:
        print(f"  failed {line}", file=out)
    for index, answer in enumerate(report.answers, start=1):
        print(f"  Q{index} [{answer.script.value}] {answer.question}", file=out)
        read = answer.interpretation
        print(
            f"     searched as: {answer.interpretation.describe()} ({read.provider}/{read.model}, "
            f"confidence {read.confidence:.2f}, {read.review.value})",
            file=out,
        )
        if read.understood:
            print(f"     understood as: {read.understood}", file=out)
        window = ""
        if answer.recent is not None and answer.recent.since is not None:
            window = f" in {answer.recent.since}..{answer.recent.until}"
        print(
            f"     {answer.state.value}: {answer.events} distinct event(s) from {answer.incidents} "
            f"incident(s) in {answer.records} record(s) of {answer.sources} publisher(s){window}, "
            f"{len(answer.citations)} quotation(s) over {answer.quoted_events} event(s), "
            f"{answer.geographic_precision} precision, {answer.review.value}",
            file=out,
        )
        if answer.comparison:
            print(f"     compared: {answer.comparison}", file=out)
        if answer.followups or answer.recorded or answer.status_unstated:
            print(
                f"     open with a quote: {len(answer.followups)}, action recorded: "
                f"{len(answer.recorded)}, no status evidenced: {answer.status_unstated}",
                file=out,
            )
        if answer.explanation is not None and answer.explanation.headline:
            print(
                f"     answer: {answer.explanation.headline} (confidence "
                f"{answer.explanation.confidence:.2f})",
                file=out,
            )
        for statement in answer.statements:
            note = ""
            if statement.kind is StatementKind.RECOMMENDATION:
                note = " - a suggestion for a person, never recorded as done"
            elif statement.kind is StatementKind.POSSIBLE_EXPLANATION:
                note = " - a reading of the numbers, not a record"
            print(
                f"     - {statement.kind.value} ({statement.review.value}, confidence "
                f"{statement.confidence:.2f}): {statement.text}"
                + " [" + ", ".join(statement.event_ids + statement.evidence_ids) + "]"
                + note,
                file=out,
            )
        if answer.explanation is not None:
            for caveat in answer.explanation.caveats:
                print(f"     caveat: {caveat}", file=out)
        for finding in answer.findings:
            print(f"     finding: {finding}", file=out)
        if answer.missing:
            print(f"     not answered: {answer.missing}", file=out)
        if not detailed:
            continue
        for citation in answer.citations:
            stamp = citation.when.date().isoformat() if citation.when is not None else "no date"
            print(
                f"     {citation.evidence_id} {citation.event_id} {citation.record_id} "
                f"({citation.source_id}, {citation.field}, {stamp}, {citation.modality.value}): "
                f"{citation.quote}"
                + (f" [{citation.source_url}]" if citation.source_url else ""),
                file=out,
            )
        for item in answer.followups:
            print(
                f"     open {item.incident_id} ({item.status}): {item.quote}"
                + (f" [{item.source_url}]" if item.source_url else ""),
                file=out,
            )
        for item in answer.recorded:
            print(
                f"     recorded {item.incident_id} ({item.status}): {item.quote}"
                + (f" [{item.source_url}]" if item.source_url else ""),
                file=out,
            )
        if answer.families_cited or answer.trends_cited:
            print(
                "     cites saved work: "
                + ", ".join(answer.families_cited + answer.trends_cited),
                file=out,
            )
    for line in report.limitations():
        print(f"  limitation: {line}", file=out)


__all__ = [
    "ANSWER_PROMPT_DIGEST",
    "ANSWER_PROMPT_VERSION",
    "ANSWER_RULES",
    "ANSWER_SCHEMA",
    "ANSWERS_FILENAME",
    "ASK_RULES",
    "ASK_SCHEMA",
    "ASK_THRESHOLD",
    "Answer",
    "AnswerCitation",
    "AnswerDraft",
    "AnswerExplanation",
    "AnswerState",
    "AnswerStore",
    "Asker",
    "AskReport",
    "COPILOT_PROMPT_DIGEST",
    "COPILOT_PROMPT_VERSION",
    "COPILOT_SUMMARY_FILENAME",
    "Corpus",
    "Drafted",
    "Interpretation",
    "InterpretationDraft",
    "MAX_DAYS_BACK",
    "MAX_EVENTS_SHOWN",
    "MAX_PLACES_PRINTED",
    "MAX_QUESTIONS_PER_RUN",
    "MAX_STATEMENTS",
    "MIN_QUOTED_EVENTS",
    "NO_CORPUS",
    "NOT_A_RECORD_QUESTION",
    "QuestionIntent",
    "QuestionScript",
    "Retrieved",
    "Row",
    "StatementDraft",
    "StatementKind",
    "answer_id_for",
    "answer_lines",
    "answer_request",
    "ask_copilot",
    "build_answer",
    "build_interpretation",
    "citation_of",
    "clean",
    "corpus_of",
    "digit_runs",
    "digit_set",
    "event_block",
    "interpret_request",
    "norm",
    "picked_evidence",
    "picked_events",
    "place_matches",
    "place_vocabulary",
    "print_answers",
    "rows_of",
    "script_of",
    "search",
    "unit_matches",
    "unit_names",
    "unknown_digits",
    "value_line",
    "window_counts",
]
