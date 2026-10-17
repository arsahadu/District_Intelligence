from __future__ import annotations

import argparse
import enum
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from urllib.parse import quote

from pydantic import ValidationError

from intelligence.api_client import PostTransport
from intelligence.briefing import (
    DEFAULT_BRIEF_DAYS,
    MAX_BRIEF_DAYS,
    BriefLanguage,
    BriefReport,
    BriefStore,
    district_briefing,
    print_briefings,
)
from intelligence.checkpoint import (
    COMPLETED,
    DEFERRED,
    FAILED,
    REVIEW_REQUIRED,
    Checkpoint,
    CheckpointError,
    fingerprint,
)
from intelligence.contract import (
    EVENT_ONLY_FIELDS,
    Incident,
    IncidentCategory,
    RecordKind,
    ReviewState,
)
from intelligence.copilot import (
    MAX_QUESTIONS_PER_RUN,
    AnswerStore,
    AskReport,
    ask_copilot,
    print_answers,
)
from intelligence.fallback import (
    ENV_CHAIN_FILE,
    Chain,
    ChainExhaustedError,
    ChainFailedError,
    chain_configs,
    chain_named,
)
from intelligence.intelligence import (
    InvalidExtraction,
    PROMPT_DIGEST,
    PROMPT_VERSION,
    extract_incident,
)
from intelligence.linking import (
    EventLink,
    LinkReport,
    LinkStore,
    link_incidents,
    print_links,
)
from intelligence.llm import (
    ENV_PREFIX,
    LLMConfig,
    LLMError,
    LLMProvider,
    ProviderNotConfigured,
    QuotaExhaustedError,
    RateLimitedError,
    build_provider,
    load_dotenv_file,
)
from intelligence.models.enums import (
    Department,
    EventType,
    SeverityLevel,
    TimePrecision,
)
from intelligence.posting import (
    PostLedger,
    PostingError,
    post_all,
    print_postings,
)
from intelligence.records import CONTENT_PATH, MappingError
from intelligence.recurrence import (
    Filters,
    IssueFamily,
    IssueReport,
    IssueStore,
    print_issues,
    recurring_issues,
)
from intelligence.router import Mode, route
from intelligence.snapshot import (
    Baseline,
    Delta,
    IngestionSnapshot,
    NO_BASELINE,
    SnapshotError,
)
from intelligence.structured import MAPPING_VERSION, process_record as process_structured
from intelligence.trends import (
    BASELINE_BLOCKS,
    COVERAGE_DRIFT,
    MIN_BASELINE_BLOCKS,
    MIN_TREND_EVENTS,
    MIN_TREND_RATIO,
    RECENT_DAYS,
    Trend,
    TrendReport,
    TrendStore,
    emerging_trends,
    print_trends,
)

ENV_API_BASE_URL = "INTELLIGENCE_API_BASE_URL"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0

DEFAULT_LIMIT = 5

DEFAULT_BATCH_SIZE = 25

MAX_SEMANTIC_CONCURRENCY = 8

RECORDS_PATH = "/records"

# /incidents stores incidents: a forecast, a market line or any other context record is read, kept and
# answered like every other record, it just is not an incident, so only this kind is ever sent back
POSTABLE_KINDS = (RecordKind.INCIDENT,)

LINE_FIELDS = (
    ("kind", "record_kind"),
    ("context", "context_type"),
    ("type", "incident_type"),
    ("category", "category"),
    ("department", "department"),
    ("severity", "severity"),
    ("priority", "priority"),
    ("status", "event_status"),
    ("event_time", "event_time"),
)

# the six fields the contract refuses a value in on a contextual record; n/a is a display, not a state
NOT_APPLICABLE_WITHOUT_A_HAPPENING = ("incident_type", *EVENT_ONLY_FIELDS)

SUMMARY_CLIP = 40
SUMMARY_DECLARED_PLACES = 3

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BACKEND = 2
EXIT_PROVIDER = 3
EXIT_DEFERRED = 4
EXIT_CHECKPOINT = 5
EXIT_POST = 6

CATEGORY_CHOICES = tuple(item.value for item in IncidentCategory)
INCIDENT_TYPE_CHOICES = tuple(item.value for item in EventType)
DEPARTMENT_CHOICES = tuple(item.value for item in Department)
SEVERITY_CHOICES = tuple(
    item.value for item in SeverityLevel if item is not SeverityLevel.UNRESOLVED
)
BRIEF_LANGUAGE_CHOICES = tuple(item.value for item in BriefLanguage)

Transport = Callable[[str, float], tuple[int, str]]


class BackendError(RuntimeError):
    ...


def prompt_version_for(mode: Mode) -> str:
    return MAPPING_VERSION if mode is Mode.STRUCTURED else PROMPT_VERSION


def prompt_digest_for(mode: Mode) -> str:
    return "" if mode is Mode.STRUCTURED else PROMPT_DIGEST


def provider_identity(
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    mode: Optional[Mode] = None,
) -> tuple[str, str]:
    name = model = ""
    if provider is not None:
        name = str(
            getattr(getattr(provider, "config", None), "provider", "")
            or getattr(provider, "name", "")
            or ""
        )
        try:
            model = str(provider.model or "")
        except Exception:
            model = ""
    if model:
        return name, model
    if mode is Mode.STRUCTURED:
        return "", ""
    try:
        settings = config if config is not None else LLMConfig.from_env(
            None if environ is None else dict(environ)
        )
    except ProviderNotConfigured:
        return "", ""
    if not settings.model:
        return "", ""
    return str(settings.provider or ""), str(settings.model)


