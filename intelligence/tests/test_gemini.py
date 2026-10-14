from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from intelligence.checkpoint import COMPLETED, FAILED, Checkpoint
from intelligence.fallback import (
    CANDIDATE_KEYS,
    MAX_CANDIDATES,
    Candidate,
    Chain,
    ChainExhaustedError,
    ChainFailedError,
    build_chain,
    chain_configs,
    classify,
)
from intelligence.intelligence import ExtractionDraft, InvalidExtraction
from intelligence.llm import (
    ENV_API_KEY,
    ENV_GEMINI_API_KEY,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_REASONING_EFFORT,
    GEMINI_MINIMAL_THINKING,
    LIMIT_UNCLASSIFIED,
    GeminiProvider,
    LLMConfig,
    LLMRequest,
    ModelUnavailableError,
    PROVIDERS,
    ProviderAuthError,
    ProviderNotConfigured,
    QuotaExhaustedError,
    RateLimitedError,
    StructuredOutputError,
    TransportError,
    VENDOR_KEY_VARS,
    build_provider,
    gemini_answer,
    gemini_schema,
    json_schema_for,
    limit_state,
)
from intelligence.pipeline import run_pipeline
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.test_batch import BASE, narrative

KEY = "gemini-key-that-is-not-a-key"
GEMINI = "https://generativelanguage.googleapis.com"
INTERACTIONS = f"{GEMINI}/v1beta/interactions"
PRIMARY_MODEL = "gemini-3.8-flash"

DEPLOYED = str(Path(__file__).resolve().parents[1] / "chain.gemini.json")
ENTRIES = json.loads(Path(DEPLOYED).read_text(encoding="utf-8"))["candidates"]
PRIMARY = ENTRIES[0]["model"]
FALLBACK = ENTRIES[1]["model"]


def config(model: str = PRIMARY_MODEL, **overrides: Any) -> LLMConfig:
    settings: dict[str, Any] = {
        "provider": "gemini",
        "model": model,
        "api_key": KEY,
        "max_retries": 0,
        "max_tokens": 8192,
        "temperature": 0.0,
    }
    settings.update(overrides)
    return LLMConfig(**settings)


class Recorder:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.slept: list[float] = []

    def transport(self, url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, str]:
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload})
        found = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return found if isinstance(found, tuple) else (200, found)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def sent(self) -> dict[str, Any]:
        return self.calls[0]["payload"]

    @property
    def sends(self) -> int:
        return len(self.calls)


def provider(replies: Any, *, settings: Optional[LLMConfig] = None) -> tuple[GeminiProvider, Recorder]:
    recorder = Recorder(replies if isinstance(replies, list) else [replies])
    return (
        GeminiProvider(settings or config(), transport=recorder.transport, sleeper=recorder.sleep),
        recorder,
    )


def request(**overrides: Any) -> LLMRequest:
    body: dict[str, Any] = {
        "system": "Report only what the record supports.",
        "user": "field data.content\nHeavy rain causes waterlogging",
        "json_schema": json_schema_for(ExtractionDraft),
    }
    body.update(overrides)
    return LLMRequest(**body)


def answer(payload: Any, **body: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return json.dumps({"id": "int_1", "status": "completed", "output_text": text, **body})


def steps(*carries: tuple[str, str], **body: Any) -> str:
    return json.dumps(
        {
            "status": body.pop("status", "completed"),
            "steps": [
                {"type": kind, "content": [{"type": "text", "text": text}]} for kind, text in carries
            ],
            **body,
        }
    )


def refused(status: int, word: str, message: str, *, reason: Optional[str] = None, retry: Optional[str] = None) -> str:
    error: dict[str, Any] = {"code": status, "message": message, "status": word}
    details: list[dict[str, str]] = []
    if reason is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason, "domain": "googleapis.com"}
        )
    if retry is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    if details:
        error["details"] = details
    return json.dumps({"error": error})


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
        return found if isinstance(found, tuple) else (200, found)

    def count(self, model: str) -> int:
        return self.sends.count(model)


def candidate(model: str, *, base_url: str = GEMINI) -> Candidate:
    return Candidate(config(model, base_url=base_url), key_source=ENV_GEMINI_API_KEY)


