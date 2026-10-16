from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from intelligence import checkpoint as state
from intelligence import pipeline
from intelligence.checkpoint import (
    CHECKPOINT_FILENAME,
    COMPLETED,
    DEFERRED,
    Entry,
    FAILED,
    RESULTS_FILENAME,
    REVIEW_REQUIRED,
    Checkpoint,
    CheckpointError,
    fingerprint,
)
from intelligence.contract import Incident, SCHEMA_VERSION
from intelligence.intelligence import PROMPT_VERSION
from intelligence.llm import (
    ENV_MODEL,
    LLMConfig,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAICompatibleProvider,
    QuotaExhaustedError,
    RateLimitedError,
    TransportError,
)
from intelligence.models.enums import TimePrecision
from intelligence.pipeline import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LIMIT,
    ENV_API_BASE_URL,
    EXIT_CHECKPOINT,
    EXIT_DEFERRED,
    EXIT_OK,
    MAX_SEMANTIC_CONCURRENCY,
    Mode,
    RunSummary,
    main,
    print_report,
    prompt_version_for,
    run_pipeline,
)
from intelligence.structured import MAPPING_VERSION
from intelligence.tests.builders import (
    NeverCalled,
    Scripted,
    listing,
    responder,
    single,
    water_answer,
)
from intelligence.tests.record_fixtures import market_price, waterlogging, weather_forecast

BASE = "http://backend.test"
PRICE_ID = market_price()["record_id"]
FORECAST_ID = weather_forecast()["record_id"]

LINE_KEYS = (
    "record_id", "state", "mode", "prompt_version", "schema_version", "input_hash",
    "provider", "model", "source_type", "record_type", "error", "incident", "saved_at",
    "prompt_digest", "attempts",
)


def narrative(count: int, *, prefix: str = "N") -> list[dict[str, Any]]:
    return [dict(waterlogging(), record_id=f"{prefix}-{index}") for index in range(count)]


def prices(count: int) -> list[dict[str, Any]]:
    return [dict(market_price(), record_id=f"A-{index}") for index in range(count)]


def lines(store: Checkpoint) -> list[dict[str, Any]]:
    text = store.results_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run(records: list[dict[str, Any]], *, store: Optional[Checkpoint] = None, **kwargs: Any):
    return run_pipeline(
        all_records=True,
        base_url=BASE,
        transport=responder(listing(*records)),
        store=store,
        **kwargs,
    )


@dataclass
class Counting(LLMProvider):
    asks: list[str] = field(default_factory=list)
    payload: Any = None
    name: str = "counting"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.asks.append(str(request.meta.get("record_id")))
        return LLMResponse(
            provider=self.name,
            model=self.model_id,
            data=water_answer() if self.payload is None else self.payload,
            latency_ms=5.0,
        )


@dataclass
class OutOfQuota(LLMProvider):
    answers: int = 0
    asks: list[str] = field(default_factory=list)
    name: str = "out-of-quota"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.asks.append(str(request.meta.get("record_id")))
        if len(self.asks) > self.answers:
            raise QuotaExhaustedError("the account allowance is spent: insufficient_quota")
        return LLMResponse(
            provider=self.name, model=self.model_id, data=water_answer(), latency_ms=5.0
        )


@dataclass
class BusyWindow(LLMProvider):
    answers: int = 0
    asks: list[str] = field(default_factory=list)
    name: str = "busy-window"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.asks.append(str(request.meta.get("record_id")))
        if len(self.asks) > self.answers:
            raise RateLimitedError(
                "http://backend.test/v1/chat/completions answered 429: Rate Limit Exceeded "
                "(after 3 send(s)) - 60s was waited over 3 send(s) before it was given up on"
            )
        return LLMResponse(
            provider=self.name, model=self.model_id, data=water_answer(), latency_ms=5.0
        )


@dataclass
class AlwaysFails(LLMProvider):
    asks: list[str] = field(default_factory=list)
    name: str = "broken"

    @property
    def model(self) -> str:
        return "gpt-oss-120b"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.asks.append(str(request.meta.get("record_id")))
        raise TransportError("the endpoint closed the connection")


@dataclass
class Mixed(LLMProvider):
    review: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    broken: tuple[str, ...] = ()
    asks: list[str] = field(default_factory=list)
    name: str = "mixed"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        record_id = str(request.meta.get("record_id"))
        self.asks.append(record_id)
        if record_id in self.refused:
            raise QuotaExhaustedError("insufficient_quota: the allowance is spent")
        if record_id in self.broken:
            raise TransportError("the endpoint closed the connection")
        payload = {"record_kind": "context"} if record_id in self.review else water_answer()
        return LLMResponse(
            provider=self.name, model=self.model_id, data=payload, latency_ms=5.0
        )


def stop_at(record_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real = pipeline.process_record

    def process(record, **kwargs):
        if record["record_id"] == record_id:
            raise KeyboardInterrupt("the console stopped the run")
        return real(record, **kwargs)

    monkeypatch.setattr(pipeline, "process_record", process)


def test_a_result_is_tagged_with_the_reading_that_produced_it():
    assert prompt_version_for(Mode.STRUCTURED) == MAPPING_VERSION
    assert prompt_version_for(Mode.SEMANTIC) == PROMPT_VERSION


def test_a_run_writes_nothing_until_a_state_dir_is_asked_for(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    code = main(
        ["--limit", "1"],
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(1))),
    )

    assert code == EXIT_OK
    assert list(tmp_path.iterdir()) == []
    assert "Checkpoint: disabled (set --state-dir to persist and resume)" in capsys.readouterr().out


def test_a_batch_of_structured_records_persists_without_any_provider(tmp_path, monkeypatch):
    def none(*args, **kwargs):
        raise AssertionError("a structured batch must not configure a model")

    monkeypatch.setattr(pipeline, "build_provider", none)
    store = Checkpoint(tmp_path / "run")

    summary = run(
        [*prices(3), dict(weather_forecast(), record_id="W-0"),
         dict(weather_forecast(), record_id="W-1")],
        store=store,
        environ={},
    )

    assert (len(summary.structured), summary.semantic, summary.failures) == (5, [], [])
    assert summary.skipped == []
    saved = lines(store)
    assert len(saved) == 5
    assert {item["state"] for item in saved} == {COMPLETED}
    assert {item["mode"] for item in saved} == {"structured"}
    assert {item["prompt_version"] for item in saved} == {MAPPING_VERSION}
    assert {item["provider"] for item in saved} == {""} and {item["model"] for item in saved} == {""}

    meta = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
    assert meta["records"] == 5 and meta["states"][COMPLETED] == 5
    assert meta["results_file"] == RESULTS_FILENAME
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["base_url"] == BASE and meta["provider"] == "" and meta["model"] == ""


