import io
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from intelligence.checkpoint import COMPLETED, DEFERRED, Entry, FAILED, Checkpoint
from intelligence.fallback import (
    CANDIDATE_KEYS,
    ENV_CHAIN_FILE,
    MAX_CANDIDATES,
    Candidate,
    Chain,
    ChainExhaustedError,
    ChainFailedError,
    ProviderNotConfigured,
    build_chain,
    chain_configs,
    chain_named,
    classify,
)
from intelligence.intelligence import InvalidExtraction
from intelligence.llm import (
    EndpointUnreachableError,
    LLMConfig,
    LLMRequest,
    ModelUnavailableError,
    OpenAICompatibleProvider,
    ProviderAuthError,
    QuotaExhaustedError,
    RateLimitedError,
    StructuredOutputError,
    TransportError,
)
from intelligence.pipeline import print_report, run_pipeline
from intelligence.records import MappingError
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.test_batch import BASE, narrative, prices

LOCAL = "http://127.0.0.1:8000/v1"
OTHER = "http://127.0.0.1:8081/v1"


def config(model: str, base_url: str = LOCAL) -> LLMConfig:
    return LLMConfig(
        provider="openai_compatible",
        model=model,
        base_url=base_url,
        api_key="test-key",
        max_retries=0,
        max_tokens=2048,
    )


def stub(model: str, *, base_url: str = LOCAL, key: str = "INTELLIGENCE_LLM_API_KEY") -> Candidate:
    return Candidate(config(model, base_url), key_source=key)


def request() -> LLMRequest:
    return LLMRequest(system="read this", user="and this", json_schema={"type": "object"})


def refused_json(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}})


def completion(answer: Any, *, finish: Optional[str] = "stop", content: Optional[str] = None) -> str:
    text = content if content is not None else (
        answer if isinstance(answer, str) else json.dumps(answer)
    )
    return json.dumps(
        {"choices": [{"message": {"content": text}, "finish_reason": finish}], "model": "served"}
    )


class Endpoint:
    def __init__(self, **by_model: Any) -> None:
        self.by_model = dict(by_model)
        self.sends: list[str] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, str]:
        model = str(payload["model"])
        self.sends.append(model)
        if model not in self.by_model:
            raise AssertionError(f"{model} was never configured to answer")
        found = self.by_model[model]
        return (200, found) if isinstance(found, str) else found

    def count(self, model: str) -> int:
        return self.sends.count(model)


def status(code: int, body: str) -> tuple[int, str]:
    return code, body


def chain_of(*models: str, transport: Any = None, elsewhere=()) -> Chain:
    apart = set(elsewhere)
    candidates = [stub(model, base_url=OTHER if model in apart else LOCAL) for model in models]
    return Chain(candidates, transport=transport)


def reads(**answers: Any) -> Any:
    def ask(provider: Any) -> Any:
        model = str(provider.model)
        found = answers.get(model)
        if isinstance(found, BaseException):
            raise found
        if found is None:
            raise AssertionError(f"{model} should not have been asked")
        return found

    return ask


def base_settings() -> LLMConfig:
    return LLMConfig(provider="openai_compatible", model="gpt-oss-120b", base_url=LOCAL)


def test_no_chain_named_leaves_the_single_configured_reader_as_the_whole_configuration():
    candidates, problems = chain_configs(
        LLMConfig(provider="groq", model="openai/gpt-oss-20b"), environ={}, chain_path=None
    )

    assert [item.label for item in candidates] == ["groq/openai/gpt-oss-20b"]
    assert problems == []
    assert not chain_named({"INTELLIGENCE_LLM_PROVIDER": "groq"}, None)


def test_a_candidate_inherits_the_configured_budget_and_timeout_rather_than_a_new_one(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        json.dumps({"candidates": [{"provider": "ollama", "model": "gemma3:27b"}]}), encoding="utf-8"
    )
    base = LLMConfig(
        provider="openai_compatible",
        model="gpt-oss-120b",
        base_url=LOCAL,
        timeout_seconds=37.5,
        max_tokens=999,
        reasoning_effort="low",
    )

    candidates, problems = chain_configs(base, environ={}, chain_path=str(path))

    found = candidates[0].config
    assert problems == []
    assert (found.provider, found.model) == ("ollama", "gemma3:27b")
    assert found.timeout_seconds == 37.5 and found.max_tokens == 999
    assert found.reasoning_effort == "low"
    assert found.base_url is None and found.resolved_base_url() == "http://127.0.0.1:11434/v1"


