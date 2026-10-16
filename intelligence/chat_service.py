from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from pydantic import ValidationError

from intelligence.chat import (
    CHAT_PROMPT_VERSION,
    DEFAULT_SESSION,
    GENERAL_PROMPT_VERSION,
    ROUTE_PROMPT_VERSION,
    VALUE_PROMPT_VERSION,
    ChatEngine,
    ChatLimits,
    Reading,
    read_state_dir,
)
from intelligence.checkpoint import CheckpointError
from intelligence.contract import SCHEMA_VERSION, Incident
from intelligence.copilot import AnswerStore
from intelligence.fallback import ENV_CHAIN_FILE, Chain, chain_configs
from intelligence.llm import LLMError, ProviderNotConfigured
from intelligence.pipeline import EXIT_OK, use_utf8_stdout

SERVICE_NAME = "intelligence.chat_service"
SERVICE_VERSION = "chat-service-12"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
ENV_HOST = "INTELLIGENCE_CHAT_HOST"
ENV_PORT = "INTELLIGENCE_CHAT_PORT"
ENV_ORIGINS = "INTELLIGENCE_CHAT_ORIGINS"
ENV_API_URL = "INTELLIGENCE_CHAT_API_URL"
DEFAULT_API_URL = "http://127.0.0.1:8000"
INCIDENTS_PATH = "/incidents"
API_TIMEOUT_SECONDS = 15.0
# Vite moves to 5174 whenever 5173 is already taken, and the browser sends whichever port the page is on.
DEFAULT_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
)
MAX_JSON_BYTES = 64 * 1024
PATHS = ("/health", "/chat")
CHAT_FIELDS = ("question", "session_id")
ANSWERS_FROM_RECORDS = (
    "the day's weather forecast values, read from the forecast records stored here",
    "market prices, read from the agriculture records stored here",
)
NO_WRITES = (
    "this service answers questions from the readings it holds; it writes nothing to the dashboard, which "
    "stays behind the pipeline's --post-incidents"
)
EXIT_LISTEN = 8


