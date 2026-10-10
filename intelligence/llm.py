"""Provider abstraction. Intelligence asks a model for structured output; it does not choose which one."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence, Type

from pydantic import BaseModel

ENV_PREFIX = "INTELLIGENCE_LLM_"

ENV_PROVIDER = ENV_PREFIX + "PROVIDER"
ENV_MODEL = ENV_PREFIX + "MODEL"
ENV_BASE_URL = ENV_PREFIX + "BASE_URL"
ENV_API_KEY = ENV_PREFIX + "API_KEY"
ENV_TIMEOUT = ENV_PREFIX + "TIMEOUT_SECONDS"
ENV_MAX_RETRIES = ENV_PREFIX + "MAX_RETRIES"
ENV_TEMPERATURE = ENV_PREFIX + "TEMPERATURE"
ENV_MAX_TOKENS = ENV_PREFIX + "MAX_TOKENS"
ENV_REASONING_EFFORT = ENV_PREFIX + "REASONING_EFFORT"

#: What a reasoning endpoint will spend its completion budget on before the JSON starts.
REASONING_EFFORTS = ("none", "default", "low", "medium", "high")

#: A schema-constrained answer that ran out of budget is a sizing decision, not a model failure.
TRUNCATION_HINT = (
    "the answer ran out of completion budget before the JSON finished, so raise "
    f"{ENV_MAX_TOKENS} or set {ENV_REASONING_EFFORT}=low to spend less of it on reasoning"
)

#: The package-local settings file. Intelligence reads it and never writes it.
# The repository root is not the right location for this project: the config lives next to the
# package code, and it must be found even when the process starts from a different working dir.
DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

#: Endpoint names, so a deployment reads as a deployment rather than as a vendor SDK.
ALIAS_BASE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "groq": "https://api.groq.com",
    "vllm": "http://127.0.0.1:8000/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """Base for every provider failure. Callers catch this, not a vendor exception."""


class ProviderNotConfigured(LLMError):
    """No provider could be built from the environment. Never answered with a stub."""


class TransportError(LLMError):
    """The endpoint could not be reached or answered with what the protocol expects."""


class StructuredOutputError(LLMError):
    """The model answered, but not with JSON this contract can read."""


def load_dotenv_file(path: Optional[str] = None) -> bool:
    """Put the local ``.env`` into ``os.environ``, leaving any real variable (even empty) alone."""
    candidate = path or DOTENV_PATH
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(os.path.join(os.path.dirname(__file__), candidate))
    target = candidate
    if not os.path.isfile(target):
        return False
    try:
        from dotenv import load_dotenv
    except ImportError as error:
        raise ProviderNotConfigured(
            f"{target} exists but python-dotenv is not installed, so its settings cannot be read;"
            " install python-dotenv or export the INTELLIGENCE_LLM_* variables"
        ) from error
    load_dotenv(target, override=False, encoding="utf-8")
    return True


@dataclass(frozen=True)
class LLMConfig:
    """Where to ask, what to ask and how hard to try. The key is never rendered."""

    provider: str = "openai_compatible"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False, compare=False)

    timeout_seconds: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: int = 4096
    reasoning_effort: Optional[str] = None

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "LLMConfig":
        """Read the environment. An explicit ``environ`` is used as-is, with no file loading."""
        if environ is not None:
            source: Mapping[str, str] = dict(environ)
        else:
            load_dotenv_file()
            source = dict(os.environ)

        def value(name: str) -> Optional[str]:
            raw = source.get(name)
            return raw if raw not in (None, "") else None

        return cls(
            provider=(value(ENV_PROVIDER) or "openai_compatible").strip(),
            model=value(ENV_MODEL),
            base_url=value(ENV_BASE_URL),
            api_key=value(ENV_API_KEY),
            timeout_seconds=_float(value(ENV_TIMEOUT), 60.0, ENV_TIMEOUT),
            max_retries=_int(value(ENV_MAX_RETRIES), 2, ENV_MAX_RETRIES),
            temperature=_float(value(ENV_TEMPERATURE), 0.0, ENV_TEMPERATURE),
            max_tokens=_int(value(ENV_MAX_TOKENS), 4096, ENV_MAX_TOKENS),
            reasoning_effort=_effort(value(ENV_REASONING_EFFORT)),
        )

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return ALIAS_BASE_URLS.get(self.provider, ALIAS_BASE_URLS["openai_compatible"])

    def summary(self) -> dict[str, Any]:
        """Safe to log or embed in a fixture: no key, no endpoint secrets."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.resolved_base_url(),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "api_key_present": bool(self.api_key),
        }


