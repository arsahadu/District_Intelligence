from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from intelligence.chat import (
    GENERAL_PROMPT_VERSION,
    LOOKUP_MODEL,
    LOOKUP_PROVIDER,
    MAX_QUESTION_CHARS,
    NO_MODEL_NOTE,
    ROUTE_PROMPT_VERSION,
    VALUE_PROMPT_DIGEST,
    VALUE_PROMPT_VERSION,
    ChatReply,
    Reading,
)
from intelligence.chat_service import (
    DEFAULT_API_URL,
    DEFAULT_ORIGINS,
    ENV_API_URL,
    ENV_HOST,
    ENV_ORIGINS,
    ENV_PORT,
    EXIT_LISTEN,
    INCIDENTS_PATH,
    MAX_JSON_BYTES,
    NO_WRITES,
    PATHS,
    SERVICE_VERSION,
    ApiCorpusError,
    ChatRequestHandler,
    ChatService,
    handler_for,
    incidents_from_api,
    listen,
    main,
    notice,
    origins_from,
    parse_args,
    respond,
    setting,
)
from intelligence.contract import Incident
from intelligence.fallback import ENV_CHAIN_FILE, Chain
from intelligence.llm import ENV_API_KEY, LLMRequest, ProviderNotConfigured, RateLimitedError
from intelligence.pipeline import EXIT_OK
from intelligence.tests.test_chat import (
    TA_ASK,
    TOMATO_TA,
    WEATHER_TA,
    background,
    entry,
    forecast,
    general_engine,
    general_route,
    price,
    reworded,
    saved,
    search_engine,
    values_engine,
    wording_engine,
)
from intelligence.tests.test_linking import Answering
from intelligence.tests.test_recurrence import flood

KEY = "gsk-sentinel-value-that-never-appears-in-an-answer"
ORIGIN = "http://localhost:5173"
API = "http://backend.test"
API_URL = f"{API}{INCIDENTS_PATH}"
NEWS = flood("NEWS-A", "செப்டம்பர் 14 அன்று", "2026-09-14")


def deployment(**extra: str) -> dict[str, str]:
    return {
        "INTELLIGENCE_LLM_PROVIDER": "groq",
        "INTELLIGENCE_LLM_MODEL": "gpt-oss-120b",
        ENV_API_KEY: KEY,
        **extra,
    }


def api(*items: Any, status: int = 200) -> Callable[[str, float], tuple[int, bytes]]:
    get_payload = json.dumps(
        [item.model_dump(mode="json") if isinstance(item, Incident) else item for item in items],
        ensure_ascii=False,
    )

    def get(url: str, timeout_seconds: float) -> tuple[int, bytes]:
        get.calls.append(url)  # type: ignore[attr-defined]
        return status, get_payload.encode("utf-8")

    get.calls = []  # type: ignore[attr-defined]
    return get


def down(error: Exception) -> Callable[[str, float], tuple[int, bytes]]:
    def get(url: str, timeout_seconds: float) -> tuple[int, bytes]:
        raise error

    return get


def build(tmp_path: Path, **fields: Any) -> ChatService:
    fields.setdefault("environ", deployment())
    fields.setdefault("origins", (ORIGIN,))
    fields.setdefault("api_url", "")
    return ChatService(**fields)


def call(service: ChatService, verb: str, path: str, **fields: Any):
    headers = fields.pop("headers", None) or {}
    body = fields.pop("body", b"")
    return service.handle(verb, path, headers, body)


def payload(response) -> dict[str, Any]:
    return json.loads(response.body().decode("utf-8"))


def ask(body: str, session: Optional[str] = None) -> bytes:
    return json.dumps(
        {"question": body} if session is None else {"question": body, "session_id": session}
    ).encode()


class Broken:
    reading = Reading()
    corpus: list[Any] = []

    def ask(self, question: str, **fields: Any) -> Any:
        raise RuntimeError("the copilot lost its corpus")


def unusable_chain(directory: Path) -> str:
    path = directory / "chain.json"
    path.write_text(json.dumps([{"provider": "groq", "model": "gpt-oss-120b"}]), encoding="utf-8")
    return str(path)


