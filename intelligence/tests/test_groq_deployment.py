from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from intelligence.checkpoint import COMPLETED, Checkpoint
from intelligence.contract import ReviewState
from intelligence.fallback import Chain, chain_configs
from intelligence.intelligence import (
    PROMPT_DIGEST,
    PROMPT_VERSION,
    REQUEST_SCHEMA,
    extract_incident,
    prompt_text,
)
from intelligence.llm import (
    ENV_API_KEY,
    ENV_GEMINI_API_KEY,
    ENV_GROQ_API_KEY,
    ENV_MODEL,
    ENV_PROVIDER,
    GroqProvider,
    LLMConfig,
    PROVIDERS,
    ProviderNotConfigured,
    VENDOR_KEY_VARS,
    build_provider,
)
from intelligence.models.enums import SeverityLevel
from intelligence.pipeline import run_pipeline
from intelligence.router import Mode
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.test_batch import BASE, narrative, prices
from intelligence.tests.test_grounding_regression import (
    BODY,
    DEAD_ID,
    HILL_ID,
    HILL_TITLE,
    LIVE_FAILURES,
    SIX,
    dead_answer,
    grounding,
    hill_answer,
    replayed,
)
from intelligence.tests.test_title_fidelity import ARTICLE, BLOB, HEADLINE, scraped_record

KEY = "groq-key-that-is-not-a-key"
OTHER = "a-credential-for-another-service"
GROQ = "https://api.groq.com"
MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.8-flash"

DEPLOYED = str(Path(__file__).resolve().parents[1] / "chain.groq.json")
ENTRIES = json.loads(Path(DEPLOYED).read_text(encoding="utf-8"))["candidates"]


class Completions:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        found = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(found, BaseException):
            raise found
        if isinstance(found, str):
            return SimpleNamespace(
                model=MODEL,
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=found), finish_reason="stop")
                ],
            )
        return found


class Endpoint:
    def __init__(self, *answers: Any) -> None:
        self.completions = Completions(
            [
                answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
                for answer in answers
            ]
        )
        self.built: list[dict[str, Any]] = []

    def client(self, **kwargs: Any) -> Any:
        self.built.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=self.completions))

    @property
    def sends(self) -> int:
        return len(self.completions.calls)

    @property
    def sent(self) -> dict[str, Any]:
        return self.completions.calls[0]


def installed(built: Endpoint, monkeypatch) -> Endpoint:
    monkeypatch.setattr("groq.Groq", built.client)
    return built


def reader(built: Endpoint) -> GroqProvider:
    return GroqProvider(
        LLMConfig(
            provider="groq",
            model=MODEL,
            api_key=KEY,
            temperature=0.0,
            max_tokens=8192,
            max_retries=0,
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=built.completions)),
    )


def gemini_service() -> LLMConfig:
    return LLMConfig(
        provider="gemini",
        model=GEMINI_MODEL,
        base_url="https://generativelanguage.googleapis.com",
        api_key=OTHER,
        max_retries=0,
        max_tokens=2048,
    )


def deployment(**extra: str) -> dict[str, str]:
    return {
        ENV_PROVIDER: "gemini",
        ENV_MODEL: GEMINI_MODEL,
        ENV_GEMINI_API_KEY: OTHER,
        ENV_GROQ_API_KEY: KEY,
        **extra,
    }


def test_groq_is_a_configurable_provider_and_its_vendor_variable_is_read():
    assert PROVIDERS["groq"] is GroqProvider
    assert GroqProvider.accepts_transport is False and GroqProvider.requires_api_key is True
    assert VENDOR_KEY_VARS["groq"] == ENV_GROQ_API_KEY

    found = LLMConfig.from_env({ENV_PROVIDER: "groq", ENV_MODEL: MODEL, ENV_GROQ_API_KEY: KEY})
    assert (found.provider, found.model, found.api_key) == ("groq", MODEL, KEY)
    assert found.resolved_base_url() == GROQ
    assert KEY not in json.dumps(found.summary())


def test_the_pipelines_own_variable_wins_and_a_gemini_credential_is_never_read_for_groq():
    both = LLMConfig.from_env(
        {ENV_PROVIDER: "groq", ENV_MODEL: MODEL, ENV_API_KEY: "the-own-one", ENV_GROQ_API_KEY: KEY}
    )
    assert both.api_key == "the-own-one"

    only_another_vendor = LLMConfig.from_env(
        {ENV_PROVIDER: "groq", ENV_MODEL: MODEL, ENV_GEMINI_API_KEY: OTHER}
    )
    assert only_another_vendor.api_key is None
    assert OTHER not in json.dumps(only_another_vendor.summary())


