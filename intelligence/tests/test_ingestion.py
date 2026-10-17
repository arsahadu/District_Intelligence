from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from intelligence import pipeline
from intelligence.contract import Issue, IssueCode, ReviewState
from intelligence.models.enums import EventType
from intelligence.records import MappingError
from intelligence.snapshot import SNAPSHOT_FILENAME, IngestionSnapshot
from intelligence.structured import process_record as process_structured
from intelligence.tests.builders import NeverCalled, listing, responder
from intelligence.tests.record_fixtures import market_price
from intelligence.tests.test_orchestration import BASE, poster, stub


def market(commodity: str, **fields: Any) -> dict[str, Any]:
    return market_price(commodity=commodity, **fields)


def arrived(*commodities: str, **fields: Any) -> list[dict[str, Any]]:
    return [market(name, **fields) for name in commodities]


def stamped(day: str, *commodities: str) -> list[dict[str, Any]]:
    return [market(name, retrieved_at=f"{day}T05:00:00+00:00") for name in commodities]


def clockless(day: str, *commodities: str) -> list[dict[str, Any]]:
    return [market(name, retrieved_at=f"{day}T05:00:00") for name in commodities]


def identifiers(directory: Any) -> set[str]:
    body = json.loads((directory / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    return set(body["ingested"])


def owed_ids(directory: Any) -> set[str]:
    body = json.loads((directory / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    return set(body["awaiting"])


def run(argv: list[str], *records: dict[str, Any], **kwargs: Any) -> int:
    return pipeline.main(
        argv,
        provider=kwargs.pop("provider", NeverCalled()),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(*records)),
        **kwargs,
    )


def test_the_first_newly_ingested_run_refuses_rather_than_reading_the_whole_table(
    tmp_path, capsys
):
    directory = tmp_path / "daily"
    fetch = responder(listing(market("Tomato")))

    code = pipeline.main(
        ["--newly-ingested", "--state-dir", str(directory)],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=fetch,
    )
    captured = capsys.readouterr()

    assert code == pipeline.EXIT_CHECKPOINT
    assert not fetch.calls
    assert "nothing can be called newly ingested" in captured.err
    assert "--mark-baseline" in captured.err
    assert not (directory / SNAPSHOT_FILENAME).exists()


def test_the_run_after_a_baseline_takes_only_the_records_that_arrived_since_it(tmp_path, capsys):
    directory = tmp_path / "daily"
    baseline = arrived("Tomato", "Onion")

    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    later = [*baseline, market("Brinjal")]
    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Ingestion baseline: the checkpoint (2 records it settled)" in out
    assert "Records fetched: 3" in out
    assert "Records ingested earlier: 2" in out
    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out
    assert identifiers(directory) == {record["record_id"] for record in later}

    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 0" in out
    assert "Records selected: 0" in out
    assert "Records attempted: 0" in out


def test_the_daily_run_takes_every_arrival_rather_than_the_default_limit(tmp_path, capsys):
    directory = tmp_path / "daily"
    new_commodities = ("Onion", "Brinjal", "Carrot", "Potato", "Beetroot", "Cabbage", "Ridgegourd")
    assert len(new_commodities) > pipeline.DEFAULT_LIMIT
    baseline = arrived("Tomato")

    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    later = [*baseline, *arrived(*new_commodities)]
    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert f"Records newly ingested: {len(new_commodities)}" in out
    assert f"Records selected: {len(new_commodities)}" in out
    assert "Records left for the next run" not in out


def test_an_explicit_limit_caps_the_arrivals_and_the_next_run_takes_what_was_left(
    tmp_path, capsys
):
    directory = tmp_path / "daily"
    baseline = arrived("Tomato")
    later = [*baseline, *arrived("Onion", "Brinjal", "Carrot", "Potato")]

    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    capped = ["--newly-ingested", "--limit", "2", "--state-dir", str(directory)]
    assert run(capped, *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 4" in out
    assert "Records selected: 2" in out
    assert "Records left for the next run: 2" in out
    assert identifiers(directory) == {record["record_id"] for record in arrived("Tomato", "Onion", "Brinjal")}

    assert run(capped, *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 2" in out
    assert "Records selected: 2" in out
    assert "Records left for the next run" not in out
    assert identifiers(directory) == {record["record_id"] for record in later}


def test_a_record_the_run_could_not_settle_is_still_newly_ingested_next_run(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "daily"
    broken = market("Onion")["record_id"]

    def reading(record: dict, **kwargs: Any):
        if record["record_id"] == broken:
            raise MappingError("the price line arrived without a market")
        return process_structured(record)

    monkeypatch.setattr(pipeline, "process_record", reading)
    baseline = arrived("Tomato")
    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    later = [*baseline, market("Onion")]
    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_PARTIAL
    out = capsys.readouterr().out

    assert "Records newly ingested: 1" in out
    assert "record unreadable" in out
    assert identifiers(directory) == {record["record_id"] for record in baseline}

    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_PARTIAL
    out = capsys.readouterr().out

    assert "Records ingested earlier: 1" in out
    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out


def test_an_arrival_stamped_in_another_clock_is_still_found_as_new(tmp_path, capsys):
    directory = tmp_path / "daily"
    baseline = arrived("Tomato")
    older = market("Onion", retrieved_at="2020-01-01T00:00:00")

    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    assert run(["--newly-ingested", "--state-dir", str(directory)], *baseline, older) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out


def test_a_snapshot_that_cannot_be_read_stops_the_run_rather_than_reprocessing_everything(
    tmp_path, capsys
):
    directory = tmp_path / "daily"
    assert run(["--all", "--state-dir", str(directory)], *arrived("Tomato")) == pipeline.EXIT_OK
    (directory / SNAPSHOT_FILENAME).write_text("{ not json", encoding="utf-8")
    capsys.readouterr()
    fetch = responder(listing(*arrived("Tomato", "Onion")))

    code = pipeline.main(
        ["--newly-ingested", "--state-dir", str(directory)],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=fetch,
    )
    captured = capsys.readouterr()

    assert code == pipeline.EXIT_CHECKPOINT
    assert not fetch.calls
    assert "nothing can be called newly ingested" in captured.err
    assert "historical reprocessing" in captured.err


def test_a_record_the_backend_returned_twice_is_read_once_and_says_so(tmp_path, capsys):
    directory = tmp_path / "daily"
    assert run(["--all", "--state-dir", str(directory)], *arrived("Tomato")) == pipeline.EXIT_OK
    capsys.readouterr()

    later = [*arrived("Tomato"), market("Onion"), market("Onion")]
    assert run(["--newly-ingested", "--state-dir", str(directory)], *later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records fetched: 3" in out
    assert "Records the backend returned twice: 1" in out
    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out


def test_the_daily_run_posts_only_what_arrived(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "daily"
    baseline = arrived("Tomato")
    assert run(["--all", "--state-dir", str(directory)], *baseline) == pipeline.EXIT_OK
    capsys.readouterr()

    newcomer = market("Onion")
    monkeypatch.setattr(
        pipeline,
        "process_record",
        lambda record, **kwargs: (
            stub(record, incident_type=EventType.URBAN_WATERLOGGING, accepted=True)
            if record["record_id"] == newcomer["record_id"]
            else process_structured(record)
        ),
    )
    calls, transport = poster()
    later = [*baseline, newcomer]
    code = pipeline.main(
        ["--newly-ingested", "--state-dir", str(directory), "--post-incidents"],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(*later)),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert len(calls) == 1
    assert json.loads(calls[0][1])["provenance"]["record_id"] == newcomer["record_id"]
    assert f"POST /incidents: INC-{newcomer['record_id']} created" in out


@pytest.mark.parametrize(
    "argv,fragment",
    [
        (["--newly-ingested"], "give it --state-dir"),
        (["--newly-ingested", "--all", "--state-dir", "run"], "already says which records to read"),
        (["--newly-ingested", "--record-id", "R", "--state-dir", "run"], "already says which"),
    ],
)
def test_the_selection_refuses_the_flags_that_would_choose_the_records_themselves(argv, fragment):
    with pytest.raises(SystemExit) as exit_state:
        pipeline.parse_args(argv)

    assert exit_state.value.code == 2


def test_a_delta_separates_arrivals_from_what_the_snapshot_has_settled(tmp_path):
    snapshot = IngestionSnapshot(tmp_path / "daily").load()
    assert snapshot.absent and snapshot.known == 0

    delta = snapshot.delta([*arrived("Tomato"), market("Onion"), market("Onion")], limit=1)
    assert [record["record_id"] for record in delta.records] == [market("Tomato")["record_id"]]
    assert (delta.fetched, delta.found, delta.already, delta.left, delta.duplicates) == (
        3, 2, 0, 1, 1,
    )

    uncapped = snapshot.delta(arrived("Tomato", "Onion"))
    assert (uncapped.found, uncapped.left) == (2, 0)
    # a row no feed gave an id is still handed to the ordinary path, which reports it
    assert snapshot.delta([dict(market("Potato"), record_id="")]).found == 1

    settled = snapshot.settle([market("Tomato")["record_id"]])
    after = settled.delta(arrived("Tomato", "Onion"))
    assert after.already == 1 and after.found == 1


def test_a_snapshot_round_trips_the_ids_it_held(tmp_path):
    directory = tmp_path / "daily"
    snapshot = IngestionSnapshot(directory, base_url=BASE).load()
    assert snapshot.absent and snapshot.known == 0

    delta = snapshot.delta(arrived("Tomato", "Onion"))
    snapshot.settle([delta.records[0]["record_id"]], review=[delta.records[1]["record_id"]])
    snapshot.flush()

    again = IngestionSnapshot(directory).load()
    assert not again.absent
    assert again.baseline == "ingested.json (1 record id)"
    assert again.seen == {market("Tomato")["record_id"]}
    assert again.awaiting == {market("Onion")["record_id"]}
    assert again.delta(arrived("Tomato", "Onion")).already == 1


def test_a_marked_baseline_settles_the_past_and_leaves_this_week_selectable(tmp_path, capsys):
    directory = tmp_path / "daily"
    history = stamped("2026-10-10", "Tomato", "Onion")
    arrived_today = stamped("2026-10-16", "Brinjal")
    fetch = responder(listing(*history, *arrived_today))

    code = pipeline.main(
        ["--mark-baseline", "--state-dir", str(directory), "--baseline-older-than", "2026-10-16"],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=fetch,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert len(fetch.calls) == 1
    assert "Arrival cutoff: records stamped before 2026-10-16 are settled" in out
    assert "Records settled: 2" in out
    assert "Records left selectable: 1 (recent)" in out
    assert "Snapshot now settles: 2 record id(s)" in out
    assert identifiers(directory) == {record["record_id"] for record in history}
    assert not (directory / "checkpoint.json").exists()
    assert not (directory / "results.jsonl").exists()


def test_the_run_after_a_marked_baseline_reads_only_what_the_cutoff_left(tmp_path, capsys):
    directory = tmp_path / "daily"
    history = stamped("2026-10-10", "Tomato")
    fresh = stamped("2026-10-16", "Brinjal")[0]

    assert run(
        ["--mark-baseline", "--state-dir", str(directory), "--baseline-older-than", "2026-10-16"],
        *history,
        fresh,
    ) == pipeline.EXIT_OK
    capsys.readouterr()

    assert run(["--newly-ingested", "--state-dir", str(directory)], *history, fresh) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Ingestion baseline: ingested.json (1 record id settled through 2026-10-16)" in out
    assert "Records fetched: 2" in out
    assert "Records ingested earlier: 1" in out
    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out

    assert run(["--newly-ingested", "--state-dir", str(directory)], *history, fresh) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 0" in out
    assert "Records selected: 0" in out


def test_the_default_cutoff_is_todays_own_day(tmp_path, capsys):
    directory = tmp_path / "daily"
    now = datetime.now(timezone.utc)
    older = market("Tomato", retrieved_at=(now - timedelta(days=3)).isoformat())
    later = market("Onion", retrieved_at=(now + timedelta(days=3)).isoformat())

    assert run(["--mark-baseline", "--state-dir", str(directory)], older, later) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert f"Arrival cutoff: records stamped before {now.date()} are settled" in out
    assert "Records settled: 1" in out
    assert "Records left selectable: 1 (recent)" in out
    assert identifiers(directory) == {older["record_id"]}


def test_a_row_whose_stamp_carries_no_clock_is_left_selectable(tmp_path, capsys):
    directory = tmp_path / "daily"
    dated = stamped("2026-10-10", "Tomato")
    naive = clockless("2026-10-10", "Onion")
    blank = [market("Brinjal", retrieved_at="")]
    unreadable = [market("Carrot", retrieved_at="last Tuesday")]

    assert run(
        ["--mark-baseline", "--state-dir", str(directory), "--baseline-older-than", "2026-10-16"],
        *dated,
        *naive,
        *blank,
        *unreadable,
    ) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records settled: 1" in out
    assert "Records left selectable: 3 (undated)" in out
    assert identifiers(directory) == {dated[0]["record_id"]}

    assert run(["--newly-ingested", "--state-dir", str(directory)], *naive) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out


def test_a_cutoff_past_todays_own_day_settles_the_rows_with_no_clock(tmp_path, capsys):
    directory = tmp_path / "daily"
    dated = stamped("2026-10-10", "Tomato")
    naive = clockless("2026-10-10", "Onion")
    today = stamped("2026-10-16", "Brinjal")

    assert run(
        ["--mark-baseline", "--state-dir", str(directory), "--baseline-older-than", "2030-01-01"],
        *dated,
        *naive,
        *today,
    ) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records settled: 3" in out
    assert "Settled without an arrival clock: 1" in out
    assert identifiers(directory) == {record["record_id"] for record in dated + naive + today}


def test_marking_a_baseline_again_settles_only_what_has_arrived_since(tmp_path, capsys):
    directory = tmp_path / "daily"
    history = stamped("2026-10-10", "Tomato", "Onion")
    argv = ["--mark-baseline", "--state-dir", str(directory), "--baseline-older-than", "2026-10-16"]

    assert run(argv, *history) == pipeline.EXIT_OK
    capsys.readouterr()

    again = [*history, *stamped("2026-10-12", "Brinjal")]
    assert run(argv, *again) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records settled: 1" in out
    assert "Snapshot now settles: 3 record id(s)" in out
    assert identifiers(directory) == {record["record_id"] for record in again}


def test_a_record_owed_a_decision_is_never_recorded_as_ingested(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "daily"
    owed_id = market("Onion")["record_id"]
    owed = {owed_id}

    def reading(record: dict, **kwargs: Any):
        found = process_structured(record)
        if record["record_id"] not in owed:
            return found
        return found.model_copy(
            update={
                "validation": found.validation.model_copy(
                    update={
                        "state": ReviewState.REVIEW_REQUIRED,
                        "issues": [
                            Issue(
                                code=IssueCode.PROVIDER_WARNING,
                                field="severity",
                                detail="the source gave no severity",
                            )
                        ],
                    }
                )
            }
        )

    monkeypatch.setattr(pipeline, "process_record", reading)
    records = arrived("Tomato", "Onion")
    assert run(["--all", "--state-dir", str(directory)], *records) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Review-required: 1" in out
    assert not (directory / SNAPSHOT_FILENAME).exists()

    assert run(["--newly-ingested", "--state-dir", str(directory)], *records) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 1" in out
    assert "Records selected: 1" in out
    assert "leaves 1 owed to a decision" in out
    assert identifiers(directory) == {market("Tomato")["record_id"]}
    assert owed_ids(directory) == {owed_id}

    assert run(["--newly-ingested", "--state-dir", str(directory)], *records) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records already awaiting a decision: 1" in out
    assert "Records selected: 1" in out

    owed.clear()
    assert run(
        ["--newly-ingested", "--no-resume", "--state-dir", str(directory)], *records
    ) == pipeline.EXIT_OK
    capsys.readouterr()

    assert identifiers(directory) == {record["record_id"] for record in records}
    assert owed_ids(directory) == set()

    assert run(["--newly-ingested", "--state-dir", str(directory)], *records) == pipeline.EXIT_OK
    out = capsys.readouterr().out

    assert "Records newly ingested: 0" in out


def test_a_snapshot_whose_owed_list_is_not_a_list_refuses_the_run(tmp_path, capsys):
    directory = tmp_path / "daily"
    assert run(["--all", "--state-dir", str(directory)], *arrived("Tomato")) == pipeline.EXIT_OK
    damaged = json.dumps({"ingested": [market("Tomato")["record_id"]], "awaiting": "not a list"})
    (directory / SNAPSHOT_FILENAME).write_text(damaged, encoding="utf-8")
    capsys.readouterr()
    fetch = responder(listing(*arrived("Tomato", "Onion")))

    code = pipeline.main(
        ["--newly-ingested", "--state-dir", str(directory)],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=fetch,
    )
    captured = capsys.readouterr()

    assert code == pipeline.EXIT_CHECKPOINT
    assert not fetch.calls
    assert "its record id lists are not lists of ids" in captured.err
    assert (directory / SNAPSHOT_FILENAME).read_text(encoding="utf-8") == damaged


@pytest.mark.parametrize(
    "argv",
    [
        ["--mark-baseline"],
        ["--mark-baseline", "--all", "--state-dir", "run"],
        ["--mark-baseline", "--record-id", "R", "--state-dir", "run"],
        ["--mark-baseline", "--newly-ingested", "--state-dir", "run"],
        ["--mark-baseline", "--limit", "2", "--state-dir", "run"],
        ["--mark-baseline", "--post-incidents", "--state-dir", "run"],
        ["--mark-baseline", "--link-events", "--state-dir", "run"],
        ["--mark-baseline", "--state-dir", "run", "--ask", "what changed?"],
        ["--mark-baseline", "--state-dir", "run", "--baseline-older-than", "last Tuesday"],
        ["--baseline-older-than", "2026-10-01", "--all", "--state-dir", "run"],
    ],
)
def test_marking_a_baseline_refuses_the_flags_that_would_read_or_write_records(argv):
    with pytest.raises(SystemExit) as exit_state:
        pipeline.parse_args(argv)

    assert exit_state.value.code == 2


def test_settling_a_record_takes_it_off_the_list_a_person_owes_a_decision_on(tmp_path):
    snapshot = IngestionSnapshot(tmp_path / "daily").load()
    snapshot.settle(["A"], review=["B"])
    assert (snapshot.seen, snapshot.awaiting) == ({"A"}, {"B"})

    snapshot.settle(["B"])
    assert (snapshot.seen, snapshot.awaiting) == ({"A", "B"}, set())
    delta = snapshot.delta([{"record_id": "A"}, {"record_id": "B"}])
    assert (delta.found, delta.already, delta.pending) == (0, 2, 0)


def test_a_record_still_owed_a_decision_is_counted_apart_but_still_selected(tmp_path):
    snapshot = IngestionSnapshot(tmp_path / "daily").load()
    snapshot.settle([], review=["B"])

    delta = snapshot.delta([{"record_id": "A"}, {"record_id": "B"}])
    assert [record["record_id"] for record in delta.records] == ["A", "B"]
    assert (delta.found, delta.already, delta.pending) == (2, 0, 1)
