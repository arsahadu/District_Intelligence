"""The runnable orchestrator: the backend's CommonRecords go in, validated Incidents come out.

``process_record`` is the one-record boundary; everything else here only moves records along it - fetch
from the API, route by shape, read them in batches, persist progress so a stopped run resumes, sort the
results, report them. No prompt, extraction, provider or validation logic lives here, and nothing is ever
sent back to the platform.
"""

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
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from urllib.parse import quote

from pydantic import ValidationError

from intelligence.checkpoint import (
    COMPLETED,
    DEFERRED,
    FAILED,
    REVIEW_REQUIRED,
    Checkpoint,
    CheckpointError,
    fingerprint,
)
from intelligence.contract import Incident, RecordKind
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
from intelligence.records import CONTENT_PATH, MappingError
from intelligence.router import Mode, route
from intelligence.structured import MAPPING_VERSION, process_record as process_structured

#: Where the platform API lives. Intelligence reads it and never writes it.
ENV_API_BASE_URL = "INTELLIGENCE_API_BASE_URL"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0

#: No argument means a small development run. The whole database is only read when asked for.
DEFAULT_LIMIT = 5

#: How many records a run reads before it writes progress. A batch is the unit of recovery.
DEFAULT_BATCH_SIZE = 25

#: Semantic reads are one prompt per record. A run may spread them over a few threads, never over all
#: of them at once, because each request is the provider's rate-limited work.
MAX_SEMANTIC_CONCURRENCY = 8

RECORDS_PATH = "/records"

#: The incident fields one console line carries, in the order the dashboard reads them.
LINE_FIELDS = (
    ("kind", "record_kind"),
    ("type", "incident_type"),
    ("category", "category"),
    ("department", "department"),
    ("severity", "severity"),
    ("priority", "priority"),
    ("status", "event_status"),
    ("event_time", "event_time"),
)

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BACKEND = 2
EXIT_PROVIDER = 3
#: Semantic records were left unread because the provider refused them on its limits.
EXIT_DEFERRED = 4
EXIT_CHECKPOINT = 5

#: (url, timeout_seconds) -> (status, body). Injected so a run can be tested without a network.
Transport = Callable[[str, float], tuple[int, str]]


class BackendError(RuntimeError):
    """The API could not be reached, answered with an error, or answered with something else."""


def prompt_version_for(mode: Mode) -> str:
    """Which reading produces a record's result. A resume reuses a result only across the same one."""
    return MAPPING_VERSION if mode is Mode.STRUCTURED else PROMPT_VERSION


def prompt_digest_for(mode: Mode) -> str:
    """The prompt text and schema a semantic reading was produced against, in one digest.

    A version label is written by a person and can be left standing while the rules beside it are
    reworded; this cannot. Copies carry nothing, because a copy is produced by code, not by a prompt.
    """
    return "" if mode is Mode.STRUCTURED else PROMPT_DIGEST


def provider_identity(
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    mode: Optional[Mode] = None,
) -> tuple[str, str]:
    """Who this run would ask, as ``(provider, model)``, or two empty strings when nothing named a model.

    A saved reading belongs to the model that produced it, so deciding whether a resume may reuse one
    means being able to name the model this run is set up for. The provider object is asked first, since
    it is what will actually be asked, and it is named by the configuration it was built from: ``vllm``
    and ``ollama`` are served by one class, so the class label would call two different readers the same
    reader and one reader two. The configuration is the fallback for the run that has not built one yet,
    and both sides of a comparison then come from the same names. Two halves are compared only where
    both runs stated them: a run with no model configured cannot invalidate a reading it never would
    have produced. A run with a chain is named by its first candidate here and states the whole ordered
    set separately, so a reading any candidate produced counts as one this run would produce again.
    """
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
    #: A copy is produced by code, not by a model, so the environment is never read for one.
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
    """Run one record through whichever path its shape asks for.

    A structured record never reaches a provider. A narrative one without a provider configured from
    ``INTELLIGENCE_LLM_*`` raises ``ProviderNotConfigured``: an unavailable model is a failure the
    caller sees, never an Incident invented from nothing.
    """
    if route(record) is Mode.STRUCTURED:
        return process_structured(record, text_keys=text_keys, extra_keys=extra_keys)
    if provider is None:
        provider = build_provider(config, environ=environ)
    return extract_incident(
        record, provider=provider, text_keys=text_keys, extra_keys=extra_keys
    )