def ask(provider: Any) -> Any:
    return provider.generate_structured(request())


def deployed_keys(**extra: str) -> dict[str, str]:
    return {ENV_GEMINI_API_KEY: KEY, **extra}


def other_service() -> LLMConfig:
    return LLMConfig(
        provider="groq",
        model="openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1",
        api_key="a-credential-for-another-service",
        max_retries=0,
        max_tokens=2048,
    )


def deployed(endpoint: Any) -> Chain:
    return build_chain(other_service(), environ=deployed_keys(), chain_path=DEPLOYED, transport=endpoint)


def test_gemini_is_a_configurable_provider_and_the_environment_names_it():
    assert PROVIDERS["gemini"] is GeminiProvider
    assert GeminiProvider.accepts_transport is True and GeminiProvider.requires_api_key is True
    assert config().resolved_base_url() == GEMINI
    found = LLMConfig.from_env({ENV_PROVIDER: "gemini", ENV_MODEL: PRIMARY, ENV_GEMINI_API_KEY: KEY})
    assert (found.provider, found.model, found.api_key) == ("gemini", PRIMARY, KEY)
    assert VENDOR_KEY_VARS["gemini"] == ENV_GEMINI_API_KEY
    built = build_provider(None, environ={ENV_PROVIDER: "gemini", ENV_MODEL: PRIMARY, ENV_GEMINI_API_KEY: KEY})
    assert isinstance(built, GeminiProvider)
    assert KEY not in json.dumps(built.config.summary())


def test_the_pipelines_own_variable_still_wins_over_the_vendors_one():
    assert (
        LLMConfig.from_env(
            {ENV_PROVIDER: "gemini", ENV_MODEL: PRIMARY, ENV_GEMINI_API_KEY: "vendors", ENV_API_KEY: "own"}
        ).api_key
        == "own"
    )


def test_without_a_credential_the_request_is_refused_before_anything_is_sent():
    recorder = Recorder([answer({})])
    with pytest.raises(ProviderNotConfigured) as raised:
        GeminiProvider(config(api_key=None), transport=recorder.transport)

    assert ENV_GEMINI_API_KEY in str(raised.value) and ENV_API_KEY in str(raised.value)
    assert KEY not in str(raised.value)
    assert recorder.sends == 0


def test_the_request_is_the_interactions_endpoint_and_the_key_travels_only_as_a_header():
    built, recorder = provider([answer({"severity": {"value": "high"}})])
    output = built.generate_structured(request())

    assert output.payload == {"severity": {"value": "high"}}
    assert output.response.provider == "gemini" and output.response.model == PRIMARY_MODEL
    assert output.response.finish_reason == "completed"
    assert recorder.calls[0]["url"] == INTERACTIONS
    assert recorder.calls[0]["headers"] == {"content-type": "application/json", "x-goog-api-key": KEY}
    assert KEY not in json.dumps(recorder.sent) and KEY not in recorder.calls[0]["url"]
    assert sorted(recorder.sent) == [
        "generation_config",
        "input",
        "model",
        "response_format",
        "system_instruction",
    ]
    assert recorder.sent["model"] == PRIMARY_MODEL
    assert recorder.sent["system_instruction"] == "Report only what the record supports."


def test_a_base_url_that_already_names_the_api_version_is_not_written_twice():
    built, recorder = provider([answer({})], settings=config(base_url=f"{GEMINI}/v1beta/"))
    built.generate_structured(request())
    assert recorder.calls[0]["url"] == INTERACTIONS


def test_the_budget_and_the_thinking_level_are_sent_in_the_endpoints_own_names():
    built, recorder = provider([answer({})])
    built.generate_structured(request())

    generation = recorder.sent["generation_config"]
    assert generation["max_output_tokens"] == 8192 and generation["temperature"] == 0.0
    assert "thinking_level" not in generation, "no effort configured leaves the model's own in force"

    for effort, level in (("low", "low"), ("medium", "medium"), ("high", "high"), ("none", GEMINI_MINIMAL_THINKING)):
        asked, sent = provider([answer({})], settings=config(reasoning_effort=effort))
        asked.generate_structured(request())
        assert sent.sent["generation_config"].get("thinking_level") == level, effort

    sized, sent = provider([answer({})])
    sized.generate_structured(request(max_tokens=1200))
    assert sent.sent["generation_config"]["max_output_tokens"] == 1200


