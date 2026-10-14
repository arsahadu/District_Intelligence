from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from groq import APIConnectionError, AuthenticationError, BadRequestError, RateLimitError

from intelligence.llm import (
    DEFAULT_MAX_WAIT_SECONDS,
    DOTENV_PATH,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MAX_WAIT,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_REASONING_EFFORT,
    ENV_TIMEOUT,
    GroqProvider,
    LIMIT_UNCLASSIFIED,
    LLMConfig,
    LLMRequest,
    LLMResponse,
    OpenAICompatibleProvider,
    ProviderNotConfigured,
    QuotaExhaustedError,
    RateLimitedError,
    StructuredOutputError,
    TransportError,
    build_provider,
    json_schema_for,
    limit_state,
    load_dotenv_file,
    parse_json_object,
    retry_guidance,
)
from intelligence.intelligence import PROMPT_DIGEST, ExtractionDraft

KEY = "sk-not-a-real-key"


def chat_body(payload: Any) -> str:
    return json.dumps({"model": "gpt-oss-120b", "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}]})


class Recorder:
    def __init__(self, replies: list[tuple[int, str]]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.slept: list[float] = []

    def transport(self, url: str, headers, payload, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload, "timeout": timeout})
        status, body = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return status, body

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def sent(self) -> dict[str, Any]:
        return self.calls[0]["payload"]


def provider(replies, *, config: Optional[LLMConfig] = None) -> tuple[OpenAICompatibleProvider, Recorder]:
    recorder = Recorder(replies)
    settings = config or LLMConfig(model="gpt-oss-120b", api_key=KEY, max_retries=2)
    return OpenAICompatibleProvider(settings, transport=recorder.transport, sleeper=recorder.sleep), recorder


def request(payload_shape: bool = True) -> LLMRequest:
    return LLMRequest(
        system="Report only what the record supports.",
        user="field data.content\nHeavy rain causes waterlogging",
        json_schema=json_schema_for(ExtractionDraft) if payload_shape else None,
    )


def test_configuration_comes_from_the_environment():
    found = LLMConfig.from_env({
        ENV_PROVIDER: "vllm",
        ENV_MODEL: "Qwen/Qwen3-27B",
        ENV_API_KEY: KEY,
        ENV_TIMEOUT: "12.5",
    })
    assert found.provider == "vllm"
    assert found.model == "Qwen/Qwen3-27B"
    assert found.timeout_seconds == 12.5
    assert found.resolved_base_url() == "http://127.0.0.1:8000/v1"
    assert LLMConfig.from_env({}).max_retries == 2


def test_an_unparseable_number_fails_as_configuration_not_as_a_default():
    with pytest.raises(ProviderNotConfigured, match="TIMEOUT_SECONDS"):
        LLMConfig.from_env({ENV_TIMEOUT: "soon"})


CONFIG_VARS = (ENV_PROVIDER, ENV_MODEL, ENV_BASE_URL, ENV_API_KEY, ENV_TIMEOUT)


def local_env_file(monkeypatch, tmp_path, text: str) -> str:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("intelligence.llm.DOTENV_PATH", str(path))
    return str(path)


def test_default_dotenv_path_is_next_to_the_intelligence_package(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    expected = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    assert os.path.normpath(DOTENV_PATH) == expected
    assert os.path.isfile(DOTENV_PATH)
    assert load_dotenv_file(".env") is True


def test_a_local_env_file_configures_the_provider(tmp_path, monkeypatch):
    local_env_file(
        monkeypatch,
        tmp_path,
        f"{ENV_PROVIDER}=openai_compatible\n{ENV_MODEL}=openai/gpt-oss-120b\n"
        f"{ENV_BASE_URL}=https://example.test/v1\n{ENV_API_KEY}={KEY}\n{ENV_TIMEOUT}=12.5\n",
    )
    found = LLMConfig.from_env()
    assert found.model == "openai/gpt-oss-120b"
    assert found.resolved_base_url() == "https://example.test/v1"
    assert found.timeout_seconds == 12.5
    assert found.summary()["api_key_present"] is True
    assert KEY not in json.dumps(found.summary())
    assert KEY not in repr(found)


def test_an_exported_variable_beats_the_env_file(tmp_path, monkeypatch):
    local_env_file(monkeypatch, tmp_path, f"{ENV_MODEL}=from-file\n{ENV_TIMEOUT}=99\n")
    monkeypatch.setenv(ENV_MODEL, "from-shell")
    found = LLMConfig.from_env()
    assert found.model == "from-shell"
    assert found.timeout_seconds == 99.0


def test_an_injected_environment_never_reads_the_file(tmp_path, monkeypatch):
    path = local_env_file(monkeypatch, tmp_path, f"{ENV_MODEL}=from-file\n")
    assert LLMConfig.from_env({ENV_MODEL: "injected"}).model == "injected"
    assert ENV_MODEL not in os.environ, path


def test_an_absent_env_file_is_not_an_error(tmp_path, monkeypatch):
    missing = local_env_file(monkeypatch, tmp_path, "")
    os.remove(missing)
    assert load_dotenv_file(missing) is False
    assert LLMConfig.from_env().model is None


def test_the_key_is_never_rendered_or_logged():
    config = LLMConfig(model="qwen", api_key=KEY)
    assert KEY not in repr(config)
    assert KEY not in str(config)
    assert config.summary()["api_key_present"] is True
    assert KEY not in json.dumps(config.summary())


def test_no_provider_is_built_from_an_incomplete_environment():
    with pytest.raises(ProviderNotConfigured, match=ENV_MODEL):
        build_provider(LLMConfig(provider="openai_compatible"), environ={})
    with pytest.raises(ProviderNotConfigured, match="known providers are"):
        build_provider(LLMConfig(provider="claude", model="x"))


def test_an_endpoint_alias_needs_no_base_url():
    recorder = Recorder([(200, chat_body({}))])
    built = OpenAICompatibleProvider(LLMConfig(provider="ollama", model="qwen27b"), transport=recorder.transport)
    built.generate_structured(request())
    assert recorder.calls[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    explicit = LLMConfig(provider="vllm", model="m", base_url="https://gpu.internal:8443/v1/")
    assert explicit.resolved_base_url() == "https://gpu.internal:8443/v1"


def test_a_structured_call_asks_for_json_and_carries_the_key_only_as_a_header():
    built, recorder = provider([(200, chat_body({"severity": {"value": "high"}}))])
    output = built.generate_structured(request())
    assert output.payload == {"severity": {"value": "high"}}
    assert recorder.sent["response_format"] == {"type": "json_object"}
    assert recorder.sent["model"] == "gpt-oss-120b"
    assert recorder.calls[0]["headers"]["authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(recorder.sent)
    assert [message["role"] for message in recorder.sent["messages"]] == ["system", "user"]


def test_a_text_call_returns_the_answer_untouched():
    built, _ = provider([(200, json.dumps({"choices": [{"message": {"content": "நீர் தேங்கியது"}}]}))])
    assert built.generate_text(request(payload_shape=False)) == "நீர் தேங்கியது"


def groq_reply(content: str, *, finish: str = "stop") -> Any:
    return SimpleNamespace(
        model="openai/gpt-oss-120b",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
    )


class GroqCompletions:
    def __init__(
        self,
        reply: Any,
        error: Optional[BaseException] = None,
        *,
        answers: Optional[list[Any]] = None,
    ) -> None:
        self.reply = reply
        self.error = error
        self.answers = answers
        self.calls: list[dict[str, Any]] = []
        self.slept: list[float] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.answers is not None:
            outcome = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if self.error is not None:
            raise self.error
        return self.reply


def groq_provider(reply: Any, error: Optional[BaseException] = None) -> tuple[GroqProvider, GroqCompletions]:
    completions = GroqCompletions(reply, error)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    config = LLMConfig(
        provider="groq", model="openai/gpt-oss-120b", api_key=KEY, temperature=0.2, max_tokens=512
    )
    return (
        GroqProvider(config, client=client, sleeper=completions.slept.append),
        completions,
    )


def test_groq_sends_one_chat_completion_with_the_configured_settings():
    built, calls = groq_provider(groq_reply("மதுரையில் நீர் தேங்கியது"))
    assert built.generate_text(request(payload_shape=False)) == "மதுரையில் நீர் தேங்கியது"
    sent = calls.calls[0]
    assert sent["model"] == "openai/gpt-oss-120b"
    assert [message["role"] for message in sent["messages"]] == ["system", "user"]
    assert sent["temperature"] == 0.2
    assert sent["max_tokens"] == 512
    assert "response_format" not in sent
    assert KEY not in repr(sent)


def test_groq_keeps_the_structured_path_and_the_response_metadata():
    built, calls = groq_provider(groq_reply('{"severity": {"value": "high"}}'))
    output = built.generate_structured(request())
    assert output.payload == {"severity": {"value": "high"}}
    assert output.response.provider == "groq"
    assert output.response.model == "openai/gpt-oss-120b"
    assert output.response.finish_reason == "stop"
    assert output.response.latency_ms is not None


def test_groq_sends_the_draft_schema_in_strict_mode_not_a_bare_json_hint():
    built, calls = groq_provider(groq_reply('{"severity": {"value": "high"}}'))
    built.generate_structured(request())
    sent = calls.calls[0]["response_format"]
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["name"] == "ExtractionDraft"
    assert sent["json_schema"]["strict"] is True

    schema = sent["json_schema"]["schema"]
    printed = json.dumps(schema)
    assert "$ref" not in printed and "$defs" not in printed
    assert schema["additionalProperties"] is False
    assert "incident_type" in schema["required"]
    grounded = schema["properties"]["incident_type"]["anyOf"][0]
    assert grounded["additionalProperties"] is False
    assert sorted(grounded["required"]) == sorted(grounded["properties"])
    assert {"type": "null"} in grounded["properties"]["quote"]["anyOf"]
    assert '"minimum"' not in printed and '"default"' not in printed
    assert KEY not in printed


def test_a_request_setting_overrides_the_configured_one():
    built, calls = groq_provider(groq_reply("{}"))
    built.complete(LLMRequest(system="s", user="u", temperature=0.9, max_tokens=64))
    assert calls.calls[0]["temperature"] == 0.9
    assert calls.calls[0]["max_tokens"] == 64


def test_a_groq_failure_keeps_its_status_and_message_and_loses_its_credential():
    unauthorized = AuthenticationError(
        "Error code: 401",
        response=httpx.Response(
            401, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        ),
        body={"error": {"message": "invalid api key", "code": "invalid_api_key"}},
    )
    built, _ = groq_provider(groq_reply("{}"), error=unauthorized)
    with pytest.raises(TransportError, match="HTTP 401") as raised:
        built.complete(request(payload_shape=False))
    reported = str(raised.value)
    assert "AuthenticationError" in reported and "invalid api key" in reported
    assert "never retried" in reported
    assert KEY not in reported

    dropped = APIConnectionError(
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    )
    built, _ = groq_provider(groq_reply("{}"), error=dropped)
    with pytest.raises(TransportError, match="APIConnectionError"):
        built.complete(request(payload_shape=False))


def test_groq_is_registered_and_refuses_to_build_without_a_key():
    assert isinstance(
        build_provider(LLMConfig(provider="groq", model="openai/gpt-oss-120b", api_key=KEY)),
        GroqProvider,
    )
    with pytest.raises(ProviderNotConfigured, match="API_KEY"):
        GroqProvider(LLMConfig(provider="groq", model="openai/gpt-oss-120b"))


def test_an_openai_style_base_url_is_reduced_to_its_origin():
    built = GroqProvider(
        LLMConfig(provider="groq", model="m", api_key=KEY, base_url="https://api.groq.com/openai/v1")
    )
    assert built._client.base_url == "https://api.groq.com"


def test_an_endpoint_that_gives_nothing_back_is_an_error_not_an_empty_incident():
    built, _ = provider([(200, json.dumps({"model": "m"}))])
    with pytest.raises(TransportError, match="no choices"):
        built.generate_structured(request())


def test_an_endpoint_that_answers_in_prose_fails_loudly():
    built, _ = provider([(200, json.dumps({"choices": [{"message": {"content": "I cannot help"}}]}))])
    with pytest.raises(StructuredOutputError, match="no JSON object"):
        built.generate_structured(request())


def test_a_server_error_is_retried_then_raised():
    built, recorder = provider([(503, "busy"), (503, "still busy"), (503, "gone")])
    with pytest.raises(TransportError, match="answered 503"):
        built.generate_structured(request())
    assert len(recorder.calls) == 3
    assert recorder.slept == [0.2, 0.4]


def test_a_retry_that_recovers_is_not_an_error():
    built, recorder = provider([(429, "slow down"), (200, chat_body({"title": {"value": "x"}}))])
    output = built.generate_structured(request())
    assert output.payload["title"]["value"] == "x"
    assert len(recorder.calls) == 2


def test_a_non_retryable_status_fails_at_once():
    built, recorder = provider([(401, "bad key"), (200, chat_body({}))])
    with pytest.raises(TransportError, match="401"):
        built.generate_structured(request())
    assert len(recorder.calls) == 1


def test_an_unreachable_endpoint_is_a_transport_error():
    def explode(url, headers, payload, timeout):
        raise TransportError(f"{url} could not be reached: refused")

    built = OpenAICompatibleProvider(LLMConfig(model="m"), transport=explode)
    with pytest.raises(TransportError, match="could not be reached"):
        built.generate_structured(request())


def test_json_is_read_out_of_a_fence_but_only_one_object():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object("here you go: {\"a\": 1}") == {"a": 1}
    with pytest.raises(StructuredOutputError, match="no JSON object"):
        parse_json_object("nothing here")
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        parse_json_object('{"a": }')
    with pytest.raises(StructuredOutputError, match="no JSON object"):
        parse_json_object("[1, 2]")
    with pytest.raises(StructuredOutputError, match="nothing to parse"):
        parse_json_object(None)


def test_the_request_schema_is_the_draft_the_pipeline_parses():
    schema = json_schema_for(ExtractionDraft)
    assert "locations" in schema["properties"]
    assert "canonical_location_id" not in json.dumps(schema)
    assert "latitude" not in json.dumps(schema)


def test_the_answer_shape_a_provider_is_asked_for_survives_a_cleanup_pass():
    schema = json_schema_for(ExtractionDraft)
    assert schema["description"] == "The whole model answer for one record."
    assert schema["$defs"]["ContextFactDraft"]["description"].startswith(
        "One value the record carries."
    )
    assert PROMPT_DIGEST == "6e2f43b839df9fca", (
        "the reuse key moved: it hashes the prompt text and the answer shape together, and the shape "
        "is generated from the draft models' docstrings, so trimming one of those rewrites what a "
        "provider is asked to answer and strands every semantic reading already in a checkpoint - "
        "stage-2.29 rewrote rules 1, 3 and 5 to forbid a quote stitched across two fields and to read "
        "severity and priority from the whole article, and the readings taken under stage-2.22 will be "
        "re-asked of the billed provider"
    )


def test_a_response_carries_what_the_endpoint_reported():
    built, _ = provider([(200, json.dumps({"model": "gpt-oss-120b-prod", "choices": [
        {"message": {"content": "{\"a\": 1}"}, "finish_reason": "stop"}]}))])
    output = built.generate_structured(request())
    response: LLMResponse = output.response
    assert response.model == "gpt-oss-120b-prod"
    assert response.finish_reason == "stop"
    assert response.latency_ms is not None
    assert json.loads(response.json()) == {"a": 1}


def test_the_reasoning_budget_is_configuration_and_is_only_sent_when_chosen():
    found = LLMConfig.from_env({ENV_MODEL: "openai/gpt-oss-20b", ENV_REASONING_EFFORT: "Low"})
    assert found.reasoning_effort == "low"
    assert found.summary()["reasoning_effort"] == "low"
    assert LLMConfig.from_env({ENV_MODEL: "m"}).reasoning_effort is None
    with pytest.raises(ProviderNotConfigured, match="REASONING_EFFORT"):
        LLMConfig.from_env({ENV_MODEL: "m", ENV_REASONING_EFFORT: "sometimes"})

    plain, calls = groq_provider(groq_reply("{}"))
    plain.complete(request(payload_shape=False))
    assert "reasoning_effort" not in calls.calls[0]

    budgeted = GroqProvider(
        LLMConfig(provider="groq", model="openai/gpt-oss-20b", api_key=KEY, reasoning_effort="low"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=(shown := GroqCompletions(groq_reply("{}"))))),
    )
    budgeted.complete(request(payload_shape=False))
    assert shown.calls[0]["reasoning_effort"] == "low"

    wire, recorder = provider(
        [(200, chat_body({}))],
        config=LLMConfig(model="gpt-oss-20b", api_key=KEY, reasoning_effort="low"),
    )
    wire.complete(request())
    assert recorder.sent["reasoning_effort"] == "low"


def test_an_answer_the_endpoint_cut_off_names_the_budget_that_ran_out():
    built, _ = groq_provider(groq_reply('{"locations": [{"text"', finish="length"))
    with pytest.raises(StructuredOutputError, match="INTELLIGENCE_LLM_MAX_TOKENS") as raised:
        built.generate_structured(request())
    assert "INTELLIGENCE_LLM_REASONING_EFFORT" in str(raised.value)

    wire, _ = provider([(200, json.dumps({"choices": [
        {"message": {"content": "{}"}, "finish_reason": "length"}]}))])
    with pytest.raises(StructuredOutputError, match="completion budget"):
        wire.complete(request())


def test_a_strict_answer_the_endpoint_could_never_validate_points_at_the_budget():
    rejected = BadRequestError(
        "Error code: 400",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        ),
        body={"error": {"message": "Failed to parse response from model ''",
                        "code": "json_validate_failed"}},
    )
    built, _ = groq_provider(groq_reply("{}"), error=rejected)
    with pytest.raises(TransportError, match="json_validate_failed") as raised:
        built.complete(request())
    reported = str(raised.value)
    assert "INTELLIGENCE_LLM_MAX_TOKENS" in reported
    assert "INTELLIGENCE_LLM_REASONING_EFFORT" in reported
    assert KEY not in reported


def groq_refusal(status: int, body: Any) -> Any:
    return RateLimitError(
        f"Error code: {status}",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        ),
        body=body,
    )


def test_only_a_refusal_the_endpoint_actually_made_is_read_as_a_limit():
    assert limit_state(200, {"error": {"code": "insufficient_quota"}}) is None
    assert limit_state(500, "upstream is down") is None
    assert limit_state(429, {"error": {"code": "insufficient_quota",
                                        "message": "You exceeded your current quota"}}) == "quota"
    assert limit_state(429, {"error": {"code": "daily_quota_exceeded",
                                        "message": "Daily token quota exceeded"}}) == "quota"
    assert limit_state(429, {"error": {"code": "rate_limit_exceeded",
                                        "message": "Too many requests"}}) == "rate"
    assert limit_state(429, "Too Many Requests") == "rate"
    assert limit_state(402, "card declined") == "quota"
    assert limit_state(429, "a body that names nothing") == LIMIT_UNCLASSIFIED


def test_a_required_wait_is_read_from_whatever_stated_it():
    assert retry_guidance({"error": {"message": "busy", "retry_after": 12}}) == 12.0
    assert retry_guidance('{"error": {"message": "busy", "retry_after_ms": 1500}}') == 1.5
    assert retry_guidance({"error": {"message": "Rate limit reached. Please try again in 30.5s."}}) == 30.5
    assert retry_guidance({"error": {"message": "Slow down, retry after 2 minutes"}}) == 120.0
    assert retry_guidance(None, {"retry-after": "47"}) == 47.0
    assert retry_guidance(None, {"x-ratelimit-reset-tokens": "1m30s"}) == 90.0
    assert retry_guidance({"error": {"message": "Rate limit reached for this model"}}) is None
    assert retry_guidance(None, {"content-type": "application/json"}) is None


def test_the_waiting_budget_is_configuration_and_is_named_when_it_was_not_enough():
    found = LLMConfig.from_env({ENV_MODEL: "m", ENV_MAX_WAIT: "90"})
    assert found.max_wait_seconds == 90.0 and found.summary()["max_wait_seconds"] == 90.0
    assert LLMConfig.from_env({ENV_MODEL: "m"}).max_wait_seconds == DEFAULT_MAX_WAIT_SECONDS
    with pytest.raises(ProviderNotConfigured, match="MAX_WAIT_SECONDS"):
        LLMConfig.from_env({ENV_MODEL: "m", ENV_MAX_WAIT: "next Tuesday"})


def test_an_exhausted_allowance_is_never_retried_and_is_named_as_quota():
    refusal = json.dumps(
        {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}
    )
    built, recorder = provider([(429, refusal), (200, chat_body({}))])

    with pytest.raises(QuotaExhaustedError, match="refused on its allowance"):
        built.generate_structured(request())

    assert len(recorder.calls) == 1 and recorder.slept == []


def test_a_daily_allowance_refusal_is_not_read_as_a_busy_window():
    refusal = json.dumps(
        {"error": {"code": "daily_quota_exceeded", "message": "Daily token quota exceeded",
                   "retry_after": 3600}}
    )
    built, recorder = provider([(429, refusal), (200, chat_body({}))])

    with pytest.raises(QuotaExhaustedError):
        built.generate_structured(request())

    assert len(recorder.calls) == 1 and recorder.slept == []


def test_a_busy_window_is_waited_out_over_its_sends_and_then_named_as_a_rate_limit():
    refusal = json.dumps({"error": {"code": "rate_limit_exceeded", "message": "Too many requests"}})
    built, recorder = provider([(429, refusal)])

    with pytest.raises(RateLimitedError, match="answered 429"):
        built.generate_structured(request())

    assert len(recorder.calls) == 3 and recorder.slept == [30.0, 30.0]
    assert sum(recorder.slept) == DEFAULT_MAX_WAIT_SECONDS


def test_a_busy_window_that_stated_its_wait_is_waited_and_asked_again():
    refusal = json.dumps(
        {"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached",
                   "retry_after": 20}}
    )
    built, recorder = provider([(429, refusal), (200, chat_body({"title": {"value": "x"}}))])

    output = built.generate_structured(request())

    assert output.payload["title"]["value"] == "x"
    assert len(recorder.calls) == 2 and recorder.slept == [20.0]


def test_a_wait_longer_than_the_budget_is_not_half_waited_and_says_so():
    refusal = json.dumps(
        {"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached",
                   "retry_after": 3600}}
    )
    built, recorder = provider([(429, refusal), (200, chat_body({}))])

    with pytest.raises(RateLimitedError, match="asked for 3600s") as raised:
        built.generate_structured(request())

    assert len(recorder.calls) == 1 and recorder.slept == []
    assert ENV_MAX_WAIT in str(raised.value)