def api_base_url(environ: Optional[Mapping[str, str]] = None) -> str:
    """The backend origin, without a trailing slash.

    With no ``environ`` the local ``.env`` is read first; a real environment variable, even an empty
    one, still wins.
    """
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
    """Read ``GET /records``: a list of CommonRecord objects, or a failure naming what was wrong."""
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
    """``GET /records``: every CommonRecord the backend holds, in the order it returns them."""
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
    """``GET /records/{record_id}`` for the one record that carries this id."""
    url = f"{(base_url or api_base_url()) + RECORDS_PATH}/{quote(str(record_id), safe='')}"
    payload = _get_json(url, base_url=base_url, transport=transport, timeout_seconds=timeout_seconds)
    return record_from_payload(payload, url=url, record_id=record_id)


@dataclass
class Outcome:
    """What one record produced: an Incident, a reason there is none, a reason it was not read, or a
    result an earlier run already read."""

    record_id: str
    source_type: str
    record_type: str
    incident: Optional[Incident] = None
    error: Optional[str] = None
    mode: Mode = Mode.SEMANTIC
    deferred: bool = False
    reused: bool = False
    #: Which provider refusal left this record unread, so the report can say what it stopped on.
    deferred_kind: str = ""
    #: Deferred because the run had already stopped asking, not because this record was refused:
    #: nothing was ever sent for it, so counting it as an attempt would overstate the work.
    never_sent: bool = False
    #: The candidate that produced this reading, when a chain was asked. Empty for a copy.
    answered_by: str = ""
    #: Every candidate skipped or refused before the one that answered, in order and with its reason.
    attempts: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return self.incident is None

    @property
    def fell_back(self) -> bool:
        """A candidate other than the configured first one had to be asked for this record."""
        return bool(self.attempts)

    @property
    def state(self) -> str:
        """Which checkpoint state this result is saved under."""
        if self.incident is not None:
            return REVIEW_REQUIRED if self.review_required else COMPLETED
        return DEFERRED if self.deferred else FAILED

    @property
    def contextual(self) -> bool:
        """The record carries context, not an incident: nothing said what happened."""
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
    """One run of the orchestrator, counted."""

    base_url: str
    fetched: int
    selected: int = 0
    skipped: list[str] = field(default_factory=list)
    #: Why the run stopped asking the provider, once it refused a record on its limits. Nothing after
    #: this point is sent, because the provider has said in its own answer that it will not read it.
    stop_reason: Optional[str] = None
    #: ``"quota"`` when the allowance is spent, ``"rate"`` when a window is merely busy.
    stop_kind: str = ""
    store: Optional[Checkpoint] = None
    outcomes: list[Outcome] = field(default_factory=list)
    #: Results an earlier run read. Kept apart from ``outcomes`` so nothing here counts as processed.
    reused: list[Outcome] = field(default_factory=list)
    #: The chain this run asked, so the report can name who read each record. Never a credential.
    chain: Optional[Chain] = None

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    @property
    def processed(self) -> int:
        """Records this run read to an end. Reused results and unread records are counted apart."""
        return len([item for item in self.outcomes if not item.deferred])

    @property
    def attempted(self) -> int:
        """Records this run sent or copied, refusals included: selected minus reused minus unread."""
        return len(self.outcomes) - len(self.unread)

    @property
    def unread(self) -> list[Outcome]:
        """Deferred records the run never sent, because it had already stopped asking."""
        return [item for item in self.outcomes if item.deferred and item.never_sent]

    @property
    def incidents(self) -> list[Outcome]:
        return [item for item in self.outcomes if not item.failed and not item.contextual]

    @property
    def contextual(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.contextual]

    @property
    def accepted(self) -> list[Outcome]:
        """Read into a result nobody has to look at. A review-required one is counted only there."""
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
        """Records the provider refused to read now, which a later run is meant to take."""
        return [item for item in self.outcomes if item.deferred]

    @property
    def refused(self) -> list[Outcome]:
        """Deferred records this run did send, and the provider turned away."""
        return [item for item in self.deferred if not item.never_sent]

    @property
    def structured(self) -> list[Outcome]:
        """Read by copying the record's own fields, with no model in the loop."""
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
    def fallbacks(self) -> list[Outcome]:
        """Records the configured first candidate could not read, so another one had to."""
        return [item for item in self.outcomes if item.fell_back]

    @property
    def turns(self) -> list[str]:
        """Every turn the chain took this run, record by record, oldest refusal first."""
        return [f"{item.record_id}: {turn}" for item in self.outcomes for turn in item.attempts]

    def answered_by(self) -> dict[str, int]:
        """How many readings each candidate produced by asking this run: requests this run spent."""
        return self._by_reader(self.outcomes)

    def reused_answered_by(self) -> dict[str, int]:
        """Who produced the readings this run only returned from the store, as their own metadata says.

        Counted apart because a reading an earlier run produced is not this run's work: merged with the
        fresh ones, a resumed batch would read as though this run had asked that model six times.
        """
        return self._by_reader(self.reused)

    @staticmethod
    def _by_reader(items: Sequence["Outcome"]) -> dict[str, int]:
        found: dict[str, int] = {}
        for item in items:
            if item.incident is not None and item.answered_by:
                found[item.answered_by] = found.get(item.answered_by, 0) + 1
        return dict(sorted(found.items(), key=lambda row: (-row[1], row[0])))

    def buckets(self) -> dict[str, int]:
        """Where the attempted records ended up: no record in two of these, and they sum to
        ``attempted``. A review-required result is counted there even when it is context, because
        the decision a person owes is the thing being reported."""
        return {
            "accepted incidents": len(self.accepted_incidents),
            "accepted context": len(self.accepted_context),
            "review-required": len(self.review_required),
            "failed": len(self.failures),
            "deferred (refused)": len(self.refused),
        }

    def accounting(self) -> str:
        """The run's totals written as equations, so no reader has to infer what a count excludes.

        Each side holds the same records split once: everything fetched is selected or left for a
        later run, everything selected was reused, attempted or never sent, and everything attempted
        sits in exactly one of ``buckets``.
        """
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
        """The console's summary block, in the order it is printed."""
        found: dict[str, Any] = {
            "Backend URL": self.base_url,
            "Records fetched": self.fetched,
            "Records selected": self.selected,
            "Records reused": len(self.reused),
            "Records attempted": self.attempted,
            "Structured (no model)": len(self.structured),
            "Narrative (model needed)": len(self.semantic),
        }
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