def _float(raw: Optional[str], default: float, name: str) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ProviderNotConfigured(f"{name} must be a number, got {raw!r}") from error


def _int(raw: Optional[str], default: int, name: str) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ProviderNotConfigured(f"{name} must be an integer, got {raw!r}") from error


def _effort(raw: Optional[str]) -> Optional[str]:
    """Reasoning budget is a deployment choice, so an unusable name is refused at read time."""
    if raw is None:
        return None
    effort = raw.strip().lower()
    if effort not in REASONING_EFFORTS:
        raise ProviderNotConfigured(
            f"{ENV_REASONING_EFFORT} must be one of {', '.join(REASONING_EFFORTS)}, got {raw!r}"
        )
    return effort


@dataclass(frozen=True)
class LLMRequest:
    """One turn of one call: what to tell the model and what shape to answer in."""

    system: str
    user: str
    json_schema: Optional[dict[str, Any]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    meta: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def messages(self) -> Sequence[dict[str, str]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


@dataclass(frozen=True)
class LLMResponse:
    """What came back, before any contract read it."""

    provider: str
    model: str
    text: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    latency_ms: Optional[float] = None
    finish_reason: Optional[str] = None

    def json(self) -> str:
        return self.text if self.text is not None else json.dumps(self.data or {})


@dataclass(frozen=True)
class StructuredOutput:
    """A parsed payload plus the response it came from."""

    payload: dict[str, Any]
    response: LLMResponse


#: (url, headers, payload, timeout) -> (status, body)
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], tuple[int, str]]


class LLMProvider(ABC):
    """The whole coupling between Intelligence and any model."""

    name: str = "llm"

    @property
    @abstractmethod
    def model(self) -> str:
        """Which model this provider is configured to ask."""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send one request and return it untouched."""

    def generate_text(self, request: LLMRequest) -> str:
        response = self.complete(request)
        if response.text is None:
            raise StructuredOutputError(
                f"{self.name} returned no text at all; nothing was invented in its place"
            )
        return response.text

    def generate_structured(self, request: LLMRequest) -> StructuredOutput:
        """Ask for JSON and parse it. A model that prose-wraps the answer fails here, loudly."""
        response = self.complete(request)
        payload = response.data
        if payload is None:
            payload = parse_json_object(response.text, provider=self.name)
        if not isinstance(payload, dict):
            raise StructuredOutputError(
                f"{self.name} returned {type(payload).__name__}, expected one JSON object"
            )
        return StructuredOutput(payload=payload, response=response)


def parse_json_object(text: Optional[str], *, provider: str = "provider") -> dict[str, Any]:
    """Read exactly one JSON object out of what a model said."""
    if text is None:
        raise StructuredOutputError(f"{provider} returned nothing to parse")
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise StructuredOutputError(
            f"{provider} returned no JSON object: {candidate[:200]!r}"
        )
    try:
        loaded = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as error:
        raise StructuredOutputError(
            f"{provider} returned text that is not valid JSON: {error}"
        ) from error
    if not isinstance(loaded, dict):
        raise StructuredOutputError(
            f"{provider} returned a {type(loaded).__name__} where the answer should be one "
            "JSON object"
        )
    return loaded


def json_schema_for(model: Type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


_STRICT_DROPPED_KEYS = frozenset(
    {
        "$defs",
        "default",
        "title",
        "examples",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


def strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Reshape a Pydantic schema into what JSON-Schema strict mode demands.

    Every object closes itself, every property is required and absence is said as null, which is
    the same absent-or-extracted distinction the draft model already carries. References are
    inlined so the answer shape is stated once, in full.
    """
    root = dict(schema)
    defs = dict(root.pop("$defs", None) or {})
    return _strict_node(root, defs, ())


