from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from intelligence.checkpoint import _write_atomically
from intelligence.contract import SCHEMA_VERSION

SNAPSHOT_FILENAME = "ingested.json"

UNDATED = "undated"
RECENT = "recent"
AWAITING = "awaiting a review decision"

NO_BASELINE = (
    f"this state directory holds neither {SNAPSHOT_FILENAME} nor a checkpoint entry it settled, so "
    "nothing can be called newly ingested and every record in the table would be read again — the "
    "historical reprocessing this selection exists to avoid. Settle what the platform already holds "
    "without reading it: --mark-baseline --state-dir DIR records the ids whose arrival stamp is older "
    "than today as already ingested, leaves today's arrivals selectable, asks no model and posts "
    "nothing. Then run --newly-ingested"
)


class SnapshotError(RuntimeError):
    pass


@dataclass
class Delta:
    records: list[dict[str, Any]]
    fetched: int
    found: int
    already: int
    left: int = 0
    duplicates: int = 0
    pending: int = 0


@dataclass
class Baseline:
    older_than: str
    marked: int
    settled_undated: int
    kept: dict[str, int]

    @property
    def skipped(self) -> int:
        return sum(self.kept.values())

    def kept_for(self, reason: str) -> int:
        return self.kept.get(reason, 0)


@dataclass
class Arrival:
    day: Optional[date]
    reason: str

    @property
    def known(self) -> bool:
        return self.day is not None


def arrival(value: Any) -> Arrival:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return Arrival(None, UNDATED)
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return Arrival(None, UNDATED)
    else:
        return Arrival(None, UNDATED)
    if moment.tzinfo is None:
        return Arrival(None, UNDATED)
    return Arrival(moment.astimezone(timezone.utc).date(), RECENT)


def _ids(payload: Mapping[str, Any], key: str) -> Optional[tuple[set[str], int]]:
    listed = payload.get(key)
    if listed is None:
        return set(), 0
    if not isinstance(listed, list):
        return None
    held = {value for value in listed if isinstance(value, str) and value}
    return held, len(listed) - len(held)