def test_a_billing_refusal_that_names_no_code_is_still_read_as_an_exhausted_allowance():
    built, recorder = provider([(402, "your card was declined"), (200, chat_body({}))])

    with pytest.raises(QuotaExhaustedError):
        built.generate_structured(request())

    assert len(recorder.calls) == 1


def test_a_refusal_that_explained_nothing_but_a_limit_gets_one_further_send():
    built, recorder = provider([(429, json.dumps({"detail": "hold on"}))])

    with pytest.raises(RateLimitedError, match="answered 429") as raised:
        built.generate_structured(request())

    assert len(recorder.calls) == 2 and recorder.slept == [0.2]
    assert "quota" not in str(raised.value).casefold()


def test_a_groq_allowance_refusal_is_separated_from_a_groq_busy_window():
    spent, calls = groq_provider(
        groq_reply("{}"),
        error=groq_refusal(429, {"error": {"code": "daily_quota_exceeded",
                                           "message": "Daily token quota exceeded"}}),
    )
    with pytest.raises(QuotaExhaustedError, match="nothing further was sent") as raised:
        spent.complete(request(payload_shape=False))
    assert "daily_quota_exceeded" in str(raised.value) and KEY not in str(raised.value)
    assert len(calls.calls) == 1 and calls.slept == []

    busy, sent = groq_provider(
        groq_reply("{}"),
        error=groq_refusal(429, {"error": {"code": "rate_limit_exceeded",
                                           "message": "Rate limit reached for this model"}}),
    )
    with pytest.raises(RateLimitedError, match="Rate limit reached"):
        busy.complete(request(payload_shape=False))
    assert len(sent.calls) == 3 and sent.slept == [30.0, 30.0]


def test_a_groq_wait_stated_in_its_headers_is_honoured_and_the_record_is_asked_again():
    refusal = httpx.Response(
        429,
        headers={"retry-after": "5", "x-ratelimit-reset-tokens": "4m"},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    busy = RateLimitError(
        "Error code: 429",
        response=refusal,
        body={"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached for this model"}},
    )
    completions = GroqCompletions(groq_reply("{}"), answers=[busy, groq_reply("{}")])
    built = GroqProvider(
        LLMConfig(provider="groq", model="openai/gpt-oss-120b", api_key=KEY),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        sleeper=completions.slept.append,
    )

    built.complete(request(payload_shape=False))

    assert completions.slept == [5.0] and len(completions.calls) == 2


def test_a_groq_refusal_whole_the_day_long_is_never_sent_again():
    refusal = httpx.Response(
        429,
        headers={"retry-after": "86400"},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    built, calls = groq_provider(
        groq_reply("{}"),
        error=RateLimitError(
            "Error code: 429",
            response=refusal,
            body={"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached"}},
        ),
    )

    with pytest.raises(RateLimitedError, match="asked for 86400s"):
        built.complete(request(payload_shape=False))

    assert len(calls.calls) == 1 and calls.slept == []