def test_a_candidate_that_cannot_be_verified_is_dropped_and_the_reason_is_kept(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key_env": "ABSENT"},
                    {"provider": "groq", "model": ""},
                    {"provider": "openai_compatible", "model": "gpt-oss-120b", "seed": 7},
                    {"provider": "ollama", "model": "qwen3:32b"},
                ]
            }
        ),
        encoding="utf-8",
    )

    candidates, problems = chain_configs(base_settings(), environ={}, chain_path=str(path))

    assert [item.label for item in candidates] == ["ollama/qwen3:32b"]
    assert len(problems) == 3
    assert "ABSENT holds none" in problems[0]
    assert "states no model" in problems[1]
    assert "seed" in problems[2]


def test_a_candidate_naming_a_provider_that_is_not_implemented_here_is_dropped(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        json.dumps(
            {"candidates": [{"provider": "mistral", "model": "mistral-large"},
                            {"provider": "ollama", "model": "qwen3:32b"}]}
        ),
        encoding="utf-8",
    )

    candidates, problems = chain_configs(base_settings(), environ={}, chain_path=str(path))

    assert [item.label for item in candidates] == ["ollama/qwen3:32b"]
    assert "names provider 'mistral'" in problems[0]


def test_a_chain_file_that_leaves_nothing_to_ask_stops_the_run(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        json.dumps({"candidates": [{"provider": "groq", "model": "m", "api_key_env": "ABSENT"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ProviderNotConfigured, match="leaves no candidate to ask"):
        chain_configs(base_settings(), environ={}, chain_path=str(path))


@pytest.mark.parametrize("body", ["{}", '{"candidates": []}'])
def test_a_chain_file_that_names_no_candidate_at_all_is_refused_by_name(tmp_path, body):
    path = tmp_path / "chain.json"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ProviderNotConfigured, match="no list of candidates"):
        chain_configs(base_settings(), environ={}, chain_path=str(path))


def test_a_chain_file_that_is_not_json_says_what_it_expected(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text("groq, ollama", encoding="utf-8")

    with pytest.raises(ProviderNotConfigured, match="is not JSON"):
        chain_configs(base_settings(), environ={}, chain_path=str(path))


def test_a_chain_longer_than_one_record_may_walk_is_refused_rather_than_truncated(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        json.dumps(
            {"candidates": [{"provider": "ollama", "model": f"m{i}"} for i in range(MAX_CANDIDATES + 1)]}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProviderNotConfigured, match=f"more than the {MAX_CANDIDATES}"):
        chain_configs(base_settings(), environ={}, chain_path=str(path))


def test_the_environment_variable_names_the_same_file_the_flag_does(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(json.dumps([{"provider": "ollama", "model": "m"}]), encoding="utf-8")

    assert chain_named({ENV_CHAIN_FILE: str(path)}, None)
    candidates, _ = chain_configs(
        base_settings(), environ={ENV_CHAIN_FILE: str(path)}, chain_path=None
    )

    assert [item.label for item in candidates] == ["ollama/m"]


def test_the_first_candidate_answered_and_nothing_else_was_asked():
    endpoint = Endpoint(first=completion(water_answer()), second=None)
    chain = chain_of("first", "second", transport=endpoint)

    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == "openai_compatible/first"
    assert answered.attempts == ()
    assert endpoint.sends == ["first"]


def test_a_model_the_endpoint_will_not_serve_gives_the_next_candidate_its_turn():
    endpoint = Endpoint(
        first=status(404, refused_json("model_not_found", "The model `first` does not exist.")),
        second=completion(water_answer()),
    )
    chain = chain_of("first", "second", transport=endpoint)

    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == "openai_compatible/second"
    assert answered.attempts[0].startswith("openai_compatible/first: model:")
    assert "does not exist" in answered.attempts[0]
    assert endpoint.sends == ["first", "second"]


def test_a_rate_limit_closes_every_model_behind_the_same_endpoint_and_credential():
    endpoint = Endpoint(first=(429, refused_json("rate_limit_exceeded", "Rate limit reached")))
    chain = chain_of("first", "second", transport=endpoint)

    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "rate"
    assert endpoint.sends == ["first"]
    assert "skipped openai_compatible/second: its endpoint and credential refused" in (
        stop.value.attempts[1]
    )


def test_a_rate_limit_on_another_account_is_answered_by_the_candidate_on_it():
    endpoint = Endpoint(
        first=(429, refused_json("rate_limit_exceeded", "Rate limit reached: too many requests")),
        second=completion(water_answer()),
    )
    chain = chain_of("first", "second", transport=endpoint, elsewhere=("second",))

    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == "openai_compatible/second"
    assert answered.attempts[0].startswith("openai_compatible/first: rate:")
    assert endpoint.sends == ["first", "second"]


def test_an_exhausted_allowance_ends_the_asking_for_the_run_across_records():
    endpoint = Endpoint(first=(402, refused_json("quota_exceeded", "Quota exceeded for the project")))
    chain = chain_of("first", "second", transport=endpoint)

    for _ in range(3):
        with pytest.raises(ChainExhaustedError) as stop:
            chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "quota"
    assert endpoint.sends == ["first"], "a spent allowance costs one refusal, not three"


def test_a_refused_credential_closes_the_models_behind_it_but_not_one_elsewhere():
    chain = Chain(
        [stub("first"), stub("sibling"), stub("elsewhere", base_url=OTHER, key="OTHER_KEY")],
        transport=Endpoint(
            first=(401, refused_json("invalid_api_key", "The API key is not valid")),
            sibling=completion(water_answer()),
            elsewhere=completion(water_answer()),
        ),
    )

    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == "openai_compatible/elsewhere"
    assert chain.candidates[0].closed_kind == "auth"
    assert chain.candidates[1].closed is None and chain.candidates[1].provider is None


def test_an_unreachable_endpoint_leaves_the_record_for_a_later_run():
    def down(url, headers, payload, timeout):
        raise EndpointUnreachableError(f"{url} could not be reached: timed out")

    chain = chain_of("first", "second", transport=down)

    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "unreachable"
    assert "timed out" in str(stop.value)


def test_an_answer_that_is_not_json_gives_the_next_model_its_turn_and_is_never_repaired():
    endpoint = Endpoint(
        first=completion(None, content="I cannot answer with the schema you asked for."),
        second="a body that is not JSON at all",
    )
    chain = chain_of("first", "second", transport=endpoint)

    with pytest.raises(ChainFailedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert "openai_compatible/first: output:" in stop.value.attempts[0]
    assert "openai_compatible/second: transient:" in stop.value.attempts[1]
    assert endpoint.sends == ["first", "second"]


def test_a_completion_cut_off_at_its_token_budget_is_named_as_the_cause():
    chain = chain_of("first", transport=Endpoint(first=completion({}, finish="length")))

    with pytest.raises(ChainFailedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert "ran out of completion budget" in str(stop.value)


def test_a_record_that_cannot_be_read_is_never_asked_of_another_model():
    chain = chain_of("first", "second")

    with pytest.raises(MappingError, match="no content"):
        chain.read(reads(first=MappingError("no content field")))

    assert chain.answered_by == {}


def test_a_refusal_about_this_record_gives_the_other_candidate_its_turn():
    chain = chain_of("first", "second", elsewhere=("second",))

    answered = chain.read(
        reads(
            first=InvalidExtraction("severity is not a token the schema offers"),
            second="second answered",
        )
    )

    assert answered.label == "openai_compatible/second"
    assert answered.attempts == (
        "openai_compatible/first: shape: severity is not a token the schema offers",
    )


def test_every_candidate_refusing_this_record_is_reported_with_every_turn_it_took():
    chain = chain_of("first", "second", elsewhere=("second",))

    with pytest.raises(ChainFailedError) as stop:
        chain.read(
            reads(
                first=StructuredOutputError("nothing to read"),
                second=TransportError("endpoint answered 500"),
            )
        )

    assert len(stop.value.attempts) == 2
    assert "transient" in stop.value.attempts[1]


def test_the_failure_vocabulary_is_the_one_the_policy_and_the_report_share():
    assert classify(QuotaExhaustedError("x")) == "quota"
    assert classify(RateLimitedError("x")) == "rate"
    assert classify(ModelUnavailableError("x")) == "model"
    assert classify(ProviderAuthError("x")) == "auth"
    assert classify(EndpointUnreachableError("x")) == "unreachable"
    assert classify(StructuredOutputError("x")) == "output"
    assert classify(InvalidExtraction("x")) == "shape"
    assert classify(MappingError("x")) == "record"


def test_a_run_with_a_chain_names_who_answered_and_keeps_the_turns_in_the_checkpoint(tmp_path):
    endpoint = Endpoint(
        first=status(404, refused_json("model_not_found", "The model `first` does not exist.")),
        second=completion(water_answer()),
    )
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(2))),
        store=store,
        environ={},
        chain=chain_of("first", "second", transport=endpoint),
    )

    assert [item.answered_by for item in summary.outcomes] == ["openai_compatible/second"] * 2
    assert summary.fallbacks == summary.outcomes
    assert endpoint.count("first") == 1, "a dead model is asked once for the run, not once a record"
    assert endpoint.count("second") == 2

    entry = store.get("N-0")
    assert (entry.state, entry.provider, entry.model) == (COMPLETED, "openai_compatible", "second")
    assert entry.prompt_digest
    assert entry.attempts[0].startswith("openai_compatible/first: model:")


def test_a_chain_defers_what_it_could_not_read_and_a_resume_asks_again(tmp_path):
    directory = tmp_path / "run"
    store = Checkpoint(directory)
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(2))),
        store=store,
        environ={},
        chain=chain_of(
            "first",
            transport=Endpoint(first=(429, refused_json("rate_limit_exceeded", "Rate limit reached"))),
        ),
    )

    assert [item.state for item in summary.outcomes] == [DEFERRED, DEFERRED]
    assert summary.stop_kind == "rate"
    assert store.get("N-0").state == DEFERRED

    later = Checkpoint(directory).load()
    answered = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(2))),
        store=later,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    assert answered.deferred == [] and len(answered.incidents) == 2
    assert later.get("N-0").state == COMPLETED