def test_a_mixed_batch_asks_one_prompt_per_record_and_saves_both_paths(tmp_path):
    provider = Counting()
    store = Checkpoint(tmp_path / "run")

    summary = run([*narrative(2), market_price(), weather_forecast()], provider=provider, store=store)

    assert provider.asks == ["N-0", "N-1"]
    assert (len(summary.semantic), len(summary.contextual)) == (2, 2)
    assert summary.failures == [] and summary.deferred == []
    assert store.get("N-0").prompt_version == PROMPT_VERSION
    assert store.get(PRICE_ID).mode == "structured"
    assert store.get("N-1").state == COMPLETED
    assert [item["record_id"] for item in lines(store)][:2] == [PRICE_ID, FORECAST_ID]
    read = Incident.model_validate(store.get("N-0").incident)
    assert read.provenance.record_id == "N-0"


def test_results_are_readable_as_json_lines_without_the_store(tmp_path):
    store = Checkpoint(tmp_path / "run")

    run([*narrative(1), market_price()], provider=Counting(), store=store)

    saved = lines(store)
    assert len(saved) == 2
    for item in saved:
        assert set(item) == set(LINE_KEYS)
        assert item["schema_version"] == SCHEMA_VERSION
        assert len(item["input_hash"]) == 64
        assert item["incident"]["schema_version"] == SCHEMA_VERSION
        Incident.model_validate(item["incident"])
    by_id = {item["record_id"]: item for item in saved}
    assert by_id["N-0"]["source_type"] == "news"
    assert by_id[PRICE_ID]["source_type"] == "agriculture"
    assert by_id["N-0"]["incident"]["provenance"]["record_id"] == "N-0"


def test_a_resumed_run_reuses_finished_results_and_reads_only_what_is_new(tmp_path):
    directory = tmp_path / "run"
    run([*narrative(2), market_price()], provider=Counting(), store=Checkpoint(directory))

    second = Counting()
    summary = run(
        [*narrative(2), market_price(), weather_forecast()],
        provider=second,
        store=Checkpoint(directory).load(),
    )

    assert second.asks == []
    assert [item.record_id for item in summary.outcomes] == [FORECAST_ID]
    assert len(summary.skipped) == 3
    assert summary.counts()["Records reused"] == 3
    assert summary.processed == 1
    assert summary.counts()["Records selected"] == 4


def test_a_fully_resumed_batch_never_configures_a_provider(tmp_path, monkeypatch):
    def none(*args, **kwargs):
        raise AssertionError("nothing left to read must not authenticate a model")

    directory = tmp_path / "run"
    run(narrative(2), provider=Counting(), store=Checkpoint(directory))
    monkeypatch.setattr(pipeline, "build_provider", none)

    summary = run(narrative(2), store=Checkpoint(directory).load(), environ={})

    assert len(summary.skipped) == 2 and summary.outcomes == []


def test_a_record_whose_text_or_geography_changed_is_read_again(tmp_path):
    directory = tmp_path / "run"
    original = dict(waterlogging(), record_id="N-0")
    run([original], provider=Counting(), store=Checkpoint(directory))

    changed = dict(
        original,
        data={"content": original["data"]["content"] + " மீண்டும் மழை.", "language": "ta"},
    )
    provider = Counting()
    summary = run([changed], provider=provider, store=Checkpoint(directory).load())

    assert provider.asks == ["N-0"] and summary.skipped == []
    assert len(summary.incidents) == 1

    moved = dict(original, location={**original["location"], "district": "Theni"})
    again = Counting()
    summary = run([moved], provider=again, store=Checkpoint(directory).load())
    assert again.asks == ["N-0"] and len(summary.incidents) == 1


