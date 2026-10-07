"""The orchestrator: reads CommonRecords from the platform API, runs the chain, reports the run."""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Optional

import pytest

from intelligence.contract import Claim, Incident, Provenance, ReviewState
from intelligence.llm import LLMProvider, LLMRequest, LLMResponse, TransportError
from intelligence.models.enums import EventType, ExtractionMethod
from intelligence import pipeline
from intelligence.pipeline import (
    BackendError,
    DEFAULT_API_BASE_URL,
    DEFAULT_LIMIT,
    ENV_API_BASE_URL,
    api_base_url,
    describe_incident,
    fetch_record,
    fetch_records,
    main,
    run_pipeline,
)
from intelligence.tests.test_pipeline import Scripted, water_answer
from intelligence.tests.record_fixtures import market_price, waterlogging, weather_forecast

BASE = "http://backend.test"

PRICE_PAYLOAD = {
    "title": {"value": market_price()["title"], "field": "title"},
    "description": {"value": "46.0", "field": "data.min_price", "quote": "46.0", "char_start": 0},
    "category": {"value": "agriculture", "field": "data.commodity", "quote": "Tomato",
                 "char_start": 0},
}


def responder(*responses: tuple[int, str]) -> Any:
    """A transport that answers each read with the next status/body and remembers the URLs."""
    calls: list[str] = []
    queue = list(responses)

    def transport(url: str, timeout_seconds: float) -> tuple[int, str]:
        calls.append(url)
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return status, body

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def unreachable(error: BaseException) -> Any:
    def transport(url: str, timeout_seconds: float) -> tuple[int, str]:
        raise error

    return transport


def listing(*records: dict) -> tuple[int, str]:
    return 200, json.dumps(list(records))


def single(record: dict) -> tuple[int, str]:
    return 200, json.dumps(record)


def stub(record: dict, *, incident_type: Optional[EventType] = None) -> Incident:
    """A minimal legal Incident: enough to count, with no model in the loop."""
    provenance = Provenance(
        record_id=record["record_id"],
        source_id=record["source_id"],
        source_type=record["source_type"],
    )
    claims = [
        Claim(
            field="title",
            value=record["title"],
            method=ExtractionMethod.SOURCE_METADATA,
            review=ReviewState.UNRESOLVED,
        )
    ]
    fields: dict[str, Any] = {"title": record["title"]}
    if incident_type is not None:
        fields["incident_type"] = incident_type
        claims.append(
            Claim(
                field="incident_type",
                value=incident_type.value,
                method=ExtractionMethod.SOURCE_METADATA,
                review=ReviewState.UNRESOLVED,
            )
        )
    return Incident(
        incident_id=f"INC-{record['record_id']}",
        provenance=provenance,
        claims=claims,
        **fields,
    )


def test_the_backend_url_defaults_to_the_local_api_and_can_be_configured():
    assert api_base_url({}) == DEFAULT_API_BASE_URL
    assert api_base_url({ENV_API_BASE_URL: ""}) == DEFAULT_API_BASE_URL
    assert api_base_url({ENV_API_BASE_URL: "https://api.example.test/"}) == "https://api.example.test"


def test_a_backend_url_that_is_not_a_url_is_refused_by_name():
    with pytest.raises(BackendError, match=ENV_API_BASE_URL):
        api_base_url({ENV_API_BASE_URL: "127.0.0.1:8000"})


def test_records_are_read_from_get_records_and_only_a_small_number_is_processed():
    records = [dict(weather_forecast(), record_id=f"W-{index}") for index in range(DEFAULT_LIMIT + 4)]
    transport = responder(listing(*records))
    provider = Scripted(payload={})

    summary = run_pipeline(base_url=BASE, provider=provider, transport=transport)

    assert transport.calls == [f"{BASE}/records"]
    assert summary.fetched == 9
    assert summary.processed == DEFAULT_LIMIT
    assert summary.base_url == BASE


def test_all_is_the_only_way_to_process_every_record():
    records = [dict(weather_forecast(), record_id=f"W-{index}") for index in range(3)]
    transport = responder(listing(*records))

    summary = run_pipeline(
        limit=None, all_records=True, base_url=BASE, provider=Scripted(payload={}),
        transport=transport,
    )

    assert summary.processed == 3 == summary.fetched