def test_a_finished_reading_survives_a_later_run_that_could_not_read_it(tmp_path):
    directory = tmp_path / "run"
    store = Checkpoint(directory)
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )
    digest = store.get("N-0").prompt_digest

    resumed = Checkpoint(directory).load()
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=resumed,
        environ={},
        chain=chain_of(
            "first", transport=Endpoint(first=(401, refused_json("invalid_api_key", "refused")))
        ),
        resume=False,
    )

    assert summary.outcomes[0].state in (FAILED, DEFERRED) or summary.outcomes[0].deferred
    assert resumed.preserved["N-0"].startswith("kept as completed")
    assert resumed.get("N-0").state == COMPLETED
    assert resumed.get("N-0").prompt_digest == digest


def test_a_reading_any_candidate_in_the_chain_produced_is_reusable_by_the_chain(tmp_path):
    directory = tmp_path / "run"
    store = Checkpoint(directory, candidates=[("openai_compatible", "first")])
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    resumed = Checkpoint(directory).load()
    resumed.candidates = [("openai_compatible", "other"), ("openai_compatible", "first")]
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=resumed,
        environ={},
        chain=chain_of("other", "first", transport=Endpoint(other=completion(water_answer()))),
    )

    assert [item.record_id for item in summary.reused] == ["N-0"] and summary.outcomes == []
    assert resumed.invalidated == {}