def test_a_result_from_another_prompt_version_is_not_reused(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    run(narrative(1), provider=Counting(), store=Checkpoint(directory))
    monkeypatch.setattr(pipeline, "PROMPT_VERSION", "stage-9.9")

    provider = Counting()
    summary = run(narrative(1), provider=provider, store=Checkpoint(directory).load())

    assert provider.asks == ["N-0"] and summary.skipped == []


def test_the_fingerprint_covers_the_text_and_the_place_the_record_declared():
    record = waterlogging()
    found = fingerprint(record)
    assert found and found == fingerprint(record)
    other_content = dict(record, data={"content": "வேறு உரை.", "language": "ta"})
    assert fingerprint(other_content) != found
    other_place = dict(record, location={**record["location"], "district": "Theni"})
    assert fingerprint(other_place) != found
    assert fingerprint({"record_id": "X", "data": "not a mapping"}) is None


def test_no_resume_reads_every_selected_record_again(tmp_path):
    directory = tmp_path / "run"
    run(narrative(2), provider=Counting(), store=Checkpoint(directory))

    provider = Counting()
    summary = run(narrative(2), provider=provider, store=Checkpoint(directory).load(), resume=False)

    assert sorted(provider.asks) == ["N-0", "N-1"]
    assert summary.skipped == [] and len(summary.incidents) == 2


def test_one_record_is_one_line_even_after_it_was_read_several_times(tmp_path):
    store = Checkpoint(tmp_path / "run")
    incident = pipeline.process_record(waterlogging(), provider=Scripted(payload=water_answer()))
    for name, error in ((FAILED, "the endpoint closed the connection"), (COMPLETED, None)):
        store.save(
            record_id="N-0",
            state=name,
            mode="semantic",
            prompt_version=PROMPT_VERSION,
            input_hash="hash",
            incident=incident.to_storage_document() if name == COMPLETED else None,
            error=error,
        )
    store.flush()

    saved = lines(store)
    assert len(saved) == 1 and saved[0]["state"] == COMPLETED
    reloaded = Checkpoint(tmp_path / "run").load()
    assert len(reloaded.entries) == 1 and reloaded.superseded == 0
    assert reloaded.counts() == {COMPLETED: 1, REVIEW_REQUIRED: 0, FAILED: 0, DEFERRED: 0}


def test_a_checkpoint_that_holds_two_lines_for_one_record_keeps_the_last(tmp_path):
    first = Entry(record_id="R-1", state=FAILED, mode="semantic",
                  prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                  error="the first reading")
    second = Entry(record_id="R-1", state=FAILED, mode="semantic",
                   prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                   error="the last reading")
    (tmp_path / RESULTS_FILENAME).write_text(first.line() + second.line(), encoding="utf-8")

    store = Checkpoint(tmp_path).load()

    assert len(store.entries) == 1 and store.superseded == 1
    assert store.get("R-1").error == "the last reading"


def test_an_interrupted_run_keeps_the_batches_it_already_wrote(tmp_path, monkeypatch):
    stop_at("A-1", monkeypatch)
    store = Checkpoint(tmp_path / "run")

    with pytest.raises(KeyboardInterrupt):
        run(prices(3), store=store, batch_size=1)

    assert [item["record_id"] for item in lines(store)] == ["A-0"]
    found = Checkpoint(tmp_path / "run").load().reusable(
        record_id="A-0", mode="structured", prompt_version=MAPPING_VERSION,
        input_hash=fingerprint(prices(1)[0]),
    )
    assert isinstance(found, Incident)


def test_a_batch_is_the_unit_of_recovery(tmp_path, monkeypatch):
    stop_at("A-1", monkeypatch)
    store = Checkpoint(tmp_path / "run")

    with pytest.raises(KeyboardInterrupt):
        run(prices(3), store=store, batch_size=3)

    assert not store.results_path.exists()
    assert Checkpoint(tmp_path / "run").load().entries == {}


def test_a_write_that_fails_leaves_the_previous_checkpoint_and_no_temp_file(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    store = Checkpoint(directory)
    run(prices(1), store=store)
    before = store.results_path.read_text(encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(state.os, "replace", refuse)
    store.save(record_id="A-9", state=FAILED, mode="structured",
               prompt_version=MAPPING_VERSION, error="nothing")

    with pytest.raises(CheckpointError, match="nothing was discarded"):
        store.flush()

    assert store.results_path.read_text(encoding="utf-8") == before
    assert list(directory.glob("*.tmp")) == []


def test_a_quota_refusal_defers_what_was_not_asked_and_keeps_what_was(tmp_path):
    provider = OutOfQuota(answers=1)
    store = Checkpoint(tmp_path / "run")

    summary = run([*narrative(3), market_price()], provider=provider, store=store, batch_size=4)

    assert provider.asks == ["N-0", "N-1"]
    assert "insufficient_quota" in (summary.stop_reason or "")
    assert (len(summary.incidents), len(summary.contextual), len(summary.deferred)) == (1, 1, 2)
    assert summary.failures == []
    assert summary.processed == 2
    assert summary.attempted == 3
    counts = summary.counts()
    assert counts["Deferred (refused)"] == 1 and counts["Deferred (never sent)"] == 1
    assert "3 attempted = 1 accepted incidents + 1 accepted context + 1 deferred (refused)" in (
        counts["Accounting"]
    )
    assert store.get("N-0").state == COMPLETED
    assert store.get("N-1").state == DEFERRED and store.get("N-2").state == DEFERRED
    assert "provider quota exhausted" in store.get("N-1").error
    assert "insufficient_quota" in store.get("N-2").error


def test_structured_records_are_still_read_after_the_provider_ran_out(tmp_path):
    provider = OutOfQuota(answers=0)

    summary = run([*narrative(1), *prices(2)], provider=provider, batch_size=1)

    assert provider.asks == ["N-0"]
    assert (len(summary.structured), len(summary.deferred), summary.processed) == (2, 1, 2)
    assert summary.failures == []


def test_deferred_records_are_read_by_the_next_run(tmp_path):
    directory = tmp_path / "run"
    run(narrative(2), provider=OutOfQuota(answers=0), store=Checkpoint(directory))

    provider = Counting()
    summary = run(narrative(2), provider=provider, store=Checkpoint(directory).load())

    assert sorted(provider.asks) == ["N-0", "N-1"]
    assert summary.skipped == [] and len(summary.incidents) == 2
    assert summary.stop_reason is None
    saved = lines(Checkpoint(directory))
    assert len(saved) == 2
    assert {item["state"] for item in saved} == {COMPLETED}


def test_a_busy_window_defers_the_records_it_did_not_read(tmp_path):
    provider = BusyWindow()
    store = Checkpoint(tmp_path / "run")

    summary = run([*narrative(5), market_price()], provider=provider, store=store, batch_size=6)

    assert provider.asks == ["N-0"]
    assert summary.stop_kind == "rate" and "provider rate limited" in (summary.stop_reason or "")
    assert (len(summary.deferred), summary.failures, len(summary.structured)) == (5, [], 1)
    assert summary.processed == 1 and summary.attempted == 2
    counts = summary.counts()
    assert counts["Deferred (refused)"] == 1 and counts["Deferred (never sent)"] == 4
    assert "6 selected = 0 reused + 2 attempted + 4 never sent" in counts["Accounting"]
    assert "2 attempted = 1 accepted context + 1 deferred (refused)" in counts["Accounting"]
    assert store.get(PRICE_ID).state == COMPLETED
    for record_id in [f"N-{index}" for index in range(5)]:
        assert store.get(record_id).state == DEFERRED
        assert "provider rate limited" in store.get(record_id).error
        assert "answered 429" in store.get(record_id).error


def test_a_busy_window_never_stops_the_records_that_need_no_model(tmp_path):
    provider = BusyWindow()

    summary = run([*narrative(1), *prices(3)], provider=provider, batch_size=1)

    assert provider.asks == ["N-0"]
    assert (len(summary.structured), len(summary.deferred), summary.failures) == (3, 1, [])


def test_a_temporary_rate_limit_is_not_saved_as_a_result(tmp_path):
    directory = tmp_path / "run"
    run(narrative(2), provider=BusyWindow(), store=Checkpoint(directory))

    store = Checkpoint(directory).load()
    record = narrative(1)[0]
    assert store.get("N-0").state == DEFERRED
    assert store.reusable(
        record_id="N-0",
        mode=Mode.SEMANTIC.value,
        prompt_version=PROMPT_VERSION,
        input_hash=fingerprint(record),
    ) is None

    provider = Counting(name="busy-window")
    resumed = run(narrative(2), provider=provider, store=store, environ={})

    assert sorted(provider.asks) == ["N-0", "N-1"]
    assert resumed.skipped == [] and len(resumed.incidents) == 2
    assert resumed.deferred == [] and resumed.stop_reason is None


def test_a_busy_window_after_a_read_record_keeps_that_result_and_defers_the_rest(tmp_path):
    directory = tmp_path / "run"
    first = run(narrative(4), provider=BusyWindow(answers=1), store=Checkpoint(directory))

    assert len(first.incidents) == 1 and len(first.deferred) == 3
    assert Checkpoint(directory).load().get("N-0").state == COMPLETED

    provider = Counting(name="busy-window")
    resumed = run(
        narrative(4), provider=provider, store=Checkpoint(directory).load(), environ={}
    )

    assert sorted(provider.asks) == ["N-1", "N-2", "N-3"]
    assert [item.record_id for item in resumed.reused] == ["N-0"]
    assert resumed.counts()["Records attempted"] == 3
    assert resumed.counts()["Records reused"] == 1


def test_a_busy_window_under_concurrency_defers_only_what_the_run_never_sent(tmp_path):
    directory = tmp_path / "run"
    provider = BusyWindow(answers=2)
    store = Checkpoint(directory)

    summary = run(
        narrative(8), provider=provider, store=store, batch_size=8, semantic_concurrency=4
    )

    assert len(summary.outcomes) == 8 and summary.failures == []
    assert summary.stop_kind == "rate" and len(summary.deferred) >= 1
    assert len(summary.incidents) + len(summary.deferred) == 8
    assert len(set(provider.asks)) == len(provider.asks) <= 8

    saved = {item["record_id"]: item for item in lines(store)}
    read = {key for key, item in saved.items() if item["state"] == COMPLETED}
    waiting = {key for key, item in saved.items() if item["state"] == DEFERRED}
    assert read <= set(provider.asks) and read | waiting == set(saved)
    assert all("rate limited" in str(item["error"]) for item in saved.values() if item["state"] == DEFERRED)

    again = Counting(name="busy-window")
    resumed = run(
        narrative(8), provider=again, store=Checkpoint(directory).load(), environ={}
    )

    assert sorted(again.asks) == sorted(waiting)
    assert len(resumed.reused) == len(read) and len(resumed.incidents) == len(waiting)


def test_main_reports_a_busy_window_as_deferred_and_exits_for_a_later_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    code = main(
        ["--limit", "3", "--state-dir", "run"],
        provider=BusyWindow(),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(3))),
    )

    out = capsys.readouterr().out
    assert code == EXIT_DEFERRED
    assert "Deferred (refused): 1" in out and "Deferred (never sent): 2" in out
    assert "Failed: 0" in out
    assert "Deferred (rate limited) (1)" in out and "Left unread (never sent) (2)" in out
    assert "the provider said this window is full" in out
    assert "no request was spent on these" in out
    assert "Stopped asking the provider" in out and "answered 429" in out
    assert "Re-run with --state-dir" in out


def test_a_failed_record_is_saved_and_never_reused(tmp_path):
    directory = tmp_path / "run"
    broken = AlwaysFails()
    summary = run(narrative(1), provider=broken, store=Checkpoint(directory))

    assert len(summary.failures) == 1 and summary.deferred == []
    assert "provider failed" in summary.failures[0].error
    assert Checkpoint(directory).load().get("N-0").state == FAILED

    provider = Counting()
    second = run(narrative(1), provider=provider, store=Checkpoint(directory).load())
    assert provider.asks == ["N-0"] and second.skipped == []
    assert second.counts()["Failed"] == 0 and len(second.incidents) == 1


def test_a_review_required_result_is_saved_and_reused(tmp_path):
    directory = tmp_path / "run"
    unresolved = Counting(payload={"record_kind": "context"})
    summary = run(narrative(1), provider=unresolved, store=Checkpoint(directory))

    assert summary.review_required
    assert Checkpoint(directory).load().get("N-0").state == REVIEW_REQUIRED

    provider = Counting()
    resumed = run(narrative(1), provider=provider, store=Checkpoint(directory).load())
    assert provider.asks == [] and len(resumed.skipped) == 1


def test_a_completed_entry_must_carry_the_incident_it_promised(tmp_path):
    store = Checkpoint(tmp_path / "run")

    with pytest.raises(CheckpointError, match="must carry its Incident document"):
        store.save(record_id="R", state=COMPLETED, mode="semantic",
                   prompt_version=PROMPT_VERSION, input_hash="hash")
    with pytest.raises(CheckpointError, match="is not a state this store records"):
        store.save(record_id="R", state="done", mode="semantic", prompt_version=PROMPT_VERSION)


def test_a_line_that_is_not_json_is_refused_with_its_file_and_number(tmp_path):
    (tmp_path / RESULTS_FILENAME).write_text("\n{ not json at all\n", encoding="utf-8")

    with pytest.raises(CheckpointError) as stop:
        Checkpoint(tmp_path).load()

    assert RESULTS_FILENAME in str(stop.value) and "line 2 is not JSON" in str(stop.value)


@pytest.mark.parametrize(
    "payload,reason",
    (
        ({"state": "failed", "mode": "semantic", "prompt_version": "p", "schema_version": "2.0"},
         "has no record_id"),
        ({"record_id": "R", "state": "processed", "mode": "semantic", "prompt_version": "p",
          "schema_version": "2.0"}, "claims unknown state"),
        ({"record_id": "R", "state": COMPLETED, "mode": "semantic", "prompt_version": "p",
          "schema_version": "2.0"}, "carries no Incident document"),
        ("[7]", "holds list where a record entry was expected"),
    ),
)
def test_an_entry_that_cannot_be_trusted_is_refused_rather_than_dropped(tmp_path, payload, reason):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    (tmp_path / RESULTS_FILENAME).write_text(body + "\n", encoding="utf-8")

    with pytest.raises(CheckpointError, match=reason):
        Checkpoint(tmp_path).load()


def test_a_saved_document_the_current_contract_refuses_is_reported_not_skipped(tmp_path):
    entry = Entry(record_id="R", state=COMPLETED, mode="semantic", prompt_version=PROMPT_VERSION,
                  schema_version=SCHEMA_VERSION, input_hash="hash",
                  incident={"title": "not an Incident"})
    (tmp_path / RESULTS_FILENAME).write_text(entry.line(), encoding="utf-8")
    store = Checkpoint(tmp_path).load()

    with pytest.raises(CheckpointError, match="not what contract"):
        store.reusable(record_id="R", mode="semantic", prompt_version=PROMPT_VERSION,
                       input_hash="hash")


def test_a_result_from_another_schema_version_or_hash_is_not_reused(tmp_path):
    entry = Entry(record_id="R", state=COMPLETED, mode="semantic", prompt_version=PROMPT_VERSION,
                  schema_version="1.0", input_hash="hash", incident={"title": "old contract"})
    (tmp_path / RESULTS_FILENAME).write_text(entry.line(), encoding="utf-8")
    store = Checkpoint(tmp_path).load()

    ask = {"record_id": "R", "mode": "semantic", "prompt_version": PROMPT_VERSION}
    assert store.reusable(**ask, input_hash="hash") is None
    assert store.reusable(record_id="R", mode="semantic", prompt_version=PROMPT_VERSION,
                          input_hash=None) is None


DROP = object()


def saved_manifest(store: Checkpoint) -> dict[str, Any]:
    return json.loads(store.checkpoint_path.read_text(encoding="utf-8"))


def rewrite_manifest(store: Checkpoint, **changes: Any) -> dict[str, Any]:
    meta = saved_manifest(store)
    for key, value in changes.items():
        if value is DROP:
            del meta[key]
        else:
            meta[key] = value
    store.checkpoint_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def saved(directory) -> Checkpoint:
    store = Checkpoint(directory)
    run(prices(1), store=store, environ={})
    return store


@pytest.mark.parametrize(
    "body,changes,reason",
    (
        ("{ not json at all", {}, "is not JSON"),
        ("", {}, "is not JSON"),
        ("[7]", {}, "holds list where checkpoint metadata was expected"),
        (None, {"updated_at": DROP, "records": DROP}, "states no updated_at, records"),
        (None, {"results_digest": None}, "states no results_digest"),
        (None, {"results_digest": "  "}, "leaves results_digest empty"),
        (None, {"results_file": "progress.jsonl"}, "describes 'progress.jsonl'"),
        (None, {"states": "5 completed"}, "holds str where state counts were expected"),
        (None, {"states": {"done": 1}}, "counts states it never recorded"),
        (None, {"states": {COMPLETED: "many"}}, "is not a count per state"),
        (None, {"records": "two"}, "states a record count that is not a number"),
        (None, {"records": 9}, "counts 9 records while"),
        (None, {"results_digest": "0" * 64}, "disagree over the same 1 records"),
        (None, {"states": {COMPLETED: 0, REVIEW_REQUIRED: 0, FAILED: 1, DEFERRED: 0}},
         "but the 1 results beside it hold"),
    ),
)
def test_metadata_that_cannot_account_for_the_results_is_refused(tmp_path, body, changes, reason):
    store = saved(tmp_path / "run")
    if body is not None:
        store.checkpoint_path.write_text(body, encoding="utf-8")
    else:
        rewrite_manifest(store, **changes)
    before = store.results_path.read_text(encoding="utf-8")

    with pytest.raises(CheckpointError, match=reason):
        Checkpoint(tmp_path / "run").load()

    assert store.results_path.read_text(encoding="utf-8") == before


def test_a_manifest_that_lags_its_results_is_read_as_an_interrupted_run(tmp_path):
    store = saved(tmp_path / "run")
    meta = rewrite_manifest(
        store, records=0, states={COMPLETED: 0, REVIEW_REQUIRED: 0, FAILED: 0, DEFERRED: 0}
    )

    found = Checkpoint(tmp_path / "run").load()

    assert len(found.entries) == 1
    assert found.incomplete and "interrupted between the two writes" in found.incomplete
    assert found.manifest == meta
    assert isinstance(found.reusable(
        record_id="A-0", mode="structured", prompt_version=MAPPING_VERSION,
        input_hash=fingerprint(prices(1)[0]),
    ), Incident)


def test_results_without_their_manifest_are_kept_and_said_to_be_unvouched(tmp_path, capsys):
    directory = tmp_path / "run"
    store = saved(directory)
    store.checkpoint_path.unlink()

    found = Checkpoint(directory).load()
    summary = run(prices(1), store=found, environ={})

    assert len(found.entries) == 1 and found.incomplete and "missing beside" in found.incomplete
    assert len(summary.reused) == 1 and summary.outcomes == []
    print_report(summary)
    assert "Saved progress was interrupted" in capsys.readouterr().out


def test_a_manifest_whose_results_are_missing_is_refused_rather_than_replaced(tmp_path):
    store = saved(tmp_path / "run")
    store.results_path.unlink()

    with pytest.raises(CheckpointError, match="is not there"):
        Checkpoint(tmp_path / "run").load()

    assert saved_manifest(store)["records"] == 1


def test_untrusted_progress_stops_the_run_before_the_backend_is_read(tmp_path, capsys):
    directory = tmp_path / "run"
    store = saved(directory)
    rewrite_manifest(store, records=4)
    before = store.results_path.read_text(encoding="utf-8")
    transport = responder(listing(*prices(1)))

    code = main(
        ["--all", "--state-dir", str(directory)],
        provider=NeverCalled(),
        environ={ENV_API_BASE_URL: BASE},
        transport=transport,
    )

    captured = capsys.readouterr()
    assert code == EXIT_CHECKPOINT and transport.calls == []
    assert "counts 4 records while" in captured.err
    assert store.results_path.read_text(encoding="utf-8") == before
    assert saved_manifest(store)["records"] == 4
    assert captured.out == ""


def test_a_crash_between_the_two_replacements_leaves_the_newer_log_readable(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "run"
    store = saved(directory)
    real = state.os.replace

    def only_the_log_lands(source, target):
        if str(target).endswith(CHECKPOINT_FILENAME):
            raise OSError("the disk went away while the manifest was being moved")
        return real(source, target)

    monkeypatch.setattr(state.os, "replace", only_the_log_lands)
    with pytest.raises(CheckpointError, match="nothing was discarded"):
        run(prices(2), store=store, batch_size=2, environ={})

    assert [item["record_id"] for item in lines(store)] == ["A-0", "A-1"]
    assert saved_manifest(store)["records"] == 1
    assert list(directory.glob("*.tmp")) == []

    monkeypatch.setattr(state.os, "replace", real)
    recovered = Checkpoint(directory).load()
    resumed = run(prices(3), store=recovered, environ={})

    assert sorted(item.record_id for item in resumed.reused) == ["A-0", "A-1"]
    assert [item.record_id for item in resumed.outcomes] == ["A-2"]
    assert saved_manifest(Checkpoint(directory))["records"] == 3

    assert recovered.incomplete and "interrupted between the two writes" in recovered.incomplete
    print_report(resumed)
    assert "Saved progress was interrupted" in capsys.readouterr().out


def test_a_run_that_wrote_after_an_absent_manifest_still_says_the_log_was_unvouched(tmp_path, capsys):
    directory = tmp_path / "run"
    store = saved(directory)
    store.checkpoint_path.unlink()

    recovered = Checkpoint(directory).load()
    summary = run(prices(2), store=recovered, environ={})

    assert len(summary.reused) == 1 and [item.record_id for item in summary.outcomes] == ["A-1"]
    assert recovered.incomplete and "missing beside" in recovered.incomplete
    print_report(summary)
    out = capsys.readouterr().out
    assert "Saved progress was interrupted" in out and "nothing vouches" in out
    assert saved_manifest(Checkpoint(directory))["records"] == 2


def test_a_reused_context_reading_keeps_the_day_it_stated_and_the_places_it_named(tmp_path):
    directory = tmp_path / "run"
    forecast = weather_forecast(event_time="2026-10-03")
    price = market_price()

    first = run([forecast, price], store=Checkpoint(directory), environ={})
    again = run([forecast, price], store=Checkpoint(directory).load(), environ={})

    assert first.reused == [] and len(first.outcomes) == 2
    assert sorted(item.record_id for item in again.reused) == sorted([FORECAST_ID, PRICE_ID])
    assert again.outcomes == []

    by_id = {item.record_id: item.incident for item in again.reused}
    assert by_id[FORECAST_ID].event_time_precision is TimePrecision.DAY
    assert by_id[PRICE_ID].event_time_precision is TimePrecision.UNKNOWN
    for incident in by_id.values():
        assert [item.resolution_state.value for item in incident.locations] == [
            "pending_gis",
            "pending_gis",
        ]
        assert incident.model_dump() == Incident.model_validate(
            incident.to_storage_document()
        ).model_dump()

    stored = {item["record_id"]: item["incident"] for item in lines(Checkpoint(directory))}
    assert stored[FORECAST_ID]["event_time_precision"] == "day"
    assert [item["text"] for item in stored[FORECAST_ID]["locations"]] == ["Madurai", "Madurai"]
    assert [item["text"] for item in stored[PRICE_ID]["locations"]] == [
        "Anna nagar Uzhavar Santhai, Madurai",
        "Madurai",
    ]


def test_reuse_returns_the_whole_context_payload_not_only_its_kind(tmp_path):
    directory = tmp_path / "run"
    forecast = weather_forecast(event_time="2026-10-03")
    price = market_price()

    first = run([forecast, price], store=Checkpoint(directory), environ={})
    again = run([forecast, price], store=Checkpoint(directory).load(), environ={})

    fresh = {item.record_id: item.incident for item in first.outcomes}
    kept = {item.record_id: item.incident for item in again.reused}
    assert sorted(fresh) == sorted(kept)

    for record_id, incident in fresh.items():
        restored = kept[record_id]
        assert restored.model_dump() == incident.model_dump()
        assert restored.record_kind is incident.record_kind
        assert restored.context_type is incident.context_type
        assert [item.field for item in restored.context_facts] == [
            item.field for item in incident.context_facts
        ]
        assert [item.field for item in restored.source_geography] == [
            item.field for item in incident.source_geography
        ]
        assert [item.evidence_id for item in restored.evidence] == [
            item.evidence_id for item in incident.evidence
        ]
        assert restored.provenance.model_dump() == incident.provenance.model_dump()

    stored = {item["record_id"]: item["incident"] for item in lines(Checkpoint(directory))}
    assert stored[PRICE_ID]["record_kind"] == "context"
    assert [item["field"] for item in stored[PRICE_ID]["context_facts"]]
    assert [item["value"] for item in stored[FORECAST_ID]["context_facts"]]
    assert stored[PRICE_ID]["source_geography"] and stored[PRICE_ID]["evidence"]
    assert stored[FORECAST_ID]["provenance"]["source_url"]
    eligible = {
        item.incident.incident_id: item.incident.record_kind.value for item in again.postable
    }
    assert eligible == {}


def test_a_context_reading_from_an_older_mapping_version_is_read_again_for_free(
    tmp_path, monkeypatch
):
    directory = tmp_path / "run"
    price = market_price()
    first = run([price], store=Checkpoint(directory), environ={})
    original = first.outcomes[0].incident

    monkeypatch.setattr(pipeline, "MAPPING_VERSION", "structured-1")
    second = Checkpoint(directory).load()
    again = run([price], store=second, environ={})

    assert again.reused == [] and len(again.outcomes) == 1
    assert "structured-1" in second.invalidated[PRICE_ID]

    reading = again.outcomes[0].incident
    assert reading.record_kind is original.record_kind
    assert reading.context_type is original.context_type
    assert [item.field for item in reading.context_facts] == [
        item.field for item in original.context_facts
    ]
    assert [item.value for item in reading.context_facts] == [
        item.value for item in original.context_facts
    ]
    assert [item.evidence_id for item in reading.evidence] == [
        item.evidence_id for item in original.evidence
    ]
    assert reading.generation.prompt_version == MAPPING_VERSION


def test_a_price_line_stored_before_its_values_were_kept_is_read_again_for_free(tmp_path):
    directory = tmp_path / "run"
    price = market_price()
    first = run([price], store=Checkpoint(directory), environ={}, provider=NeverCalled())

    document = first.outcomes[0].incident.to_storage_document()
    for key in ("declared_event_time", "declared_event_time_precision", "fields_without_value"):
        del document["provenance"][key]
    thin = Incident.model_validate(document)
    assert thin.provenance.declared_event_time is None

    written = Checkpoint(directory)
    written.save(
        record_id=PRICE_ID,
        state=COMPLETED,
        mode=Mode.STRUCTURED.value,
        prompt_version="structured-2",
        input_hash=fingerprint(price),
        incident=document,
    )
    written.flush()

    resumed = Checkpoint(directory).load()
    assert resumed.get(PRICE_ID).prompt_version == "structured-2"
    again = run([price], store=resumed, environ={}, provider=NeverCalled())

    assert again.reused == [] and len(again.outcomes) == 1
    assert "structured-2" in resumed.invalidated[PRICE_ID]

    reading = again.outcomes[0].incident
    assert reading.generation.prompt_version == MAPPING_VERSION
    assert (reading.record_kind, reading.context_type) == (thin.record_kind, thin.context_type)
    assert [item.field for item in reading.context_facts] == [
        item.field for item in thin.context_facts
    ]
    assert [item.value for item in reading.context_facts] == [
        item.value for item in thin.context_facts
    ]
    assert reading.provenance.declared_event_time == thin.event_time
    assert reading.provenance.declared_event_time_precision is TimePrecision.UNKNOWN
    assert reading.provenance.fields_without_value == []
    assert {item["record_id"]: item["prompt_version"] for item in lines(written)} == {
        PRICE_ID: MAPPING_VERSION
    }


def test_the_run_names_the_model_it_would_ask_and_stays_silent_when_none_is(tmp_path, monkeypatch):
    def none(*args, **kwargs):
        raise AssertionError("an identity is read from the configuration, never from a call")

    monkeypatch.setattr(pipeline, "build_provider", none)
    assert pipeline.provider_identity(Scripted(payload=water_answer())) == ("scripted", "gpt-oss-120b")
    assert pipeline.provider_identity(config=LLMConfig(provider="groq", model="llama-x")) == ("groq", "llama-x")
    assert pipeline.provider_identity(environ={ENV_MODEL: "Qwen/Qwen3-27B"}) == (
        "openai_compatible", "Qwen/Qwen3-27B",
    )
    assert pipeline.provider_identity(environ={}) == ("", "")
    assert pipeline.provider_identity(NeverCalled(), environ={}) == ("", "")


def test_a_reading_is_not_reused_as_another_models_answer(tmp_path):
    directory = tmp_path / "run"
    run(narrative(1), provider=Scripted(payload=water_answer()), store=Checkpoint(directory),
        environ={})
    entry = Checkpoint(directory).load().get("N-0")
    assert (entry.provider, entry.model) == ("scripted", "gpt-oss-120b")

    other = Scripted(payload=water_answer(), name="groq", model_id="llama-3.3-70b-versatile")
    store = Checkpoint(directory).load()
    summary = run(narrative(1), provider=other, store=store, environ={})

    assert summary.skipped == [] and len(summary.incidents) == 1
    assert "model gpt-oss-120b" in store.invalidated["N-0"]


def test_two_alias_providers_are_named_by_what_the_run_was_configured_with(tmp_path):
    def never(*args):
        raise AssertionError("naming a provider sends no request")

    vllm = LLMConfig(provider="vllm", model="qwen-32b", base_url="http://127.0.0.1:9")
    ollama = LLMConfig(provider="ollama", model="qwen-32b", base_url="http://127.0.0.1:11434/v1")
    for config in (vllm, ollama):
        built = OpenAICompatibleProvider(config, transport=never)
        assert pipeline.provider_identity(built) == pipeline.provider_identity(config=config)

    directory = tmp_path / "run"
    reading = pipeline.process_record(
        waterlogging(), provider=Scripted(payload=water_answer()), environ={}
    )
    store = Checkpoint(directory, base_url=BASE, provider="vllm", model="qwen-32b")
    store.save(record_id="N-0", state="completed", mode="semantic", prompt_version=PROMPT_VERSION,
               input_hash="h", incident=reading.to_storage_document())
    store.flush()
    ask = dict(record_id="N-0", mode="semantic", prompt_version=PROMPT_VERSION, input_hash="h")

    same = Checkpoint(directory, base_url=BASE, provider="vllm", model="qwen-32b").load()
    assert isinstance(same.reusable(**ask), Incident)

    moved = Checkpoint(directory, base_url=BASE, provider="ollama", model="qwen-32b").load()
    assert moved.reusable(**ask) is None
    assert moved.invalidated["N-0"] == "it was read through vllm while this run reads through ollama"


def test_a_model_change_does_not_invalidate_a_copy_and_is_said_out_loud(tmp_path, capsys):
    directory = tmp_path / "run"
    run([*prices(1), *narrative(1)], provider=Scripted(payload=water_answer()),
        store=Checkpoint(directory), environ={})
    other = Scripted(payload=water_answer(), name="groq", model_id="llama-3.3-70b-versatile")

    store = Checkpoint(directory).load()
    summary = run([*prices(1), *narrative(1)], provider=other, store=store, environ={})

    assert [item.record_id for item in summary.reused] == ["A-0"]
    assert [item.record_id for item in summary.outcomes] == ["N-0"]
    assert store.get("A-0").provider == "" and store.get("A-0").model == ""
    print_report(summary)
    out = capsys.readouterr().out
    assert "Read again rather than reused, 1 saved result(s)" in out
    assert "N-0: it was read by model gpt-oss-120b" in out


def test_a_saved_pair_from_another_backend_is_matched_on_the_record_not_the_url(tmp_path, capsys):
    directory = tmp_path / "run"
    store = saved(directory)

    moved = "http://other-backend.test"
    summary = run_pipeline(
        all_records=True, base_url=moved, transport=responder(listing(*prices(1))),
        store=Checkpoint(directory).load(), environ={},
    )

    assert len(summary.reused) == 1 and summary.outcomes == []
    print_report(summary)
    out = capsys.readouterr().out
    assert f"The saved pair was written against {BASE}, not {moved}" in out

    reissued = [dict(market_price(min_price=52.0), record_id="A-0")]
    again = run_pipeline(
        all_records=True, base_url=moved, transport=responder(listing(*reissued)),
        store=Checkpoint(directory).load(), environ={},
    )

    assert again.reused == [] and len(again.structured) == 1
    assert "the record's own text or geography changed" in again.store.invalidated["A-0"]


def test_a_fully_resumed_mixed_dataset_reports_the_results_it_reused(tmp_path, capsys):
    directory = tmp_path / "run"
    records = [*narrative(2), market_price(), weather_forecast()]
    run(records, provider=Scripted(payload=water_answer()), store=Checkpoint(directory), environ={})
    capsys.readouterr()

    summary = run(records, provider=NeverCalled(), store=Checkpoint(directory).load(), environ={})
    print_report(summary)
    out = capsys.readouterr().out

    assert summary.outcomes == [] and summary.processed == 0
    counts = summary.counts()
    assert counts["Records reused"] == 4
    assert counts["Reused incidents"] == 2 and counts["Reused contextual"] == 2
    assert counts["Reused review-required"] == 0
    assert counts["Records selected"] == 4 and counts["Records attempted"] == 0
    assert counts["Accepted incidents"] == 0 and counts["Structured (no model)"] == 0
    assert sorted(item.record_id for item in summary.reused) == sorted(
        ["N-0", "N-1", PRICE_ID, FORECAST_ID]
    )
    assert {item.mode for item in summary.reused} == {Mode.SEMANTIC, Mode.STRUCTURED}
    assert "Reused from the checkpoint (4)" in out and "Reused incidents: 2" in out
    assert "Reused contextual: 2" in out
    assert "already read by an earlier run" in out


def test_a_quota_refusal_under_concurrency_saves_what_was_read_and_defers_the_rest(tmp_path):
    directory = tmp_path / "run"
    provider = OutOfQuota(answers=2)
    store = Checkpoint(directory)

    summary = run(narrative(8), provider=provider, store=store, batch_size=8, semantic_concurrency=4)

    assert len(summary.outcomes) == 8 and summary.failures == []
    assert summary.stop_reason and len(summary.deferred) >= 1
    assert len(summary.incidents) + len(summary.deferred) == 8
    assert len(set(provider.asks)) == len(provider.asks)
    saved_lines = lines(store)
    assert len(saved_lines) == 8
    ids = {f"N-{index}" for index in range(8)}
    completed = {item["record_id"] for item in saved_lines if item["state"] == COMPLETED}
    deferred = {item["record_id"] for item in saved_lines if item["state"] == DEFERRED}
    assert completed <= set(provider.asks) and completed | deferred == ids
    assert all("quota" in str(item["error"]).lower() for item in saved_lines
               if item["state"] == DEFERRED)

    again = Counting(name="out-of-quota")
    resumed = run(narrative(8), provider=again, store=Checkpoint(directory).load(), environ={})

    assert sorted(again.asks) == sorted(deferred)
    assert len(resumed.reused) == len(completed) and len(resumed.incidents) == len(deferred)


def test_the_summary_counts_everything_a_bulk_run_did(tmp_path, capsys):
    store = Checkpoint(tmp_path / "run")
    records = [*narrative(3), market_price(), weather_forecast()]
    summary = run(records, provider=OutOfQuota(answers=1), store=store, batch_size=5)

    print_report(summary)

    out = capsys.readouterr().out
    for label, value in (
        ("Records fetched", 5),
        ("Records selected", 5),
        ("Records reused", 0),
        ("Records attempted", 4),
        ("Structured (no model)", 2),
        ("Narrative (model needed)", 3),
        ("Accepted incidents", 1),
        ("Accepted context", 2),
        ("Review-required", 0),
        ("Failed", 0),
        ("Deferred (refused)", 1),
        ("Deferred (never sent)", 1),
    ):
        assert f"{label}: {value}" in out
    assert "5 fetched = 5 selected (every record the API returned)" in out
    assert "5 selected = 0 reused + 4 attempted + 1 never sent" in out
    assert (
        "4 attempted = 1 accepted incidents + 2 accepted context + 1 deferred (refused)" in out
    )
    assert f"Checkpoint: {store.checkpoint_path}" in out
    assert f"Results: {store.results_path}" in out
    assert "Deferred (provider quota)" in out
    assert "N-2 deferred: provider quota exhausted" in out
    assert "nothing in these evidenced an event" in out
    assert f"Re-run with --state-dir {store.directory} to read them." in out
    assert "Stopped asking the provider" in out


def test_a_limited_run_accounts_for_the_records_it_did_not_select(tmp_path, capsys):
    main(
        ["--limit", "2", "--state-dir", str(tmp_path / "run")],
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(7))),
    )

    out = capsys.readouterr().out
    assert "7 fetched = 2 selected + 5 not selected (--limit/--record-id)" in out
    assert "2 selected = 0 reused + 2 attempted" in out