def test_without_a_credential_groq_is_refused_before_any_client_exists(monkeypatch):
    built = installed(Endpoint("{}"), monkeypatch)

    with pytest.raises(ProviderNotConfigured) as refused:
        GroqProvider(LLMConfig(provider="groq", model=MODEL))

    assert ENV_GROQ_API_KEY in str(refused.value) and ENV_API_KEY in str(refused.value)
    assert KEY not in str(refused.value)
    assert built.built == [] and built.sends == 0


def test_the_client_is_built_on_groqs_own_origin_from_an_openai_style_url(monkeypatch):
    built = installed(Endpoint("{}"), monkeypatch)
    provider = build_provider(
        LLMConfig(provider="groq", model=MODEL, api_key=KEY, base_url=f"{GROQ}/openai/v1")
    )

    assert isinstance(provider, GroqProvider)
    assert built.built[0]["base_url"] == GROQ
    assert built.built[0]["api_key"] == KEY
    assert built.built[0]["max_retries"] == 0
    assert built.built[0]["timeout"] == provider.config.timeout_seconds
    assert KEY not in str(provider.config.summary())


def test_the_shipped_chain_names_one_groq_candidate_and_no_gemini_at_all():
    assert len(ENTRIES) == 1
    entry = ENTRIES[0]
    assert sorted(entry) == ["api_key_env", "base_url", "model", "provider"]
    assert entry["provider"] == "groq" and entry["model"] == MODEL
    assert entry["api_key_env"] == ENV_GROQ_API_KEY and entry["base_url"] == GROQ
    assert "gemini" not in Path(DEPLOYED).read_text(encoding="utf-8").lower()

    candidates, problems = chain_configs(
        gemini_service(), environ={ENV_GROQ_API_KEY: KEY}, chain_path=DEPLOYED
    )
    assert problems == []
    assert [item.label for item in candidates] == [f"groq/{MODEL}"]
    assert [item.config.provider for item in candidates] == ["groq"]
    assert [item.key_source for item in candidates] == [ENV_GROQ_API_KEY]
    assert [item.config.api_key for item in candidates] == [KEY]
    assert [item.scope for item in candidates] == [f"{GROQ}|{ENV_GROQ_API_KEY}"]
    assert [item.provider for item in candidates] == [None], "naming is not asking"
    assert Chain(candidates).identities == [("groq", MODEL)]

    with pytest.raises(ProviderNotConfigured) as unheld:
        chain_configs(gemini_service(), environ={}, chain_path=DEPLOYED)
    assert ENV_GROQ_API_KEY in str(unheld.value)
    assert OTHER not in str(unheld.value) and KEY not in str(unheld.value)


def test_the_chain_builds_only_groq_when_building_a_gemini_provider_is_a_failure(monkeypatch):
    class NeverGemini:
        accepts_transport = False
        requires_api_key = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("a gemini provider was built while groq was the whole chain")

    monkeypatch.setitem(PROVIDERS, "gemini", NeverGemini)
    built = installed(Endpoint("{}"), monkeypatch)
    candidates, _ = chain_configs(
        gemini_service(), environ={ENV_GROQ_API_KEY: KEY}, chain_path=DEPLOYED
    )

    assert all(isinstance(item.build(), GroqProvider) for item in candidates)
    assert built.built != []


def test_a_run_over_the_deployed_chain_asks_groq_once_and_records_it_as_the_reader(tmp_path, monkeypatch):
    built = installed(Endpoint(water_answer()), monkeypatch)
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ=deployment(),
        chain_path=DEPLOYED,
    )

    assert summary.outcomes[0].state == COMPLETED
    assert summary.chain is not None and summary.chain.describe() == f"groq/{MODEL}"
    assert built.sends == 1
    assert built.sent["model"] == MODEL
    assert built.built[0]["base_url"] == GROQ and built.built[0]["api_key"] == KEY
    assert OTHER not in json.dumps(built.built)

    saved = store.get("N-0")
    assert (saved.provider, saved.model) == ("groq", MODEL)
    assert (saved.prompt_version, saved.prompt_digest) == (PROMPT_VERSION, PROMPT_DIGEST)