def describe_incident(incident: Incident) -> str:
    """One line per Incident: id, title and the fields the dashboard sorts on."""
    title = " ".join((incident.title or "-").split())
    if len(title) > 60:
        title = title[:59] + "..."
    parts = [incident.incident_id, f"title={title}"]
    for label, name in LINE_FIELDS:
        value = getattr(incident, name)
        if value is None:
            rendered = "-"
        elif isinstance(value, enum.Enum):
            rendered = value.value
        elif isinstance(value, datetime):
            rendered = value.isoformat()
        else:
            rendered = str(value)
        parts.append(f"{label}={rendered}")
    if incident.context_facts:
        parts.append(f"facts={len(incident.context_facts)}")
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
    """One record through its own path.

    A refusal on the provider's limits is not this record's failure, so it comes back deferred;
    anything else that went wrong is reported as a failure of this record's own read.

    With a ``chain``, the whole read - prompt, grounding, contract - is what each candidate is given,
    and what a candidate ends on decides what happens next: a refusal about the account closes that
    account for the run and defers this record, a refusal about this model gives the next candidate a
    turn, and a candidate that answered with something unreadable is reported with every turn it took.
    """
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
        #: Saved as deferred, never as a result: the next run reads this record again.
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
            #: No candidate left that could answer: this is the provider's state, not the record's.
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
    """One selected record, waiting for the reading its shape calls for."""

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
    """Who produced a saved reading, named as the chain names it. A copy names nobody."""
    if entry is None or not getattr(entry, "model", ""):
        return ""
    provider = str(getattr(entry, "provider", "") or "")
    return f"{provider}/{entry.model}" if provider else str(entry.model)