def test_no_record_is_counted_twice_and_every_selected_record_is_counted(tmp_path):
    directory = tmp_path / "run"
    records = [*narrative(5), market_price()]

    def check(summary: RunSummary) -> None:
        assert sum(summary.buckets().values()) == summary.attempted
        assert summary.attempted + len(summary.reused) + len(summary.unread) == summary.selected
        assert summary.selected <= summary.fetched
        read = {item.record_id for item in summary.accepted}
        assert read & {item.record_id for item in summary.review_required} == set()

    first = run(
        records,
        provider=Mixed(broken=("N-0",), review=("N-1",), refused=("N-3",)),
        store=Checkpoint(directory),
        batch_size=6,
    )

    check(first)
    assert first.buckets() == {
        "accepted incidents": 1,
        "accepted context": 1,
        "review-required": 1,
        "failed": 1,
        "deferred (refused)": 1,
    }
    assert first.unread[0].record_id == "N-4"
    assert first.review_required[0].contextual and first.counts()["Accepted context"] == 1

    resumed = run(
        records, provider=Counting(name="mixed"), store=Checkpoint(directory).load(), batch_size=6
    )

    check(resumed)
    assert len(resumed.reused) == 3 and resumed.attempted == 3
    assert resumed.buckets()["accepted incidents"] == 3 and resumed.buckets()["failed"] == 0