def test_record_id_mode_reads_one_record_and_quotes_the_path():
    record = dict(market_price(), record_id="AGRI 7")
    transport = responder(single(record))

    summary = run_pipeline(
        record_id="AGRI 7", base_url=BASE, provider=Scripted(payload=PRICE_PAYLOAD),
        transport=transport,
    )

    assert transport.calls == [f"{BASE}/records/AGRI%207"]
    assert (summary.fetched, summary.processed) == (1, 1)
    assert summary.incidents == [] and len(summary.contextual) == 1


def test_one_provider_is_built_for_the_run_and_a_missing_one_stops_it_before_any_read():
    transport = responder(listing(waterlogging()))

    code = main(["--record-id", "NEWS-MDU-WLR-0001"], environ={}, transport=transport)

    assert code == pipeline.EXIT_PROVIDER
    assert transport.calls == []


def test_the_run_classifies_incidents_context_and_failures():
    transport = responder(listing(waterlogging(), market_price()))
    payloads = [water_answer(), PRICE_PAYLOAD]

    class InOrder(LLMProvider):
        name = "scripted"

        @property
        def model(self) -> str:
            return "gpt-oss-120b"

        def complete(self, request: LLMRequest) -> LLMResponse:
            payload = payloads.pop(0)
            return LLMResponse(provider=self.name, model=self.model, data=payload, latency_ms=9.0)

    summary = run_pipeline(
        limit=None, all_records=True, base_url=BASE, provider=InOrder(), transport=transport
    )

    assert (len(summary.incidents), len(summary.contextual)) == (1, 1)
    assert summary.failures == [] and summary.review_required == []
    assert summary.counts()["Incidents produced"] == 1
    assert summary.counts()["Non-incident/contextual"] == 1
    assert summary.incidents[0].incident.incident_type is EventType.URBAN_WATERLOGGING
    assert summary.contextual[0].incident.incident_type is EventType.UNRESOLVED


def test_a_record_that_cannot_be_processed_is_reported_and_the_run_continues(monkeypatch):
    records = [waterlogging(), market_price(), weather_forecast()]

    def only_two(record, **kwargs):
        if record["record_id"] == market_price()["record_id"]:
            raise TransportError("groq call failed: HTTP 413 over the tokens per minute limit")
        return stub(record, incident_type=EventType.URBAN_WATERLOGGING)

    monkeypatch.setattr(pipeline, "process_record", only_two)
    summary = run_pipeline(
        limit=None, all_records=True, base_url=BASE, provider=Scripted(payload={}),
        transport=responder(listing(*records)),
    )

    assert summary.processed == 3
    assert [item.record_id for item in summary.failures] == [market_price()["record_id"]]
    assert "HTTP 413" in summary.failures[0].error
    assert len(summary.incidents) == 2


def test_a_response_that_is_not_a_list_of_records_is_a_clear_failure():
    for body, message in (
        ('{"records": []}', "where the records list should be"),
        ("[7]", "expected one CommonRecord object"),
        ('[{"title": "no id here"}]', "without a record_id"),
        ("not json at all", "not JSON"),
    ):
        transport = responder((200, body))
        with pytest.raises(BackendError, match=message):
            fetch_records(base_url=BASE, transport=transport)


def test_a_single_record_response_is_matched_to_the_record_that_was_asked_for():
    transport = responder(single(waterlogging()))
    with pytest.raises(BackendError, match="not matched to the request"):
        fetch_record("SOMETHING-ELSE", base_url=BASE, transport=transport)

    found = fetch_record(waterlogging()["record_id"], base_url=BASE, transport=transport)
    assert found["record_id"] == waterlogging()["record_id"]


def test_an_error_status_or_an_unreachable_backend_says_which():
    with pytest.raises(BackendError, match="HTTP 500"):
        fetch_records(base_url=BASE, transport=responder((500, "database is down")))

    with pytest.raises(BackendError, match="is the backend running"):
        fetch_records(
            base_url=BASE,
            transport=unreachable(urllib.error.URLError(ConnectionRefusedError())),
        )


def test_the_console_line_names_every_field_a_run_is_read_by():
    incident = stub(waterlogging(), incident_type=EventType.URBAN_WATERLOGGING)
    line = describe_incident(incident)

    assert line.startswith(incident.incident_id)
    for label in ("type=", "category=", "department=", "severity=", "priority=", "status=",
                  "event_time=", "review="):
        assert label in line
    assert "type=urban_waterlogging" in line
    assert "priority=-" in line