class IngestionSnapshot:
    # the exclusion set is record ids, never a timestamp: retrieved_at is stamped naive-local by the
    # news feed and UTC-aware by the weather and agriculture feeds, and GET /records never returns the
    # insert column it orders by. A clock is consulted once, by mark(), and only to keep a record
    # selectable — an ambiguous stamp is left out of the baseline rather than settled on a guess
    def __init__(self, directory: Any, *, base_url: str = "") -> None:
        self.directory = Path(str(directory))
        self.path = self.directory / SNAPSHOT_FILENAME
        self.base_url = base_url
        self.seen: set[str] = set()
        self.awaiting: set[str] = set()
        self.loaded = False
        self.absent = False
        self.unreadable: Optional[str] = None
        self.dropped = 0
        self.baseline = ""
        self.settled_through = ""
        self.written = False

    def load(self) -> "IngestionSnapshot":
        if not self.path.exists():
            self.absent = True
            self.loaded = True
            return self
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.unreadable = self._refusal(str(error))
            self.loaded = True
            return self
        if not isinstance(payload, Mapping):
            self.unreadable = self._refusal("it is not one object")
            self.loaded = True
            return self
        if not isinstance(payload.get("ingested"), list):
            self.unreadable = self._refusal("it holds no ingested list")
            self.loaded = True
            return self
        held = _ids(payload, "ingested")
        pending = _ids(payload, "awaiting") if "awaiting" in payload else (set(), 0)
        block = payload.get("baseline")
        if held is None or pending is None or (block is not None and not isinstance(block, Mapping)):
            self.unreadable = self._refusal("its record id lists are not lists of ids")
            self.loaded = True
            return self
        self.seen, self.awaiting = held[0], pending[0]
        self.dropped = held[1] + pending[1]
        if isinstance(block, Mapping):
            through = str(block.get("older_than") or "")
            self.settled_through = through
        self.baseline = self.describe_baseline()
        self.loaded = True
        return self

    def describe_baseline(self) -> str:
        held = f"{self.path.name} ({len(self.seen)} record id{'s' if len(self.seen) != 1 else ''}"
        if self.settled_through:
            return f"{held} settled through {self.settled_through})"
        return f"{held})"

    @staticmethod
    def _refusal(reason: str) -> str:
        return (
            f"{SNAPSHOT_FILENAME} {reason}, so nothing can be called newly ingested. Treating every "
            "record as new would read the whole table again, which is the historical reprocessing this "
            "selection exists to avoid"
        )

    def seed(self, record_ids: Iterable[str]) -> "IngestionSnapshot":
        self.seen = {str(value) for value in record_ids if str(value)}
        return self

    @property
    def known(self) -> int:
        return len(self.seen)

    @property
    def pending(self) -> int:
        return len(self.awaiting)

    def delta(self, records: Sequence[Mapping[str, Any]], *, limit: Optional[int] = None) -> Delta:
        fresh: list[dict[str, Any]] = []
        here: set[str] = set()
        already = duplicates = pending = 0
        for record in records:
            record_id = str(record.get("record_id") or "")
            if record_id and record_id in here:
                duplicates += 1
                continue
            here.add(record_id)
            if record_id and record_id in self.seen:
                already += 1
                continue
            if record_id in self.awaiting:
                pending += 1
            fresh.append(dict(record))
        left = 0
        if limit is not None and len(fresh) > limit:
            left = len(fresh) - limit
            fresh = fresh[:limit]
        return Delta(
            records=fresh,
            fetched=len(records),
            found=len(fresh) + left,
            already=already,
            left=left,
            duplicates=duplicates,
            pending=pending,
        )

    def mark(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        older_than: date,
        today: Optional[date] = None,
    ) -> Baseline:
        # a cutoff past the run's own day is the operator asking for the whole table, so a row whose
        # stamp carries no clock is settled with it; before that day an unclearable stamp keeps the
        # row selectable, because guessing it settled is how today's records go unread
        whole_table = older_than > (today or datetime.now(timezone.utc).date())
        marked = undated = 0
        kept: dict[str, int] = {}
        for record in records:
            record_id = str(record.get("record_id") or "")
            reached = arrival(record.get("retrieved_at"))
            if record_id in self.awaiting:
                reason = AWAITING
            elif not reached.known and not whole_table:
                reason = reached.reason
            elif reached.day is not None and reached.day >= older_than:
                reason = RECENT
            else:
                if record_id not in self.seen:
                    self.seen.add(record_id)
                    marked += 1
                    if not reached.known:
                        undated += 1
                continue
            kept[reason] = kept.get(reason, 0) + 1
        self.settled_through = older_than.isoformat()
        self.baseline = self.describe_baseline()
        return Baseline(
            older_than=self.settled_through,
            marked=marked,
            settled_undated=undated,
            kept=kept,
        )

    def settle(
        self,
        record_ids: Iterable[str],
        *,
        review: Iterable[str] = (),
    ) -> "IngestionSnapshot":
        completed = {str(value) for value in record_ids if str(value)}
        self.seen.update(completed)
        self.awaiting.update(str(value) for value in review if str(value))
        # a record that came back settled is no longer owed to a person, and one still owed a
        # decision is not something this directory has finished with
        self.awaiting.difference_update(completed)
        return self

    def flush(self) -> None:
        body = json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "schema_version": SCHEMA_VERSION,
                "base_url": self.base_url,
                "records": len(self.seen),
                "pending": len(self.awaiting),
                "ingested": sorted(self.seen),
                "awaiting": sorted(self.awaiting),
                "baseline": (
                    None
                    if not self.settled_through
                    else {"older_than": self.settled_through, "settled": len(self.seen)}
                ),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        try:
            _write_atomically(self.path, body)
        except OSError as error:
            raise SnapshotError(f"{self.path} could not be written: {error}") from error
        self.absent = False
        self.written = True


__all__ = [
    "AWAITING",
    "Baseline",
    "Delta",
    "IngestionSnapshot",
    "NO_BASELINE",
    "RECENT",
    "SNAPSHOT_FILENAME",
    "UNDATED",
    "Arrival",
    "SnapshotError",
    "arrival",
]