def test_a_reading_from_a_model_off_the_chain_is_read_again(tmp_path):
    directory = tmp_path / "run"
    store = Checkpoint(directory, candidates=[("openai_compatible", "first")])
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    resumed = Checkpoint(directory).load()
    resumed.candidates = [("openai_compatible", "different")]
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=resumed,
        environ={},
        chain=chain_of("different", transport=Endpoint(different=completion(water_answer()))),
    )

    assert summary.skipped == [] and len(summary.incidents) == 1
    assert "not one of the candidates this run asks" in resumed.invalidated["N-0"]


def test_a_chain_never_reads_a_record_that_needs_no_reading(tmp_path):
    endpoint = Endpoint(first=None)
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*prices(2))),
        store=Checkpoint(tmp_path / "run"),
        environ={},
        chain=chain_of("first", transport=endpoint),
    )

    assert endpoint.sends == [] and len(summary.outcomes) == 2
    assert summary.counts()["Structured (no model)"] == 2


def test_a_reworded_prompt_re_reads_what_the_older_wording_produced(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    store = Checkpoint(directory)
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    monkeypatch.setattr("intelligence.pipeline.PROMPT_DIGEST", "a-different-wording")
    resumed = Checkpoint(directory).load()
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=resumed,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    assert summary.skipped == [] and len(summary.incidents) == 1
    assert "a rule in the prompt has since been reworded" in resumed.invalidated["N-0"]


def test_a_reading_saved_before_prompts_were_digests_is_not_reused_quietly(tmp_path):
    directory = tmp_path / "run"
    store = Checkpoint(directory)
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )
    saved = Entry(**{**store.get("N-0").as_dict(), "prompt_digest": ""})
    store.entries["N-0"] = saved
    store.flush()

    resumed = Checkpoint(directory).load()
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=resumed,
        environ={},
        chain=chain_of("first", transport=Endpoint(first=completion(water_answer()))),
    )

    assert summary.skipped == []
    assert "cannot tell whether the wording still matches" in resumed.invalidated["N-0"]