@dataclass(frozen=True)
class Response:
    status: int
    payload: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    def body(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def refused(status: int, kind: str, detail: str, **extra: Any) -> Response:
    return Response(status, {"error": kind, "detail": detail, **extra})


class ApiCorpusError(RuntimeError):
    pass


def incidents_from_api(
    url: str,
    *,
    transport: Optional[Callable[[str, float], tuple[int, bytes]]] = None,
    timeout_seconds: float = API_TIMEOUT_SECONDS,
) -> tuple[list[Incident], list[str]]:
    get = transport or _http_get
    try:
        status, body = get(url, timeout_seconds)
    except (OSError, ValueError) as error:
        raise ApiCorpusError(f"{url} could not be reached ({getattr(error, 'reason', None) or error})") from error
    if status // 100 != 2:
        raise ApiCorpusError(f"{url} answered HTTP {status}: {body[:200]!r}")
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as error:
        raise ApiCorpusError(f"{url} answered with a body that is not JSON: {error}") from error
    if not isinstance(payload, list):
        raise ApiCorpusError(f"{url} returned {type(payload).__name__} where the incident list should be")
    incidents: list[Incident] = []
    problems: list[str] = []
    for index, entry in enumerate(payload):
        try:
            incidents.append(Incident.model_validate(entry))
        except ValidationError as error:
            issues = error.errors()
            label = entry.get("incident_id") if isinstance(entry, dict) else type(entry).__name__
            problems.append(f"{label or index}: {issues[0]['msg'] if issues else error}")
    return incidents, problems


def _http_get(url: str, timeout_seconds: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.status, response.read()


class ChatService:
    def __init__(
        self,
        *,
        environ: Optional[Mapping[str, str]] = None,
        state_dir: Optional[Any] = None,
        engine: Optional[ChatEngine] = None,
        limits: Optional[ChatLimits] = None,
        origins: Optional[Sequence[str]] = None,
        chain_path: Optional[str] = None,
        chain_factory: Optional[Callable[[], Chain]] = None,
        api_url: Optional[str] = None,
        api_transport: Optional[Callable[[str, float], tuple[int, bytes]]] = None,
    ) -> None:
        source = None if environ is None else dict(environ)
        self.environ = source
        self.env_view = os.environ if source is None else source
        self.state_dir = None if state_dir is None else Path(str(state_dir))
        self.origins = DEFAULT_ORIGINS if origins is None else tuple(origins)
        self.chain_path = (chain_path or "").strip() or self.env_view.get(ENV_CHAIN_FILE, "")
        self.chain_factory = chain_factory
        self.api_url = (
            DEFAULT_API_URL if api_url is None else str(api_url)
        ).strip().rstrip("/") or None
        self.api_transport = api_transport
        self.notes: list[str] = []
        self.api_read: dict[str, Any] = {
            "url": f"{self.api_url}{INCIDENTS_PATH}" if self.api_url else "",
            "state": "not_read",
            "detail": "",
        }
        self._chain: Optional[Chain] = None
        self._lock = threading.RLock()

        if engine is not None:
            self.engine = engine
        else:
            reading = read_state_dir(self.state_dir) if self.state_dir is not None else Reading()
            store = None
            if self.state_dir is not None:
                try:
                    store = AnswerStore(self.state_dir).load()
                except CheckpointError as error:
                    self.notes.append(
                        f"saved answers beside {self.state_dir} could not be reused: {error}, so every "
                        "question is read again"
                    )
            self.engine = ChatEngine(reading, chain_factory=self.chain, limits=limits, store=store)
        if self.api_url is not None:
            self.sync_corpus()

    def sync_corpus(self) -> int:
        url = f"{self.api_url}{INCIDENTS_PATH}"
        try:
            incidents, problems = incidents_from_api(url, transport=self.api_transport)
        except ApiCorpusError as error:
            self.api_read = {"url": url, "state": "unreachable", "detail": str(error)}
            self.notes.append(
                f"{url} did not answer, so the chat is answering only from the readings saved beside "
                f"{self.state_dir or 'no state directory'}"
            )
            return 0
        added = self.engine.add(incidents)
        self.api_read = {
            "url": url,
            "state": "read",
            "held": len(incidents),
            "added": added,
            "rejected": len(problems),
            "detail": "; ".join(problems[:3]),
        }
        if problems:
            self.engine.reading.caveats.append(
                f"{len(problems)} incident(s) at {url} were not usable as contract {SCHEMA_VERSION} input: "
                + "; ".join(problems[:3])
            )
        return added

    def chain(self) -> Chain:
        with self._lock:
            if self._chain is None:
                if self.chain_factory is not None:
                    self._chain = self.chain_factory()
                else:
                    candidates, problems = chain_configs(
                        None, environ=self.environ, chain_path=self.chain_path or None
                    )
                    self._chain = Chain(candidates, problems=problems)
            return self._chain

    def reopen_chain(self) -> None:
        """A service outlives one question, so a chain that ran out of candidates is built again for the next."""
        with self._lock:
            if self._chain is not None and not self._chain.available():
                self._chain = None

    def model_status(self) -> dict[str, Any]:
        try:
            chain = self.chain()
        except ProviderNotConfigured as error:
            return {
                "configured": False,
                "chain": "",
                "candidates": [],
                "chain_state": "",
                "detail": str(error),
            }
        rows: list[dict[str, Any]] = []
        detail: Optional[str] = None
        for candidate in chain.candidates:
            row: dict[str, Any] = {
                "provider": candidate.config.provider,
                "model": candidate.config.model,
                "key_source": candidate.key_source,
                "api_key_present": False,
            }
            try:
                provider = candidate.build()
            except (ProviderNotConfigured, LLMError) as error:
                row["detail"] = str(error)
                detail = str(error)
            else:
                config = getattr(provider, "config", candidate.config)
                row["api_key_present"] = bool(getattr(config, "api_key", ""))
            rows.append(row)
        return {
            "configured": any(row["api_key_present"] for row in rows),
            "chain": chain.describe(),
            "chain_state": chain.scopes_note(),
            "candidates": rows,
            "problems": list(chain.problems),
            "detail": detail or "",
        }

    def health(self) -> Response:
        reading = self.engine.reading
        return Response(
            200,
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "schema_version": SCHEMA_VERSION,
                "chat_prompt_version": CHAT_PROMPT_VERSION,
                "general_prompt_version": GENERAL_PROMPT_VERSION,
                "route_prompt_version": ROUTE_PROMPT_VERSION,
                "value_prompt_version": VALUE_PROMPT_VERSION,
                "endpoints": list(PATHS),
                "answers_from_records": list(ANSWERS_FROM_RECORDS),
                "model": self.model_status(),
                "reading": {
                    "directory": reading.directory,
                    "readings": len(reading.incidents),
                    "records": reading.records,
                    "sources": reading.sources,
                    "waiting_for_review": reading.waiting,
                    "links": len(reading.links),
                    "recurring_issues": len(reading.families),
                    "measured_trends": len(reading.trends),
                    "caveats": list(reading.caveats),
                    "unreadable": list(reading.unreadable),
                },
                "limits": asdict(self.engine.limits),
                "dashboard_read": dict(self.api_read),
                "notes": list(self.notes),
                "origins": list(self.origins),
                "writes": NO_WRITES,
            },
        )

    def cors(self, origin: str) -> dict[str, str]:
        if origin not in self.origins:
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Accept, Content-Type",
            "Access-Control-Max-Age": "600",
        }

    def chat(self, body: bytes) -> Response:
        if len(body) > MAX_JSON_BYTES:
            return refused(
                413,
                "too_large",
                f"the request is {len(body)} bytes and a question takes at most {MAX_JSON_BYTES}",
            )
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except UnicodeDecodeError:
            return refused(400, "not_utf8", "the request body is not utf-8 text")
        except json.JSONDecodeError as error:
            return refused(400, "not_json", f"the request body is not one JSON object: {error}")
        if not isinstance(payload, dict):
            return refused(400, "not_an_object", "a question is one JSON object with a question field")
        unknown = sorted(set(payload) - set(CHAT_FIELDS))
        if unknown:
            return refused(
                400,
                "unknown_field",
                f"{', '.join(unknown)} is not a field this endpoint takes; it takes "
                f"{', '.join(CHAT_FIELDS)}",
            )
        question = payload.get("question", "")
        if not isinstance(question, str):
            return refused(400, "bad_question", "the question must be text")
        session = payload.get("session_id") or DEFAULT_SESSION
        if not isinstance(session, str) or len(session) > 64:
            return refused(400, "bad_session", "the session id must be text of at most 64 characters")
        with self._lock:
            self.reopen_chain()
            reply = self.engine.ask(question, session_id=session)
        return Response(200, reply.as_dict())

    def handle(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes = b""
    ) -> Response:
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        where = (path or "/").split("?", 1)[0]
        verb = (method or "").upper()
        cors = self.cors(lowered.get("origin", ""))

        if verb == "OPTIONS":
            allow = {"Allow": "GET, POST, OPTIONS"}
            if cors:
                return Response(204, {}, {**cors, **allow})
            return Response(
                403,
                {
                    "error": "origin_not_allowed",
                    "detail": f"{lowered.get('origin') or 'no origin'} is not an origin this service answers to",
                },
                allow,
            )
        if where == "/health":
            response = self.health() if verb == "GET" else _wrong_method("GET")
        elif where == "/chat":
            response = self.chat(body) if verb == "POST" else _wrong_method("POST")
        else:
            response = refused(404, "unknown_path", f"{where} is not one of {', '.join(PATHS)}")
        return Response(response.status, response.payload, {**response.headers, **cors})