def process_record(
    record: Any,
    *,
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Incident:
    if route(record) is Mode.STRUCTURED:
        return process_structured(record, text_keys=text_keys, extra_keys=extra_keys)
    if provider is None:
        provider = build_provider(config, environ=environ)
    return extract_incident(
        record, provider=provider, text_keys=text_keys, extra_keys=extra_keys
    )


def api_base_url(environ: Optional[Mapping[str, str]] = None) -> str:
    if environ is not None:
        source: Mapping[str, str] = environ
    else:
        load_dotenv_file()
        source = os.environ
    raw = (source.get(ENV_API_BASE_URL) or "").strip() or DEFAULT_API_BASE_URL
    if not raw.startswith(("http://", "https://")):
        raise BackendError(
            f"{ENV_API_BASE_URL} must be an http(s) URL, got {raw!r}"
        )
    return raw.rstrip("/")


def _http_get(url: str, timeout_seconds: float) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _get_json(
    url: str,
    *,
    base_url: Optional[str],
    transport: Optional[Transport],
    timeout_seconds: float,
) -> Any:
    get = transport or _http_get
    try:
        status, body = get(url, timeout_seconds)
    except OSError as error:
        raise BackendError(
            f"{url} could not be reached ({getattr(error, 'reason', None) or error}); "
            f"is the backend running and is {ENV_API_BASE_URL} correct?"
        ) from error
    if status // 100 != 2:
        raise BackendError(f"{url} answered HTTP {status}: {body[:300]}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise BackendError(
            f"{url} answered HTTP {status} with a body that is not JSON: {body[:300]!r}"
        ) from error


def records_from_payload(payload: Any, *, url: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise BackendError(
            f"{url} returned {type(payload).__name__} where the records list should be"
        )
    records: list[dict[str, Any]] = []
    for position, item in enumerate(payload):
        if not isinstance(item, dict):
            raise BackendError(
                f"{url} returned {type(item).__name__} at position {position}, expected one "
                "CommonRecord object"
            )
        if not item.get("record_id"):
            raise BackendError(
                f"{url} returned the record at position {position} without a record_id, so no "
                "result could be attributed to it"
            )
        records.append(item)
    return records


def record_from_payload(payload: Any, *, url: str, record_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BackendError(
            f"{url} returned {type(payload).__name__} where one CommonRecord object should be"
        )
    if payload.get("record_id") != record_id:
        raise BackendError(
            f"{url} was asked for {record_id!r} and returned "
            f"{payload.get('record_id')!r}; results are not matched to the request"
        )
    return payload


def fetch_records(
    *,
    base_url: Optional[str] = None,
    transport: Optional[Transport] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    url = (base_url or api_base_url()) + RECORDS_PATH
    return records_from_payload(
        _get_json(url, base_url=base_url, transport=transport, timeout_seconds=timeout_seconds),
        url=url,
    )


def fetch_record(
    record_id: str,
    *,
    base_url: Optional[str] = None,
    transport: Optional[Transport] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    url = f"{(base_url or api_base_url()) + RECORDS_PATH}/{quote(str(record_id), safe='')}"
    payload = _get_json(url, base_url=base_url, transport=transport, timeout_seconds=timeout_seconds)
    return record_from_payload(payload, url=url, record_id=record_id)


@dataclass
class Outcome:
    record_id: str
    source_type: str
    record_type: str
    incident: Optional[Incident] = None
    error: Optional[str] = None
    mode: Mode = Mode.SEMANTIC
    deferred: bool = False
    reused: bool = False
    deferred_kind: str = ""
    never_sent: bool = False
    answered_by: str = ""
    attempts: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return self.incident is None

    @property
    def fell_back(self) -> bool:
        return bool(self.attempts)

    @property
    def state(self) -> str:
        if self.incident is not None:
            return REVIEW_REQUIRED if self.review_required else COMPLETED
        return DEFERRED if self.deferred else FAILED

    @property
    def contextual(self) -> bool:
        return (
            self.incident is not None
            and self.incident.record_kind is not RecordKind.INCIDENT
        )

    @property
    def review_required(self) -> bool:
        return self.incident is not None and self.incident.review_required

    def describe(self) -> str:
        if self.incident is None:
            return f"{self.record_id} {'deferred' if self.deferred else 'failed'}: {self.error}"
        line = f"{describe_incident(self.incident)} | path={self.mode.value}"
        if self.fell_back:
            line += f" | read by={self.answered_by} after {len(self.attempts)} turn(s)"
        return line


@dataclass
class RunSummary:
    base_url: str
    fetched: int
    selected: int = 0
    skipped: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None
    stop_kind: str = ""
    store: Optional[Checkpoint] = None
    outcomes: list[Outcome] = field(default_factory=list)
    reused: list[Outcome] = field(default_factory=list)
    chain: Optional[Chain] = None
    baseline: str = ""
    newly_ingested: Optional[int] = None
    already_ingested: Optional[int] = None
    awaiting_review: Optional[int] = None
    left_by_limit: Optional[int] = None
    duplicate_rows: Optional[int] = None

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    @property
    def processed(self) -> int:
        return len([item for item in self.outcomes if not item.deferred])

    @property
    def attempted(self) -> int:
        return len(self.outcomes) - len(self.unread)

    @property
    def unread(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.deferred and item.never_sent]

    @property
    def incidents(self) -> list[Outcome]:
        return [item for item in self.outcomes if not item.failed and not item.contextual]

    @property
    def contextual(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.contextual]

    @property
    def accepted(self) -> list[Outcome]:
        return [
            item
            for item in self.outcomes
            if item.incident is not None and not item.review_required
        ]

    @property
    def accepted_incidents(self) -> list[Outcome]:
        return [item for item in self.accepted if not item.contextual]

    @property
    def accepted_context(self) -> list[Outcome]:
        return [item for item in self.accepted if item.contextual]

    @property
    def review_required(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.review_required]

    @property
    def failures(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.failed and not item.deferred]

    @property
    def deferred(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.deferred]

    @property
    def refused(self) -> list[Outcome]:
        return [item for item in self.deferred if not item.never_sent]

    @property
    def structured(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.mode is Mode.STRUCTURED]

    @property
    def semantic(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.mode is Mode.SEMANTIC]

    @property
    def reused_incidents(self) -> list[Outcome]:
        return [item for item in self.reused if not item.failed and not item.contextual]

    @property
    def reused_contextual(self) -> list[Outcome]:
        return [item for item in self.reused if item.contextual]

    @property
    def reused_review_required(self) -> list[Outcome]:
        return [item for item in self.reused if item.review_required]

    @property
    def postable(self) -> list[Outcome]:
        seen: set[str] = set()
        eligible: list[Outcome] = []
        for item in [*self.outcomes, *self.reused]:
            incident = item.incident
            if incident is None or incident.review_required:
                continue
            if incident.record_kind not in POSTABLE_KINDS:
                continue
            if incident.incident_id in seen:
                continue
            seen.add(incident.incident_id)
            eligible.append(item)
        return eligible

    @property
    def fallbacks(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.fell_back]

    @property
    def turns(self) -> list[str]:
        return [f"{item.record_id}: {turn}" for item in self.outcomes for turn in item.attempts]

    def answered_by(self) -> dict[str, int]:
        return self._by_reader(self.outcomes)

    def reused_answered_by(self) -> dict[str, int]:
        return self._by_reader(self.reused)

    @staticmethod
    def _by_reader(items: Sequence["Outcome"]) -> dict[str, int]:
        found: dict[str, int] = {}
        for item in items:
            if item.incident is not None and item.answered_by:
                found[item.answered_by] = found.get(item.answered_by, 0) + 1
        return dict(sorted(found.items(), key=lambda row: (-row[1], row[0])))

    def buckets(self) -> dict[str, int]:
        return {
            "accepted incidents": len(self.accepted_incidents),
            "accepted context": len(self.accepted_context),
            "review-required": len(self.review_required),
            "failed": len(self.failures),
            "deferred (refused)": len(self.refused),
        }

    def accounting(self) -> str:
        unselected = self.fetched - self.selected
        first = f"{self.fetched} fetched = {self.selected} selected"
        first += (
            f" + {unselected} not selected (--limit/--record-id)" if unselected
            else " (every record the API returned)"
        )
        second = f"{self.selected} selected = {len(self.reused)} reused + {self.attempted} attempted"
        if self.unread:
            second += f" + {len(self.unread)} never sent"
        parts = " + ".join(f"{count} {label}" for label, count in self.buckets().items() if count)
        third = f"{self.attempted} attempted = {parts or 'nothing'}"
        return "; ".join((first, second, third))

    def counts(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "Backend URL": self.base_url,
            "Records fetched": self.fetched,
        }
        if self.baseline:
            found["Ingestion baseline"] = self.baseline
        if self.newly_ingested is not None:
            found["Records newly ingested"] = self.newly_ingested
        if self.already_ingested is not None:
            found["Records ingested earlier"] = self.already_ingested
        if self.awaiting_review:
            found["Records already awaiting a decision"] = self.awaiting_review
        found["Records selected"] = self.selected
        if self.left_by_limit:
            found["Records left for the next run"] = self.left_by_limit
        if self.duplicate_rows:
            found["Records the backend returned twice"] = self.duplicate_rows
        found["Records reused"] = len(self.reused)
        found["Records attempted"] = self.attempted
        found["Structured (no model)"] = len(self.structured)
        found["Narrative (model needed)"] = len(self.semantic)
        if self.chain is not None:
            found["Model chain"] = self.chain.describe()
            if len(self.chain.candidates) > 1:
                found["Chain scopes"] = self.chain.scopes_note()
            for note in self.chain.problems:
                found["Candidate dropped"] = note
        if answered := self.answered_by():
            found["Read by"] = ", ".join(f"{label} ({count})" for label, count in answered.items())
        if before := self.reused_answered_by():
            found["Read by an earlier run"] = ", ".join(
                f"{label} ({count})" for label, count in before.items()
            )
        if self.fallbacks:
            found["Read by a fallback"] = len(self.fallbacks)
        for label, count in self.buckets().items():
            found[label.capitalize()] = count
        found["Deferred (never sent)"] = len(self.unread)
        if self.reused:
            found["Reused incidents"] = len(self.reused_incidents)
            found["Reused contextual"] = len(self.reused_contextual)
            found["Reused review-required"] = len(self.reused_review_required)
        found["Accounting"] = self.accounting()
        if self.store is None:
            found["Checkpoint"] = "disabled (set --state-dir to persist and resume)"
        else:
            found["Checkpoint"] = str(self.store.checkpoint_path)
            found["Results"] = str(self.store.results_path)
        return found


def _clip(value: Any, limit: int) -> str:
    flat = " ".join(str("" if value is None else value).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def describe_incident(incident: Incident) -> str:
    title = " ".join((incident.title or "-").split())
    if len(title) > 60:
        title = title[:59] + "..."
    parts = [incident.incident_id, f"title={title}"]
    contextual = incident.record_kind in (RecordKind.FORECAST, RecordKind.CONTEXT)
    for label, name in LINE_FIELDS:
        if contextual and name in NOT_APPLICABLE_WITHOUT_A_HAPPENING:
            parts.append(f"{label}=n/a")
            continue
        value = getattr(incident, name)
        if value is None:
            rendered = "-"
        elif isinstance(value, enum.Enum):
            rendered = value.value
        elif isinstance(value, datetime):
            if name == "event_time" and incident.event_time_precision is TimePrecision.DAY:
                # a day the record stated prints as the day it stated, never as a clock time it never named
                rendered = value.date().isoformat()
            else:
                rendered = value.isoformat()
        else:
            rendered = str(value)
        parts.append(f"{label}={rendered}")
    if incident.context_facts:
        parts.append(f"facts={len(incident.context_facts)}")
        parts.append("values=" + ";".join(
            f"{fact.field.rsplit('.', 1)[-1]}={_clip(fact.value, SUMMARY_CLIP)}"
            for fact in incident.context_facts
        ))
    places = []
    for item in incident.locations:
        text = _clip(item.text, SUMMARY_CLIP)
        if text and text not in places:
            places.append(text)
    if places:
        parts.append("places=" + ";".join(places))
    else:
        declared = []
        for item in incident.source_geography:
            value = _clip(item.value, SUMMARY_CLIP)
            if value and value not in declared:
                declared.append(value)
        if declared:
            hidden = len(declared) - SUMMARY_DECLARED_PLACES
            parts.append(
                f"places=none in the account (declared: {';'.join(declared[:SUMMARY_DECLARED_PLACES])})"
                + (f" +{hidden} more" if hidden > 0 else "")
            )
    state = incident.validation.state.value
    findings = len(incident.validation.issues)
    parts.append(f"review={state}({findings})")
    return " | ".join(parts)


def run_record(
    record: Any,
    *,
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
    chain: Optional[Chain] = None,
) -> Outcome:
    payload = record if isinstance(record, Mapping) else {}
    record_id = str(payload.get("record_id") or "?")
    source_type = str(payload.get("source_type") or "")
    record_type = str(payload.get("record_type") or "")
    mode = route(record)

    def stopped(error: str, turns: Sequence[str] = ()) -> Outcome:
        return Outcome(
            record_id, source_type, record_type, error=error, mode=mode, attempts=tuple(turns)
        )

    def unread(reason: str, kind: str) -> Outcome:
        return Outcome(
            record_id,
            source_type,
            record_type,
            error=reason,
            mode=mode,
            deferred=True,
            deferred_kind=kind,
        )

    if chain is not None and mode is Mode.SEMANTIC:
        try:
            answered = chain.read(
                lambda asked: process_record(
                    record, provider=asked, text_keys=text_keys, extra_keys=extra_keys
                )
            )
        except ChainExhaustedError as error:
            return unread(f"no candidate to ask ({error.kind}): {error}", error.kind)
        except ChainFailedError as error:
            return stopped(f"every candidate refused: {error}", error.attempts)
        except MappingError as error:
            return stopped(f"record unreadable: {error}")
        except ValidationError as error:
            return stopped(f"contract rejected: {error}")
        return Outcome(
            record_id,
            source_type,
            record_type,
            incident=answered.result,
            mode=mode,
            answered_by=answered.label,
            attempts=answered.attempts,
        )

    try:
        incident = process_record(
            record,
            provider=provider,
            config=config,
            environ=environ,
            text_keys=text_keys,
            extra_keys=extra_keys,
        )
    except MappingError as error:
        return stopped(f"record unreadable: {error}")
    except InvalidExtraction as error:
        return stopped(f"extraction refused: {error}")
    except ValidationError as error:
        return stopped(f"contract rejected: {error}")
    except QuotaExhaustedError as error:
        return unread(f"provider quota exhausted: {error}", "quota")
    except RateLimitedError as error:
        return unread(f"provider rate limited: {error}", "rate")
    except LLMError as error:
        return stopped(f"provider failed: {error}")
    return Outcome(record_id, source_type, record_type, incident=incident, mode=mode)


@dataclass
class _Job:
    record: Mapping[str, Any]
    record_id: str
    mode: Mode
    prompt_version: str
    source_type: str = ""
    record_type: str = ""
    input_hash: Optional[str] = None
    prompt_digest: str = ""


def _job(record: Any) -> _Job:
    payload = record if isinstance(record, Mapping) else {}
    mode = route(record)
    return _Job(
        record=payload,
        record_id=str(payload.get("record_id") or "?"),
        mode=mode,
        prompt_version=prompt_version_for(mode),
        source_type=str(payload.get("source_type") or ""),
        record_type=str(payload.get("record_type") or ""),
        prompt_digest=prompt_digest_for(mode),
    )


def _answered_by(entry: Any) -> str:
    if entry is None or not getattr(entry, "model", ""):
        return ""
    provider = str(getattr(entry, "provider", "") or "")
    return f"{provider}/{entry.model}" if provider else str(entry.model)


def _chunks(items: Sequence[_Job], size: int) -> Iterator[Sequence[_Job]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _deferred(job: _Job, reason: str, kind: str = "") -> Outcome:
    return Outcome(
        job.record_id,
        job.source_type,
        job.record_type,
        error=reason,
        mode=job.mode,
        deferred=True,
        deferred_kind=kind,
        never_sent=True,
    )


def _collect(summary: RunSummary, job: _Job, outcome: Outcome) -> None:
    summary.add(outcome)
    if summary.store is None:
        return
    asked_provider, _, asked_model = outcome.answered_by.partition("/")
    summary.store.save(
        record_id=job.record_id,
        state=outcome.state,
        mode=job.mode.value,
        prompt_version=job.prompt_version,
        input_hash=job.input_hash,
        incident=(
            outcome.incident.to_storage_document() if outcome.incident is not None else None
        ),
        error=outcome.error,
        source_type=job.source_type,
        record_type=job.record_type,
        provider=asked_provider,
        model=asked_model,
        prompt_digest=job.prompt_digest,
        attempts=outcome.attempts,
    )


def _read_batch(
    jobs: Sequence[_Job],
    *,
    summary: RunSummary,
    provider: Optional[LLMProvider],
    config: Optional[LLMConfig],
    environ: Optional[Mapping[str, str]],
    concurrency: int,
    chain: Optional[Chain] = None,
) -> None:
    structured = [job for job in jobs if job.mode is Mode.STRUCTURED]
    semantic = [job for job in jobs if job.mode is Mode.SEMANTIC]

    def read(job: _Job) -> Outcome:
        return run_record(
            job.record,
            provider=provider,
            config=config,
            environ=environ,
            chain=chain,
        )

    for job in structured:
        _collect(summary, job, run_record(job.record, provider=None, config=config, environ=environ))

    if not semantic:
        return
    if summary.stop_reason:
        for job in semantic:
            _collect(summary, job, _deferred(job, summary.stop_reason, summary.stop_kind))
        return

    def stop(outcome: Outcome) -> None:
        if summary.stop_reason is None and outcome.deferred:
            summary.stop_reason = outcome.error
            summary.stop_kind = outcome.deferred_kind

    if concurrency == 1:
        for job in semantic:
            if summary.stop_reason:
                _collect(summary, job, _deferred(job, summary.stop_reason, summary.stop_kind))
                continue
            outcome = read(job)
            stop(outcome)
            _collect(summary, job, outcome)
        return

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="semantic") as pool:
        futures = [pool.submit(read, job) for job in semantic]
        positions = {future: position for position, future in enumerate(futures)}
        results: list[Optional[Outcome]] = [None] * len(semantic)
        for future in as_completed(futures):
            position = positions[future]
            job = semantic[position]
            try:
                outcome = future.result()
            except CancelledError:
                outcome = _deferred(job, summary.stop_reason or "", summary.stop_kind)
            except Exception as error:
                outcome = Outcome(
                    job.record_id, job.source_type, job.record_type,
                    error=f"reading failed: {error}", mode=job.mode,
                )
            results[position] = outcome
            if summary.stop_reason is None and outcome.deferred:
                stop(outcome)
                for later, other in enumerate(futures):
                    if results[later] is None and other.cancel():
                        results[later] = _deferred(
                            semantic[later], summary.stop_reason, summary.stop_kind
                        )

    for job, outcome in zip(semantic, results):
        _collect(
            summary, job, outcome or _deferred(job, summary.stop_reason or "", summary.stop_kind)
        )


def run_pipeline(
    *,
    limit: Optional[int] = DEFAULT_LIMIT,
    all_records: bool = False,
    record_id: Optional[str] = None,
    base_url: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    transport: Optional[Transport] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    store: Optional[Checkpoint] = None,
    resume: bool = True,
    semantic_concurrency: int = 1,
    chain: Optional[Chain] = None,
    chain_path: Optional[str] = None,
    records: Optional[Sequence[Mapping[str, Any]]] = None,
    ingestion: Optional[IngestionSnapshot] = None,
) -> RunSummary:
    if batch_size < 1:
        raise ValueError(f"--batch-size must be at least 1, got {batch_size}")
    if not 1 <= semantic_concurrency <= MAX_SEMANTIC_CONCURRENCY:
        raise ValueError(
            f"semantic concurrency must be between 1 and {MAX_SEMANTIC_CONCURRENCY}, got "
            f"{semantic_concurrency}"
        )

    origin = base_url or api_base_url(environ)
    delta: Optional[Delta] = None
    if records is not None:
        selected = list(records)
        fetched = len(selected)
    elif record_id is not None:
        selected = [
            fetch_record(
                record_id, base_url=origin, transport=transport,
                timeout_seconds=timeout_seconds,
            )
        ]
        fetched = 1
    else:
        listed = fetch_records(
            base_url=origin, transport=transport, timeout_seconds=timeout_seconds
        )
        fetched = len(listed)
        if ingestion is not None:
            delta = ingestion.delta(listed, limit=limit)
            selected = delta.records
        else:
            selected = listed if all_records or limit is None else listed[:limit]

    jobs = [_job(record) for record in selected]
    summary = RunSummary(base_url=origin, fetched=fetched, selected=len(jobs), store=store)
    if delta is not None and ingestion is not None:
        summary.baseline = ingestion.baseline
        summary.newly_ingested = delta.found
        summary.already_ingested = delta.already
        summary.awaiting_review = delta.pending
        summary.left_by_limit = delta.left
        summary.duplicate_rows = delta.duplicates

    if chain is not None:
        ask_chain = chain
    elif provider is not None:
        ask_chain = None
    elif chain_named(environ, chain_path):
        candidates, problems = chain_configs(config, environ=environ, chain_path=chain_path)
        ask_chain = Chain(candidates, problems=problems)
    else:
        ask_chain = None
    if ask_chain is not None:
        summary.chain = ask_chain

    if store is not None:
        store.base_url = origin
        if ask_chain is not None:
            store.candidates = ask_chain.identities
            store.provider, store.model = ask_chain.candidates[0].identity
        else:
            store.provider, store.model = provider_identity(provider, config, environ)
        for job in jobs:
            job.input_hash = fingerprint(job.record)
        if resume:
            todo = []
            for job in jobs:
                found = store.reusable(
                    record_id=job.record_id,
                    mode=job.mode.value,
                    prompt_version=job.prompt_version,
                    input_hash=job.input_hash,
                    prompt_digest=job.prompt_digest,
                )
                if found is not None:
                    summary.skipped.append(found.incident_id)
                    saved = store.get(job.record_id)
                    summary.reused.append(
                        Outcome(
                            job.record_id, job.source_type, job.record_type,
                            incident=found, mode=job.mode, reused=True,
                            answered_by=_answered_by(saved),
                        )
                    )
                    continue
                todo.append(job)
            jobs = todo

    if (
        provider is None
        and ask_chain is None
        and any(job.mode is Mode.SEMANTIC for job in jobs)
    ):
        provider = build_provider(config, environ=environ)
        if store is not None:
            store.provider, store.model = provider_identity(provider, config, environ)

    for batch in _chunks(jobs, batch_size):
        _read_batch(
            batch,
            summary=summary,
            provider=provider,
            config=config,
            environ=environ,
            concurrency=semantic_concurrency,
            chain=ask_chain,
        )
        if store is not None:
            store.flush()
    return summary


def print_report(summary: RunSummary, *, detailed: bool = False, stream: Any = None) -> None:
    out = stream if stream is not None else sys.stdout
    for label, value in summary.counts().items():
        print(f"{label}: {value}", file=out)

    def block(heading: str, items: Sequence[Outcome], note: str = "") -> None:
        if not items:
            return
        print(f"\n{heading} ({len(items)})", file=out)
        for item in items:
            print(f"  {item.describe()}", file=out)
        if note:
            print(f"  {note}", file=out)

    block("Accepted incidents", summary.accepted_incidents)
    block(
        "Accepted context/forecast",
        summary.accepted_context,
        "nothing in these evidenced an event, so they are filed as forecast or context and keep "
        "the values their own fields hold",
    )
    block(
        "Review-required",
        summary.review_required,
        "read, but a field is missing, conflicting or unsupported by the evidence, so the result "
        "waits for a person instead of being trusted as it stands",
    )
    stop_notes = {
        "quota": (
            "Deferred (provider quota)",
            "the provider said this account has no allowance left, so the run stopped sending rather "
            "than spend one refused request per record; they stay unprocessed in the checkpoint, not "
            "failed",
        ),
        "rate": (
            "Deferred (rate limited)",
            "the provider said this window is full, so the run stopped sending rather than spend one "
            "refused request per record; they stay unprocessed in the checkpoint, not failed",
        ),
        "auth": (
            "Deferred (credential refused)",
            "the provider refused the credential, so no model behind it can be read until it is "
            "fixed; the records stay unprocessed in the checkpoint, not failed",
        ),
        "unreachable": (
            "Deferred (endpoint unreachable)",
            "the endpoint could not be reached at all, so nothing was decided about these records; a "
            "later run asks again",
        ),
        "model": (
            "Deferred (no candidate left)",
            "every model this run was configured to ask refused to serve the read, so the records "
            "stay unprocessed in the checkpoint rather than failed",
        ),
    }
    heading, note = stop_notes.get(
        summary.stop_kind,
        (
            "Deferred (provider refused)",
            "not read, because the provider refused on its allowance: they stay unprocessed in "
            "the checkpoint rather than recorded as failures",
        ),
    )
    block(heading, summary.refused, note)
    block(
        "Left unread (never sent)",
        summary.unread,
        "the run had already stopped asking this provider, so no request was spent on these and "
        "nothing about them was decided",
    )
    block("Failures", summary.failures)
    block(
        "Reused from the checkpoint",
        summary.reused,
        "read by an earlier run and returned from the store, so nothing here was read again and no "
        "request was spent on it",
    )

    store = summary.store
    if store is not None:
        if store.incomplete:
            print(f"\nSaved progress was interrupted: {store.incomplete}", file=out)
        if store.stale_version:
            print(
                f"\n{store.checkpoint_path.name} was written for contract {store.stale_version}; each "
                "saved result is still matched on its own schema version before it is reused",
                file=out,
            )
        if store.previous_base_url and store.previous_base_url != summary.base_url:
            print(
                f"\nThe saved pair was written against {store.previous_base_url}, not "
                f"{summary.base_url}; a result is reused across that move only when the record's own "
                "fingerprint matches, so a reissued record is read again",
                file=out,
            )
        if store.invalidated:
            print(
                f"\nRead again rather than reused, {len(store.invalidated)} saved result(s):", file=out
            )
            for record_id, reason in sorted(store.invalidated.items()):
                print(f"  {record_id}: {reason}", file=out)
        if store.preserved:
            print(
                f"\nKept as an earlier run read them, {len(store.preserved)} result(s): a re-read that "
                "ended refused or deferred cannot erase a finished reading, so the saved Incident stays "
                "and is reported stale rather than overwritten",
                file=out,
            )
            for record_id, reason in sorted(store.preserved.items()):
                print(f"  {record_id}: {reason}", file=out)

    turns = summary.turns
    if turns:
        print(
            f"\nChain turns ({len(turns)}): why a model other than the first was asked, from what "
            "each candidate actually said",
            file=out,
        )
        for line in turns[:20]:
            print(f"  {line}", file=out)
        if len(turns) > 20:
            print(f"  ... {len(turns) - 20} more, each in its checkpoint entry", file=out)

    waiting = [*summary.review_required, *summary.reused_review_required]
    if waiting:
        print(
            "\nNeeds a person: " + ", ".join(item.incident.incident_id for item in waiting),
            file=out,
        )

    if summary.stop_reason:
        print(f"\nStopped asking the provider: {summary.stop_reason}", file=out)
    if summary.deferred and summary.store is not None:
        print(f"Re-run with --state-dir {summary.store.directory} to read them.", file=out)

    if not summary.outcomes:
        print(
            "\nEvery record selected was already read by an earlier run, so nothing was read again."
            if summary.skipped
            else "\nThe backend returned no records to process.",
            file=out,
        )
    if detailed:
        for item in [*summary.outcomes, *summary.reused]:
            if item.incident is None:
                continue
            print(
                "\n" + json.dumps(item.incident.to_storage_document(), ensure_ascii=False, indent=2),
                file=out,
            )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m intelligence.pipeline",
        description=(
            "Read CommonRecords and run the intelligence pipeline. --post-incidents opts in to "
            "writing accepted Incidents, forecasts and context records back."
        ),
    )
    selection = parser.add_argument_group("selection")
    selection.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            f"process this many records, taken from the front of the order the backend returned them; "
            f"which records those are is the API's decision and is not the same set next run "
            f"(default {DEFAULT_LIMIT}; use --all for every record)"
        ),
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="process every record the backend returns",
    )
    selection.add_argument(
        "--record-id",
        help="process only this record, read with GET /records/{record_id}",
    )
    selection.add_argument(
        "--newly-ingested",
        action="store_true",
        dest="newly_ingested",
        help=(
            "process only the records this state directory has never settled, in the order the backend "
            "returned them; needs --state-dir, and takes all of them unless --limit caps the set"
        ),
    )
    selection.add_argument(
        "--mark-baseline",
        action="store_true",
        dest="mark_baseline",
        help=(
            "settle the table into --state-dir without processing it: one GET /records, no model call, "
            "no reading, no posting. Every record whose arrival stamp is older than --baseline-older-than "
            "is written to ingested.json as already ingested; today's arrivals, records with no usable "
            "stamp and records still awaiting a decision are left selectable, so the next "
            "--newly-ingested run reads only what is actually new"
        ),
    )
    selection.add_argument(
        "--baseline-older-than",
        metavar="YYYY-MM-DD",
        dest="baseline_older_than",
        help=(
            "the date --mark-baseline cuts at, defaulting to today in UTC; a cutoff past today settles "
            "the whole table, including rows whose arrival stamp carries no clock"
        ),
    )
    bulk = parser.add_argument_group("bulk processing")
    bulk.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"records to read before progress is written (default {DEFAULT_BATCH_SIZE})",
    )
    bulk.add_argument(
        "--state-dir",
        metavar="DIR",
        help="write results and the checkpoint here so a run can resume; nothing is written without it",
    )
    bulk.add_argument(
        "--no-resume",
        action="store_true",
        help="reprocess records an earlier run left in --state-dir instead of reusing them",
    )
    bulk.add_argument(
        "--semantic-concurrency",
        type=int,
        default=1,
        help=(
            "narrative records to ask the provider about at once "
            f"(1-{MAX_SEMANTIC_CONCURRENCY}; default 1)"
        ),
    )
    chain = parser.add_argument_group("model chain")
    chain.add_argument(
        "--llm-chain",
        metavar="FILE",
        dest="llm_chain",
        help=(
            f"JSON file listing the models to try, in order, for a narrative record; without it the "
            f"single model {ENV_PREFIX}* names is the only one asked. Also settable as "
            f"{ENV_CHAIN_FILE}"
        ),
    )
    parser.add_argument("--json", action="store_true", help="also print each Incident in full")
    parser.add_argument(
        "--post-incidents",
        action="store_true",
        help="POST accepted Incidents, forecasts and context records to the platform API, newly "
             "read or reused from a checkpoint; review-required and unresolved results are not posted",
    )
    parser.add_argument(
        "--link-events",
        action="store_true",
        help="ask the model whether accepted incidents that share a type, place, person or category "
             "describe one event; links are kept beside the checkpoint in --state-dir and never merge "
             "or change an Incident",
    )
    recurring = parser.add_argument_group("recurring issues and emerging trends")
    recurring.add_argument(
        "--recurring-issues",
        action="store_true",
        help="ask the model which of the distinct events that share a place and a kind of problem are "
             "one issue that keeps happening; counts, areas and time ranges are computed from the "
             "records, and one request is made per candidate group. Repeated reports of one happening "
             "count once only when --link-events decisions are already saved in --state-dir",
    )
    recurring.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                           help="count only events dated on or after this date")
    recurring.add_argument("--until", type=date.fromisoformat, metavar="YYYY-MM-DD",
                           help="count only events dated on or before this date")
    recurring.add_argument("--category", choices=CATEGORY_CHOICES, metavar="CATEGORY",
                           help="count only incidents in this contract category")
    recurring.add_argument("--incident-type", choices=INCIDENT_TYPE_CHOICES, metavar="TYPE",
                           dest="incident_type", help="count only incidents of this event type")
    recurring.add_argument("--department", choices=DEPARTMENT_CHOICES, metavar="DEPARTMENT",
                           help="count only incidents assigned to this department")
    recurring.add_argument("--min-severity", choices=SEVERITY_CHOICES, metavar="SEVERITY",
                           dest="min_severity",
                           help="count only incidents at this severity or above; an unresolved severity "
                                "is never read as at least anything")
    recurring.add_argument("--place", metavar="TEXT",
                           help="count only incidents naming a place that contains this text, at any "
                                "level a record named")
    trends = parser.add_argument_group("emerging trends")
    trends.add_argument(
        "--emerging-trends",
        action="store_true",
        help="count the distinct events in a recent window against the equal windows immediately before "
             "it, per category, event type, place and saved recurring issue. Every window, count, mean and "
             "ratio is arithmetic over the records; one interpretation is asked per candidate increase and "
             "can only lower what the numbers decided. Repeated reports of one happening count once only "
             "when --link-events decisions are already saved in --state-dir",
    )
    trends.add_argument("--recent-days", type=int, metavar="DAYS", dest="recent_days",
                        help=f"how many days the recent window measures (default {RECENT_DAYS}); each "
                             f"baseline window is the same length so the counts stay comparable")
    trends.add_argument("--baseline-blocks", type=int, metavar="N", dest="baseline_blocks",
                        help=f"how many equal windows before the recent one are averaged as the baseline "
                             f"(default {BASELINE_BLOCKS}); history that holds fewer than "
                             f"{MIN_BASELINE_BLOCKS} of them measures no increase at all")
    trends.add_argument("--min-events", type=int, metavar="N", dest="min_events",
                        help=f"how many distinct events the recent window needs before a rise can be "
                             f"alert-eligible (default {MIN_TREND_EVENTS})")
    trends.add_argument("--min-rise", type=float, metavar="TIMES", dest="min_rise",
                        help=f"how many times its baseline average a window's events must be, both as "
                             f"counted and after correcting for how many records the run read there "
                             f"(default {MIN_TREND_RATIO})")
    trends.add_argument("--coverage-drift", type=float, metavar="TIMES", dest="coverage_drift",
                        help=f"how many times more reports per event, or reports from publishers the "
                             f"baseline never had, the recent window may carry before the rise is read as "
                             f"more coverage rather than more happening (default {COVERAGE_DRIFT})")
    copilot = parser.add_argument_group("collector copilot")
    copilot.add_argument(
        "--ask",
        action="append",
        metavar="QUESTION",
        help="answer this question from the records the run read, and repeat it for more; the question "
             "carries its own dates, places, category, type, department and severity, in Tamil, English "
             f"or mixed. Each one is read into a search and then answered, at most {MAX_QUESTIONS_PER_RUN} "
             "questions per run, and the search and the answer are reused from --state-dir when the "
             "records shown to the model are unchanged",
    )
    briefing = parser.add_argument_group("district briefing")
    briefing.add_argument(
        "--briefing",
        action="store_true",
        help="assemble the period this run read into one district briefing a Collector can read without "
             "being asked: the counts, window and places measured from the records, the saved recurring "
             "issues and measured increases that overlap it, the reports still waiting for a department "
             "and the actions the records themselves state, each with suggested actions beside it. One "
             "request phrases what was already measured; the briefing and its summary are kept beside "
             "the checkpoint in --state-dir and reused when the records shown are unchanged",
    )
    briefing.add_argument("--brief-days", type=int, metavar="DAYS", dest="brief_days",
                          help=f"how many days the period counts back from its end (default "
                               f"{DEFAULT_BRIEF_DAYS}); --since sets where the period starts instead")
    briefing.add_argument("--brief-language", choices=BRIEF_LANGUAGE_CHOICES, metavar="LANGUAGE",
                          dest="brief_language",
                          help="write the briefing's headline, lines and actions in this language "
                               f"(default {BriefLanguage.ENGLISH.value}); place names and quotations stay "
                               "in the script the record used")
    args = parser.parse_args(argv)

    if args.record_id is not None and (args.all or args.limit is not None):
        parser.error("--record-id selects one record; do not combine it with --limit or --all")
    if args.all and args.limit is not None:
        parser.error("--all already means every record; do not also set --limit")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1, or use --all")
    if args.newly_ingested and not args.state_dir:
        parser.error(
            "--newly-ingested compares the table against what the state directory has settled; give it "
            "--state-dir"
        )
    if args.newly_ingested and (args.all or args.record_id is not None):
        parser.error(
            "--newly-ingested already says which records to read: the ones this state directory has "
            "never settled. --all and --record-id each say that themselves"
        )
    if args.baseline_older_than is not None:
        if not args.mark_baseline:
            parser.error(
                "--baseline-older-than only sets where --mark-baseline cuts; without it no record is "
                "settled and no date is applied"
            )
        try:
            args.baseline_older_than = date.fromisoformat(args.baseline_older_than.strip())
        except ValueError:
            parser.error("--baseline-older-than must be a date written YYYY-MM-DD")
    if args.mark_baseline:
        if not args.state_dir:
            parser.error(
                "--mark-baseline writes the settled record ids into ingested.json, which lives in a "
                "state directory; give it --state-dir"
            )
        if args.all or args.record_id is not None or args.newly_ingested or args.limit is not None:
            parser.error(
                "--mark-baseline settles records without reading any of them, so --all, --record-id, "
                "--newly-ingested and --limit choose nothing here"
            )
        choosing = (
            ("--post-incidents", args.post_incidents),
            ("--link-events", args.link_events),
            ("--recurring-issues", args.recurring_issues),
            ("--emerging-trends", args.emerging_trends),
            ("--ask", args.ask),
            ("--briefing", args.briefing),
        )
        asked = [name for name, set_ in choosing if set_]
        if asked:
            parser.error(
                f"{' and '.join(asked)} work on the incidents a run produces; --mark-baseline produces "
                "none, it only settles which records count as already ingested"
            )
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if not 1 <= args.semantic_concurrency <= MAX_SEMANTIC_CONCURRENCY:
        parser.error(f"--semantic-concurrency must be between 1 and {MAX_SEMANTIC_CONCURRENCY}")
    if args.no_resume and not args.state_dir:
        parser.error("--no-resume only means something with --state-dir")
    issue_filter_names = (
        "since", "until", "category", "incident_type", "department", "min_severity", "place",
    )
    counted = args.recurring_issues or args.emerging_trends or args.briefing
    if args.ask and any(getattr(args, name) is not None for name in issue_filter_names):
        parser.error(
            "--ask reads its dates, places, category, type, department and severity out of the question, "
            "so --since, --until, --category, --incident-type, --department, --min-severity and --place "
            "would set the same search twice"
        )
    if any(getattr(args, name) is not None for name in issue_filter_names) and not counted:
        parser.error(
            "--since, --until, --category, --incident-type, --department, --min-severity and --place "
            "narrow the events a run counts; give --recurring-issues, --emerging-trends or --briefing for "
            "them to mean something. --ask needs none of them, since the question names what it searches "
            "for"
        )
    if (args.ask or args.briefing) and not (
        args.all or args.record_id or args.newly_ingested or args.limit is not None
    ):
        parser.error(
            f"--ask answers and --briefing writes from the records this run read, so say which to read: "
            f"--all for every record, --record-id for one, --newly-ingested "
            f"for what arrived since the last run, or --limit N to use "
            f"the first N instead of the default {DEFAULT_LIMIT}"
        )
    if args.since is not None and args.until is not None and args.until < args.since:
        parser.error("--until must be on or after --since")
    trend_setting_names = (
        "recent_days", "baseline_blocks", "min_events", "min_rise", "coverage_drift",
    )
    if any(getattr(args, name) is not None for name in trend_setting_names) and not args.emerging_trends:
        parser.error(
            "--recent-days, --baseline-blocks, --min-events, --min-rise and --coverage-drift set how an "
            "emerging trend is measured; give --emerging-trends for them to mean something"
        )
    if args.recent_days is not None and not 1 <= args.recent_days <= 365:
        parser.error("--recent-days must be between 1 and 365")
    if args.baseline_blocks is not None and args.baseline_blocks < MIN_BASELINE_BLOCKS:
        parser.error(
            f"--baseline-blocks must be at least {MIN_BASELINE_BLOCKS}, since one earlier window is not a "
            "baseline"
        )
    if args.min_events is not None and args.min_events < 2:
        parser.error("--min-events must be at least 2, since one event is not a rise")
    if args.min_rise is not None and args.min_rise < 1.0:
        parser.error("--min-rise must be at least 1.0, or nothing counts as an increase")
    if args.coverage_drift is not None and args.coverage_drift < 1.0:
        parser.error("--coverage-drift must be at least 1.0")
    brief_setting_names = ("brief_days", "brief_language")
    if any(getattr(args, name) is not None for name in brief_setting_names) and not args.briefing:
        parser.error(
            "--brief-days and --brief-language set the period and language of a district briefing; give "
            "--briefing for them to mean something"
        )
    if args.brief_days is not None and args.since is not None:
        parser.error(
            "--brief-days counts the period back from where it ends, and --since already says where it "
            "starts; give one of them"
        )
    if args.brief_days is not None and not 1 <= args.brief_days <= MAX_BRIEF_DAYS:
        parser.error(f"--brief-days must be between 1 and {MAX_BRIEF_DAYS}")
    args.recent_days = RECENT_DAYS if args.recent_days is None else args.recent_days
    args.baseline_blocks = BASELINE_BLOCKS if args.baseline_blocks is None else args.baseline_blocks
    args.min_events = MIN_TREND_EVENTS if args.min_events is None else args.min_events
    args.min_rise = MIN_TREND_RATIO if args.min_rise is None else args.min_rise
    args.coverage_drift = COVERAGE_DRIFT if args.coverage_drift is None else args.coverage_drift
    args.brief_days = DEFAULT_BRIEF_DAYS if args.brief_days is None else args.brief_days
    args.brief_language = BriefLanguage(
        args.brief_language or BriefLanguage.ENGLISH.value
    )
    return args


def issue_filters(args: argparse.Namespace) -> Filters:
    return Filters(
        since=args.since,
        until=args.until,
        category=args.category,
        incident_type=args.incident_type,
        department=args.department,
        min_severity=args.min_severity,
        place=args.place,
    )


def decided_links(args: argparse.Namespace, linked: Optional[LinkReport]) -> list[EventLink]:
    if linked is not None:
        return linked.links
    if args.state_dir:
        return list(LinkStore(args.state_dir).load().links.values())
    return []


def grouped_families(
    issues: Optional[IssueReport], store: Optional[IssueStore]
) -> list[IssueFamily]:
    if issues is not None:
        return issues.accepted
    if store is None:
        return []
    return [
        family
        for group in store.buckets.values()
        for family in group
        if family.review is ReviewState.ACCEPTED
    ]


def measured_trends(trends: Optional[TrendReport], store: Optional[TrendStore]) -> list[Trend]:
    if trends is not None:
        return [item for item in trends.trends if item.review is ReviewState.ACCEPTED]
    if store is None:
        return []
    return [item for item in store.by_id.values() if item.review is ReviewState.ACCEPTED]


def use_utf8_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def settled_ids(store: Optional[Checkpoint]) -> set[str]:
    if store is None:
        return set()
    # only a completed reading counts as settled here: a review_required document is one a person
    # still owes a decision on, so seeding from it would drop it out of the daily run
    return {record_id for record_id, entry in store.entries.items() if entry.state == COMPLETED}


def print_baseline(baseline: Baseline, snapshot: IngestionSnapshot, *, fetched: int) -> None:
    print(f"Baseline: {snapshot.path}")
    print(f"Arrival cutoff: records stamped before {baseline.older_than} are settled")
    print(f"Records fetched: {fetched}")
    print(f"Records settled: {baseline.marked}")
    if baseline.settled_undated:
        print(
            f"Settled without an arrival clock: {baseline.settled_undated} "
            f"(the cutoff is past today, so the whole table was settled)"
        )
    for reason, count in sorted(baseline.kept.items(), key=lambda item: (-item[1], item[0])):
        print(f"Records left selectable: {count} ({reason})")
    if not baseline.kept:
        print("Records left selectable: 0 (nothing)")
    print(f"Snapshot now settles: {snapshot.known} record id(s)")
    if snapshot.pending:
        print(f"Snapshot still owes a decision: {snapshot.pending} record id(s)")
    print(
        "Next run: --newly-ingested --state-dir "
        f"{snapshot.directory} reads only what arrived after {baseline.older_than}"
    )


def abort(kind: str, detail: str, code: int) -> int:
    print(f"{kind} failed: {detail}", file=sys.stderr)
    return code


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    provider: Optional[LLMProvider] = None,
    environ: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    incident_transport: Optional[PostTransport] = None,
) -> int:
    use_utf8_stdout()
    args = parse_args(argv)
    limit = DEFAULT_LIMIT if args.limit is None else args.limit
    store = Checkpoint(args.state_dir) if args.state_dir else None
    snapshot: Optional[IngestionSnapshot] = None
    try:
        if store is not None:
            store.load()
        if args.mark_baseline:
            origin = api_base_url(environ)
            snapshot = IngestionSnapshot(args.state_dir, base_url=origin).load()
            if snapshot.unreadable:
                return abort("ingestion", snapshot.unreadable, EXIT_CHECKPOINT)
            listed = fetch_records(base_url=origin, transport=transport)
            baseline = snapshot.mark(
                listed,
                older_than=args.baseline_older_than or datetime.now(timezone.utc).date(),
            )
            try:
                snapshot.flush()
            except SnapshotError as error:
                return abort("ingestion", str(error), EXIT_CHECKPOINT)
            print_baseline(baseline, snapshot, fetched=len(listed))
            return EXIT_OK
        if args.newly_ingested:
            snapshot = IngestionSnapshot(args.state_dir, base_url=api_base_url(environ)).load()
            if snapshot.unreadable:
                return abort("ingestion", snapshot.unreadable, EXIT_CHECKPOINT)
            if snapshot.absent:
                snapshot.seed(settled_ids(store))
                snapshot.baseline = f"the checkpoint ({snapshot.known} records it settled)"
            if not snapshot.known:
                return abort("ingestion", NO_BASELINE, EXIT_CHECKPOINT)
        uncapped = args.all or (args.newly_ingested and args.limit is None)
        summary = run_pipeline(
            limit=None if uncapped else limit,
            all_records=args.all,
            record_id=args.record_id,
            provider=provider,
            environ=environ,
            transport=transport,
            batch_size=args.batch_size,
            store=store,
            resume=not args.no_resume,
            semantic_concurrency=args.semantic_concurrency,
            chain_path=args.llm_chain,
            ingestion=snapshot,
        )
    except BackendError as error:
        return abort("backend", str(error), EXIT_BACKEND)
    except CheckpointError as error:
        return abort("checkpoint", str(error), EXIT_CHECKPOINT)
    except LLMError as error:
        return abort("provider", str(error), EXIT_PROVIDER)
    print_report(summary, detailed=args.json)
    if snapshot is not None:
        states = [*summary.outcomes, *summary.reused]
        snapshot.settle(
            [item.record_id for item in states if item.state == COMPLETED],
            review=[item.record_id for item in states if item.state == REVIEW_REQUIRED],
        )
        try:
            snapshot.flush()
        except SnapshotError as error:
            return abort("ingestion", str(error), EXIT_CHECKPOINT)
        print(
            f"Ingestion state: {snapshot.path} settles {snapshot.known} record id(s)"
            + (f" and leaves {snapshot.pending} owed to a decision" if snapshot.pending else "")
        )
    read = [item.incident for item in [*summary.outcomes, *summary.reused] if item.incident is not None]
    linked: Optional[LinkReport] = None
    if args.link_events:
        try:
            links = LinkStore(args.state_dir).load() if args.state_dir else None
            linked = link_incidents(
                read,
                chain_factory=lambda: summary.chain
                or Chain.single(provider or build_provider(None, environ=environ)),
                store=links,
            )
        except CheckpointError as error:
            return abort("checkpoint", str(error), EXIT_CHECKPOINT)
        print_links(linked, detailed=args.json)
    issues: Optional[IssueReport] = None
    grouped: Optional[IssueStore] = None
    if args.recurring_issues:
        try:
            grouped = IssueStore(args.state_dir).load() if args.state_dir else None
            issues = recurring_issues(
                read,
                filters=issue_filters(args),
                chain_factory=lambda: summary.chain
                or Chain.single(provider or build_provider(None, environ=environ)),
                store=grouped,
                links=decided_links(args, linked),
            )
        except CheckpointError as error:
            return abort("checkpoint", str(error), EXIT_CHECKPOINT)
        print_issues(issues, detailed=args.json)
    trends: Optional[TrendReport] = None
    if args.emerging_trends:
        try:
            if grouped is None and args.state_dir:
                grouped = IssueStore(args.state_dir).load()
            trends = emerging_trends(
                read,
                filters=issue_filters(args),
                chain_factory=lambda: summary.chain
                or Chain.single(provider or build_provider(None, environ=environ)),
                families=grouped_families(issues, grouped),
                links=decided_links(args, linked),
                store=TrendStore(args.state_dir).load() if args.state_dir else None,
                recent_days=args.recent_days,
                baseline_blocks=args.baseline_blocks,
                min_events=args.min_events,
                min_ratio=args.min_rise,
                coverage_drift=args.coverage_drift,
            )
        except CheckpointError as error:
            return abort("checkpoint", str(error), EXIT_CHECKPOINT)
        print_trends(trends, detailed=args.json)
    asked: Optional[AskReport] = None
    if args.ask:
        try:
            if grouped is None and args.state_dir:
                grouped = IssueStore(args.state_dir).load()
            earlier: Optional[TrendStore] = None
            if trends is None and args.state_dir:
                earlier = TrendStore(args.state_dir).load()
            asked = ask_copilot(
                read,
                args.ask,
                chain_factory=lambda: summary.chain
                or Chain.single(provider or build_provider(None, environ=environ)),
                links=decided_links(args, linked),
                families=grouped_families(issues, grouped),
                measured=measured_trends(trends, earlier),
                store=AnswerStore(args.state_dir).load() if args.state_dir else None,
            )
        except CheckpointError as error:
            return abort("checkpoint", str(error), EXIT_CHECKPOINT)
        print_answers(asked, detailed=args.json)
    briefed: Optional[BriefReport] = None
    if args.briefing:
        try:
            if grouped is None and args.state_dir:
                grouped = IssueStore(args.state_dir).load()
            saved_trends: Optional[TrendStore] = None
            if trends is None and args.state_dir:
                saved_trends = TrendStore(args.state_dir).load()
            briefed = district_briefing(
                read,
                filters=issue_filters(args),
                days_back=args.brief_days,
                language=args.brief_language,
                chain_factory=lambda: summary.chain
                or Chain.single(provider or build_provider(None, environ=environ)),
                links=decided_links(args, linked),
                families=grouped_families(issues, grouped),
                measured=measured_trends(trends, saved_trends),
                store=BriefStore(args.state_dir).load() if args.state_dir else None,
            )
        except CheckpointError as error:
            return abort("checkpoint", str(error), EXIT_CHECKPOINT)
        print_briefings(briefed, detailed=args.json)
    if args.post_incidents:
        accepted = [item.incident for item in summary.postable]
        if not accepted:
            kept = len(summary.accepted_context)
            print(
                "POST /incidents: nothing to send — the run produced no accepted incident"
                + (
                    f"; its {kept} accepted context/forecast record(s) were read and kept, and are "
                    "not incidents"
                    if kept
                    else ""
                )
            )
        else:
            if args.state_dir is None:
                print(
                    "Posting ledger: none, because --state-dir was not given, so nothing is remembered "
                    "between runs — every accepted record is sent again and the platform answers "
                    "HTTP 409 for the ones it already holds"
                )
            ledger = (
                PostLedger(args.state_dir, base_url=summary.base_url).load()
                if args.state_dir
                else None
            )
            posted = post_all(
                accepted,
                base_url=summary.base_url,
                transport=incident_transport,
                ledger=ledger,
            )
            print_postings(posted)
            if ledger is not None:
                try:
                    ledger.flush()
                except PostingError as error:
                    print(f"Posting ledger: {error}", file=sys.stderr)
                    return EXIT_POST
            if posted.failed:
                return EXIT_POST
    if summary.stop_reason or (linked is not None and linked.stop_reason) or (
        issues is not None and issues.stop_reason
    ) or (trends is not None and trends.stop_reason) or (asked is not None and asked.stop_reason) or (
        briefed is not None and briefed.stop_reason
    ):
        return EXIT_DEFERRED
    if summary.failures or (linked is not None and linked.failed) or (
        issues is not None and issues.failed
    ) or (trends is not None and trends.failed) or (asked is not None and asked.failed) or (
        briefed is not None and briefed.failed
    ):
        return EXIT_PARTIAL
    return EXIT_OK


__all__ = [
    "BackendError",
    "Chain",
    "Checkpoint",
    "CheckpointError",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LIMIT",
    "ENV_API_BASE_URL",
    "ENV_CHAIN_FILE",
    "EXIT_CHECKPOINT",
    "EXIT_DEFERRED",
    "EXIT_POST",
    "Incident",
    "LLMConfig",
    "LLMProvider",
    "MAX_SEMANTIC_CONCURRENCY",
    "Mode",
    "Outcome",
    "PROMPT_DIGEST",
    "PROMPT_VERSION",
    "RunSummary",
    "api_base_url",
    "build_provider",
    "chain_configs",
    "chain_named",
    "describe_incident",
    "fetch_record",
    "fetch_records",
    "main",
    "print_report",
    "process_record",
    "process_structured",
    "prompt_digest_for",
    "prompt_version_for",
    "provider_identity",
    "route",
    "run_pipeline",
    "run_record",
]


if __name__ == "__main__":
    raise SystemExit(main())
