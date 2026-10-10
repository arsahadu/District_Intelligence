"""Proof that this session cannot spend a request: every outbound path is closed and says so.

Answering providers with injected transports is a design choice, not a boundary. These tests are the
boundary. Each one takes a path a real call would travel - name resolution, a socket, urllib, httpx,
the backend fetch, a provider holding a credential on purpose - and shows it raising before anything
is sent. A dead proxy is not what stops any of them, and no test here reads the repository's ``.env``.
Two of them look at the environment instead of a socket, so a credential cannot be left behind by one
test and picked up by the next.
"""

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
    """Run something that would send, and hand back the reason it could not."""
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
    #: The one funnel every provider in this layer sends through, and the backend GET beside it.
    refuses_to_send(lambda: urllib.request.urlopen("http://127.0.0.1:8000/records", timeout=1))


def test_httpx_cannot_send_either():
    """The SDK path is closed too: whatever a client raises, it raised because the socket refused."""
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
    """The guard is not a credential check: this config carries a key and a real endpoint."""
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
    """With a proxy configured, direct or proxied, the socket is refused either way."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    refuses_to_send(lambda: urllib.request.urlopen("https://example.com", timeout=1))


def test_the_suite_holds_no_credential_to_send():
    """The deployment is out of the environment, so nothing here could authenticate if it did send."""
    settings = LLMConfig.from_env()
    assert settings.api_key is None
    with pytest.raises(ProviderNotConfigured):
        GeminiProvider(settings)


def test_reading_an_env_file_really_does_write_through(tmp_path, monkeypatch):
    """Why the session's credential strip cannot be the only boundary, stated as a fact to be undone.

    ``load_dotenv`` puts the file's keys into the live ``os.environ``, and a ``delenv`` recorded
    before the read has nothing to restore for a key that was absent then. A test that points
    ``DOTENV_PATH`` at a deployment file of its own - as the provider tests must - therefore leaves
    that deployment behind unless something takes it back.
    """
    path = tmp_path / ".env"
    path.write_text(f"{ENV_MODEL}=openai/gpt-oss-120b\n{ENV_API_KEY}=sk-not-a-real-key\n", encoding="utf-8")
    monkeypatch.setattr(llm, "DOTENV_PATH", str(path))
    assert LLMConfig.from_env().api_key == "sk-not-a-real-key"
    assert os.environ.get(ENV_API_KEY) == "sk-not-a-real-key"


def test_a_deployment_written_through_by_a_test_did_not_survive_it():
    #: Runs after the one above, which is the point: the leak is contained inside its own test.
    assert ENV_API_KEY not in os.environ
    assert ENV_MODEL not in os.environ
    assert LLMConfig.from_env().api_key is None


def test_a_real_extraction_request_is_still_undeliverable():
    """The whole semantic prompt, built and aimed at the endpoint, stops at the socket."""
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