def test_the_schema_sent_carries_the_shape_and_none_of_the_keywords_this_endpoint_has_no_words_for():
    built, recorder = provider([answer({})])
    built.generate_structured(request())

    asked = recorder.sent["response_format"]
    assert asked["mime_type"] == "application/json"
    sent = asked["schema"]
    rendered = json.dumps(sent)
    for absent in ("$defs", "$ref", '"default"', "ExtractionDraft", "propertyOrdering", "additionalProperties"):
        assert absent not in rendered, absent

    assert sent["type"] == "object"
    assert sent["properties"]["record_kind"] == {"type": ["string", "null"]}
    assert sent["properties"]["context_type"] == {"type": ["string", "null"]}
    assert sent["properties"]["severity"]["type"] == ["object", "null"]
    assert sent["properties"]["severity"]["properties"]["char_start"] == {
        "minimum": 0,
        "type": ["integer", "null"],
    }
    assert sent["properties"]["locations"]["type"] == "array"
    assert "properties" in sent["properties"]["locations"]["items"]


def test_a_field_of_the_answer_that_happens_to_be_called_title_survives_the_conversion():
    converted = gemini_schema(json_schema_for(ExtractionDraft))

    assert "title" in converted["properties"]
    assert converted["properties"]["title"]["properties"]["value"]["type"] == ["string", "null"]


def test_an_answer_that_arrives_only_in_steps_is_read_from_the_last_one_holding_text():
    built, _ = provider(
        [steps(("thought_summary", "Considering the record."), ("model_output", '{"severity": {"value": "low"}}'))]
    )
    assert built.generate_structured(request()).payload == {"severity": {"value": "low"}}

    assert gemini_answer({"output_text": '{"a": 1}', "steps": []}) == ('{"a": 1}', "output_text")
    assert gemini_answer({"steps": []}) == (None, "0 step(s), none holding text")
    assert gemini_answer({}) == (None, "neither output_text nor steps")


def test_an_answer_that_is_not_json_is_refused_rather_than_read_as_an_empty_one():
    built, recorder = provider([answer("I cannot answer with the schema you asked for.")])

    with pytest.raises(StructuredOutputError, match="no JSON object") as raised:
        built.generate_structured(request())

    assert "I cannot answer" in str(raised.value) and classify(raised.value) == "output"
    assert recorder.sends == 1


def test_an_answer_with_no_text_at_all_says_where_it_looked_and_what_the_status_said():
    built, _ = provider([(200, json.dumps({"status": "completed", "steps": []}))])
    with pytest.raises(StructuredOutputError) as raised:
        built.generate_structured(request())
    assert "no text to read" in str(raised.value) and "0 step(s)" in str(raised.value)

    failed_run, _ = provider([(200, json.dumps({"status": "failed"}))])
    with pytest.raises(StructuredOutputError, match="status failed"):
        failed_run.generate_structured(request())


def test_an_answer_the_endpoint_stopped_at_its_budget_is_named_as_truncated_and_never_parsed():
    half = '{"locations": [{"text": "மதுரை'
    built, _ = provider(
        [
            (
                200,
                json.dumps(
                    {
                        "status": "incomplete",
                        "output_text": half,
                        "usage": {"total_input_tokens": 9000, "total_output_tokens": 8192, "total_thought_tokens": 8192},
                    }
                ),
            )
        ]
    )

    with pytest.raises(StructuredOutputError) as raised:
        built.generate_structured(request())

    said = str(raised.value)
    assert "ran out of completion budget" in said and classify(raised.value) == "output"
    assert "8192" in said and ENV_MAX_TOKENS in said and ENV_REASONING_EFFORT in said
    assert "total_thought_tokens" not in said, "the numbers are reported, not the endpoint's key names"
    assert GEMINI_MINIMAL_THINKING in said, "this endpoint has no level below minimal and the report says so"
    assert half not in said, "a half answer is not quoted back as though it were evidence"


