from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from pydantic import ValidationError

from intelligence.context import build_context
from intelligence.contract import Incident, SCHEMA_VERSION
from intelligence.records import MappingError
from intelligence.records import input_hash as record_input_hash
from intelligence.router import Mode

RESULTS_FILENAME = "results.jsonl"
CHECKPOINT_FILENAME = "checkpoint.json"

COMPLETED = "completed"
REVIEW_REQUIRED = "review_required"
FAILED = "failed"
DEFERRED = "deferred"
STATES = (COMPLETED, REVIEW_REQUIRED, FAILED, DEFERRED)

REUSABLE_STATES = (COMPLETED, REVIEW_REQUIRED)

_REQUIRED_KEYS = ("record_id", "state", "mode", "prompt_version", "schema_version")

_REQUIRED_META = (
    "updated_at", "schema_version", "base_url", "provider", "model", "records", "states",
    "results_file", "results_digest",
)

_DECISIVE_META = ("schema_version", "results_file", "results_digest")


class CheckpointError(RuntimeError):
    ...


def fingerprint(record: Any) -> Optional[str]:
    try:
        return record_input_hash(build_context(record).record)
    except MappingError:
        return None


@dataclass(frozen=True)
class Entry:
    record_id: str
    state: str
    mode: str
    prompt_version: str
    schema_version: str
    input_hash: Optional[str] = None
    provider: str = ""
    model: str = ""
    source_type: str = ""
    record_type: str = ""
    error: Optional[str] = None
    incident: Optional[dict[str, Any]] = None
    saved_at: str = ""
    prompt_digest: str = ""
    attempts: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "state": self.state,
            "mode": self.mode,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "input_hash": self.input_hash,
            "provider": self.provider,
            "model": self.model,
            "source_type": self.source_type,
            "record_type": self.record_type,
            "error": self.error,
            "incident": self.incident,
            "saved_at": self.saved_at,
            "prompt_digest": self.prompt_digest,
            "attempts": list(self.attempts),
        }

    def line(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"

    @classmethod
    def read(cls, line: str, *, number: int, path: Path) -> "Entry":
        where = f"{path} line {number}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise CheckpointError(f"{where} is not JSON ({error}); saved progress was not changed")
        if not isinstance(payload, dict):
            raise CheckpointError(f"{where} holds {type(payload).__name__} where a record entry was expected")
        missing = [key for key in _REQUIRED_KEYS if not payload.get(key)]
        if missing:
            raise CheckpointError(f"{where} has no {', '.join(missing)}, so its result cannot be trusted")
        state = str(payload["state"])
        if state not in STATES:
            raise CheckpointError(f"{where} claims unknown state {state!r}; expected one of {', '.join(STATES)}")
        incident = payload.get("incident") if isinstance(payload.get("incident"), dict) else None
        if state in REUSABLE_STATES and incident is None:
            raise CheckpointError(
                f"{where} is {state} but carries no Incident document, so nothing could be resumed from it"
            )
        return cls(
            record_id=str(payload["record_id"]),
            state=state,
            mode=str(payload["mode"]),
            prompt_version=str(payload["prompt_version"]),
            schema_version=str(payload["schema_version"]),
            input_hash=(str(payload["input_hash"]) if payload.get("input_hash") else None),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            source_type=str(payload.get("source_type") or ""),
            record_type=str(payload.get("record_type") or ""),
            error=(str(payload["error"]) if payload.get("error") else None),
            incident=incident,
            saved_at=str(payload.get("saved_at") or ""),
            prompt_digest=str(payload.get("prompt_digest") or ""),
            attempts=tuple(
                str(item) for item in (payload.get("attempts") or ()) if str(item).strip()
            ),
        )


class Checkpoint:
    def __init__(
        self,
        directory: Any,
        *,
        base_url: str = "",
        provider: str = "",
        model: str = "",
        candidates: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.directory = Path(str(directory))
        self.results_path = self.directory / RESULTS_FILENAME
        self.checkpoint_path = self.directory / CHECKPOINT_FILENAME
        self.base_url = base_url
        self.provider = provider
        self.model = model
        self.candidates = tuple(
            (str(name), str(model)) for name, model in candidates if str(model or "")
        )
        self.entries: dict[str, Entry] = {}
        self.superseded = 0
        self.loaded = False
        self.manifest: Optional[dict[str, Any]] = None
        self.incomplete: Optional[str] = None
        self.stale_version: Optional[str] = None
        self.previous_base_url = ""
        self.invalidated: dict[str, str] = {}
        self.preserved: dict[str, str] = {}

    def load(self) -> "Checkpoint":
        text: Optional[str] = None
        if self.results_path.exists():
            try:
                text = self.results_path.read_text(encoding="utf-8")
            except OSError as error:
                raise CheckpointError(f"{self.results_path} could not be read: {error}") from error
        manifest = self._read_manifest()
        if text is not None:
            for number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                entry = Entry.read(line, number=number, path=self.results_path)
                if entry.record_id in self.entries:
                    self.superseded += 1
                self.entries[entry.record_id] = entry
        self.manifest = manifest
        self._reconcile(manifest, text)
        self.loaded = True
        return self

    def _read_manifest(self) -> Optional[dict[str, Any]]:
        if not self.checkpoint_path.exists():
            return None
        where = str(self.checkpoint_path)
        try:
            body = self.checkpoint_path.read_text(encoding="utf-8")
        except OSError as error:
            raise CheckpointError(f"{where} could not be read: {error}") from error
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise CheckpointError(f"{where} is not JSON ({error}); saved progress was not changed") from error
        if not isinstance(payload, dict):
            raise CheckpointError(f"{where} holds {type(payload).__name__} where checkpoint metadata was expected")
        missing = [key for key in _REQUIRED_META if payload.get(key) is None]
        if missing:
            raise CheckpointError(
                f"{where} states no {', '.join(missing)}, so it cannot account for the results beside it"
            )
        blank = [key for key in _DECISIVE_META if not str(payload[key]).strip()]
        if blank:
            raise CheckpointError(
                f"{where} leaves {', '.join(blank)} empty, which is what ties it to "
                f"{RESULTS_FILENAME}; the saved progress was not changed"
            )
        if str(payload["results_file"]) != RESULTS_FILENAME:
            raise CheckpointError(
                f"{where} describes {payload['results_file']!r}, not the {RESULTS_FILENAME} in {self.directory}"
            )
        states = payload["states"]
        if not isinstance(states, dict):
            raise CheckpointError(f"{where} holds {type(states).__name__} where state counts were expected")
        unknown = sorted(str(key) for key in states if str(key) not in STATES)
        if unknown:
            raise CheckpointError(f"{where} counts states it never recorded: {', '.join(unknown)}")
        return payload

    def _reconcile(self, manifest: Optional[dict[str, Any]], text: Optional[str]) -> None:
        if manifest is None:
            if text is not None:
                self.incomplete = (
                    f"{CHECKPOINT_FILENAME} is missing beside {RESULTS_FILENAME}, so nothing vouches for "
                    "the saved results; they are kept and read as they stand"
                )
            return
        where = str(self.checkpoint_path)
        if text is None:
            raise CheckpointError(
                f"{where} counts {manifest['records']} records but {self.results_path} is not there; "
                "the pair is incomplete, so nothing was overwritten"
            )
        if str(manifest["schema_version"]) != SCHEMA_VERSION:
            self.stale_version = str(manifest["schema_version"])
        self.previous_base_url = str(manifest["base_url"] or "")
        try:
            stated = int(manifest["records"])
        except (TypeError, ValueError):
            raise CheckpointError(
                f"{where} states a record count that is not a number: {manifest['records']!r}"
            )
        found = self.counts()
        if stated > len(self.entries):
            raise CheckpointError(
                f"{where} counts {stated} records while {RESULTS_FILENAME} holds {len(self.entries)}; a "
                "manifest written after its results cannot be the one that belongs to them, so nothing "
                "was overwritten"
            )
        if stated < len(self.entries):
            self.incomplete = (
                f"{CHECKPOINT_FILENAME} counts {stated} records while {RESULTS_FILENAME} holds "
                f"{len(self.entries)}; the run was interrupted between the two writes, so the results "
                "log is read as the fuller of the pair"
            )
            return
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if str(manifest["results_digest"]) != digest:
            raise CheckpointError(
                f"{where} and {RESULTS_FILENAME} disagree over the same {stated} records: the recorded "
                f"digest is {manifest['results_digest']} and the file reads {digest}, so the results "
                "were changed outside a run and nothing was overwritten"
            )
        try:
            stated_states = {state: int(manifest["states"].get(state, 0)) for state in STATES}
        except (TypeError, ValueError):
            raise CheckpointError(
                f"{where} counts {manifest['states']!r} by state, which is not a count per state"
            )
        if sum(stated_states.values()) != stated:
            raise CheckpointError(
                f"{where} counts {stated_states} by state, which is "
                f"{sum(stated_states.values())} records against its own total of {stated}"
            )
        if stated_states != found:
            raise CheckpointError(
                f"{where} counts {stated_states} but the {stated} results beside it hold {found}; the "
                "metadata was changed outside a run and nothing was overwritten"
            )

    def get(self, record_id: str) -> Optional[Entry]:
        return self.entries.get(str(record_id))

    def reusable(
        self,
        *,
        record_id: str,
        mode: str,
        prompt_version: str,
        input_hash: Optional[str],
        prompt_digest: str = "",
    ) -> Optional[Incident]:
        entry = self.get(record_id)
        if entry is None or entry.state not in REUSABLE_STATES:
            return None
        reason = self._why_not_reusable(
            entry,
            mode=mode,
            prompt_version=prompt_version,
            input_hash=input_hash,
            prompt_digest=prompt_digest,
        )
        if reason is not None:
            self.invalidated[entry.record_id] = reason
            return None
        try:
            return Incident.model_validate(entry.incident)
        except (ValidationError, TypeError) as error:
            raise CheckpointError(
                f"{entry.record_id}: its saved result is not what contract {SCHEMA_VERSION} accepts: {error}"
            ) from error

    def _why_not_reusable(
        self,
        entry: Entry,
        *,
        mode: str,
        prompt_version: str,
        input_hash: Optional[str],
        prompt_digest: str = "",
    ) -> Optional[str]:
        if entry.mode != mode:
            return f"it was read as {entry.mode} while this record routes as {mode}"
        if entry.prompt_version != prompt_version:
            return f"it was read by {entry.prompt_version} while this run reads with {prompt_version}"
        if entry.schema_version != SCHEMA_VERSION:
            return f"it was written for contract {entry.schema_version}, not {SCHEMA_VERSION}"
        if input_hash is None:
            return "this record cannot be fingerprinted, so its saved result cannot be matched to it"
        if entry.input_hash != input_hash:
            return "the record's own text or geography changed since it was read"
        if mode != Mode.SEMANTIC.value:
            return None
        if prompt_digest and entry.prompt_digest != prompt_digest:
            if not entry.prompt_digest:
                return (
                    f"it was read by {entry.prompt_version} before a saved reading carried the prompt "
                    "it was read with, so this run cannot tell whether the wording still matches"
                )
            return (
                f"it was read with prompt {entry.prompt_digest} while this run asks with "
                f"{prompt_digest}, so a rule in the prompt has since been reworded"
            )
        if self.candidates:
            if (entry.provider, entry.model) not in self.candidates:
                return (
                    f"it was read by {entry.provider or 'an unknown provider'}/"
                    f"{entry.model or 'an unknown model'}, which is not one of the candidates this run "
                    f"asks ({', '.join(f'{name}/{model}' for name, model in self.candidates)})"
                )
            return None
        if self.model and entry.model and self.model != entry.model:
            return f"it was read by model {entry.model} while this run is configured for {self.model}"
        if self.provider and entry.provider and self.provider != entry.provider:
            return f"it was read through {entry.provider} while this run reads through {self.provider}"
        return None

    def save(
        self,
        *,
        record_id: str,
        state: str,
        mode: str,
        prompt_version: str,
        input_hash: Optional[str] = None,
        incident: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        source_type: str = "",
        record_type: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        prompt_digest: str = "",
        attempts: Sequence[str] = (),
    ) -> Entry:
        if state not in STATES:
            raise CheckpointError(f"{state!r} is not a state this store records: {', '.join(STATES)}")
        if state in REUSABLE_STATES and incident is None:
            raise CheckpointError(f"a {state} entry must carry its Incident document")
        semantic = mode == Mode.SEMANTIC.value
        entry = Entry(
            record_id=str(record_id),
            state=state,
            mode=mode,
            prompt_version=prompt_version,
            schema_version=SCHEMA_VERSION,
            input_hash=input_hash,
            provider=((provider or self.provider) if semantic else ""),
            model=((model or self.model) if semantic else ""),
            source_type=source_type,
            record_type=record_type,
            error=error,
            incident=incident,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            prompt_digest=(prompt_digest if semantic else ""),
            attempts=tuple(str(item) for item in attempts if str(item).strip()),
        )
        previous = self.entries.get(entry.record_id)
        if (
            previous is not None
            and previous.state in REUSABLE_STATES
            and state not in REUSABLE_STATES
        ):
            self.preserved[entry.record_id] = (
                f"kept as {previous.state} from {previous.prompt_version}; this run's re-read ended "
                f"{state}: {_one_line(error)}"
            )
            return previous
        self.entries[entry.record_id] = entry
        return entry

    def counts(self) -> dict[str, int]:
        found = {state: 0 for state in STATES}
        for entry in self.entries.values():
            found[entry.state] = found.get(entry.state, 0) + 1
        return found

    def flush(self) -> None:
        body = "".join(entry.line() for entry in self.entries.values())
        metadata = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema_version": SCHEMA_VERSION,
            "base_url": self.base_url,
            "provider": self.provider,
            "model": self.model,
            "chain": [f"{name}/{model}" for name, model in self.candidates],
            "records": len(self.entries),
            "states": self.counts(),
            "results_file": RESULTS_FILENAME,
            "results_digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "superseded_entries": self.superseded,
        }
        try:
            _write_atomically(self.results_path, body)
            _write_atomically(self.checkpoint_path, json.dumps(metadata, indent=2) + "\n")
        except OSError as error:
            raise CheckpointError(
                f"progress could not be written to {self.directory}: {error}; nothing was discarded "
                "from the previous checkpoint"
            ) from error
        self.manifest = metadata
def _one_line(text: Optional[str]) -> str:
    return " ".join(str(text or "without a reason").split())[:220]


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


__all__ = [
    "CHECKPOINT_FILENAME",
    "COMPLETED",
    "Checkpoint",
    "CheckpointError",
    "DEFERRED",
    "Entry",
    "FAILED",
    "RESULTS_FILENAME",
    "REVIEW_REQUIRED",
    "REUSABLE_STATES",
    "STATES",
    "fingerprint",
]