def test_health_names_the_chain_and_never_the_credential(tmp_path: Path) -> None:
    service = build(tmp_path, engine=values_engine([price(), forecast()]))
    response = call(service, "GET", "/health")
    body = payload(response)

    assert response.status == 200
    assert body["version"] == SERVICE_VERSION
    assert body["endpoints"] == list(PATHS)
    assert "documents" not in body
    assert body["model"]["configured"] is True
    assert body["model"]["chain"] == "groq/gpt-oss-120b"
    assert body["model"]["candidates"][0]["key_source"] == ENV_API_KEY
    assert body["model"]["candidates"][0]["api_key_present"] is True
    assert KEY not in response.body().decode("utf-8")
    assert set(body["model"]["candidates"][0]) == {
        "provider",
        "model",
        "key_source",
        "api_key_present",
    }
    assert body["writes"] == NO_WRITES
    assert body["reading"]["readings"] == 2


def test_health_says_which_questions_the_records_answer_themselves(tmp_path: Path) -> None:
    body = payload(call(build(tmp_path), "GET", "/health"))
    assert len(body["answers_from_records"]) == 2
    assert any("forecast" in line for line in body["answers_from_records"])
    assert any("price" in line for line in body["answers_from_records"])
    assert not any("chat" in line for line in body["answers_from_records"])
    assert body["value_prompt_version"] == VALUE_PROMPT_VERSION
    assert body["route_prompt_version"] == ROUTE_PROMPT_VERSION
    assert body["general_prompt_version"] == GENERAL_PROMPT_VERSION


def test_health_reports_a_chain_that_cannot_be_asked(tmp_path: Path) -> None:
    service = ChatService(
        environ=deployment(**{ENV_API_KEY: ""}),
        engine=values_engine([]),
        chain_path=unusable_chain(tmp_path),
        api_url="",
    )
    body = payload(call(service, "GET", "/health"))
    assert body["model"]["configured"] is False
    assert body["model"]["chain"] == ""
    assert ENV_API_KEY in body["model"]["detail"]
    assert KEY not in json.dumps(body)


def rate_limited_once() -> Answering:
    asked: list[int] = []

    def answer(request: LLMRequest) -> dict[str, Any]:
        asked.append(1)
        if len(asked) == 1:
            raise RateLimitedError("the endpoint asked for a pause")
        if request.meta["prompt_version"] == ROUTE_PROMPT_VERSION:
            return general_route()
        return background()

    return Answering(answer)


def test_a_chain_a_rate_limit_closed_is_asked_again_by_the_next_question(tmp_path: Path) -> None:
    judge = rate_limited_once()
    built: list[int] = []

    def factory() -> Chain:
        built.append(1)
        return Chain.single(judge)

    service = build(
        tmp_path,
        state_dir=saved(tmp_path / "read", entry(price())),
        chain_factory=factory,
    )
    first = payload(
        call(service, "POST", "/chat", body=ask("how many districts are there in tamil nadu?", "s-1"))
    )
    assert first["state"] == "not_answerable"
    assert first["path"] == "record_search"
    assert first["model_calls"] == 0
    assert any(
        "the routing question asked before the records were searched was not answered" in line
        for line in first["limitations"]
    )
    assert any(
        "the records were searched as though it needed them" in line for line in first["limitations"]
    )
    assert len(built) == 1
    assert len(judge.asked) == 1

    health = call(service, "GET", "/health")
    assert "closed" in payload(health)["model"]["chain_state"]
    assert KEY not in health.body().decode("utf-8")

    again = payload(
        call(service, "POST", "/chat", body=ask("how many districts are there in tamil nadu?", "s-2"))
    )
    assert len(built) == 2
    assert again["path"] == "general" and again["state"] == "qualified"
    assert again["model_calls"] == 2
    assert again["citations"] == []
    assert not any("every candidate in the chain is already closed" in line for line in again["limitations"])


