from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from intelligence.checkpoint import CheckpointError, _write_atomically
from intelligence.contract import (
    SCHEMA_VERSION,
    Incident,
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
from intelligence.models.enums import ExtractionMethod
from intelligence.recurrence import (
    OLDEST,
    Bucket,
    EventReference,
    Filters,
    IssueArea,
    IssueFamily,
    Unit,
    areas_of,
    event_units,
    incident_when,
    shown_units,
)

TREND_PROMPT_VERSION = "trend-1"
TRENDS_FILENAME = "trends.jsonl"
TREND_SUMMARY_FILENAME = "trends.json"

RECENT_DAYS = 30
BASELINE_BLOCKS = 3
MIN_BASELINE_BLOCKS = 2
MIN_TREND_EVENTS = 3
MIN_TREND_RATIO = 2.0
COVERAGE_DRIFT = 1.5
TREND_THRESHOLD = 0.8
MAX_TREND_EVENTS = 8
MAX_TREND_REQUESTS = 6
MIN_QUOTED_EVENTS = 2

CLAIM_WORDS = ("confirmed", "statistically significant", "proves", "proof", "caused by")
NOT_ASKED = "no reading was asked"


class TrendDimension(str, Enum):
    CATEGORY = "category"
    INCIDENT_TYPE = "incident_type"
    PLACE = "place"
    ISSUE_FAMILY = "issue_family"


class TrendStatus(str, Enum):
    POTENTIAL = "potential"
    ALERT_ELIGIBLE = "alert_eligible"


def clean(value: Any) -> str:
    return str(value or "").strip()


def fold(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def rounded(value: float) -> float:
    return round(value, 4)


def trend_id_for(
    dimension: TrendDimension,
    value: str,
    since: date,
    until: date,
    event_ids: Sequence[str],
) -> str:
    seed = "|".join(
        (dimension.value, fold(value), since.isoformat(), until.isoformat(), *sorted(event_ids))
    )
    return "TRD-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def claim_words_in(texts: Sequence[Optional[str]]) -> list[str]:
    found: list[str] = []
    for item in texts:
        lowered = clean(item).casefold()
        for word in CLAIM_WORDS:
            if word in lowered and word not in found:
                found.append(word)
    return found


class TrendBlock(StrictModel):
    since: date
    until: date
    days: int
    events: int
    incidents: int
    records: int
    sources: int
    pool_records: int
    pool_sources: int
    event_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _a_window_counts_what_it_lists(self) -> "TrendBlock":
        if self.days != (self.until - self.since).days + 1:
            raise ValueError("days is not the length of the window it names")
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
        if self.incidents < self.events:
            raise ValueError("one distinct event always carries at least one incident")
        if self.events and not self.pool_records:
            raise ValueError("a window that counted events cannot say it held no records")
        return self


def block_of(
    span: tuple[date, date], units: Sequence[Unit], coverage: tuple[int, int]
) -> TrendBlock:
    since, until = span
    event_ids = sorted(unit.event_id for unit in units)
    incident_ids = sorted({key for unit in units for key in unit.incident_ids})
    record_ids = sorted({key for unit in units for key in unit.record_ids})
    source_ids = sorted(
        {incident.provenance.source_id for unit in units for incident in unit.incidents}
    )
    return TrendBlock(
        since=since,
        until=until,
        days=(until - since).days + 1,
        events=len(event_ids),
        incidents=len(incident_ids),
        records=len(record_ids),
        sources=len(source_ids),
        pool_records=coverage[0],
        pool_sources=coverage[1],
        event_ids=event_ids,
        incident_ids=incident_ids,
        record_ids=record_ids,
        source_ids=source_ids,
    )


class TrendArithmetic(StrictModel):
    recent_days: int
    min_events: int
    min_ratio: float
    coverage_drift: float
    blocks: list[TrendBlock]
    baseline_mean: float
    baseline_max: int
    baseline_pool_mean: float
    change: float
    per_event_recent: float
    per_event_baseline: float
    ratio: Optional[float] = None

    @property
    def recent(self) -> TrendBlock:
        return self.blocks[-1]

    @property
    def baseline(self) -> list[TrendBlock]:
        return self.blocks[:-1]

    @property
    def share_recent(self) -> float:
        return block_ratio(self.recent.events, self.recent.pool_records)

    @property
    def share_baseline(self) -> Optional[float]:
        rates = [
            block.events / block.pool_records
            for block in self.baseline
            if block.pool_records > 0
        ]
        if not rates:
            return None
        return rounded(sum(rates) / len(rates))

    @property
    def share_ratio(self) -> Optional[float]:
        rates = [
            block.events / block.pool_records
            for block in self.baseline
            if block.pool_records > 0
        ]
        base = sum(rates) / len(rates) if rates else 0.0
        if not base or not self.recent.pool_records:
            return None
        return rounded((self.recent.events / self.recent.pool_records) / base)

    @model_validator(mode="after")
    def _the_numbers_are_the_windows_they_came_from(self) -> "TrendArithmetic":
        if self.recent_days < 1:
            raise ValueError("a window has to be at least one day long")
        if self.min_events < 1:
            raise ValueError("min_events has to be at least 1")
        if self.min_ratio < 1.0:
            raise ValueError("min_ratio has to be at least 1, or nothing is an increase")
        if self.coverage_drift < 1.0:
            raise ValueError("coverage_drift has to be at least 1")
        if len(self.blocks) < MIN_BASELINE_BLOCKS + 1:
            raise ValueError(
                f"a comparison needs {MIN_BASELINE_BLOCKS} baseline window(s) beside the recent one"
            )
        for block in self.blocks:
            if block.days != self.recent_days:
                raise ValueError(
                    f"{block.since.isoformat()}..{block.until.isoformat()} is {block.days} day(s), not the "
                    f"{self.recent_days} the recent window measures, so the windows are not comparable"
                )
        for index in range(len(self.blocks) - 1):
            if self.blocks[index].until + timedelta(days=1) != self.blocks[index + 1].since:
                raise ValueError("the windows are not back to back, so one is missing from the series")
        baseline = self.baseline
        mean = sum(block.events for block in baseline) / len(baseline)
        pool_mean = sum(block.pool_records for block in baseline) / len(baseline)
        baseline_events = sum(block.events for block in baseline)
        baseline_incidents = sum(block.incidents for block in baseline)
        per_recent = block_ratio(self.recent.incidents, self.recent.events)
        per_baseline = block_ratio(baseline_incidents, baseline_events)
        ratio = None if mean == 0 else rounded(self.recent.events / mean)
        if not math.isclose(self.baseline_mean, rounded(mean), abs_tol=1e-9):
            raise ValueError("baseline_mean is not the mean of the baseline windows")
        if self.baseline_max != max(block.events for block in baseline):
            raise ValueError("baseline_max is not the busiest baseline window")
        if not math.isclose(self.baseline_pool_mean, rounded(pool_mean), abs_tol=1e-9):
            raise ValueError("baseline_pool_mean is not the mean record count of the baseline windows")
        if not math.isclose(self.change, rounded(self.recent.events - mean), abs_tol=1e-9):
            raise ValueError("change is not the recent window minus the baseline average")
        if self.ratio != ratio:
            raise ValueError("ratio is not the recent window over the baseline average")
        if not math.isclose(self.per_event_recent, per_recent, abs_tol=1e-9):
            raise ValueError("per_event_recent is not the recent window's reports per event")
        if not math.isclose(self.per_event_baseline, per_baseline, abs_tol=1e-9):
            raise ValueError("per_event_baseline is not the baseline's reports per event")
        return self


def block_ratio(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else rounded(part / whole)


@dataclass
class Windows:
    since: Optional[date] = None
    until: Optional[date] = None
    recent_days: int = RECENT_DAYS
    spans: list[tuple[date, date]] = field(default_factory=list)
    blocked: Optional[str] = None

    @property
    def recent(self) -> Optional[tuple[date, date]]:
        return self.spans[-1] if self.spans else None

    def describe(self) -> str:
        if self.blocked or not self.spans:
            return self.blocked or "no windows"
        first, last = self.spans[0], self.spans[-1]
        return (
            f"recent {last[0].isoformat()}..{last[1].isoformat()} ({self.recent_days} days) against "
            f"{len(self.spans) - 1} earlier window(s) of {self.recent_days} day(s) from "
            f"{first[0].isoformat()}"
        )


def spans_for(
    start: date, end: date, recent_days: int, blocks: int
) -> tuple[list[tuple[date, date]], Optional[str]]:
    recent = (end - timedelta(days=recent_days - 1), end)
    history = (recent[0] - start).days
    usable = max(0, min(blocks, history // recent_days))
    if usable < MIN_BASELINE_BLOCKS:
        return [], (
            f"the records read reach back {history + recent_days} day(s) to {start.isoformat()}, which "
            f"leaves {usable} complete window(s) of {recent_days} day(s) before the recent one - a "
            f"comparison needs at least {MIN_BASELINE_BLOCKS}, so no increase was measured"
        )
    spans = [recent]
    for step in range(1, usable + 1):
        high = recent[0] - timedelta(days=1 + recent_days * (step - 1))
        spans.append((high - timedelta(days=recent_days - 1), high))
    return sorted(spans), None


def windows_of(
    dated: Sequence[Unit], filters: Filters, recent_days: int, baseline_blocks: int
) -> Windows:
    days = [unit.day for unit in dated if unit.day is not None]
    end = filters.until or (max(days) if days else None)
    start = filters.since or (min(days) if days else None)
    if start is not None and end is not None and start > end:
        return Windows(
            since=start,
            until=end,
            recent_days=recent_days,
            blocked=f"the window given ends {end.isoformat()}, before it starts on {start.isoformat()}",
        )
    if not days:
        return Windows(
            recent_days=recent_days,
            blocked="no event the run counted carries a date, so nothing can sit in a window",
        )
    spans, blocked = spans_for(start, end, recent_days, baseline_blocks)
    return Windows(
        since=start, until=end, recent_days=recent_days, spans=spans, blocked=blocked
    )


def coverage_of(every: Iterable[Incident], spans: Sequence[tuple[date, date]]) -> list[tuple[int, int]]:
    records: list[set[str]] = [set() for _ in spans]
    sources: list[set[str]] = [set() for _ in spans]
    for incident in every:
        when, _basis = incident_when(incident)
        if when is None:
            continue
        day = when.date()
        for index, (low, high) in enumerate(spans):
            if low <= day <= high:
                records[index].add(incident.provenance.record_id)
                sources[index].add(incident.provenance.source_id)
                break
    return [(len(items), len(sources[index])) for index, items in enumerate(records)]


@dataclass
class Group:
    dimension: TrendDimension
    value: str
    title: Optional[str] = None
    family_id: Optional[str] = None
    units: list[Unit] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.dimension.value, self.value)

    def add(self, unit: Unit) -> None:
        if all(existing.event_id != unit.event_id for existing in self.units):
            self.units.append(unit)


def dimension_groups(units: Sequence[Unit], families: Sequence[IssueFamily]) -> list[Group]:
    index = {unit.event_id: unit for unit in units}
    found: dict[tuple[str, str], Group] = {}

    def group(dimension: TrendDimension, value: str, title: Optional[str], family_id: Optional[str] = None) -> Group:
        existing = found.get((dimension.value, fold(value)))
        if existing is None:
            existing = Group(
                dimension=dimension, value=fold(value), title=title, family_id=family_id
            )
            found[(dimension.value, fold(value))] = existing
        return existing

    for unit in units:
        for item in sorted(unit.categories, key=lambda value: value.value):
            group(TrendDimension.CATEGORY, item.value, item.value).add(unit)
        for item in sorted(unit.types, key=lambda value: value.value):
            group(TrendDimension.INCIDENT_TYPE, item.value, item.value).add(unit)
        for name, kind, _ids in unit.areas:
            group(TrendDimension.PLACE, f"{kind.value}:{name}", name).add(unit)
    for family in families:
        holder = group(
            TrendDimension.ISSUE_FAMILY, family.family_id, family.label, family.family_id
        )
        for event_id in family.event_ids:
            unit = index.get(event_id)
            if unit is not None:
                holder.add(unit)
    return [found[key] for key in sorted(found)]


@dataclass
class Candidate:
    trend_id: str
    group: Group
    units: list[Unit]
    shown: list[Unit]
    truncated: int
    arithmetic: TrendArithmetic
    window: tuple[date, date]

    @property
    def rank(self) -> tuple[float, int, str]:
        return (-(self.arithmetic.ratio or 0.0), -self.arithmetic.recent.events, self.trend_id)


def candidate_of(
    group: Group,
    spans: Sequence[tuple[date, date]],
    coverage: Sequence[tuple[int, int]],
    *,
    recent_days: int,
    min_events: int,
    min_ratio: float,
    coverage_drift: float,
) -> Optional[Candidate]:
    per_span: list[list[Unit]] = []
    for low, high in spans:
        per_span.append(
            [unit for unit in group.units if unit.day is not None and low <= unit.day <= high]
        )
    recent_units = per_span[-1]
    if len(recent_units) < min_events:
        return None
    baseline = per_span[:-1]
    mean = sum(len(items) for items in baseline) / len(baseline)
    if mean > 0 and len(recent_units) <= mean:
        return None
    blocks = [
        block_of(span, items, coverage[index])
        for index, (span, items) in enumerate(zip(spans, per_span))
    ]
    ordered = sorted(recent_units, key=lambda unit: (unit.when or OLDEST, unit.event_id), reverse=True)
    shown = ordered[:MAX_TREND_EVENTS]
    return Candidate(
        trend_id=trend_id_for(
            group.dimension,
            group.value,
            blocks[-1].since,
            blocks[-1].until,
            [unit.event_id for unit in recent_units],
        ),
        group=group,
        units=recent_units,
        shown=shown,
        truncated=len(recent_units) - len(shown),
        arithmetic=TrendArithmetic(
            recent_days=recent_days,
            min_events=min_events,
            min_ratio=min_ratio,
            coverage_drift=coverage_drift,
            blocks=blocks,
            baseline_mean=rounded(mean),
            baseline_max=max(len(items) for items in baseline),
            baseline_pool_mean=rounded(
                sum(block.pool_records for block in blocks[:-1]) / len(blocks[:-1])
            ),
            change=rounded(len(recent_units) - mean),
            per_event_recent=block_ratio(
                blocks[-1].incidents, blocks[-1].events
            ),
            per_event_baseline=block_ratio(
                sum(block.incidents for block in blocks[:-1]),
                sum(block.events for block in blocks[:-1]),
            ),
            ratio=None if mean == 0 else rounded(len(recent_units) / mean),
        ),
        window=(blocks[-1].since, blocks[-1].until),
    )


class TrendReading(StrictModel):
    title: Optional[str] = None
    pattern: Optional[str] = None
    one_problem: Optional[bool] = None
    explanations: list[str] = Field(default_factory=list)
    cited_event_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    method: ExtractionMethod = ExtractionMethod.LLM
    provider: str
    model: str
    prompt_version: str
    prompt_digest: str
    shown_hash: str
    decided_at: datetime


class Trend(StrictModel):
    schema_version: str = SCHEMA_VERSION
    trend_id: str
    dimension: TrendDimension
    value: str
    title: Optional[str] = None
    status: TrendStatus
    review: ReviewState
    findings: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    events: int = 0
    incidents: int = 0
    records: int = 0
    shown_events: int = 0
    references: list[EventReference] = Field(default_factory=list)
    areas: list[IssueArea] = Field(default_factory=list)
    geographic_precision: str = "unknown"
    first_event: Optional[date] = None
    last_event: Optional[date] = None
    severities: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    incident_types: list[str] = Field(default_factory=list)
    arithmetic: TrendArithmetic
    family_id: Optional[str] = None
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0
    reading: Optional[TrendReading] = None
    method: ExtractionMethod = ExtractionMethod.STATISTICAL
    provider: str
    model: str
    prompt_version: str
    prompt_digest: str
    shown_hash: str
    decided_at: datetime

    @model_validator(mode="after")
    def _a_trend_is_the_numbers_and_what_they_allow(self) -> "Trend":
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
        if self.events != self.arithmetic.recent.events:
            raise ValueError("a trend counts the events its recent window counted")
        if self.event_ids != sorted(self.arithmetic.recent.event_ids):
            raise ValueError("a trend lists the events its recent window lists")
        if self.incident_ids != sorted(self.arithmetic.recent.incident_ids):
            raise ValueError("a trend lists the incidents its recent window listed")
        if self.record_ids != sorted(self.arithmetic.recent.record_ids):
            raise ValueError("a trend lists the records its recent window listed")
        if self.trend_id != trend_id_for(
            self.dimension,
            self.value,
            self.arithmetic.recent.since,
            self.arithmetic.recent.until,
            self.event_ids,
        ):
            raise ValueError(f"trend id {self.trend_id!r} is not the one its window and events derive")
        if not self.event_ids:
            raise ValueError("a trend without events in its recent window measures nothing")
        if self.shown_events > self.events:
            raise ValueError("a reading cannot have been shown more events than the window holds")
        if len(self.references) != self.incidents:
            raise ValueError("every incident in the recent window carries one reference with its own source")
        if {item.event_id for item in self.references} != set(self.event_ids):
            raise ValueError("every event the trend counts carries at least one reference")
        stamps = [item.when for item in self.references if item.when is not None]
        if stamps:
            low, high = min(stamps).date(), max(stamps).date()
            if self.first_event != low or self.last_event != high:
                raise ValueError("first_event and last_event are not the window's own earliest and latest")
        elif self.first_event is not None or self.last_event is not None:
            raise ValueError("a trend with no dated reference cannot name a date")
        if self.review is ReviewState.UNRESOLVED:
            raise ValueError("a trend is alert-eligible or potential; it is never left unresolved")
        if (self.status is TrendStatus.ALERT_ELIGIBLE) != (self.review is ReviewState.ACCEPTED):
            raise ValueError("only an alert-eligible trend is accepted; a potential one waits for a person")
        expected = rules(
            self.arithmetic,
            deduplicated=self.deduplicated,
            undecided_links=self.undecided_links,
            references=self.references,
            reading=self.reading,
            generated=self.generated(),
        )
        if self.status is TrendStatus.ALERT_ELIGIBLE:
            if self.findings:
                raise ValueError("an alert-eligible trend carries a finding, so it is not eligible")
            if expected:
                raise ValueError(
                    "an alert-eligible trend does not meet every requirement: " + "; ".join(expected)
                )
        elif not self.findings:
            raise ValueError("a potential trend says what keeps it from being alert-eligible")
        missing = [item for item in expected if item not in self.findings]
        if missing:
            raise ValueError("the findings do not name: " + "; ".join(missing))
        if self.reading is not None and self.provider == NOT_ASKED:
            raise ValueError("a trend with a reading names the provider that gave it")
        return self

    def generated(self) -> list[Optional[str]]:
        texts: list[Optional[str]] = [self.title]
        if self.reading is not None:
            texts += [self.reading.title, self.reading.pattern, *self.reading.explanations]
        return texts


def numeric_rules(
    arithmetic: TrendArithmetic, deduplicated: bool, undecided_links: int
) -> list[str]:
    found: list[str] = []
    if not deduplicated:
        found.append(
            "no link decisions were available, so repeated reports of one happening may be counted as "
            "separate events in every window"
        )
    if undecided_links:
        found.append(
            f"{undecided_links} link decision(s) wait for a person, so two happenings that may be one are "
            "counted as two"
        )
    empty = [block for block in arithmetic.blocks if block.pool_records == 0]
    if empty:
        found.append(
            "no record of any kind was read for "
            + ", ".join(f"{block.since.isoformat()}..{block.until.isoformat()}" for block in empty)
            + ", so nothing was counted in that window rather than nothing happening"
        )
    if arithmetic.baseline_mean == 0:
        found.append(
            f"the baseline windows hold none of these events, so {arithmetic.recent.events} event(s) now is "
            "a new appearance or newly covered reporting rather than a measured increase"
        )
    elif arithmetic.ratio is not None and arithmetic.ratio < arithmetic.min_ratio:
        found.append(
            f"the recent window is {arithmetic.ratio:.2f} times its baseline average, under the "
            f"{arithmetic.min_ratio:.2f} threshold"
        )
    if arithmetic.recent.events < arithmetic.min_events:
        found.append(
            f"the recent window holds {arithmetic.recent.events} distinct event(s), under the "
            f"{arithmetic.min_events} an alert needs"
        )
    base = arithmetic.share_baseline
    share = arithmetic.share_ratio
    if base and share is not None and share < arithmetic.min_ratio:
        found.append(
            f"these events are {arithmetic.share_recent:.2f} of the records read in the recent window "
            f"against {base:.2f} in its baseline, a corrected rise of "
            f"{share:.2f} times under the {arithmetic.min_ratio:.2f} the raw one reached, so what grew may "
            "be how much the run read rather than what happened"
        )
    if (
        arithmetic.per_event_baseline > 0
        and arithmetic.per_event_recent >= arithmetic.coverage_drift * arithmetic.per_event_baseline
    ):
        found.append(
            f"the recent window carries {arithmetic.per_event_recent:.2f} reports per event against "
            f"{arithmetic.per_event_baseline:.2f} in its baseline, so more reporters may describe the same "
            "happenings"
        )
    before: set[str] = set()
    for block in arithmetic.baseline:
        before |= set(block.source_ids)
    new = sorted(set(arithmetic.recent.source_ids) - before)
    if new and arithmetic.baseline_mean > 0:
        found.append(
            "publishers " + ", ".join(new) + " report in the recent window and not in its baseline, so a "
            "rise in coverage cannot be told from a rise in events"
        )
    return found


def evidence_rules(
    arithmetic: TrendArithmetic,
    references: Sequence[EventReference],
    reading: Optional[TrendReading],
) -> list[str]:
    found: list[str] = []
    need = max(MIN_QUOTED_EVENTS, arithmetic.min_events)
    quoted = {item.event_id for item in references if item.evidence_ids}
    if len(quoted) < need:
        found.append(
            f"only {len(quoted)} of its events carry a quote from their own record, and an alert needs "
            f"{need}"
        )
    if reading is None:
        return found
    if reading.one_problem is False:
        found.append(
            "the reading answered that the events shown are different problems, so one rise across them "
            "is not one pattern"
        )
    if reading.confidence < TREND_THRESHOLD:
        found.append(
            f"the reading is held at confidence {reading.confidence:.2f}, below {TREND_THRESHOLD}"
        )
    if len(reading.cited_event_ids) < MIN_QUOTED_EVENTS:
        found.append("the reading named fewer than two of the events it was shown")
    return found


def rules(
    arithmetic: TrendArithmetic,
    *,
    deduplicated: bool,
    undecided_links: int,
    references: Sequence[EventReference],
    reading: Optional[TrendReading],
    generated: Sequence[Optional[str]],
) -> list[str]:
    found = numeric_rules(arithmetic, deduplicated, undecided_links)
    found += evidence_rules(arithmetic, references, reading)
    for word in claim_words_in(generated):
        found.append(
            f"the wording says {word!r}, which a measured increase never states - an explanation is "
            "offered as a reading, not as a cause"
        )
    return found


class TrendDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = None
    pattern: Optional[str] = None
    one_problem: Optional[bool] = None
    explanations: list[str] = Field(default_factory=list)
    cited_events: list[int] = Field(default_factory=list)
    confidence: Optional[float] = None


TREND_SCHEMA = json_schema_for(TrendDraft)

TREND_RULES = "\n".join(
    (
        "You are shown one district pattern: the distinct events that fall in the recent window, and the "
        "counts the pipeline already computed for the equal windows before it. Answer with one JSON object "
        "and nothing else.",
        "1. Every number printed was measured from the records. Nothing you write is used as a count, a "
        "rate, a date or a total, and no answer of yours can raise or lower what the measurements decided.",
        "2. one_problem says whether the events shown are one kind of problem or several. Answer false when "
        "they are several: a rise across mixed problems is not one pattern, even when every report names "
        "the same place, category or department.",
        "3. title names the pattern in a few words and pattern says in one sentence what the recent window "
        "shows beside its baseline. Use the events' own words for what is named, in whichever language they "
        "are written.",
        "4. Never add a place, a number, a date or a toll the shown events do not carry, and never restate a "
        "measured count as something you found.",
        "5. explanations lists what could account for the rise: a season or a festival, one ongoing problem "
        "newly reported, more reporters covering the district, a feed that only started filing. Each is a "
        "possible reading of the same numbers, not a finding.",
        "6. Never write that an increase is confirmed, was proved, or is caused by something. Say may, "
        "could, or fits instead - a coverage change and a real change look identical in these numbers.",
        "7. Name in cited_events only the numbered events printed below, and name at least two of them. "
        "Never invent an index and never name an event you were not shown.",
        "8. confidence is how sure you are of what you wrote, from 0.0 to 1.0; when you are guessing, "
        "answer below 0.5.",
        "9. Leave a field null rather than filling it with something the events do not say.",
    )
)

TREND_PROMPT_DIGEST = hashlib.sha256(
    (TREND_RULES + json.dumps(TREND_SCHEMA, sort_keys=True)).encode("utf-8")
).hexdigest()[:16]


def _window_line(arithmetic: TrendArithmetic) -> list[str]:
    lines = [
        f"{index + 1}. {block.since.isoformat()}..{block.until.isoformat()}: {block.events} distinct "
        f"event(s) from {block.incidents} incident(s) in {block.records} record(s) of "
        f"{block.sources} publisher(s); the run held {block.pool_records} record(s) of any kind there"
        for index, block in enumerate(arithmetic.blocks)
    ]
    lines.append(
        f"baseline average {arithmetic.baseline_mean:.2f} event(s), busiest baseline window "
        f"{arithmetic.baseline_max}, recent window {arithmetic.recent.events}, change "
        f"{arithmetic.change:+.2f}, recent over baseline "
        + (
            "not computed because the baseline holds none of these events"
            if arithmetic.ratio is None
            else f"{arithmetic.ratio:.2f} times"
        )
    )
    lines.append(
        f"An alert would need at least {arithmetic.min_events} distinct event(s) in the recent window and a "
        f"rise of {arithmetic.min_ratio:.2f} times its baseline average; windows the run read no records for "
        f"refuse an alert at any rise."
    )
    lines.append(
        "corrected for how many records the run read in each window "
        + (
            "the baseline windows held no records, so no correction is possible"
            if arithmetic.share_baseline is None
            else "the baseline windows hold none of these events, so there is nothing to correct against"
            if not arithmetic.share_baseline
            else f"these events are {arithmetic.share_recent:.2f} per record read recently against "
            f"{arithmetic.share_baseline:.2f} in its baseline, {arithmetic.share_ratio:.2f} times"
        )
    )
    return lines


def _recent_block(index: int, unit: Unit, rows: Sequence[tuple[str, str, str, str, str]]) -> str:
    lines = [f"Recent event {index} ({unit.event_id})"]
    lines.append(
        f"date: {unit.day.isoformat() if unit.day else 'no date'} (from {unit.when_basis})"
    )
    lines.append("incidents: " + ", ".join(unit.incident_ids))
    lines.append("records: " + ", ".join(unit.record_ids))
    lines.append(
        "publishers: " + ", ".join(sorted({item.provenance.source_id for item in unit.incidents}))
    )
    lines.append("sources: " + (", ".join(unit.source_urls) or "none named"))
    lines.append(
        "incident_type: " + (", ".join(sorted(item.value for item in unit.types)) or "not stated")
    )
    lines.append(
        "category: " + (", ".join(sorted(item.value for item in unit.categories)) or "not stated")
    )
    lines.append(
        "severity: " + (", ".join(sorted(item.value for item in unit.severities)) or "not stated")
    )
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


def trend_request(candidate: Candidate, *, deduplicated: bool) -> LLMRequest:
    arithmetic = candidate.arithmetic
    rows = shown_units(
        Bucket(
            bucket_id=candidate.trend_id,
            units=tuple(candidate.shown),
            signals=(),
            shown=tuple(candidate.shown),
        )
    )
    user = "\n".join(_window_line(arithmetic))
    user += "\n\n" + "\n\n".join(
        _recent_block(index, unit, rows) for index, unit in enumerate(candidate.shown, start=1)
    )
    extra = [
        f"Pattern: {candidate.group.dimension.value} = {candidate.group.value}",
        f"The recent window holds {len(candidate.units)} distinct event(s) and "
        f"{len(candidate.shown)} are printed here; interpret only what is printed.",
        "Every count above was measured by the pipeline from the records, never asked of you."
        + (
            " Repeated reports of one happening were merged into one event before counting."
            if deduplicated
            else " No link decisions were available, so two records may describe one happening and the "
            "counts can be inflated by coverage."
        ),
    ]
    if candidate.truncated:
        extra.append(
            f"{candidate.truncated} further event(s) fall in the same window but were not shown to you."
        )
    return LLMRequest(
        system=TREND_RULES,
        user=user + "\n\n" + "\n".join(extra),
        json_schema=TREND_SCHEMA,
        meta={"trend_id": candidate.trend_id, "prompt_version": TREND_PROMPT_VERSION},
    )


def _cited(draft: TrendDraft, shown: Sequence[Unit], findings: list[str]) -> list[str]:
    by_index = {index: unit for index, unit in enumerate(shown, start=1)}
    picked: list[str] = []
    for item in draft.cited_events:
        try:
            index = int(item)
        except (TypeError, ValueError):
            findings.append(f"the reading named event {item!r}, which is not a printed number")
            continue
        if index not in by_index:
            findings.append(f"the reading named event {index}, which was not shown to it")
            continue
        event_id = by_index[index].event_id
        if event_id not in picked:
            picked.append(event_id)
    return picked


def build_trend(
    candidate: Candidate,
    draft: Optional[TrendDraft],
    *,
    extras: list[str],
    deduplicated: bool,
    links: int,
    undecided_links: int,
    provider: str,
    model: str,
    shown: str,
    decided_at: datetime,
) -> Trend:
    units = sorted(candidate.units, key=lambda unit: (unit.when or OLDEST, unit.event_id))
    event_ids = sorted(unit.event_id for unit in units)
    incident_ids = sorted({key for unit in units for key in unit.incident_ids})
    record_ids = sorted({key for unit in units for key in unit.record_ids})
    source_urls = sorted({url for unit in units for url in unit.source_urls})
    findings = list(extras)
    generated = [candidate.group.title]
    references: list[EventReference] = []
    for unit in units:
        for incident in sorted(unit.incidents, key=lambda item: item.incident_id):
            items = shown_evidence(incident)
            references.append(
                EventReference(
                    event_id=unit.event_id,
                    incident_id=incident.incident_id,
                    record_id=incident.provenance.record_id,
                    source_id=incident.provenance.source_id,
                    source_url=incident.provenance.source_url,
                    when=unit.when,
                    when_basis=unit.when_basis,
                    evidence_ids=[item.evidence_id for item in items],
                    quotes=[item.quote for item in items],
                )
            )
    areas, precision = areas_of(units)
    reading: Optional[TrendReading] = None
    if draft is None:
        provider = NOT_ASKED
        model = NOT_ASKED
    else:
        cited = _cited(draft, candidate.shown, findings)
        confidence = draft.confidence
        if confidence is None or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            findings.append(f"the reading gave confidence {confidence!r}; {DEFAULT_CONFIDENCE} was used")
            confidence = DEFAULT_CONFIDENCE
        reading = TrendReading(
            title=clean(draft.title) or None,
            pattern=clean(draft.pattern) or None,
            one_problem=draft.one_problem,
            explanations=[clean(item) for item in draft.explanations if clean(item)],
            cited_event_ids=cited,
            confidence=confidence,
            provider=provider or "unknown",
            model=model or "unknown",
            prompt_version=TREND_PROMPT_VERSION,
            prompt_digest=TREND_PROMPT_DIGEST,
            shown_hash=shown,
            decided_at=decided_at,
        )
        generated += [reading.title, reading.pattern, *reading.explanations]
    arithmetic = candidate.arithmetic
    findings += rules(
        arithmetic,
        deduplicated=deduplicated,
        undecided_links=undecided_links,
        references=references,
        reading=reading,
        generated=generated,
    )
    stamps = [unit.day for unit in units if unit.day is not None]
    trend = Trend(
        trend_id=candidate.trend_id,
        dimension=candidate.group.dimension,
        value=candidate.group.value,
        title=candidate.group.title,
        status=TrendStatus.ALERT_ELIGIBLE if not findings else TrendStatus.POTENTIAL,
        review=ReviewState.ACCEPTED if not findings else ReviewState.REVIEW_REQUIRED,
        findings=findings,
        event_ids=event_ids,
        incident_ids=incident_ids,
        record_ids=record_ids,
        source_urls=source_urls,
        events=len(event_ids),
        incidents=len(incident_ids),
        records=len(record_ids),
        shown_events=len(candidate.shown),
        references=references,
        areas=areas,
        geographic_precision=precision,
        first_event=min(stamps) if stamps else None,
        last_event=max(stamps) if stamps else None,
        severities=sorted(
            {item.value for unit in units for item in unit.severities}
        ),
        categories=sorted({item.value for unit in units for item in unit.categories}),
        incident_types=sorted({item.value for unit in units for item in unit.types}),
        arithmetic=arithmetic,
        family_id=candidate.group.family_id,
        deduplicated=deduplicated,
        links=links,
        undecided_links=undecided_links,
        reading=reading,
        provider=provider or "unknown",
        model=model or "unknown",
        prompt_version=TREND_PROMPT_VERSION,
        prompt_digest=TREND_PROMPT_DIGEST,
        shown_hash=shown,
        decided_at=decided_at,
    )
    return trend


@dataclass
class TrendReport:
    filters: Filters = field(default_factory=Filters)
    windows: Windows = field(default_factory=Windows)
    recent_days: int = RECENT_DAYS
    baseline_blocks: int = BASELINE_BLOCKS
    min_events: int = MIN_TREND_EVENTS
    min_ratio: float = MIN_TREND_RATIO
    coverage_drift: float = COVERAGE_DRIFT
    eligible: int = 0
    skipped_review: int = 0
    excluded_by_filter: int = 0
    units: int = 0
    undated: int = 0
    outside: int = 0
    groups: int = 0
    candidates: int = 0
    asked: int = 0
    reused: int = 0
    truncated: int = 0
    trends: list[Trend] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None
    deduplicated: bool = False
    links: int = 0
    undecided_links: int = 0
    families: int = 0

    @property
    def alerts(self) -> list[Trend]:
        return [item for item in self.trends if item.status is TrendStatus.ALERT_ELIGIBLE]

    @property
    def potential(self) -> list[Trend]:
        return [item for item in self.trends if item.status is TrendStatus.POTENTIAL]

    @property
    def not_interpreted(self) -> int:
        return len([item for item in self.trends if item.reading is None])

    def limitations(self) -> list[str]:
        lines: list[str] = []
        if self.windows.blocked:
            lines.append(self.windows.blocked)
        if not self.deduplicated:
            lines.append(
                "no link decisions were available, so each accepted incident was counted as its own event "
                "and repeated reports of one happening count separately in every window"
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
                f"{self.undated} distinct event(s) carry no date the record or the reading gave, so they sit "
                "in no window and in no count"
            )
        if self.outside:
            lines.append(
                f"{self.outside} dated event(s) fall before the earliest window or after the recent one, so "
                "they are outside the comparison rather than absent"
            )
        if not self.families:
            lines.append(
                "no saved recurring issue was available, so patterns were counted only by the categories, "
                "event types and places the records themselves name"
            )
        if self.not_interpreted:
            lines.append(
                f"{self.not_interpreted} candidate increase(s) were measured without an interpretation, so "
                "no reading of what they mean was added to them"
            )
        if self.failed:
            lines.append(f"{len(self.failed)} interpretation request(s) got no usable answer")
        if self.truncated:
            lines.append(
                f"{self.truncated} event(s) sit in a recent window but were beyond the "
                f"{MAX_TREND_EVENTS} printed in one request, so a reading may cover less than the window"
            )
        lines.append(
            "a counted increase is arithmetic over the events the records evidenced; an explanation beside "
            "it is a model's reading of the same numbers, never a recorded cause"
        )
        lines.append(
            "these counts cover only the records this run read, so an empty window says nothing about a "
            "district that was never reported on"
        )
        return lines

    def counts(self) -> dict[str, Any]:
        return {
            "Filters": self.filters.describe(),
            "Windows": self.windows.describe(),
            "Eligible incidents": self.eligible,
            "Left out (waiting for a person)": self.skipped_review,
            "Left out (outside the filters)": self.excluded_by_filter,
            "Distinct events dated": self.units,
            "Events with no date": self.undated,
            "Events outside the windows": self.outside,
            "Patterns counted": self.groups,
            "Candidate increases": self.candidates,
            "Interpreted now": self.asked,
            "Reused decisions": self.reused,
            "Measured without a reading": self.not_interpreted,
            "Potential trends": len(self.potential),
            "Alert-eligible trends": len(self.alerts),
        }


def emerging_trends(
    incidents: Iterable[Incident],
    *,
    chain_factory: Callable[[], Chain],
    filters: Optional[Filters] = None,
    recent_days: int = RECENT_DAYS,
    baseline_blocks: int = BASELINE_BLOCKS,
    min_events: int = MIN_TREND_EVENTS,
    min_ratio: float = MIN_TREND_RATIO,
    coverage_drift: float = COVERAGE_DRIFT,
    families: Sequence[IssueFamily] = (),
    links: Sequence[EventLink] = (),
    store: Optional[TrendStore] = None,
    now: Optional[datetime] = None,
) -> TrendReport:
    every = list(incidents)
    applied = filters or Filters()
    candidates: list[Trend] = []
    report = TrendReport(
        filters=applied,
        recent_days=recent_days,
        baseline_blocks=baseline_blocks,
        min_events=min_events,
        min_ratio=min_ratio,
        coverage_drift=coverage_drift,
        deduplicated=bool(links),
        links=len(links),
        undecided_links=len(
            [link for link in links if link.review is ReviewState.REVIEW_REQUIRED]
        ),
        families=len(families),
    )
    incident_pool = [item for item in every if item.record_kind is RecordKind.INCIDENT]
    report.skipped_review = len([item for item in incident_pool if item.review_required])
    kept = eligible(incident_pool)
    inside = [item for item in kept if applied.keeps(item)]
    report.eligible = len(kept)
    report.excluded_by_filter = len(kept) - len(inside)
    units = event_units(inside, links)
    report.undated = len([unit for unit in units if unit.when is None])
    dated = [unit for unit in units if unit.when is not None]
    report.units = len(dated)
    windows = windows_of(dated, applied, recent_days, baseline_blocks)
    report.windows = windows
    if windows.blocked or not windows.spans:
        return report
    low, high = windows.spans[0][0], windows.spans[-1][1]
    report.units = len([unit for unit in dated if low <= unit.day <= high])
    report.outside = len(dated) - report.units
    coverage = coverage_of(every, windows.spans)
    groups = dimension_groups(
        [unit for unit in dated if unit.day is not None and low <= unit.day <= high], families
    )
    report.groups = len(groups)
    measured: list[Candidate] = []
    for group in groups:
        found = candidate_of(
            group,
            windows.spans,
            coverage,
            recent_days=recent_days,
            min_events=min_events,
            min_ratio=min_ratio,
            coverage_drift=coverage_drift,
        )
        if found is not None:
            measured.append(found)
    measured.sort(key=lambda item: item.rank)
    report.candidates = len(measured)
    report.truncated = sum(item.truncated for item in measured)
    decided_at = now or datetime.now(timezone.utc)
    chain: Optional[Chain] = None
    for candidate in measured:
        request = trend_request(candidate, deduplicated=report.deduplicated)
        shown = shown_hash(request)
        saved = store.reusable(candidate.trend_id, shown) if store is not None else None
        if saved is not None:
            candidates.append(saved)
            report.reused += 1
            continue
        draft: Optional[TrendDraft] = None
        provider = model = ""
        extras: list[str] = []
        if report.stop_reason is not None:
            extras.append(f"no interpretation was asked: {report.stop_reason}")
        elif report.asked >= MAX_TREND_REQUESTS:
            extras.append(
                f"no interpretation was asked for it - this run interprets at most "
                f"{MAX_TREND_REQUESTS} candidate increase(s)"
            )
        else:
            if chain is None:
                try:
                    chain = chain_factory()
                except LLMError as error:
                    report.stop_reason = str(error)
                    extras.append(f"no interpretation was available: {error}")
            if chain is not None:
                try:
                    answered = chain.read(lambda provider_: provider_.generate_structured(request))
                except ChainExhaustedError as error:
                    report.stop_reason = str(error)
                    extras.append(f"no interpretation was available: {error}")
                except LLMError as error:
                    report.failed.append(f"{candidate.trend_id}: {error}")
                    extras.append(f"no interpretation was available: {error}")
                else:
                    report.asked += 1
                    provider, model = answered.identity
                    try:
                        draft = TrendDraft.model_validate(answered.result.payload)
                    except ValidationError as error:
                        report.failed.append(
                            f"{candidate.trend_id}: the answer is not a reading "
                            f"({error.error_count()} problem(s))"
                        )
                        extras.append("the answer was not a readable interpretation")
        candidates.append(
            build_trend(
                candidate,
                draft,
                extras=extras,
                deduplicated=report.deduplicated,
                links=report.links,
                undecided_links=report.undecided_links,
                provider=provider,
                model=model,
                shown=shown,
                decided_at=decided_at,
            )
        )
    report.trends = candidates
    if store is not None:
        store.save(report)
    return report


class TrendStore:
    def __init__(self, directory: Any) -> None:
        self.directory = Path(str(directory))
        self.trends_path = self.directory / TRENDS_FILENAME
        self.summary_path = self.directory / TREND_SUMMARY_FILENAME
        self.by_id: dict[str, Trend] = {}

    def load(self) -> "TrendStore":
        if not self.trends_path.exists():
            return self
        try:
            text = self.trends_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{self.trends_path} could not be read: {error}") from error
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                trend = Trend.model_validate_json(line)
            except ValidationError as error:
                problem = error.errors()[0].get("msg", "invalid")
                raise CheckpointError(
                    f"{self.trends_path} line {number} is not a trend this pipeline wrote "
                    f"({problem}); the saved trends were not changed"
                ) from error
            self.by_id[trend.trend_id] = trend
        return self

    def reusable(self, trend_id: str, shown: str) -> Optional[Trend]:
        found = self.by_id.get(trend_id)
        if found is None:
            return None
        if (
            found.schema_version != SCHEMA_VERSION
            or found.prompt_digest != TREND_PROMPT_DIGEST
            or found.shown_hash != shown
        ):
            return None
        return found

    def save(self, report: TrendReport) -> None:
        for trend in report.trends:
            self.by_id[trend.trend_id] = trend
        lines = [
            json.dumps(
                self.by_id[key].model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
            + "\n"
            for key in sorted(self.by_id)
        ]
        _write_atomically(self.trends_path, "".join(lines))
        summary = {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": TREND_PROMPT_VERSION,
            "prompt_digest": TREND_PROMPT_DIGEST,
            "filters": report.filters.model_dump(mode="json"),
            "windows": {
                "recent_days": report.recent_days,
                "baseline_blocks": report.baseline_blocks,
                "min_events": report.min_events,
                "min_ratio": report.min_ratio,
                "coverage_drift": report.coverage_drift,
                "spans": [[low.isoformat(), high.isoformat()] for low, high in report.windows.spans],
                "blocked": report.windows.blocked,
            },
            "counts": report.counts(),
            "limitations": report.limitations(),
            "trends": [trend.model_dump(mode="json") for trend in report.trends],
        }
        _write_atomically(
            self.summary_path,
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def print_trends(report: TrendReport, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream or sys.stdout
    print(
        f"Emerging trends ({TREND_PROMPT_VERSION}, {TREND_PROMPT_DIGEST}, incident contract "
        f"{SCHEMA_VERSION})",
        file=out,
    )
    for label, value in report.counts().items():
        print(f"  {label}: {value}", file=out)
    if report.stop_reason:
        print(f"  Stopped: {report.stop_reason}", file=out)
    for line in report.failed:
        print(f"  failed {line}", file=out)
    for trend in report.trends:
        rise = (
            "from no baseline event"
            if trend.arithmetic.ratio is None
            else f"{trend.arithmetic.ratio:.2f}x baseline"
        )
        line = (
            f"  {trend.trend_id} {trend.dimension.value}={trend.value}: "
            f"{trend.events} distinct event(s) in {trend.arithmetic.recent.since}.."
            f"{trend.arithmetic.recent.until} against an average of "
            f"{trend.arithmetic.baseline_mean:.2f}, {rise}, {trend.status.value} "
            f"(confidence {trend.reading.confidence:.2f})"
            if trend.reading is not None
            else (
                f"  {trend.trend_id} {trend.dimension.value}={trend.value}: "
                f"{trend.events} distinct event(s) in {trend.arithmetic.recent.since}.."
                f"{trend.arithmetic.recent.until} against an average of "
                f"{trend.arithmetic.baseline_mean:.2f}, {rise}, {trend.status.value}"
            )
        )
        if trend.findings:
            line += " | " + "; ".join(trend.findings)
        print(line, file=out)
        if not detailed:
            continue
        if trend.reading is not None:
            reading = trend.reading
            print(
                f"    reading: {reading.title or 'untitled'} - "
                f"{reading.pattern or 'no pattern stated'}",
                file=out,
            )
            print(
                "    may fit instead: "
                + ("; ".join(reading.explanations) if reading.explanations else "none offered")
                + " (a reading of the numbers, not a record)",
                file=out,
            )
        for block in trend.arithmetic.blocks:
            print(
                f"    {block.since}..{block.until}: {block.events} event(s), {block.incidents} "
                f"incident(s), {block.records} record(s), {block.sources} publisher(s), "
                f"{block.pool_records} record(s) read",
                file=out,
            )
        print(
            "    places: "
            + (
                ", ".join(f"{area.name} ({area.level})" for area in trend.areas)
                or "none named by the records"
            ),
            file=out,
        )
        print(f"    at {trend.geographic_precision} precision", file=out)
        for item in trend.references:
            quote = item.quotes[0] if item.quotes else "no quote in the record"
            print(
                f"    {item.event_id} {item.record_id} ({item.source_id}): {quote}",
                file=out,
            )
    for line in report.limitations():
        print(f"  limitation: {line}", file=out)


__all__ = [
    "BASELINE_BLOCKS",
    "CLAIM_WORDS",
    "COVERAGE_DRIFT",
    "Candidate",
    "Group",
    "MAX_TREND_EVENTS",
    "MAX_TREND_REQUESTS",
    "MIN_BASELINE_BLOCKS",
    "MIN_QUOTED_EVENTS",
    "MIN_TREND_EVENTS",
    "MIN_TREND_RATIO",
    "NOT_ASKED",
    "RECENT_DAYS",
    "TREND_PROMPT_DIGEST",
    "TREND_PROMPT_VERSION",
    "TREND_RULES",
    "TREND_SCHEMA",
    "TREND_SUMMARY_FILENAME",
    "TREND_THRESHOLD",
    "TRENDS_FILENAME",
    "Trend",
    "TrendArithmetic",
    "TrendBlock",
    "TrendDimension",
    "TrendDraft",
    "TrendReading",
    "TrendReport",
    "TrendStatus",
    "TrendStore",
    "Windows",
    "block_of",
    "block_ratio",
    "build_trend",
    "candidate_of",
    "claim_words_in",
    "clean",
    "coverage_of",
    "dimension_groups",
    "emerging_trends",
    "evidence_rules",
    "fold",
    "numeric_rules",
    "print_trends",
    "rounded",
    "rules",
    "spans_for",
    "trend_id_for",
    "trend_request",
    "windows_of",
]
