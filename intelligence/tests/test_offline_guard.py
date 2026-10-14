from __future__ import annotations

import os
import socket
import urllib.request

import httpx
import pytest

from intelligence import llm
from intelligence.context import build_context
from intelligence.intelligence import extraction_request
from intelligence.llm import (
    ENV_API_KEY,
    ENV_MODEL,
    GeminiProvider,
    LLMConfig,
    LLMRequest,
    ProviderNotConfigured,
)
from intelligence.pipeline import fetch_records
from intelligence.tests.record_fixtures import waterlogging

BLOCKED = "network access is blocked for the intelligence test session"


def refuses_to_send(action) -> str:
    with pytest.raises(RuntimeError) as caught:
        action()
    assert BLOCKED in str(caught.value)
    return str(caught.value)


def test_a_socket_cannot_connect():
    refuses_to_send(lambda: socket.socket().connect(("127.0.0.1", 9)))


def test_a_port_cannot_be_probed():
    refuses_to_send(lambda: socket.socket().connect_ex(("127.0.0.1", 5432)))


def test_no_name_can_be_resolved():
    refuses_to_send(
        lambda: socket.getaddrinfo("generativelanguage.googleapis.com", 443)
    )


def test_urllib_cannot_open_a_url():
    refuses_to_send(lambda: urllib.request.urlopen("http://127.0.0.1:8000/records", timeout=1))


def test_httpx_cannot_send_either():
    with pytest.raises(Exception) as caught:
        httpx.Client(timeout=1).get("https://generativelanguage.googleapis.com/v1beta/interactions")
    error = caught.value
    trail = " ".join(
        str(item)
        for item in (error, getattr(error, "__cause__", None), getattr(error, "__context__", None))
        if item is not None
    )
    assert BLOCKED in trail


def test_the_backend_fetch_cannot_be_reached():
    refuses_to_send(lambda: fetch_records(base_url="http://127.0.0.1:8000"))


def test_a_provider_holding_a_key_on_purpose_still_cannot_send():
    provider = GeminiProvider(
        LLMConfig(
            provider="gemini",
            model="gemini-3.8-flash",
            api_key="a-key-held-on-purpose-to-prove-the-point",
            base_url="https://generativelanguage.googleapis.com",
            max_retries=0,
        )
    )
    refuses_to_send(lambda: provider.complete(LLMRequest(system="s", user="u")))


def test_a_proxy_setting_is_not_what_stops_a_send(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    refuses_to_send(lambda: urllib.request.urlopen("https://example.com", timeout=1))


def test_the_suite_holds_no_credential_to_send():
    settings = LLMConfig.from_env()
    assert settings.api_key is None
    with pytest.raises(ProviderNotConfigured):
        GeminiProvider(settings)


def test_reading_an_env_file_really_does_write_through(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(f"{ENV_MODEL}=openai/gpt-oss-120b\n{ENV_API_KEY}=sk-not-a-real-key\n", encoding="utf-8")
    monkeypatch.setattr(llm, "DOTENV_PATH", str(path))
    assert LLMConfig.from_env().api_key == "sk-not-a-real-key"
    assert os.environ.get(ENV_API_KEY) == "sk-not-a-real-key"


def test_a_deployment_written_through_by_a_test_did_not_survive_it():
    assert ENV_API_KEY not in os.environ
    assert ENV_MODEL not in os.environ
    assert LLMConfig.from_env().api_key is None


def test_a_real_extraction_request_is_still_undeliverable():
    request = extraction_request(build_context(waterlogging()))
    provider = GeminiProvider(
        LLMConfig(
            provider="gemini",
            model="gemini-3.8-flash",
            api_key="held-on-purpose",
            max_retries=0,
        )
    )
    refuses_to_send(lambda: provider.generate_structured(request))