def test_the_chain_says_which_account_closed_and_never_the_credential():
    chain = Chain(
        [stub("first"), stub("second"), stub("elsewhere", base_url=OTHER, key="OTHER_KEY")],
        transport=Endpoint(
            first=(429, refused_json("rate_limit_exceeded", "Rate limit reached")),
            elsewhere=completion(water_answer()),
        ),
    )
    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == "openai_compatible/elsewhere"
    assert answered.attempts[0].startswith("openai_compatible/first: rate:")
    assert answered.attempts[1].startswith("skipped openai_compatible/second:")

    note = chain.scopes_note()
    assert "test-key" not in note
    assert "INTELLIGENCE_LLM_API_KEY" in note and "OTHER_KEY" in note
    assert "closed with its endpoint" in note and ": open" in note
    assert "http://127.0.0.1:8000" in note and "http://127.0.0.1:8081" in note


def test_the_report_names_the_chain_who_answered_and_the_turns_it_took():
    stream = io.StringIO()
    endpoint = Endpoint(
        first=status(404, refused_json("model_not_found", "The model `first` does not exist.")),
        second=completion(water_answer()),
    )
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        environ={},
        chain=chain_of("first", "second", transport=endpoint),
    )
    print_report(summary, stream=stream)
    out = stream.getvalue()

    assert "Model chain: openai_compatible/first, openai_compatible/second" in out
    assert "Chain scopes:" in out and "[INTELLIGENCE_LLM_API_KEY]: open" in out
    assert "Read by: openai_compatible/second (1)" in out
    assert "Read by a fallback: 1" in out
    assert "Chain turns (1)" in out
    assert "N-0: openai_compatible/first: model:" in out
    assert endpoint.sends == ["first", "second"]


DEPLOYED = str(Path(__file__).resolve().parents[1] / "chain.openrouter.json")
OPENROUTER = "https://openrouter.ai/api/v1"
ENTRIES = json.loads(Path(DEPLOYED).read_text(encoding="utf-8"))["candidates"]
PRIMARY = ENTRIES[0]["model"]
FALLBACK = ENTRIES[1]["model"]
OPENROUTER_MODELS = {"qwen/qwen3.8-flash", "openai/gpt-oss-120b"}


def other_service() -> LLMConfig:
    return LLMConfig(
        provider="groq",
        model="openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1",
        api_key="a-credential-for-another-service",
        max_retries=0,
        max_tokens=2048,
    )


def deployed_keys(**extra: str) -> dict[str, str]:
    return {"OPENROUTER_API_KEY": "test-key", **extra}


def deployed(endpoint: Any) -> Chain:
    return build_chain(other_service(), environ=deployed_keys(), chain_path=DEPLOYED, transport=endpoint)


def test_the_deployed_chain_asks_the_named_model_first_and_the_other_one_second():
    candidates, problems = chain_configs(
        other_service(), environ=deployed_keys(), chain_path=DEPLOYED
    )

    assert problems == []
    assert [item.label for item in candidates] == [
        f"openai_compatible/{PRIMARY}",
        f"openai_compatible/{FALLBACK}",
    ]
    assert [item.config.resolved_base_url() for item in candidates] == [OPENROUTER, OPENROUTER]
    assert [item.key_source for item in candidates] == ["OPENROUTER_API_KEY"] * 2
    assert candidates[0].config.max_retries == 0 and candidates[0].config.max_tokens == 2048
    assert [item.provider for item in candidates] == [None, None], "naming is not asking"
    assert all(isinstance(item.build(), OpenAICompatibleProvider) for item in candidates)


def test_the_deployed_chain_puts_both_models_on_one_account_and_the_second_behind_it():
    candidates, _ = chain_configs(other_service(), environ=deployed_keys(), chain_path=DEPLOYED)

    assert candidates[0].scope == candidates[1].scope
    assert candidates[0].scope == f"https://openrouter.ai|OPENROUTER_API_KEY"
    assert [item.scope for item in candidates] == [candidates[0].scope] * 2


