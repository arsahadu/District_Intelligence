from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from intelligence.api_client import (
    DEFAULT_TIMEOUT_SECONDS,
    IncidentAPIError,
    PostResult,
    post_incident,
)
from intelligence.checkpoint import _write_atomically
from intelligence.contract import Incident, SCHEMA_VERSION
from intelligence.spans import compute_field_hash

POSTED_FILENAME = "posted.json"

CREATED = "created"
DUPLICATE = "duplicate"
UNCHANGED = "unchanged"
FAILED = "failed"
STATES = (CREATED, DUPLICATE, UNCHANGED, FAILED)
HELD = (CREATED, DUPLICATE)
SENT = (CREATED, DUPLICATE, FAILED)
_ENTRY_KEYS = ("digest", "state", "status_code", "record_id", "kind", "at", "previous")

# the two stamps a re-read of an unchanged record always moves, and nothing else
_UNSTAMPED = (("validation", "checked_at"), ("generation", "generated_at"))


class PostingError(RuntimeError):
    pass


def document_digest(incident: Incident) -> str:
    document = dict(incident.to_storage_document())
    for block, name in _UNSTAMPED:
        held = document.get(block)
        if isinstance(held, dict) and name in held:
            document[block] = {key: value for key, value in held.items() if key != name}
    return compute_field_hash(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PostRow:
    incident_id: str
    record_id: str
    kind: str
    state: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    note: Optional[str] = None
    held_since: Optional[str] = None

    def line(self) -> str:
        if self.state == UNCHANGED:
            return (
                f"Not posted: {self.incident_id} holds exactly what was already "
                f"sent on {self.held_since or 'an earlier run'}, so it was not sent again"
            )
        if self.state == FAILED:
            return f"POST /incidents failed for {self.incident_id}: {self.error}"
        outcome = "already exists (duplicate)" if self.state == DUPLICATE else CREATED
        return f"POST /incidents: {self.incident_id} {outcome} (HTTP {self.status_code})"


class PostLedger:
    def __init__(self, directory: Any, *, base_url: str = "") -> None:
        self.directory = Path(str(directory))
        self.path = self.directory / POSTED_FILENAME
        self.base_url = base_url
        self.entries: dict[str, dict[str, Any]] = {}
        self.loaded = False
        self.unreadable: Optional[str] = None
        self.dropped = 0
        self.disclosed = False
        self.written = False

    def load(self) -> "PostLedger":
        if not self.path.exists():
            self.loaded = True
            return self
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.unreadable = self._cannot_read(str(error))
            self.loaded = True
            return self
        if not isinstance(payload, Mapping) or not isinstance(payload.get("posted"), Mapping):
            self.unreadable = self._cannot_read("it holds no posted object")
            self.loaded = True
            return self
        held: dict[str, dict[str, Any]] = {}
        for key, value in payload["posted"].items():
            if not isinstance(value, Mapping) or not all(name in value for name in _ENTRY_KEYS):
                self.dropped += 1
                continue
            if str(value["state"]) not in HELD or not str(value["digest"]):
                self.dropped += 1
                continue
            held[str(key)] = {name: value[name] for name in _ENTRY_KEYS}
        self.entries = held
        self.loaded = True
        return self

    @staticmethod
    def _cannot_read(reason: str) -> str:
        return (
            f"{POSTED_FILENAME} {reason}, so nothing in it is trusted and every accepted record is "
            "sent again; the platform answers HTTP 409 for the ones it already holds, so a re-send "
            "stores nothing twice"
        )

    def known(self, incident_id: str) -> Optional[dict[str, Any]]:
        return self.entries.get(incident_id)

    def hold(
        self,
        incident: Incident,
        *,
        digest: str,
        state: str,
        status_code: Optional[int],
        previous: Optional[str],
    ) -> None:
        self.entries[incident.incident_id] = {
            "digest": digest,
            "state": state,
            "status_code": status_code,
            "record_id": incident.provenance.record_id,
            "kind": incident.record_kind.value,
            "at": _now(),
            "previous": previous,
        }

    def flush(self) -> None:
        body = json.dumps(
            {
                "updated_at": _now(),
                "schema_version": SCHEMA_VERSION,
                "base_url": self.base_url,
                "records": len(self.entries),
                "posted": {key: self.entries[key] for key in sorted(self.entries)},
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        try:
            _write_atomically(self.path, body)
        except OSError as error:
            raise PostingError(f"{self.path} could not be written: {error}") from error
        self.written = True

    def counts(self) -> dict[str, int]:
        found = {state: 0 for state in HELD}
        for entry in self.entries.values():
            found[str(entry["state"])] = found.get(str(entry["state"]), 0) + 1
        return found


@dataclass
class PostReport:
    base_url: str
    rows: list[PostRow]
    ledger_path: Optional[str] = None
    warnings: tuple[str, ...] = ()

    def _with(self, state: str) -> list[PostRow]:
        return [row for row in self.rows if row.state == state]

    @property
    def sent(self) -> list[PostRow]:
        return [row for row in self.rows if row.state in SENT]

    @property
    def created(self) -> list[PostRow]:
        return self._with(CREATED)

    @property
    def duplicates(self) -> list[PostRow]:
        return self._with(DUPLICATE)

    @property
    def unchanged(self) -> list[PostRow]:
        return self._with(UNCHANGED)

    @property
    def failed(self) -> list[PostRow]:
        return self._with(FAILED)

    @property
    def changed(self) -> list[PostRow]:
        return [row for row in self.rows if row.note]

    def counts(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "already existed": len(self.duplicates),
            "unchanged, not sent": len(self.unchanged),
            "failed": len(self.failed),
        }

    def summary(self) -> str:
        parts = [f"{count} {label}" for label, count in self.counts().items() if count]
        line = f"{len(self.sent)} sent = " + ("; ".join(parts) if parts else "nothing")
        if self.changed:
            line += f" ({len(self.changed)} posted copy differs from the newer reading)"
        return line


def post_all(
    incidents: Iterable[Incident],
    *,
    base_url: str,
    transport: Optional[Any] = None,
    ledger: Optional[PostLedger] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> PostReport:
    rows: list[PostRow] = []
    warnings: list[str] = []
    if ledger is not None:
        if not ledger.loaded:
            ledger.load()
        if not ledger.disclosed:
            if ledger.unreadable:
                warnings.append(ledger.unreadable)
            if ledger.dropped:
                warnings.append(
                    f"{ledger.path.name} holds {ledger.dropped} entry(ies) that do not describe a "
                    "posted document, so they were ignored rather than trusted"
                )
            ledger.disclosed = True
    for incident in incidents:
        digest = document_digest(incident)
        prior = ledger.known(incident.incident_id) if ledger is not None else None
        held = str(prior["digest"]) if prior is not None else None
        if held == digest:
            rows.append(
                PostRow(
                    incident.incident_id,
                    incident.provenance.record_id,
                    incident.record_kind.value,
                    UNCHANGED,
                    held_since=str((prior or {}).get("at") or ""),
                )
            )
            continue
        try:
            result: PostResult = post_incident(
                incident, base_url=base_url, transport=transport, timeout_seconds=timeout_seconds
            )
        except IncidentAPIError as error:
            rows.append(
                PostRow(
                    incident.incident_id,
                    incident.provenance.record_id,
                    incident.record_kind.value,
                    FAILED,
                    error=str(error),
                )
            )
            continue
        state = DUPLICATE if result.duplicate else CREATED
        note = None
        if prior is not None and state == DUPLICATE:
            note = (
                "the platform already holds a different reading of this record, and POST /incidents "
                "only ever creates, so this newer reading was not written"
            )
        if ledger is not None:
            ledger.hold(
                incident,
                digest=digest if state == CREATED else str(held or digest),
                state=CREATED if state == CREATED else str((prior or {}).get("state") or DUPLICATE),
                status_code=result.status_code,
                previous=held,
            )
        rows.append(
            PostRow(
                incident.incident_id,
                incident.provenance.record_id,
                incident.record_kind.value,
                state,
                status_code=result.status_code,
                note=note,
                held_since=str((prior or {}).get("at") or "") if note else None,
            )
        )
    return PostReport(
        base_url=base_url,
        rows=rows,
        ledger_path=str(ledger.path) if ledger is not None else None,
        warnings=tuple(warnings),
    )


def print_postings(report: PostReport, *, stream: Any = None, errors: Any = None) -> None:
    out = stream if stream is not None else sys.stdout
    err = errors if errors is not None else sys.stderr
    for row in report.rows:
        target = err if row.state == FAILED else out
        print(row.line(), file=target)
        if row.note:
            print(f"  {row.note}", file=target)
    if report.warnings:
        print("\nPosting notes", file=out)
        for note in report.warnings:
            print(f"  {note}", file=out)
    if report.rows:
        print(f"\nPosting: {report.summary()}", file=out)
        if report.ledger_path:
            print(f"Posting ledger: {report.ledger_path}", file=out)


__all__ = [
    "CREATED",
    "DUPLICATE",
    "FAILED",
    "POSTED_FILENAME",
    "PostLedger",
    "PostReport",
    "PostRow",
    "PostingError",
    "SENT",
    "STATES",
    "UNCHANGED",
    "document_digest",
    "post_all",
    "print_postings",
]