def _wrong_method(method: str) -> Response:
    return Response(
        405,
        {"error": "wrong_method", "detail": f"this path takes {method}"},
        {"Allow": method},
    )


def respond(
    service: ChatService, verb: str, path: str, headers: Mapping[str, str], body: bytes = b""
) -> Response:
    try:
        return service.handle(verb, path, headers, body)
    except Exception as error:
        # A request must not take the service down: the kind and message are reported, never a traceback.
        return refused(500, "service_failure", f"{type(error).__name__}: {error}")


class ChatRequestHandler(BaseHTTPRequestHandler):
    service: ChatService
    server_version = SERVICE_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _headers(self) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in self.headers.items()}

    def _send(self, response: Response) -> None:
        body = b"" if response.status == 204 or self.command == "HEAD" else response.body()
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in response.headers.items():
            if key.lower() != "content-length":
                self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _run(self, verb: str, body: bytes = b"") -> None:
        response = respond(self.service, verb, self.path, self._headers(), body)
        if response.status >= 500:
            print(
                f"{self.address_string()} {verb} {self.path} failed: {response.body().decode('utf-8')}",
                file=sys.stderr,
            )
        self._send(response)

    def do_GET(self) -> None:
        self._run("GET")

    def do_HEAD(self) -> None:
        self._run("GET")

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(refused(400, "bad_content_length", "Content-Length is not a number"))
            return
        if length > MAX_JSON_BYTES:
            self.close_connection = True
            self._send(
                refused(
                    413,
                    "too_large",
                    f"the request is {length} bytes and this service reads at most {MAX_JSON_BYTES}",
                )
            )
            return
        self._run("POST", self.rfile.read(length) if length > 0 else b"")

    def do_OPTIONS(self) -> None:
        self._run("OPTIONS")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} {format % args}", file=sys.stderr)


def handler_for(service: ChatService) -> type[ChatRequestHandler]:
    return type("BoundChatRequestHandler", (ChatRequestHandler,), {"service": service})


def listen(
    host: str, port: int, service: ChatService, *, server_factory: Any = ThreadingHTTPServer
) -> Any:
    return server_factory((host, port), handler_for(service))


def setting(environ: Mapping[str, str], name: str, default: str) -> str:
    return (environ.get(name) or "").strip() or default


def origins_from(raw: str) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or DEFAULT_ORIGINS