def _strict_node(node: Any, defs: dict[str, Any], trail: tuple[str, ...]) -> Any:
    if isinstance(node, (list, tuple)):
        return [_strict_node(item, defs, trail) for item in node]
    if not isinstance(node, Mapping):
        return node
    reference = str(node.get("$ref", ""))
    if reference:
        name = reference.rsplit("/", 1)[-1]
        if name in trail or name not in defs:
            raise StructuredOutputError(
                f"{reference} cannot be inlined into one strict schema"
            )
        return _strict_node(defs[name], defs, trail + (name,))
    shaped: dict[str, Any] = {}
    for key, value in node.items():
        if key == "$ref":
            continue
        if key == "properties":
            shaped["properties"] = {
                name: _strict_node(subschema, defs, trail)
                for name, subschema in dict(value).items()
            }
        elif key not in _STRICT_DROPPED_KEYS:
            shaped[key] = _strict_node(value, defs, trail)
    properties = shaped.get("properties")
    if isinstance(properties, dict):
        shaped["type"] = "object"
        shaped["additionalProperties"] = False
        shaped["required"] = list(properties)
    else:
        shaped.pop("required", None)
    return shaped


def _schema_name(schema: Mapping[str, Any]) -> str:
    """A stable label for the endpoint: the schema's own title, cleaned to the allowed alphabet."""
    label = str(schema.get("title") or "response")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_")
    return (cleaned or "response")[:64]


def _require_model(config: LLMConfig) -> None:
    if not config.model:
        raise ProviderNotConfigured(
            f"{ENV_MODEL} is not set, so no model can be asked. Configure a provider or "
            "pass an LLMConfig with a model name."
        )


class OpenAICompatibleProvider(LLMProvider):
    """One chat endpoint, one JSON contract: serves GPT-OSS 120B, Qwen 27B and equivalents."""

    name = "openai_compatible"

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Optional[Transport] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        _require_model(config)
        self.config = config
        self._transport = transport or self._urllib_transport
        self._sleeper = sleeper

    @property
    def model(self) -> str:
        return str(self.config.model)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _urllib_transport(self, url, headers, payload, timeout):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as error:
            raise TransportError(f"{url} could not be reached: {error}") from error

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.resolved_base_url() + "/chat/completions"
        attempts = max(1, self.config.max_retries + 1)
        last_status, last_body = 0, ""
        for attempt in range(attempts):
            status, body = self._transport(
                url, self._headers(), payload, self.config.timeout_seconds
            )
            if status // 100 == 2:
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    raise TransportError(
                        f"{url} answered {status} with a body that is not JSON: {body[:200]!r}"
                    ) from error
            last_status, last_body = status, body
            if status not in _RETRY_STATUS or attempt == attempts - 1:
                break
            self._sleeper(0.2 * (attempt + 1))
        raise TransportError(
            f"{url} answered {last_status}: {last_body[:300]}"
            + (f" (after {attempts} attempt(s))" if attempts > 1 else "")
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(request.messages()),
            "temperature": (
                self.config.temperature if request.temperature is None else request.temperature
            ),
            "max_tokens": request.max_tokens or self.config.max_tokens,
        }
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if request.json_schema:
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        raw = self._post(payload)
        latency = (time.monotonic() - started) * 1000.0

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TransportError(
                f"{self.name} returned no choices: {str(raw)[:300]}"
            )
        message = choices[0].get("message") or {}
        text = message.get("content")
        finish = choices[0].get("finish_reason")
        if finish == "length":
            raise StructuredOutputError(
                f"{self.name} ran out of completion budget: {TRUNCATION_HINT}"
            )
        data = None
        if request.json_schema and isinstance(text, str):
            data = parse_json_object(text, provider=self.name)
        return LLMResponse(
            provider=self.name,
            model=str(raw.get("model") or self.model),
            text=text if isinstance(text, str) else None,
            data=data,
            latency_ms=round(latency, 3),
            finish_reason=finish if isinstance(finish, str) else None,
        )