def test_an_exhausted_allowance_costs_one_send_and_never_a_second():
    built, recorder = provider(
        [(429, refused(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota, please check your plan and billing details.", reason="QUOTA_EXCEEDED"))],
        settings=config(max_retries=3, max_wait_seconds=60.0),
    )

    with pytest.raises(QuotaExhaustedError) as raised:
        built.generate_structured(request())

    assert recorder.sends == 1 and recorder.slept == []
    assert "nothing further was sent" in str(raised.value) and KEY not in str(raised.value)


def test_a_busy_window_is_waited_what_the_endpoint_asked_and_then_given_up_on():
    busy = (429, refused(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota).", reason="RATE_LIMIT_EXCEEDED", retry="0.5s"))
    built, recorder = provider([busy, busy], settings=config(max_retries=2, max_wait_seconds=60.0))

    with pytest.raises(RateLimitedError) as raised:
        built.generate_structured(request())

    assert recorder.slept == [0.5, 0.5], "the endpoint's own wait, not a step of this layer's choosing"
    assert recorder.sends == 3 and classify(raised.value) == "rate"

    long = (429, refused(429, "RESOURCE_EXHAUSTED", "Please retry later.", reason="RATE_LIMIT_EXCEEDED", retry="900s"))
    waiting, waited = provider([long], settings=config(max_retries=2, max_wait_seconds=60.0))
    with pytest.raises(RateLimitedError, match="900s"):
        waiting.generate_structured(request())
    assert waited.sends == 1 and waited.slept == [], "a wait past the whole budget stops the sends, saying so"


def test_a_limit_that_named_neither_an_allowance_nor_a_window_is_never_reported_as_spent_quota():
    bare = (429, refused(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota)."))
    assert limit_state(429, json.loads(bare[1])) == LIMIT_UNCLASSIFIED

    built, recorder = provider([bare], settings=config(max_retries=2, max_wait_seconds=60.0))
    with pytest.raises(RateLimitedError) as raised:
        built.generate_structured(request())

    assert not isinstance(raised.value, QuotaExhaustedError)
    assert recorder.sends == 2, "one further send for a refusal that explained nothing, then it stops"


def test_a_refused_credential_or_an_unserved_model_ends_the_sending_at_once():
    key, sends = provider(
        [(401, refused(401, "UNAUTHENTICATED", "API key not valid. Please pass a valid API key."))],
        settings=config(max_retries=3),
    )
    with pytest.raises(ProviderAuthError) as raised:
        key.generate_structured(request())
    assert "credential was refused" in str(raised.value) and KEY not in str(raised.value)
    assert sends.sends == 1

    denied, _ = provider(
        [(403, refused(403, "PERMISSION_DENIED", "Permission denied on resource project."))],
        settings=config(max_retries=3),
    )
    with pytest.raises(ProviderAuthError):
        denied.generate_structured(request())

    absent, counts = provider(
        [(404, refused(404, "NOT_FOUND", f"Model [id=models/{PRIMARY}] was not found or does not exist."))],
        settings=config(max_retries=3),
    )
    with pytest.raises(ModelUnavailableError) as raised:
        absent.generate_structured(request())
    assert "does not serve this model" in str(raised.value) and counts.sends == 1


def test_a_service_that_was_unavailable_is_sent_again_until_the_retries_run_out():
    built, recorder = provider(
        [(503, refused(503, "UNAVAILABLE", "The service is currently unavailable."))],
        settings=config(max_retries=2, max_wait_seconds=60.0),
    )

    with pytest.raises(TransportError) as raised:
        built.generate_structured(request())

    assert recorder.sends == 3 and recorder.slept == [0.2, 0.4]
    assert "after 3 send(s)" in str(raised.value)


def test_a_request_this_endpoint_refused_as_invalid_is_not_sent_again():
    built, recorder = provider(
        [(400, refused(400, "INVALID_ARGUMENT", "field response_format.schema: unknown keyword"))],
        settings=config(max_retries=3),
    )

    with pytest.raises(TransportError) as raised:
        built.generate_structured(request())

    assert recorder.sends == 1 and "INVALID_ARGUMENT" in str(raised.value)


def test_the_shipped_chain_file_holds_only_identity_and_names_the_gemini_credential():
    assert 1 < len(ENTRIES) <= MAX_CANDIDATES
    for entry in ENTRIES:
        assert sorted(entry) == ["api_key_env", "base_url", "model", "provider"]
        assert all(key in CANDIDATE_KEYS for key in entry)
        assert entry["provider"] == "gemini"
        assert entry["base_url"] == GEMINI
        assert entry["api_key_env"] == ENV_GEMINI_API_KEY
        assert "api_key" not in entry
    assert PRIMARY != FALLBACK


def test_the_shipped_chain_asks_the_gemini_model_named_first_and_the_other_one_second():
    candidates, problems = chain_configs(other_service(), environ=deployed_keys(), chain_path=DEPLOYED)

    assert problems == []
    assert [item.label for item in candidates] == [f"gemini/{PRIMARY}", f"gemini/{FALLBACK}"]
    assert [item.config.resolved_base_url() for item in candidates] == [GEMINI, GEMINI]
    assert [item.key_source for item in candidates] == [ENV_GEMINI_API_KEY] * 2
    assert [item.config.max_tokens for item in candidates] == [2048, 2048]
    assert [item.config.api_key for item in candidates] == [KEY, KEY]
    assert [type(item.build()) for item in candidates] == [type(candidates[0].build())] * 2
    assert candidates[0].build().name == "gemini"


def test_the_shipped_chain_puts_both_models_on_one_gemini_account():
    candidates, _ = chain_configs(other_service(), environ=deployed_keys(), chain_path=DEPLOYED)

    assert candidates[0].scope == candidates[1].scope == f"{GEMINI}|{ENV_GEMINI_API_KEY}"


def test_a_gemini_chain_with_no_credential_can_ask_nothing():
    with pytest.raises(ProviderNotConfigured) as raised:
        chain_configs(other_service(), environ={}, chain_path=DEPLOYED)

    assert f"{ENV_GEMINI_API_KEY} holds none" in str(raised.value)
    assert str(raised.value).count(PRIMARY) == 1 and str(raised.value).count(FALLBACK) == 1
    assert KEY not in str(raised.value)


def test_a_spent_gemini_allowance_costs_the_shipped_chain_one_send_across_records():
    endpoint = Endpoint(
        **{
            PRIMARY: (
                429,
                refused(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.", reason="QUOTA_EXCEEDED"),
            )
        }
    )
    chain = deployed(endpoint)

    for _ in range(3):
        with pytest.raises(ChainExhaustedError) as stop:
            chain.read(ask)

    assert stop.value.kind == "quota"
    assert endpoint.sends == [PRIMARY], "a spent account is refused once for the run, not once a record"
    assert chain.available() == []


def test_a_busy_gemini_window_defers_the_record_and_a_refused_key_is_said_as_the_account():
    endpoint = Endpoint(
        **{PRIMARY: (429, refused(429, "RESOURCE_EXHAUSTED", "Please retry.", reason="RATE_LIMIT_EXCEEDED"))}
    )
    chain = deployed(endpoint)
    with pytest.raises(ChainExhaustedError) as stop:
        chain.read(ask)
    assert stop.value.kind == "rate" and endpoint.sends == [PRIMARY]

    refused_key = Endpoint(**{PRIMARY: (401, refused(401, "UNAUTHENTICATED", "API key not valid."))})
    closed = deployed(refused_key)
    with pytest.raises(ChainExhaustedError) as stop:
        closed.read(ask)

    note = closed.scopes_note()
    assert stop.value.kind == "auth" and KEY not in note
    assert f"gemini/{PRIMARY} at {GEMINI} [{ENV_GEMINI_API_KEY}]: closed: auth" in note
    assert f"gemini/{FALLBACK} at {GEMINI} [{ENV_GEMINI_API_KEY}]: closed" in note


def test_a_model_gemini_does_not_serve_gives_the_shipped_fallback_its_turn():
    endpoint = Endpoint(
        **{
            PRIMARY: (404, refused(404, "NOT_FOUND", f"Model [id=models/{PRIMARY}] was not found or does not exist.")),
            FALLBACK: answer(water_answer()),
        }
    )
    chain = deployed(endpoint)

    answered = chain.read(ask)

    assert answered.label == f"gemini/{FALLBACK}"
    assert answered.attempts[0].startswith(f"gemini/{PRIMARY}: model:")
    assert endpoint.sends == [PRIMARY, FALLBACK]

    chain.read(ask)
    assert endpoint.sends == [PRIMARY, FALLBACK, FALLBACK], "a dead model is asked once for the run, not once a record"


def test_an_unreadable_answer_from_both_models_is_the_records_failure_not_the_accounts():
    endpoint = Endpoint(
        **{
            PRIMARY: answer("I cannot answer with the shape you asked for."),
            FALLBACK: json.dumps({"status": "completed", "steps": []}),
        }
    )
    chain = deployed(endpoint)

    with pytest.raises(ChainFailedError) as stop:
        chain.read(ask)

    assert stop.value.attempts[0].startswith(f"gemini/{PRIMARY}: output:")
    assert f"gemini/{FALLBACK}: output:" in stop.value.attempts[1]
    assert endpoint.sends == [PRIMARY, FALLBACK]
    assert chain.available(), "an unreadable answer says nothing about the account, so both stay askable"


def test_a_run_on_the_gemini_chain_records_who_answered_in_the_checkpoint(tmp_path):
    endpoint = Endpoint(
        **{
            PRIMARY: (404, refused(404, "NOT_FOUND", f"Model [id=models/{PRIMARY}] was not found or does not exist.")),
            FALLBACK: answer(water_answer()),
        }
    )
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=deployed(endpoint),
    )

    assert summary.outcomes[0].state == COMPLETED
    assert summary.outcomes[0].answered_by == f"gemini/{FALLBACK}"
    assert summary.answered_by() == {f"gemini/{FALLBACK}": 1}
    assert len(summary.incidents) == 1 and summary.deferred == []
    assert (store.provider, store.model) == ("gemini", PRIMARY)
    assert store.candidates == [("gemini", PRIMARY), ("gemini", FALLBACK)]
    entry = store.get("N-0")
    assert (entry.provider, entry.model) == ("gemini", FALLBACK)
    assert entry.attempts[0].startswith(f"gemini/{PRIMARY}: model:")


def test_an_extraction_gemini_answered_outside_the_shape_is_refused_and_says_what_arrived(tmp_path):
    wrapped = dict(water_answer(), record_kind={"value": "incident", "field": "data.content"})
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={},
        chain=Chain([candidate(PRIMARY)], transport=Endpoint(**{PRIMARY: answer(wrapped)})),
    )

    assert summary.outcomes[0].state == FAILED
    assert summary.outcomes[0].error.startswith("every candidate refused:")
    assert "record_kind arrived as an object" in summary.outcomes[0].error
    assert "Nothing was coerced" in summary.outcomes[0].error
    assert store.get("N-0").model == PRIMARY
    with pytest.raises(InvalidExtraction):
        raise InvalidExtraction(summary.outcomes[0].error)


def test_an_injected_transport_reaches_a_gemini_candidate_and_still_not_an_sdk_one():
    endpoint = Endpoint(**{PRIMARY: answer({"severity": {"value": "high"}})})
    built = candidate(PRIMARY).build(transport=endpoint)

    assert built.generate_structured(request()).payload == {"severity": {"value": "high"}}

    sdk = Candidate(LLMConfig(provider="groq", model="m", api_key=KEY))
    with pytest.raises(ProviderNotConfigured, match="injected transport cannot reach it"):
        sdk.build(transport=endpoint)


def test_a_gemini_provider_that_was_handed_over_is_the_one_the_run_asks(tmp_path):
    built, recorder = provider([answer(water_answer())], settings=config(model=PRIMARY_MODEL))
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ={ENV_PROVIDER: "gemini", ENV_MODEL: "a-model-elsewhere", ENV_GEMINI_API_KEY: KEY},
        provider=built,
    )

    assert summary.outcomes[0].state == COMPLETED and recorder.sends == 1
    assert store.get("N-0").model == PRIMARY_MODEL
    assert summary.chain is None