def test_the_groq_request_carries_the_compacted_prompt_and_the_answer_schema_untouched():
    built = Endpoint(dead_answer())
    extract_incident(SIX[DEAD_ID], provider=reader(built))

    sent = built.sent
    assert sent["model"] == MODEL and sent["max_tokens"] == 8192 and sent["temperature"] == 0.0
    assert [message["role"] for message in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"].startswith(prompt_text())
    rules = prompt_text().split("Rules:\n")[1]
    assert [int(item) for item in re.findall(r"(?m)^(\d+)\. ", rules)] == list(range(1, 14))

    envelope = sent["response_format"]
    assert envelope["type"] == "json_schema" and envelope["json_schema"]["strict"] is True
    schema = envelope["json_schema"]["schema"]
    assert schema["description"] == REQUEST_SCHEMA["description"]
    assert sorted(schema["properties"]) == sorted(REQUEST_SCHEMA["properties"])
    assert schema["additionalProperties"] is False
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    assert KEY not in json.dumps(sent)


def test_a_groq_answer_of_exact_tamil_spans_is_grounded_and_its_offsets_replay():
    built = Endpoint(dead_answer())
    found = extract_incident(SIX[DEAD_ID], provider=reader(built))

    assert found.generation.provider == "groq" and found.generation.model == MODEL
    assert grounding(found) == LIVE_FAILURES[DEAD_ID]
    assert found.description in BODY[DEAD_ID]
    replayed(found, SIX[DEAD_ID])


def test_a_groq_answer_that_keeps_the_records_own_headline_is_accepted_as_it_stands():
    built = Endpoint(hill_answer())
    found = extract_incident(SIX[HILL_ID], provider=reader(built))

    assert found.title == HILL_TITLE
    assert ("title", "unsupported_claim") not in grounding(found)
    replayed(found, SIX[HILL_ID])


def test_a_groq_answer_that_sends_a_body_sentence_as_the_headline_is_refused_and_reviewed():
    cited = hill_answer()["description"]
    answer = hill_answer() | {
        "title": {
            "value": cited["value"],
            "field": cited["field"],
            "quote": cited["quote"],
            "char_start": cited["char_start"],
            "confidence": 0.9,
        }
    }
    found = extract_incident(SIX[HILL_ID], provider=reader(Endpoint(answer)))

    assert found.title == HILL_TITLE
    assert ("title", "unsupported_claim") in grounding(found)
    assert found.review_required is True
    assert found.validation.state is ReviewState.REVIEW_REQUIRED


def test_a_groq_answer_that_flattened_its_own_reply_is_never_evidence():
    found = extract_incident(scraped_record(), provider=reader(Endpoint({"title": {"value": BLOB}})))

    assert BLOB not in ARTICLE
    assert found.title == HEADLINE
    assert found.description is None
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert {("title", "unknown_field"), ("title", "unsupported_claim")} <= grounding(found)


def test_a_groq_answer_naming_a_token_the_taxonomy_does_not_carry_leaves_the_field_unresolved():
    answer = dead_answer() | {
        "severity": {"value": "catastrophic", "field": "data.content", "quote": "பலியானார்",
                     "char_start": BODY[DEAD_ID].index("பலியானார்"), "confidence": 0.9}
    }
    found = extract_incident(SIX[DEAD_ID], provider=reader(Endpoint(answer)))

    assert found.severity is SeverityLevel.UNRESOLVED
    assert any("severity" in item.field for item in found.validation.issues)
    assert found.review_required is True
    assert found.description in BODY[DEAD_ID]


def test_a_value_shaped_record_never_builds_a_groq_client(tmp_path, monkeypatch):
    built = installed(Endpoint("{}"), monkeypatch)
    store = Checkpoint(tmp_path / "run")
    summary = run_pipeline(
        base_url=BASE,
        transport=responder(listing(*prices(1))),
        store=store,
        environ=deployment(),
        chain_path=DEPLOYED,
    )

    assert summary.outcomes[0].state == COMPLETED
    assert store.get("A-0").mode == Mode.STRUCTURED.value
    assert built.built == [] and built.sends == 0


def test_a_reading_groq_stored_is_not_reused_after_groq_leaves_the_chain(tmp_path, monkeypatch):
    installed(Endpoint(water_answer()), monkeypatch)
    store = Checkpoint(tmp_path / "run")
    run_pipeline(
        base_url=BASE,
        transport=responder(listing(*narrative(1))),
        store=store,
        environ=deployment(),
        chain_path=DEPLOYED,
    )

    ask = {
        "record_id": "N-0",
        "mode": "semantic",
        "prompt_version": PROMPT_VERSION,
        "input_hash": store.get("N-0").input_hash,
        "prompt_digest": PROMPT_DIGEST,
    }
    moved = Checkpoint(tmp_path / "run").load()
    moved.candidates = [("gemini", GEMINI_MODEL)]
    assert moved.reusable(**ask) is None
    assert "which is not one of the candidates this run asks" in moved.invalidated["N-0"]

    same = Checkpoint(tmp_path / "run").load()
    same.candidates = [("groq", MODEL)]
    assert same.reusable(**ask) is not None