def notice(service: ChatService, host: str, port: int) -> None:
    reading = service.engine.reading
    model = service.model_status()
    lines = [
        f"Chat service: http://{host}:{port} ({SERVICE_VERSION}, contract {SCHEMA_VERSION})",
        f"Answers from: {reading.directory or 'no readings yet: start it with --state-dir'}",
        (
            f"Readings held: {len(reading.incidents)} in {reading.records} record(s) filed by "
            f"{reading.sources} publisher(s), {reading.waiting} waiting for review"
        ),
        (
            f"Model: {model['chain']}"
            if model["configured"]
            else f"Model: none configured, so questions about what happened are refused. {model['detail']}"
        ),
        "Field values: forecast and price questions answer with no model call",
        "Routing: a model call decides whether a question needs the records or is answered as background",
        f"Origins allowed to call: {', '.join(service.origins)}",
        _dashboard_line(service),
        f"Writes: {NO_WRITES}",
        *(f"Note: {item}" for item in service.notes),
        *(f"Reading caveat: {item}" for item in reading.caveats),
    ]
    print("\n".join(lines), file=sys.stderr)


def _dashboard_line(service: ChatService) -> str:
    read = service.api_read
    if read["state"] == "read":
        return (
            f"Dashboard read: {read['url']} gave {read['held']} incident(s), {read['added']} new to the "
            f"corpus, {read['rejected']} not usable as contract {SCHEMA_VERSION} input"
            + (f" ({read['detail']})" if read["detail"] else "")
        )
    if read["state"] == "unreachable":
        return f"Dashboard read: {read['detail']}, answering from stored readings only"
    return f"Dashboard read: off, set --api-url or {ENV_API_URL} to read the dashboard's own incidents"


def parse_args(argv: Optional[Sequence[str]] = None, environ: Optional[Mapping[str, str]] = None) -> argparse.Namespace:
    source = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        prog="python -m intelligence.chat_service",
        description="Answer collector questions over the readings stored here and the dashboard's own incidents.",
    )
    parser.add_argument("--host", default=setting(source, ENV_HOST, DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(setting(source, ENV_PORT, str(DEFAULT_PORT))))
    parser.add_argument("--state-dir", dest="state_dir", default=None)
    parser.add_argument(
        "--origins",
        default=setting(source, ENV_ORIGINS, ",".join(DEFAULT_ORIGINS)),
        help="comma-separated browser origins allowed to call this service",
    )
    parser.add_argument(
        "--api-url",
        dest="api_url",
        default=setting(source, ENV_API_URL, DEFAULT_API_URL),
        help="backend base URL whose /incidents list joins the corpus; pass an empty value to read stored readings only",
    )
    parser.add_argument("--llm-chain", dest="llm_chain", default=None)
    args = parser.parse_args(argv)
    if not 0 < args.port < 65536:
        parser.error(f"--port must be between 1 and 65535, got {args.port}")
    return args


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    service: Optional[ChatService] = None,
    server_factory: Any = None,
) -> int:
    use_utf8_stdout()
    view = os.environ if environ is None else environ
    args = parse_args(argv, view)
    chat = service or ChatService(
        environ=environ,
        state_dir=args.state_dir,
        origins=origins_from(args.origins),
        chain_path=args.llm_chain,
        api_url=args.api_url,
    )
    notice(chat, args.host, args.port)
    try:
        server = (
            listen(args.host, args.port, chat)
            if server_factory is None
            else server_factory(args.host, args.port, chat)
        )
    except OSError as error:
        print(
            f"chat service failed: it could not listen on {args.host}:{args.port} ({error}); "
            f"pass --port or set {ENV_PORT}",
            file=sys.stderr,
        )
        return EXIT_LISTEN
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("chat service stopped", file=sys.stderr)
    finally:
        server.server_close()
    return EXIT_OK


__all__ = [
    "ANSWERS_FROM_RECORDS",
    "API_TIMEOUT_SECONDS",
    "CHAT_FIELDS",
    "DEFAULT_API_URL",
    "DEFAULT_HOST",
    "DEFAULT_ORIGINS",
    "DEFAULT_PORT",
    "ENV_API_URL",
    "ENV_HOST",
    "ENV_ORIGINS",
    "ENV_PORT",
    "EXIT_LISTEN",
    "INCIDENTS_PATH",
    "MAX_JSON_BYTES",
    "NO_WRITES",
    "PATHS",
    "Response",
    "SERVICE_NAME",
    "SERVICE_VERSION",
    "ApiCorpusError",
    "ChatRequestHandler",
    "ChatService",
    "handler_for",
    "incidents_from_api",
    "listen",
    "main",
    "notice",
    "origins_from",
    "refused",
    "respond",
    "setting",
]


if __name__ == "__main__":
    raise SystemExit(main())