def test_health_says_the_chain_it_holds_and_never_a_credential(tmp_path: Path) -> None:
    service = build(
        tmp_path,
        state_dir=saved(tmp_path / "read", entry(price())),
        chain_factory=lambda: Chain.single(Answering(general_route)),
    )
    health = call(service, "GET", "/health")
    body = payload(health)
    assert "open" in body["model"]["chain_state"]
    assert KEY not in health.body().decode("utf-8")


def test_health_says_the_dashboard_was_never_read_when_it_was_turned_off(tmp_path: Path) -> None:
    body = payload(call(build(tmp_path, engine=values_engine([])), "GET", "/health"))
    assert body["dashboard_read"] == {"url": "", "state": "not_read", "detail": ""}


def test_chat_answers_a_tamil_price_question_from_the_records_when_no_model_is_left(tmp_path: Path) -> None:
    service = build(tmp_path, engine=values_engine([price()]))
    response = call(service, "POST", "/chat", body=ask(TOMATO_TA))
    reply = ChatReply.model_validate(payload(response))

    assert response.status == 200
    assert reply.state.value == "grounded"
    assert reply.path.value == "record_values"
    assert reply.model_calls == 0
    assert reply.provider == LOOKUP_PROVIDER
    assert reply.model == LOOKUP_MODEL
    assert reply.script == "tamil"
    assert (reply.headline or "").startswith("Tomato at Anna nagar")
    assert reply.limitations[0] == NO_MODEL_NOTE
    assert reply.citations
    assert reply.citations[0].field.startswith("data.")


def test_chat_words_a_price_answer_from_the_model_and_still_names_the_records(tmp_path: Path) -> None:
    engine, _ = wording_engine([price()], reworded)
    body = payload(call(build(tmp_path, engine=engine), "POST", "/chat", body=ask(TOMATO_TA)))

    assert body["path"] == "record_values" and body["state"] == "grounded"
    assert body["model_calls"] == 1
    assert body["prompt_version"] == VALUE_PROMPT_VERSION
    assert body["prompt_digest"] == VALUE_PROMPT_DIGEST
    assert body["provider"] == "scripted" and body["model"] == "gpt-oss-120b"
    assert body["headline"].startswith("As these records put it: ")
    assert body["citations"] and body["record_ids"]
    assert any("wording a model put together" in line for line in body["limitations"])
    assert KEY not in json.dumps(body, ensure_ascii=False)


def test_chat_answers_a_forecast_question_from_the_records_own_values(tmp_path: Path) -> None:
    service = build(tmp_path, engine=values_engine([forecast()]))
    body = payload(call(service, "POST", "/chat", body=ask(WEATHER_TA)))
    assert body["state"] == "grounded"
    assert body["model_calls"] == 0
    assert body["record_kinds"] == ["forecast"]
    assert body["sources"] >= 1


def test_chat_keeps_a_cited_answer_in_the_shape_the_frontend_reads(tmp_path: Path) -> None:
    engine, judge = search_engine([NEWS])
    service = build(tmp_path, engine=engine)
    body = payload(call(service, "POST", "/chat", body=ask(TA_ASK)))
    reply = ChatReply.model_validate(body)

    assert judge.asked
    assert reply.model_calls >= 1
    assert reply.provider and reply.model
    assert reply.statements[0].kind.value in {"source_fact", "calculated", "explanation"}
    assert set(body["statements"][0]) >= {"text", "kind", "review", "confidence"}
    assert set(body["citations"][0]) >= {"record_id", "field", "quote", "evidence_id", "source_id"}
    assert set(body) >= {"limitations", "standing"}
    assert not any(line.startswith("answered from ") for line in body["limitations"])
    assert any(line.startswith("answered from ") for line in body["standing"])
    assert reply.citations
    assert KEY not in json.dumps(body)


def test_chat_gives_the_session_id_back(tmp_path: Path) -> None:
    service = build(tmp_path, engine=values_engine([price()]))
    body = payload(call(service, "POST", "/chat", body=ask(TOMATO_TA, "s-7")))
    assert body["session_id"] == "s-7"