def test_the_summary_reports_a_checkpoint_disabled_run_without_paths(tmp_path, capsys):
    summary = RunSummary(base_url=BASE, fetched=1, selected=1)

    print_report(summary)

    out = capsys.readouterr().out
    assert "Checkpoint: disabled (set --state-dir to persist and resume)" in out
    assert "Results:" not in out
    assert "The backend returned no records to process." in out


def test_main_writes_the_checkpoint_and_reports_where(tmp_path, capsys):
    directory = tmp_path / "run"

    code = main(
        ["--all", "--state-dir", str(directory)],
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(2))),
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "Records attempted: 2" in out
    assert RESULTS_FILENAME in out and CHECKPOINT_FILENAME in out
    assert len(lines(Checkpoint(directory))) == 2


def test_main_reuses_a_stored_run_and_prints_that_it_read_nothing(tmp_path, capsys):
    directory = tmp_path / "run"
    argv = ["--all", "--state-dir", str(directory)]
    main(
        argv,
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(1))),
    )
    capsys.readouterr()

    code = main(
        argv,
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(1))),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "Records reused: 1" in out
    assert "Records attempted: 0" in out
    assert "already read by an earlier run" in out


def test_one_record_can_be_read_into_the_checkpoint_and_reused_from_it(tmp_path, capsys):
    directory = tmp_path / "run"
    argv = ["--record-id", "NEWS-MDU-WLR-0001", "--state-dir", str(directory)]
    transport = responder(single(waterlogging()))

    code = main(
        argv,
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=transport,
    )

    assert code == EXIT_OK
    assert transport.calls == [f"{BASE}/records/NEWS-MDU-WLR-0001"]
    entry = Checkpoint(directory).load().get("NEWS-MDU-WLR-0001")
    assert entry.state == COMPLETED and entry.mode == "semantic"

    again = NeverCalled()
    code = main(
        argv,
        provider=again,
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(single(waterlogging())),
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "Records reused: 1" in out and "Records attempted: 0" in out


def test_the_limit_still_bounds_a_run_that_is_writing_a_checkpoint(tmp_path, capsys):
    main(
        ["--state-dir", str(tmp_path / "run")],
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(DEFAULT_LIMIT + 3))),
    )

    out = capsys.readouterr().out
    assert f"Records fetched: {DEFAULT_LIMIT + 3}" in out
    assert f"Records attempted: {DEFAULT_LIMIT}" in out
    assert len(lines(Checkpoint(tmp_path / "run"))) == DEFAULT_LIMIT