def _groq_base_url(config: LLMConfig) -> str:
    """Origin only: the SDK adds /openai/v1/chat/completions, so an OpenAI-style base URL asks twice."""
    parts = urlsplit(config.resolved_base_url())
    return f"{parts.scheme}://{parts.netloc}"


def _groq_failure(error: BaseException) -> str:
    """Type, HTTP status and message from the SDK. Never the request, whose headers hold the key."""
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    detail = str(error)[:300]
    code = None
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            code = inner.get("code")
        detail = str(inner if isinstance(inner, dict) else body)[:300]
    prefix = type(error).__name__ + (f" (HTTP {status})" if status else "")
    reason = f"{prefix}: {detail}" if detail else prefix
    if code == "json_validate_failed" or "json_validate_failed" in detail:
        reason += f"; the schema-constrained answer never arrived complete - {TRUNCATION_HINT}"
    if status in (401, 403):
        reason += "; authentication and permission failures are never retried"
    return reason


class GroqProvider(LLMProvider):
    """Groq through the official SDK, behind the same request and response shapes."""

    name = "groq"

    def __init__(self, config: LLMConfig, *, client: Any = None) -> None:
        _require_model(config)
        if client is None:
            if not config.api_key:
                raise ProviderNotConfigured(
                    f"{ENV_API_KEY} is not set, so groq cannot authenticate. Export it or put it "
                    "in the local .env, or pass an LLMConfig with a key."
                )
            from groq import Groq

            client = Groq(
                api_key=config.api_key,
                base_url=_groq_base_url(config),
                timeout=config.timeout_seconds,
                max_retries=config.max_retries,
            )
        self.config = config
        self._client = client

    @property
    def model(self) -> str:
        return str(self.config.model)

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": list(request.messages()),
            "temperature": (
                self.config.temperature if request.temperature is None else request.temperature
            ),
            "max_tokens": request.max_tokens or self.config.max_tokens,
        }
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        if request.json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(request.json_schema),
                    "strict": True,
                    "schema": strict_json_schema(request.json_schema),
                },
            }

        from groq import GroqError

        started = time.monotonic()
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except GroqError as error:
            raise TransportError(f"{self.name} call failed: {_groq_failure(error)}") from error
        latency = (time.monotonic() - started) * 1000.0

        choices = getattr(completion, "choices", None)
        if not choices:
            raise TransportError(f"{self.name} returned no choices: {str(completion)[:300]}")
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        finish = getattr(choices[0], "finish_reason", None)
        if finish == "length":
            raise StructuredOutputError(
                f"{self.name} ran out of completion budget: {TRUNCATION_HINT}"
            )
        data = None
        if request.json_schema and isinstance(text, str):
            data = parse_json_object(text, provider=self.name)
        return LLMResponse(
            provider=self.name,
            model=str(getattr(completion, "model", None) or self.model),
            text=text if isinstance(text, str) else None,
            data=data,
            latency_ms=round(latency, 3),
            finish_reason=finish if isinstance(finish, str) else None,
        )


PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "groq": GroqProvider,
    "vllm": OpenAICompatibleProvider,
    "ollama": OpenAICompatibleProvider,
}


def build_provider(
    config: Optional[LLMConfig] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
) -> LLMProvider:
    """Make the provider the environment names. Refuses to guess a model."""
    settings = LLMConfig.from_env(environ) if config is None else config
    provider_class = PROVIDERS.get(settings.provider)
    if provider_class is None:
        raise ProviderNotConfigured(
            f"unknown provider {settings.provider!r}; known providers are "
            + ", ".join(sorted(PROVIDERS))
        )
    if not settings.model:
        raise ProviderNotConfigured(
            f"no model configured for provider {settings.provider!r}: set {ENV_MODEL}, or "
            "pass LLMConfig(model=...)"
        )
    if transport is not None:
        return provider_class(settings, transport=transport)
    return provider_class(settings)