@pytest.mark.parametrize(
    "body,kind",
    [
        (b"not json at all", "not_json"),
        (b'["a question"]', "not_an_object"),
        (b'{"question": "x", "district": "Madurai"}', "unknown_field"),
        (b'{"question": "x", "file_name": "notice.pdf"}', "unknown_field"),
        (b'{"question": 41}', "bad_question"),
        (b'{"question": "x", "session_id": 41}', "bad_session"),
        (b'{"question": "x", "session_id": "' + b"t" * 65 + b'"}', "bad_session"),
    ],
)
def test_chat_refuses_a_request_that_is_not_one_question(body: bytes, kind: str, tmp_path: Path) -> None:
    response = call(build(tmp_path, engine=values_engine([price()])), "POST", "/chat", body=body)
    assert response.status == 400
    assert payload(response)["error"] == kind


def test_chat_refuses_a_body_bigger_than_a_question(tmp_path: Path) -> None:
    body = json.dumps({"question": "வ" * MAX_JSON_BYTES}).encode("utf-8")
    response = call(build(tmp_path, engine=values_engine([])), "POST", "/chat", body=body)
    assert response.status == 413
    assert str(MAX_JSON_BYTES) in payload(response)["detail"]


def test_chat_passes_an_over_long_question_to_the_engines_own_limit(tmp_path: Path) -> None:
    service = build(tmp_path, engine=values_engine([price()]))
    response = call(service, "POST", "/chat", body=ask("x" * (MAX_QUESTION_CHARS + 1)))
    body = payload(response)
    assert response.status == 200
    assert body["state"] == "not_answerable"
    assert body["model_calls"] == 0


@pytest.mark.parametrize("question", ["hi", "வணக்கம்", "what can you do", "thanks"])
def test_chat_asks_the_model_for_a_question_no_record_answers_and_marks_it_background(
    tmp_path: Path, question: str
) -> None:
    engine, _ = general_engine()
    body = payload(call(build(tmp_path, engine=engine), "POST", "/chat", body=ask(question)))
    assert body["path"] == "general"
    assert body["state"] == "qualified"
    assert body["model_calls"] == 2
    assert body["citations"] == [] and body["record_ids"] == [] and body["records"] == 0
    assert body["provider"] == "scripted" and body["model"] == "gpt-oss-120b"
    assert KEY not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize(
    "verb,path,method",
    [("POST", "/health", "GET"), ("GET", "/chat", "POST")],
)
def test_each_path_names_the_verb_it_takes(verb: str, path: str, method: str, tmp_path: Path) -> None:
    response = call(build(tmp_path), verb, path)
    assert response.status == 405
    assert response.headers["Allow"] == method
    assert payload(response)["error"] == "wrong_method"


@pytest.mark.parametrize("where", ["/answers", "/documents", "/upload", "/"])
def test_a_path_that_is_not_answered_for_lists_the_two_that_are(tmp_path: Path, where: str) -> None:
    response = call(build(tmp_path), "POST" if where == "/documents" else "GET", where)
    assert response.status == 404
    assert payload(response)["error"] == "unknown_path"
    assert all(item in payload(response)["detail"] for item in PATHS)
    assert len(PATHS) == 2


def test_an_allowed_origin_gets_cors_on_every_answer(tmp_path: Path) -> None:
    response = call(build(tmp_path), "GET", "/health", headers={"Origin": ORIGIN})
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert response.headers["Access-Control-Allow-Headers"] == "Accept, Content-Type"
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_a_foreign_origin_gets_no_cors_and_a_refused_preflight(tmp_path: Path) -> None:
    service = build(tmp_path)
    response = call(service, "GET", "/chat", headers={"Origin": "http://evil.test"}, body=ask(TOMATO_TA))
    assert "Access-Control-Allow-Origin" not in response.headers
    preflight = call(service, "OPTIONS", "/chat", headers={"Origin": "http://evil.test"})
    assert preflight.status == 403
    assert payload(preflight)["error"] == "origin_not_allowed"