def test_the_summary_prints_counts_and_groups_the_results(capsys):
    summary = pipeline.RunSummary(base_url=BASE, fetched=42)
    summary.add(pipeline.Outcome("R-1", "news", "article",
                                 incident=stub(waterlogging(),
                                               incident_type=EventType.URBAN_WATERLOGGING)))
    summary.add(pipeline.Outcome("R-2", "agriculture", "price", incident=stub(market_price())))
    summary.add(pipeline.Outcome("R-3", "weather", "warning", error="extraction refused: no quote"))

    pipeline.print_report(summary)
    out = capsys.readouterr().out

    for label, value in (
        ("Backend URL", BASE),
        ("Records fetched", 42),
        ("Records processed", 3),
        ("Incidents produced", 1),
        ("Non-incident/contextual", 1),
        ("Review-required results", 2),
        ("Failures", 1),
    ):
        assert f"{label}: {value}" in out
    assert "Failures (1)" in out and "R-3 failed: extraction refused" in out
    assert "no incident type was supported by a quote" in out
    assert "Needs a person: INC-NEWS-MDU-WLR-0001, INC-AGRI" in out
    assert out.count("INC-NEWS-MDU-WLR-0001 |") == 1


def test_no_argument_processes_only_the_safe_default_number_of_records(capsys, monkeypatch):
    records = [dict(waterlogging(), record_id=f"NEWS-{index}") for index in range(DEFAULT_LIMIT + 3)]
    monkeypatch.setattr(pipeline, "process_record", lambda record, **kwargs: stub(record))

    code = main(
        [],
        provider=Scripted(payload={}),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*records)),
    )

    out = capsys.readouterr().out
    assert code == pipeline.EXIT_OK
    assert f"Records fetched: {DEFAULT_LIMIT + 3}" in out
    assert f"Records processed: {DEFAULT_LIMIT}" in out


def test_main_runs_the_batch_and_only_reads_from_the_backend(capsys, monkeypatch):
    transport = responder(listing(waterlogging()))
    monkeypatch.setattr(
        pipeline, "process_record",
        lambda record, **kwargs: stub(record, incident_type=EventType.URBAN_WATERLOGGING),
    )

    code = main(
        ["--limit", "1"],
        provider=Scripted(payload={}),
        environ={ENV_API_BASE_URL: BASE},
        transport=transport,
    )

    captured = capsys.readouterr()
    assert code == pipeline.EXIT_OK
    assert transport.calls == [f"{BASE}/records"]
    assert "Incidents produced: 1" in captured.out
    assert "INC-NEWS-MDU-WLR-0001" in captured.out


def test_main_prints_full_documents_only_when_asked(capsys, monkeypatch):
    monkeypatch.setattr(pipeline, "process_record", lambda record, **kwargs: stub(record))

    def run(*argv: str) -> str:
        code = main(
            list(argv),
            provider=Scripted(payload={}),
            environ={ENV_API_BASE_URL: BASE},
            transport=responder(listing(waterlogging())),
        )
        assert code == pipeline.EXIT_OK
        return capsys.readouterr().out

    assert '"schema_version"' not in run("--limit", "1")
    assert '"schema_version": "2.0"' in run("--limit", "1", "--json")


def test_main_reports_a_backend_failure_and_returns_the_backend_code(capsys):
    code = main(
        [],
        provider=Scripted(payload={}),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder((503, "upstream unavailable")),
    )

    assert code == pipeline.EXIT_BACKEND
    assert f"{BASE}/records answered HTTP 503" in capsys.readouterr().err


def test_the_cli_refuses_selections_that_would_read_too_much():
    for argv in (
        ["--limit", "0"],
        ["--all", "--limit", "5"],
        ["--record-id", "R-1", "--limit", "3"],
        ["--record-id", "R-1", "--all"],
    ):
        with pytest.raises(SystemExit) as stop:
            pipeline.parse_args(argv)
        assert stop.value.code == 2

    assert pipeline.parse_args([]).limit is None
    assert pipeline.parse_args(["--all"]).all is True
    assert pipeline.parse_args(["--limit", "20"]).limit == 20