def _chunks(items: Sequence[_Job], size: int) -> Iterator[Sequence[_Job]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _deferred(job: _Job, reason: str, kind: str = "") -> Outcome:
    """The record a run that had stopped asking leaves for a later one: nothing was sent for it."""
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
    #: A model id may hold a slash (``openai/gpt-oss-20b``); a provider name never does.
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
    """Read one batch: the copies first, then the prompts, and every result into the summary.

    Once the provider has refused a record on its limits, the run stops asking it: what was not read,
    in this batch and in every later one, is deferred under the reason the provider gave. The copies
    in the same batch are still read, because they cost the provider nothing.
    """
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
        """Remember the first refusal on limits, which is what ends the asking for this run."""
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
                #: Only a send that had not started can be called off, and only those are recorded as
                #: unread: what was already in flight is kept as the answer it really produced.
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
) -> RunSummary:
    """Fetch, route, read in batches, and persist each batch when a store was given.

    ``limit`` is applied client-side because ``GET /records`` has no paging; it is None only when
    ``all_records`` was asked for. One reader is built per run, and only when a record still to be
    read is narrative - so a batch of price lines, and a batch whose narratives an earlier run already
    read, never authenticate and never spend quota. Batching is internal: the API is read once.

    Without ``chain`` or ``chain_path`` the reader is the one ``INTELLIGENCE_LLM_*`` names, exactly as
    before. With either, a narrative record walks that ordered chain: one send per candidate, and
    whichever of them answers is the reader the result is stored under.
    """
    if batch_size < 1:
        raise ValueError(f"--batch-size must be at least 1, got {batch_size}")
    if not 1 <= semantic_concurrency <= MAX_SEMANTIC_CONCURRENCY:
        raise ValueError(
            f"semantic concurrency must be between 1 and {MAX_SEMANTIC_CONCURRENCY}, got "
            f"{semantic_concurrency}"
        )

    origin = base_url or api_base_url(environ)
    if record_id is not None:
        selected = [
            fetch_record(
                record_id, base_url=origin, transport=transport,
                timeout_seconds=timeout_seconds,
            )
        ]
        fetched = 1
    else:
        records = fetch_records(
            base_url=origin, transport=transport, timeout_seconds=timeout_seconds
        )
        fetched = len(records)
        selected = records if all_records or limit is None else records[:limit]

    jobs = [_job(record) for record in selected]
    summary = RunSummary(base_url=origin, fetched=fetched, selected=len(jobs), store=store)

    #: Resolved before the reuse decision, because which readers a run is prepared to ask is part of
    #: whether a saved result is the one this run would produce. Reading the list authenticates nothing
    #: and sends nothing: it opens the file the configuration names and looks up the variables in it.
    if chain is not None:
        #: A caller that hands over a Chain means that chain, even when it names one candidate.
        ask_chain = chain
    elif provider is not None:
        #: A caller that hands over a reader means that reader. The chain the local configuration
        #: names belongs to a run that said nothing about who answers: consulting it here would
        #: replace the caller's own provider with a live endpoint it never asked for.
        ask_chain = None
    elif chain_named(environ, chain_path):
        candidates, problems = chain_configs(config, environ=environ, chain_path=chain_path)
        ask_chain = Chain(candidates, problems=problems)
    else:
        #: Nothing was pointed at a chain, so the one reader ``INTELLIGENCE_LLM_*`` names is built when
        #: a narrative record needs it, exactly as it was before a chain existed.
        ask_chain = None
    if ask_chain is not None:
        summary.chain = ask_chain

    if store is not None:
        #: Known before the reuse decision: an entry saved by another model must not be read as this
        #: run's own work, and a store that cannot say who wrote it cannot be asked to vouch for one.
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
            "Read CommonRecords from the platform API and run them through the intelligence "
            "pipeline. Nothing is posted back."
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
    args = parser.parse_args(argv)

    if args.record_id is not None and (args.all or args.limit is not None):
        parser.error("--record-id selects one record; do not combine it with --limit or --all")
    if args.all and args.limit is not None:
        parser.error("--all already means every record; do not also set --limit")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1, or use --all")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if not 1 <= args.semantic_concurrency <= MAX_SEMANTIC_CONCURRENCY:
        parser.error(f"--semantic-concurrency must be between 1 and {MAX_SEMANTIC_CONCURRENCY}")
    if args.no_resume and not args.state_dir:
        parser.error("--no-resume only means something with --state-dir")
    return args


def use_utf8_stdout() -> None:
    """Tamil titles and quotes should reach a console rather than become escapes."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def abort(kind: str, detail: str, code: int) -> int:
    print(f"{kind} failed: {detail}", file=sys.stderr)
    return code


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    provider: Optional[LLMProvider] = None,
    environ: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
) -> int:
    use_utf8_stdout()
    args = parse_args(argv)
    limit = DEFAULT_LIMIT if args.limit is None else args.limit
    store = Checkpoint(args.state_dir) if args.state_dir else None
    try:
        if store is not None:
            store.load()
        summary = run_pipeline(
            limit=None if args.all else limit,
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
        )
    except BackendError as error:
        return abort("backend", str(error), EXIT_BACKEND)
    except CheckpointError as error:
        return abort("checkpoint", str(error), EXIT_CHECKPOINT)
    except LLMError as error:
        return abort("provider", str(error), EXIT_PROVIDER)
    print_report(summary, detailed=args.json)
    if summary.stop_reason:
        return EXIT_DEFERRED
    return EXIT_PARTIAL if summary.failures else EXIT_OK


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