def test_main_exits_with_the_quota_code_and_saves_what_it_deferred(tmp_path, capsys):
    directory = tmp_path / "run"

    code = main(
        ["--all", "--state-dir", str(directory)],
        provider=OutOfQuota(answers=0),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(2))),
    )

    out = capsys.readouterr().out
    assert code == EXIT_DEFERRED
    assert "Deferred (refused): 1" in out and "Deferred (never sent): 1" in out
    assert "Deferred (provider quota)" in out
    assert "Stopped asking the provider" in out
    assert {item["state"] for item in lines(Checkpoint(directory))} == {DEFERRED}


def test_main_exits_with_the_checkpoint_code_and_keeps_the_file_it_rejected(tmp_path, capsys):
    directory = tmp_path / "run"
    directory.mkdir()
    broken = directory / RESULTS_FILENAME
    broken.write_text("{ not json\n", encoding="utf-8")

    code = main(
        ["--all", "--state-dir", str(directory)],
        provider=Scripted(payload=water_answer()),
        environ={ENV_API_BASE_URL: BASE},
        transport=responder(listing(*narrative(1))),
    )

    captured = capsys.readouterr()
    assert code == EXIT_CHECKPOINT
    assert "checkpoint failed" in captured.err
    assert RESULTS_FILENAME in captured.err and "line 1" in captured.err
    assert broken.read_text(encoding="utf-8") == "{ not json\n"
    assert captured.out == ""