def test_the_deployed_chain_file_holds_no_credential_and_only_the_fields_the_loader_reads():
    entries = ENTRIES

    assert 1 < len(entries) <= MAX_CANDIDATES
    assert {entry["model"] for entry in entries} == OPENROUTER_MODELS
    for entry in entries:
        assert sorted(entry) == ["api_key_env", "base_url", "model", "provider"]
        assert "api_key" not in entry
        assert all(key in CANDIDATE_KEYS for key in entry)
        assert entry["provider"] == "openai_compatible"
        assert entry["base_url"] == OPENROUTER
        assert entry["api_key_env"] == "OPENROUTER_API_KEY"


def test_the_deployed_chain_never_carries_a_credential_from_the_configuration_it_inherits():
    held = chain_configs(other_service(), environ=deployed_keys(), chain_path=DEPLOYED)[0]
    assert [item.config.api_key for item in held] == ["test-key", "test-key"]

    unheld = chain_configs(other_service(), environ={}, chain_path=DEPLOYED)[0]
    assert [item.label for item in unheld] == [
        f"openai_compatible/{PRIMARY}",
        f"openai_compatible/{FALLBACK}",
    ]
    assert [item.config.api_key for item in unheld] == [None, None]


def test_a_spent_openrouter_allowance_costs_the_deployed_chain_one_send():
    endpoint = Endpoint(
        **{
            PRIMARY: status(
                429,
                refused_json("insufficient_quota", "You have exceeded your allocation for this month"),
            )
        }
    )
    chain = deployed(endpoint)

    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "quota"
    assert endpoint.sends == [PRIMARY]
    assert f"skipped openai_compatible/{FALLBACK}: its endpoint and credential refused" in (
        stop.value.attempts[1]
    )
    assert chain.available() == []

    with pytest.raises(ChainExhaustedError) as again:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert endpoint.sends == [PRIMARY], "a spent account is refused once, not once per record"
    assert all(turn.startswith("skipped ") for turn in again.value.attempts)


def test_a_busy_openrouter_window_costs_the_deployed_chain_nothing_further():
    endpoint = Endpoint(
        **{PRIMARY: status(429, refused_json("rate_limit_exceeded", "Rate limit reached for this key"))}
    )
    chain = deployed(endpoint)

    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "rate"
    assert endpoint.sends == [PRIMARY]


def test_a_model_openrouter_does_not_serve_gives_the_deployed_fallback_its_turn():
    endpoint = Endpoint(
        **{
            PRIMARY: status(404, refused_json("model_not_found", f"The model `{PRIMARY}` does not exist")),
            FALLBACK: completion(water_answer()),
        }
    )
    chain = deployed(endpoint)

    answered = chain.read(lambda provider: provider.generate_structured(request()))

    assert answered.label == f"openai_compatible/{FALLBACK}"
    assert answered.attempts[0].startswith(f"openai_compatible/{PRIMARY}: model:")
    assert endpoint.sends == [PRIMARY, FALLBACK]

    chain.read(lambda provider: provider.generate_structured(request()))

    assert endpoint.sends == [PRIMARY, FALLBACK, FALLBACK]


def test_a_refused_openrouter_key_is_said_to_be_the_account_and_never_the_credential():
    endpoint = Endpoint(
        **{PRIMARY: status(401, refused_json("no_api_key", "No credentials provided."))}
    )
    chain = deployed(endpoint)

    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(lambda provider: provider.generate_structured(request()))

    assert stop.value.kind == "auth"
    assert endpoint.sends == [PRIMARY]
    note = chain.scopes_note()
    assert "test-key" not in note
    assert f"openai_compatible/{PRIMARY} at https://openrouter.ai [OPENROUTER_API_KEY]: closed: auth" in note
    assert f"openai_compatible/{FALLBACK} at https://openrouter.ai [OPENROUTER_API_KEY]: closed" in note


def test_the_deployed_chain_names_both_models_in_the_report_and_stores_who_answered():
    endpoint = Endpoint(
        **{
            PRIMARY: status(404, refused_json("model_not_found", f"The model `{PRIMARY}` does not exist")),
            FALLBACK: completion(water_answer()),
        }
    )
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        environ={},
        chain=deployed(endpoint),
    )

    assert summary.outcomes[0].answered_by == f"openai_compatible/{FALLBACK}"
    assert summary.outcomes[0].attempts[0].startswith(f"openai_compatible/{PRIMARY}: model:")
    assert summary.chain.describe() == f"openai_compatible/{PRIMARY}, openai_compatible/{FALLBACK}"
    assert summary.answered_by() == {f"openai_compatible/{FALLBACK}": 1}
    assert endpoint.sends == [PRIMARY, FALLBACK]
