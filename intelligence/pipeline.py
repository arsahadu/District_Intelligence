"""The runnable orchestrator: the backend's CommonRecords go in, validated Incidents come out.

``process_record`` is the one-record boundary. Everything below it only moves records along that
boundary: it asks the backend API for CommonRecords, runs each through the existing chain, sorts
what came back and reports it. No prompt, extraction, provider or validation logic lives here, and
nothing is ever sent back to the backend.
"""

from __future__ import annotations

import argparse
import enum
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import quote

from pydantic import ValidationError

from intelligence.contract import Incident, RecordKind
from intelligence.intelligence import InvalidExtraction, extract_incident
from intelligence.llm import LLMConfig, LLMError, LLMProvider, build_provider, load_dotenv_file
from intelligence.records import CONTENT_PATH, MappingError

#: Where the platform API lives. Intelligence reads it and never writes it.
ENV_API_BASE_URL = "INTELLIGENCE_API_BASE_URL"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0

#: No argument means a small development run. The whole database is only read when asked for.
DEFAULT_LIMIT = 5

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

#: (url, timeout_seconds) -> (status, body). Injected so a run can be tested without a network.
Transport = Callable[[str, float], tuple[int, str]]


class BackendError(RuntimeError):
    """The API could not be reached, answered with an error, or answered with something else."""


def process_record(
    record: Any,
    *,
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Incident:
    """Run one record through the Stage 1 chain.

    With no provider the configuration comes from ``INTELLIGENCE_LLM_*``, in the environment or
    in the repository ``.env``. If none can be built this raises ``ProviderNotConfigured``: an
    unavailable model is a failure the caller sees, never an Incident invented from nothing.
    """
    if provider is None:
        provider = build_provider(config, environ=environ)
    return extract_incident(record, provider=provider, text_keys=text_keys, extra_keys=extra_keys)


def api_base_url(environ: Optional[Mapping[str, str]] = None) -> str:
    """The backend origin, without a trailing slash.

    With no ``environ`` the local ``.env`` is read first, so a deployment URL can live there; a
    real environment variable (even an empty one) still wins, and an explicit mapping is used as-is.
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
    """What one record produced. Either an Incident or the reason there is none."""

    record_id: str
    source_type: str
    record_type: str
    incident: Optional[Incident] = None
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.incident is None

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
            return f"{self.record_id} failed: {self.error}"
        return describe_incident(self.incident)


@dataclass
class RunSummary:
    """One run of the orchestrator, counted."""

    base_url: str
    fetched: int
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    @property
    def processed(self) -> int:
        return len(self.outcomes)

    @property
    def incidents(self) -> list[Outcome]:
        return [item for item in self.outcomes if not item.failed and not item.contextual]

    @property
    def contextual(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.contextual]

    @property
    def review_required(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.review_required]

    @property
    def failures(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.failed]

    def counts(self) -> dict[str, Any]:
        """The console's summary block, in the order it is printed."""
        return {
            "Backend URL": self.base_url,
            "Records fetched": self.fetched,
            "Records processed": self.processed,
            "Incidents produced": len(self.incidents),
            "Non-incident/contextual": len(self.contextual),
            "Review-required results": len(self.review_required),
            "Failures": len(self.failures),
        }


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
    provider: LLMProvider,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Outcome:
    """One record through the chain, with any refusal turned into a reported failure."""
    payload = record if isinstance(record, Mapping) else {}
    record_id = str(payload.get("record_id") or "?")
    source_type = str(payload.get("source_type") or "")
    record_type = str(payload.get("record_type") or "")
    try:
        incident = process_record(
            record, provider=provider, text_keys=text_keys, extra_keys=extra_keys
        )
    except MappingError as error:
        return Outcome(record_id, source_type, record_type, error=f"record unreadable: {error}")
    except InvalidExtraction as error:
        return Outcome(record_id, source_type, record_type, error=f"extraction refused: {error}")
    except ValidationError as error:
        return Outcome(record_id, source_type, record_type, error=f"contract rejected: {error}")
    except LLMError as error:
        return Outcome(record_id, source_type, record_type, error=f"provider failed: {error}")
    return Outcome(record_id, source_type, record_type, incident=incident)


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
) -> RunSummary:
    """Fetch, process, collect.

    With no ``record_id`` the whole records list is read and ``limit`` of it is processed; ``limit``
    is None only when ``all_records`` was asked for. The provider is built once, before any record is
    fetched, so a missing model configuration stops the run instead of failing every record.
    """
    if provider is None:
        provider = build_provider(config, environ=environ)
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

    summary = RunSummary(base_url=origin, fetched=fetched)
    for record in selected:
        summary.add(run_record(record, provider=provider))
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

    block("Incidents", summary.incidents)
    block(
        "Non-incident/contextual records",
        summary.contextual,
        "nothing in these evidenced an event, so they are filed as forecast or context and keep "
        "the values their own fields hold",
    )
    block("Failures", summary.failures)

    if summary.review_required:
        print(
            "\nNeeds a person: " + ", ".join(item.incident.incident_id
                                             for item in summary.review_required),
            file=out,
        )

    if not summary.outcomes:
        print("\nThe backend returned no records to process.", file=out)
    if detailed:
        for item in summary.outcomes:
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
        help=f"process this many records (default {DEFAULT_LIMIT}; use --all for every record)",
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
    parser.add_argument("--json", action="store_true", help="also print each Incident in full")
    args = parser.parse_args(argv)

    if args.record_id is not None and (args.all or args.limit is not None):
        parser.error("--record-id selects one record; do not combine it with --limit or --all")
    if args.all and args.limit is not None:
        parser.error("--all already means every record; do not also set --limit")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1, or use --all")
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
    try:
        summary = run_pipeline(
            limit=None if args.all else limit,
            all_records=args.all,
            record_id=args.record_id,
            provider=provider,
            environ=environ,
            transport=transport,
        )
    except BackendError as error:
        return abort("backend", str(error), EXIT_BACKEND)
    except LLMError as error:
        return abort("provider", str(error), EXIT_PROVIDER)
    print_report(summary, detailed=args.json)
    return EXIT_PARTIAL if summary.failures else EXIT_OK


__all__ = [
    "BackendError",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_LIMIT",
    "ENV_API_BASE_URL",
    "Incident",
    "LLMConfig",
    "LLMProvider",
    "Outcome",
    "RunSummary",
    "api_base_url",
    "build_provider",
    "describe_incident",
    "fetch_record",
    "fetch_records",
    "main",
    "print_report",
    "process_record",
    "run_pipeline",
    "run_record",
]


if __name__ == "__main__":
    raise SystemExit(main())
