from __future__ import annotations

import io
import json
import tempfile
from typing import Any

from intelligence import pipeline
from intelligence.contract import RecordKind
from intelligence.models.enums import EventType
from intelligence.posting import (
    CREATED,
    DUPLICATE,
    FAILED,
    POSTED_FILENAME,
    PostLedger,
    document_digest,
    post_all,
    print_postings,
)
from intelligence.structured import process_record as process_structured
from intelligence.tests.builders import (
    NeverCalled,
    Scripted,
    listing,
    responder,
    single,
    water_answer,
)
from intelligence.tests.record_fixtures import (
    market_price,
    twin_article,
    waterlogging,
    weather_forecast,
)
from intelligence.tests.test_orchestration import BASE, poster, stub


def accepted_stub(record: dict, **_kwargs: Any) -> Any:
    return stub(record, incident_type=EventType.URBAN_WATERLOGGING, accepted=True)


def read_ledger(directory: Any) -> dict:
    return json.loads((directory / POSTED_FILENAME).read_text(encoding="utf-8"))


def test_an_unchanged_reading_is_sent_once_and_a_later_run_does_not_send_it_again(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(pipeline, "process_record", accepted_stub)
    directory = tmp_path / "run"
    argv = ["--limit", "1", "--state-dir", str(directory), "--post-incidents"]
    calls, transport = poster()

    first = pipeline.main(
        argv,
        provider=Scripted(payload={}),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert first == pipeline.EXIT_OK
    assert len(calls) == 1
    assert "POST /incidents: INC-NEWS-MDU-WLR-0001 created (HTTP 201)" in out
    assert "Posting: 1 sent = 1 created" in out

    again = pipeline.main(
        argv,
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert again == pipeline.EXIT_OK
    assert len(calls) == 1
    assert "Not posted: INC-NEWS-MDU-WLR-0001 holds exactly what was already sent on" in out
    assert "Posting: 0 sent = 1 unchanged, not sent" in out
    held = read_ledger(directory)["posted"]["INC-NEWS-MDU-WLR-0001"]
    assert held["state"] == CREATED and held["record_id"] == "NEWS-MDU-WLR-0001"


def test_a_newer_reading_of_a_held_incident_is_sent_again_and_says_the_copy_was_not_replaced(tmp_path):
    directory = tmp_path / "run"
    posted: list[tuple[str, bytes]] = []

    def created(url: str, body: bytes, timeout: float) -> tuple[int, str]:
        posted.append((url, body))
        return 201, body.decode("utf-8")

    first_reading = process_structured(market_price())
    ledger = PostLedger(directory, base_url=BASE)
    post_all([first_reading], base_url=BASE, transport=created, ledger=ledger)
    ledger.flush()

    newer_reading = process_structured(market_price(min_price=40.0))
    assert newer_reading.incident_id == first_reading.incident_id

    calls, transport_409 = poster(409)
    reloaded = PostLedger(directory).load()
    report = post_all([newer_reading], base_url=BASE, transport=transport_409, ledger=reloaded)
    reloaded.flush()
    printed = io.StringIO()
    print_postings(report, stream=printed)

    assert len(calls) == 1
    assert [row.state for row in report.rows] == [DUPLICATE]
    assert "only ever creates" in report.rows[0].note
    assert "Posting: 1 sent = 1 already existed (1 posted copy differs from the newer reading)" in (
        printed.getvalue()
    )
    held = read_ledger(directory)["posted"][newer_reading.incident_id]
    assert held["state"] == CREATED
    assert held["digest"] == document_digest(first_reading)
    assert held["digest"] != document_digest(newer_reading)
    assert held["previous"] == document_digest(first_reading)


def test_a_refused_post_is_left_out_of_the_ledger_so_the_next_run_sends_it_again(
    tmp_path, capsys, monkeypatch
):
    directory = tmp_path / "run"
    argv = ["--limit", "1", "--state-dir", str(directory), "--post-incidents"]
    monkeypatch.setattr(pipeline, "process_record", accepted_stub)
    calls, failing = poster(503)

    refused = pipeline.main(
        argv,
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=failing,
    )
    captured = capsys.readouterr()

    assert refused == pipeline.EXIT_POST
    assert "POST /incidents failed for INC-NEWS-MDU-WLR-0001" in captured.err
    assert "Posting: 1 sent = 1 failed" in captured.out
    assert read_ledger(directory)["posted"] == {}

    sent, working = poster()
    retried = pipeline.main(
        argv,
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=working,
    )
    out = capsys.readouterr().out

    assert retried == pipeline.EXIT_OK
    assert len(sent) == 1
    assert "POST /incidents: INC-NEWS-MDU-WLR-0001 created (HTTP 201)" in out


def test_one_refused_record_never_hides_the_record_that_was_created(capsys, monkeypatch):
    monkeypatch.setattr(pipeline, "process_record", accepted_stub)
    calls: list[tuple[str, bytes]] = []

    def transport(url: str, body: bytes, timeout: float) -> tuple[int, str]:
        calls.append((url, body))
        if len(calls) == 1:
            return 500, "the backend is down"
        return 201, body.decode("utf-8")

    code = pipeline.main(
        ["--limit", "2", "--post-incidents"],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging(), twin_article())),
        incident_transport=transport,
    )
    captured = capsys.readouterr()

    assert code == pipeline.EXIT_POST
    assert len(calls) == 2
    assert "POST /incidents failed for INC-NEWS-MDU-WLR-0001" in captured.err
    assert "POST /incidents: INC-NEWS-MDU-0002 created (HTTP 201)" in captured.out
    assert "Posting: 2 sent = 1 created; 1 failed" in captured.out


def test_a_run_that_reads_a_forecast_and_a_price_line_sends_only_its_incident(capsys):
    calls, transport = poster()

    code = pipeline.main(
        ["--all", "--post-incidents"],
        provider=Scripted(payload=water_answer()),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging(), weather_forecast(), market_price())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert {json.loads(body.decode("utf-8"))["incident_id"] for _, body in calls} == {
        "INC-NEWS-MDU-WLR-0001"
    }
    assert "POST /incidents: INC-NEWS-MDU-WLR-0001 created (HTTP 201)" in out
    assert "Posting: 1 sent = 1 created" in out
    assert "Accepted context: 2" in out
    assert "INC-WEATHER-MDU-03-Oct created" not in out
    assert "INC-AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03 created" not in out


def test_a_ledger_that_cannot_be_read_is_said_rather_than_silently_ignored(
    tmp_path, capsys, monkeypatch
):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / POSTED_FILENAME).write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(pipeline, "process_record", accepted_stub)
    calls, transport = poster(409)

    code = pipeline.main(
        [
            "--record-id", waterlogging()["record_id"], "--state-dir", str(directory),
            "--post-incidents",
        ],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(single(waterlogging())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert len(calls) == 1
    assert "nothing in it is trusted" in out
    assert "stores nothing twice" in out
    assert read_ledger(directory)["posted"]["INC-NEWS-MDU-WLR-0001"]["state"] == DUPLICATE


def test_a_review_required_reading_is_never_sent_and_says_nothing_was_sent(capsys, monkeypatch):
    monkeypatch.setattr(
        pipeline, "process_record", lambda record, **kwargs: stub(record, accepted=False)
    )
    calls, transport = poster()

    code = pipeline.main(
        ["--limit", "1", "--post-incidents"],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert not calls
    assert "POST /incidents: nothing to send" in out
    assert "Posting:" not in out


def test_a_forecast_entry_an_earlier_build_wrote_in_the_ledger_is_left_alone(tmp_path, capsys):
    directory = tmp_path / "run"
    directory.mkdir()
    held = process_structured(weather_forecast())
    ledger = PostLedger(directory, base_url=BASE)
    ledger.hold(
        held, digest=document_digest(held), state=CREATED, status_code=201, previous=None
    )
    ledger.flush()
    calls, transport = poster()

    code = pipeline.main(
        [
            "--record-id", "WEATHER-MDU-03-Oct", "--state-dir", str(directory),
            "--post-incidents",
        ],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(single(weather_forecast())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert not calls
    assert "POST /incidents: nothing to send" in out
    assert "Accepted context: 1" in out
    entry = read_ledger(directory)["posted"]["INC-WEATHER-MDU-03-Oct"]
    assert entry["kind"] == RecordKind.FORECAST.value and entry["state"] == CREATED


def test_posting_without_a_state_dir_says_that_nothing_is_remembered_between_runs(
    capsys, monkeypatch
):
    monkeypatch.setattr(pipeline, "process_record", accepted_stub)
    calls, transport = poster(409)

    code = pipeline.main(
        ["--limit", "1", "--post-incidents"],
        provider=NeverCalled(),
        environ={pipeline.ENV_API_BASE_URL: BASE},
        transport=responder(listing(waterlogging())),
        incident_transport=transport,
    )
    out = capsys.readouterr().out

    assert code == pipeline.EXIT_OK
    assert len(calls) == 1
    assert "Posting ledger: none, because --state-dir was not given" in out
    assert "POST /incidents: INC-NEWS-MDU-WLR-0001 already exists (duplicate) (HTTP 409)" in out
    assert "Posting: 1 sent = 1 already existed" in out


def test_a_refused_row_is_printed_to_the_error_stream_and_holds_nothing(tmp_path):
    ledger = PostLedger(tempfile.mkdtemp(), base_url=BASE).load()
    report = post_all(
        [process_structured(market_price())],
        base_url=BASE,
        transport=lambda *_: (422, "unsupported"),
        ledger=ledger,
    )
    ledger.flush()
    out, err = io.StringIO(), io.StringIO()
    print_postings(report, stream=out, errors=err)

    assert [row.state for row in report.rows] == [FAILED]
    assert report.failed[0].record_id == market_price()["record_id"]
    assert ledger.entries == {}
    assert err.getvalue().startswith("POST /incidents failed for INC-")
    assert "Posting: 1 sent = 1 failed" in out.getvalue()