def test_an_allowed_preflight_carries_no_body(tmp_path: Path) -> None:
    response = call(
        build(tmp_path),
        "OPTIONS",
        "/chat",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert response.status == 204
    assert payload(response) == {}
    assert response.headers["Allow"] == "GET, POST, OPTIONS"


def test_the_dashboards_own_incidents_join_the_corpus_a_question_is_answered_from() -> None:
    read = api(price(), NEWS)
    service = ChatService(environ=deployment(), api_url=API, api_transport=read)
    health = payload(call(service, "GET", "/health"))

    assert read.calls == [API_URL]
    assert health["dashboard_read"] == {
        "url": API_URL,
        "state": "read",
        "held": 2,
        "added": 2,
        "rejected": 0,
        "detail": "",
    }
    assert health["reading"]["readings"] == 2
    answer = payload(call(service, "POST", "/chat", body=ask(TOMATO_TA)))
    assert answer["state"] == "grounded"
    assert answer["model_calls"] == 0
    assert answer["records"] == 1


def test_reading_the_dashboard_twice_adds_the_same_incidents_once() -> None:
    read = api(price())
    service = ChatService(environ=deployment(), api_url=API, api_transport=read)
    assert service.engine.reading.records == 1
    assert service.sync_corpus() == 0
    assert read.calls == [API_URL, API_URL]
    assert payload(call(service, "GET", "/health"))["reading"]["readings"] == 1


def test_an_engine_handed_to_the_service_still_reads_the_dashboard() -> None:
    read = api(price())
    service = ChatService(
        environ=deployment(), api_url=API, api_transport=read, engine=values_engine([forecast()])
    )
    assert read.calls == [API_URL]
    assert len(service.engine.reading.incidents) == 2
    assert payload(call(service, "GET", "/health"))["dashboard_read"]["added"] == 1


def test_a_dashboard_that_does_not_answer_leaves_the_stored_readings_answering(tmp_path: Path) -> None:
    directory = saved(tmp_path / "read", entry(price()))
    service = ChatService(
        environ=deployment(),
        state_dir=directory,
        api_url=API,
        api_transport=down(OSError("connection refused")),
    )
    health = payload(call(service, "GET", "/health"))

    assert health["dashboard_read"]["state"] == "unreachable"
    assert "connection refused" in health["dashboard_read"]["detail"]
    assert any("did not answer" in line for line in health["notes"])
    assert payload(call(service, "POST", "/chat", body=ask(TOMATO_TA)))["state"] == "grounded"


def test_an_entry_the_incident_contract_refuses_is_named_and_the_others_still_answer() -> None:
    broken = price(record_id="BAD-1").model_dump(mode="json") | {"event_time": "not a time"}
    read = api(price(), broken)
    service = ChatService(environ=deployment(), api_url=API, api_transport=read)
    health = payload(call(service, "GET", "/health"))

    assert health["dashboard_read"]["held"] == 1
    assert health["dashboard_read"]["rejected"] == 1
    assert "BAD-1" in health["dashboard_read"]["detail"]
    assert any("not usable as contract" in line for line in health["reading"]["caveats"])
    assert payload(call(service, "POST", "/chat", body=ask(TOMATO_TA)))["records"] == 1


def test_a_body_that_is_not_the_incident_list_is_refused_with_what_arrived() -> None:
    with pytest.raises(ApiCorpusError) as not_list:
        incidents_from_api(API_URL, transport=lambda url, timeout: (200, b'{"incidents": []}'))
    assert "where the incident list should be" in str(not_list.value)

    with pytest.raises(ApiCorpusError) as not_json:
        incidents_from_api(API_URL, transport=lambda url, timeout: (200, b"<html>nope</html>"))
    assert "not JSON" in str(not_json.value)

    with pytest.raises(ApiCorpusError) as error_status:
        incidents_from_api(API_URL, transport=lambda url, timeout: (503, b"backend down"))
    assert "HTTP 503" in str(error_status.value)

    with pytest.raises(ApiCorpusError) as missing:
        incidents_from_api(API_URL, transport=down(OSError("connection refused")))
    assert "could not be reached" in str(missing.value)


def test_a_chain_file_the_deployment_names_is_read_and_its_gap_reported(tmp_path: Path) -> None:
    service = ChatService(
        environ=deployment(**{ENV_CHAIN_FILE: unusable_chain(tmp_path), ENV_API_KEY: ""}),
        engine=values_engine([]),
        api_url="",
    )
    with pytest.raises(ProviderNotConfigured) as raised:
        service.chain()
    assert ENV_CHAIN_FILE in str(raised.value)
    assert ENV_API_KEY in str(raised.value)


def test_the_readings_beside_a_state_dir_answer_through_the_service(tmp_path: Path) -> None:
    directory = saved(tmp_path / "read", entry(price()), entry(NEWS))
    service = ChatService(environ=deployment(), state_dir=directory, api_url="")
    health = payload(call(service, "GET", "/health"))
    assert health["reading"]["records"] == 2
    assert health["reading"]["waiting_for_review"] == 0
    assert health["reading"]["directory"].endswith("read")

    answer = payload(call(service, "POST", "/chat", body=ask(TOMATO_TA)))
    assert answer["state"] == "grounded"
    assert answer["model_calls"] == 0
    assert answer["records"] == 1


def test_answers_that_cannot_be_read_are_noted_rather_than_silently_dropped(tmp_path: Path) -> None:
    directory = saved(tmp_path / "read", entry(price()))
    (directory / "answers.jsonl").write_text("{ not an answer\n", encoding="utf-8")
    service = ChatService(environ=deployment(), state_dir=directory, api_url="")
    health = payload(call(service, "GET", "/health"))
    assert any("every question is read again" in line for line in health["notes"])
    assert payload(call(service, "POST", "/chat", body=ask(TOMATO_TA)))["state"] == "grounded"


def test_a_request_that_breaks_the_service_is_reported_not_raised(tmp_path: Path) -> None:
    service = build(tmp_path, engine=Broken())  # type: ignore[arg-type]
    response = respond(service, "POST", "/chat", {}, ask(TOMATO_TA))
    assert response.status == 500
    body = payload(response)
    assert body["error"] == "service_failure"
    assert "RuntimeError: the copilot lost its corpus" in body["detail"]
    assert "Traceback" not in response.body().decode("utf-8")


def test_a_failed_request_is_written_to_the_console_that_logged_the_access_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = ChatRequestHandler.__new__(ChatRequestHandler)
    handler.service = build(tmp_path, engine=Broken())  # type: ignore[arg-type]
    handler.path = "/chat"
    handler.command = "POST"
    handler.headers = {}
    handler.client_address = ("10.0.0.1", 1)
    sent: list[Any] = []
    handler._send = sent.append  # type: ignore[method-assign]
    handler._run("POST", ask(TOMATO_TA))
    assert sent[0].status == 500
    logged = capsys.readouterr().err
    assert "POST /chat failed" in logged
    assert "RuntimeError: the copilot lost its corpus" in logged
    assert "Traceback" not in logged


def test_the_service_serves_the_handler_it_is_bound_to(tmp_path: Path) -> None:
    service = build(tmp_path)
    seen: list[tuple[Any, Any]] = []

    class Factory:
        def __init__(self, address: Any, handler: Any) -> None:
            seen.append((address, handler))

    listen("127.0.0.1", 8010, service, server_factory=Factory)
    address, handler = seen[0]
    assert address == ("127.0.0.1", 8010)
    assert issubclass(handler, ChatRequestHandler)
    assert handler.service is service
    assert handler.protocol_version == "HTTP/1.1"
    assert handler_for(service).service is service


class _Server:
    def __init__(self) -> None:
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_main_listens_stops_and_tells_the_operator_what_it_can_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = saved(tmp_path / "read", entry(price()))
    opened: list[tuple[str, int, ChatService, Any]] = []

    def factory(host: str, port: int, chat: ChatService) -> Any:
        server = _Server()
        opened.append((host, port, chat, server))
        return server

    code = main(
        ["--state-dir", str(directory), "--api-url", ""], environ=deployment(), server_factory=factory
    )
    host, port, chat, server = opened[0]
    assert code == EXIT_OK
    assert (host, port) == ("127.0.0.1", 8010)
    assert chat.state_dir == directory
    assert chat.api_url is None
    assert server.closed is True

    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert f"Chat service: http://{host}:{port}" in text
    assert "with no model call" in text
    assert "Routing:" in text
    assert "Dashboard read: off" in text
    assert KEY not in text


def test_main_reports_a_port_that_is_already_taken(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def factory(host: str, port: int, chat: ChatService) -> Any:
        raise OSError("only one service may hold 8010")

    code = main(["--port", "8010"], environ=deployment(), service=build(tmp_path), server_factory=factory)
    assert code == EXIT_LISTEN
    err = capsys.readouterr().err
    assert "8010" in err
    assert "--port" in err
    assert ENV_PORT in err


def test_notice_reports_the_dashboard_it_read_and_never_a_credential(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = ChatService(environ=deployment(), api_url=API, api_transport=api(price()))
    notice(service, "127.0.0.1", 8010)
    err = capsys.readouterr().err
    assert KEY not in err
    assert "groq/gpt-oss-120b" in err
    assert f"Dashboard read: {API_URL} gave 1 incident(s), 1 new to the corpus" in err
    assert NO_WRITES in err
    assert "Readings held: 1 in 1 record(s)" in err


def test_notice_reports_a_dashboard_that_did_not_answer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = ChatService(
        environ=deployment(), api_url=API, api_transport=down(OSError("connection refused"))
    )
    notice(service, "127.0.0.1", 8010)
    err = capsys.readouterr().err
    assert f"Dashboard read: {API_URL} could not be reached (connection refused)" in err
    assert "could not be reached" in err
    assert err.count(API_URL) >= 2
    assert any(line.startswith("Note:") for line in err.splitlines())


def test_parse_args_takes_the_environment_and_the_command_line_over_it() -> None:
    args = parse_args(
        ["--port", "9000", "--api-url", "http://collector.test:8000"],
        environ={
            ENV_HOST: "0.0.0.0",
            ENV_PORT: "8011",
            ENV_ORIGINS: "http://localhost:5173, http://collector.test",
            ENV_API_URL: "http://env.test:8000",
        },
    )
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.api_url == "http://collector.test:8000"
    assert origins_from(args.origins) == ("http://localhost:5173", "http://collector.test")


def test_parse_args_defaults_and_rejects_an_impossible_setting() -> None:
    args = parse_args([], environ={})
    assert args.port == 8010
    assert args.host == "127.0.0.1"
    assert args.api_url == DEFAULT_API_URL
    assert args.origins == ",".join(DEFAULT_ORIGINS)
    assert setting({}, ENV_PORT, "8010") == "8010"
    assert origins_from("  ,  ") == DEFAULT_ORIGINS
    with pytest.raises(SystemExit):
        parse_args(["--port", "0"], environ={})


def test_the_dashboard_on_either_dev_port_is_allowed_to_ask() -> None:
    assert DEFAULT_ORIGINS == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    )
    service = ChatService(
        environ=deployment(), engine=values_engine([]), api_url="", origins=DEFAULT_ORIGINS
    )
    assert service.cors("http://localhost:5174")["Access-Control-Allow-Origin"] == (
        "http://localhost:5174"
    )
    assert service.cors("http://collector.test") == {}


def test_the_api_url_a_service_is_given_is_the_one_it_reads() -> None:
    assert ChatService(environ=deployment(), api_url="", engine=values_engine([])).api_url is None
    default = ChatService(environ=deployment(), engine=values_engine([]), api_transport=api())
    assert default.api_url == DEFAULT_API_URL
    trailing = ChatService(
        environ=deployment(), api_url=f"{API}/", engine=values_engine([]), api_transport=api()
    )
    assert trailing.api_url == API
    assert trailing.api_read["url"] == API_URL


def test_running_the_module_as_a_process_reaches_main(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "intelligence.chat_service", "--state-dir", str(tmp_path), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0
    assert "usage: python -m intelligence.chat_service" in completed.stdout
    for name in ("--state-dir", "--port", "--origins", "--api-url", "--llm-chain"):
        assert name in completed.stdout