def test_concurrent_reads_ask_every_record_exactly_once(tmp_path):
    provider = Counting()

    summary = run(narrative(6), provider=provider, semantic_concurrency=4, batch_size=6)

    assert sorted(provider.asks) == [f"N-{index}" for index in range(6)]
    assert len(summary.incidents) == 6 and summary.failures == []
    assert sorted(item.incident.provenance.record_id for item in summary.incidents) == sorted(
        provider.asks
    )


def test_a_quota_refusal_under_concurrency_defers_every_unread_record(tmp_path):
    provider = OutOfQuota(answers=0)

    summary = run(narrative(6), provider=provider, semantic_concurrency=3, batch_size=6)

    assert len(summary.outcomes) == 6
    assert all(item.deferred for item in summary.outcomes)
    assert len(set(provider.asks)) == len(provider.asks)
    assert len(provider.asks) <= 6
    assert summary.stop_reason and summary.failures == []
    assert summary.processed == 0


def test_run_pipeline_refuses_a_batch_or_concurrency_it_cannot_honour():
    transport = responder(listing())

    with pytest.raises(ValueError, match="batch-size"):
        run_pipeline(base_url=BASE, transport=transport, batch_size=0)
    with pytest.raises(ValueError, match="concurrency"):
        run_pipeline(base_url=BASE, transport=transport, semantic_concurrency=0)
    with pytest.raises(ValueError, match=str(MAX_SEMANTIC_CONCURRENCY)):
        run_pipeline(base_url=BASE, transport=transport, semantic_concurrency=9)


def test_the_bulk_options_are_refused_before_anything_is_read():
    for argv in (
        ["--batch-size", "0"],
        ["--semantic-concurrency", "0"],
        ["--semantic-concurrency", str(MAX_SEMANTIC_CONCURRENCY + 1)],
        ["--no-resume"],
        ["--batch-size", "0", "--state-dir", "runs/a"],
    ):
        with pytest.raises(SystemExit) as stop:
            pipeline.parse_args(argv)
        assert stop.value.code == 2

    parsed = pipeline.parse_args([])
    assert parsed.batch_size == DEFAULT_BATCH_SIZE
    assert parsed.state_dir is None
    assert parsed.semantic_concurrency == 1
    assert parsed.no_resume is False
    assert inspect.signature(run_pipeline).parameters["semantic_concurrency"].default == 1
    assert MAX_SEMANTIC_CONCURRENCY == 8

    chosen = pipeline.parse_args(
        ["--batch-size", "7", "--state-dir", "runs/a", "--semantic-concurrency", "4", "--no-resume"]
    )
    assert (chosen.batch_size, chosen.state_dir, chosen.semantic_concurrency, chosen.no_resume) \
        == (7, "runs/a", 4, True)
