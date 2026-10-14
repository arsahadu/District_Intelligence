from __future__ import annotations

import email.utils
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
ENV_MAX_WAIT = ENV_PREFIX + "MAX_WAIT_SECONDS"
ENV_TEMPERATURE = ENV_PREFIX + "TEMPERATURE"
ENV_MAX_TOKENS = ENV_PREFIX + "MAX_TOKENS"
ENV_REASONING_EFFORT = ENV_PREFIX + "REASONING_EFFORT"

REASONING_EFFORTS = ("none", "default", "low", "medium", "high")

EFFORT_VALUES = ("low", "medium", "high")

DEFAULT_MAX_WAIT_SECONDS = 60.0

BACKOFF_STEP_SECONDS = 0.2

TRUNCATION_HINT = (
    "the answer ran out of completion budget before the JSON finished, so raise "
    f"{ENV_MAX_TOKENS} or set {ENV_REASONING_EFFORT}=low to spend less of it on reasoning"
)

DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

ALIAS_BASE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "groq": "https://api.groq.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "vllm": "http://127.0.0.1:8000/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}

ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
ENV_GROQ_API_KEY = "GROQ_API_KEY"
VENDOR_KEY_VARS = {"gemini": ENV_GEMINI_API_KEY, "groq": ENV_GROQ_API_KEY}
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    ...


class ProviderNotConfigured(LLMError):
    ...


class TransportError(LLMError):
    ...


class RateLimitedError(TransportError):
    ...
class QuotaExhaustedError(TransportError):
    ...
class StructuredOutputError(LLMError):
    ...


class ModelUnavailableError(TransportError):
    ...
class ProviderAuthError(TransportError):
    ...
class EndpointUnreachableError(TransportError):
    ...
QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "quota_exceeded",
        "daily_quota_exceeded",
        "daily_quota_exceeded_",
        "alloc_quota_exceeded",
        "usage_exceeded",
        "daily_limit_exceeded",
        "tokens_limit_reached",
        "billing_not_active",
    }
)

RATE_LIMIT_CODES = frozenset(
    {"rate_limit_exceeded", "requests_limit_exceeded", "requests_exceeded", "model_rate_limited"}
)

LIMIT_STATUSES = frozenset({402, 429})

LIMIT_UNCLASSIFIED = "unknown"


def _named(holder: Mapping[str, Any]) -> Optional[str]:
    for key in ("code", "type"):
        value = holder.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    for detail in holder.get("details") or ():
        if isinstance(detail, Mapping):
            reason = detail.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip().casefold()
    status = holder.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip().casefold()
    return None


def _error_detail(body: Any) -> tuple[Optional[str], str]:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None, body
    if not isinstance(body, Mapping):
        return None, str(body or "")
    inner = body.get("error")
    if isinstance(inner, Mapping):
        return _named(inner), str(inner.get("message") or "")
    if isinstance(inner, str):
        return None, inner
    return _named(body), str(body)


def limit_state(status: Optional[int], body: Any) -> Optional[str]:
    if status not in LIMIT_STATUSES:
        return None
    code, message = _error_detail(body)
    if code in QUOTA_CODES:
        return "quota"
    text = message.casefold()
    if "quota" in text and any(word in text for word in ("exceed", "limit", "credit", "spent")):
        return "quota"
    if code in RATE_LIMIT_CODES or "rate limit" in text or "too many requests" in text:
        return "rate"
    return "quota" if status == 402 else LIMIT_UNCLASSIFIED


AUTH_STATUSES = frozenset({401, 403})
AUTH_CODES = frozenset(
    {
        "invalid_api_key",
        "invalid_key",
        "authentication_error",
        "unauthorized",
        "no_api_key",
        "unauthenticated",
        "permission_denied",
    }
)

MODEL_REFUSAL_STATUSES = frozenset({400, 404, 410, 422})
MODEL_UNAVAILABLE_CODES = frozenset(
    {
        "model_not_found",
        "model_not_available",
        "model_not_supported",
        "unknown_model",
        "unsupported_model",
        "invalid_model",
    }
)
MODEL_UNAVAILABLE_PHRASES = (
    "not found",
    "does not exist",
    "no such model",
    "unknown model",
    "not available",
    "is not available",
    "not served",
    "not supported",
    "is not a valid model",
)


def auth_refusal(status: Optional[int], body: Any) -> bool:
    if status in AUTH_STATUSES:
        return True
    code, _ = _error_detail(body)
    return code in AUTH_CODES


def model_unavailable(status: Optional[int], body: Any) -> bool:
    if status not in MODEL_REFUSAL_STATUSES:
        return False
    code, message = _error_detail(body)
    if code in MODEL_UNAVAILABLE_CODES:
        return True
    if status not in (404, 410):
        return False
    text = message.casefold()
    return "model" in text and any(phrase in text for phrase in MODEL_UNAVAILABLE_PHRASES)


def failure_kind(status: Optional[int], body: Any) -> Optional[str]:
    limit = limit_state(status, body)
    if limit == "quota":
        return "quota"
    if limit == "rate":
        return "rate"
    if limit == LIMIT_UNCLASSIFIED:
        return "limit"
    if auth_refusal(status, body):
        return "auth"
    if model_unavailable(status, body):
        return "model"
    return None


def refusal_error(url: str, status: Optional[int], body: Any, *, prefix: str = "answered") -> TransportError:
    detail = f"{url} {prefix} {status}: {str(body)[:300]}"
    kind = failure_kind(status, body)
    if kind == "auth":
        return ProviderAuthError(f"{detail} - the credential was refused, so nothing further was sent")
    if kind == "model":
        return ModelUnavailableError(
            f"{detail} - the endpoint says it does not serve this model, so nothing further was sent to it"
        )
    return TransportError(detail)


REASONING_FIELDS = ("reasoning", "reasoning_content", "thinking")


def answer_diagnosis(provider: str, content: Any, message: Any) -> str:
    if isinstance(message, Mapping):
        carried = [(name, message.get(name)) for name in REASONING_FIELDS]
    else:
        carried = [(name, getattr(message, name, None)) for name in REASONING_FIELDS]
    reasoning = next(
        ((name, text) for name, text in carried if isinstance(text, str) and text.strip()), None
    )
    parts: list[str] = []
    if content is None:
        parts.append("no message content at all")
    elif isinstance(content, str) and not content.strip():
        parts.append("empty message content")
    if not parts:
        return ""
    if reasoning is not None:
        parts.append(
            f"the text it did send sat in `{reasoning[0]}` ({len(reasoning[1])} characters), which "
            "this pipeline does not read as an answer"
        )
    return f"{provider} answered with " + ", ".join(parts)


def _token_count(usage: Mapping[str, Any], *keys: str) -> Optional[int]:
    for holder in (usage, usage.get("completion_tokens_details"), usage.get("output_tokens_details")):
        if not isinstance(holder, Mapping):
            continue
        for key in keys:
            value = holder.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    return None


def budget_note(usage: Mapping[str, Any], *, limit: int, effort: Optional[str]) -> str:
    spent = _token_count(
        usage, "reasoning_tokens", "thinking_tokens", "total_thought_tokens", "thought_tokens"
    )
    output = _token_count(
        usage, "completion_tokens", "output_tokens", "total_output_tokens"
    )
    said: list[str] = []
    if spent is not None:
        said.append(
            f"the endpoint counted {spent} of the "
            f"{output if output is not None else '?'} output token(s) it returned as reasoning, "
            f"against the {limit} this request allowed"
        )
    elif output is not None:
        said.append(f"the endpoint counted {output} output token(s) against the {limit} allowed")
    else:
        said.append(f"the response carried no usage block, so nothing is known of where the {limit} went")
    if effort == EFFORT_VALUES[0]:
        said.append(
            f"the configured {ENV_REASONING_EFFORT} is already {effort!r}, so what ran out is the "
            "answer's own length rather than thinking left running"
        )
    elif effort in EFFORT_VALUES:
        said.append(
            f"the configured {ENV_REASONING_EFFORT} is {effort!r}, so {EFFORT_VALUES[0]} asks for "
            "less thinking and 'none' for none of it where the endpoint has that"
        )
    elif effort == "none":
        said.append(
            f"no thinking was asked for ({ENV_REASONING_EFFORT}=none), so this was the answer itself "
            f"outgrowing the budget rather than the model reasoning about the record"
        )
    else:
        said.append(
            f"{ENV_REASONING_EFFORT} is "
            + (f"{effort!r}, which leaves the model's own thinking budget in force"
               if effort == "default" else "unset, which leaves the model's own thinking budget in force")
            + f", and 'none' asks for none of it"
        )
    return "; ".join(said) + ". "


GUIDANCE_FIELDS = (
    ("retry_after", 1.0),
    ("retry_after_seconds", 1.0),
    ("retry_after_ms", 0.001),
    ("retry_delay", 1.0),
    ("retryDelay", 1.0),
)
GUIDANCE_HEADERS = (
    ("retry-after-ms", 0.001),
    ("retry-after", 1.0),
    ("x-ratelimit-reset-tokens", 1.0),
    ("x-ratelimit-reset-requests", 1.0),
)

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]*)$", re.I)
_DURATION_PART = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)", re.I)
_GUIDANCE_MESSAGE = re.compile(
    r"(?:try again|come back|retry|wait|resets?|available)\D{0,12}?(?:in|after)\s*"
    r"(\d+(?:\.\d+)?)\s*(ms|msec|millisec\w*|sec\w*|s|min\w*|m|hour\w*|hr\w*|h|day\w*|d)",
    re.I,
)
_UNIT_SECONDS = (
    ("millise", 0.001),
    ("msec", 0.001),
    ("ms", 0.001),
    ("second", 1.0),
    ("sec", 1.0),
    ("s", 1.0),
    ("minute", 60.0),
    ("min", 60.0),
    ("m", 60.0),
    ("hour", 3600.0),
    ("hr", 3600.0),
    ("h", 3600.0),
    ("day", 86400.0),
    ("d", 86400.0),
)


def _unit_seconds(raw: str) -> Optional[float]:
    text = raw.casefold()
    for spelling, seconds in _UNIT_SECONDS:
        if text.startswith(spelling):
            return seconds
    return None


def _compound_seconds(text: str) -> Optional[float]:
    total, position = 0.0, 0
    while position < len(text):
        match = _DURATION_PART.match(text, position)
        if match is None:
            return None
        unit = _unit_seconds(match.group(2))
        if unit is None:
            return None
        total += float(match.group(1)) * unit
        position = match.end()
    return total


def _stated_seconds(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    match = _DURATION.match(text)
    if match:
        if not match.group(2):
            return float(match.group(1))
        unit = _unit_seconds(match.group(2))
        return None if unit is None else float(match.group(1)) * unit
    parts = email.utils.parsedate_tz(text)
    if parts is not None:
        return max(0.0, float(email.utils.mktime_tz(parts) - time.time()))
    return _compound_seconds(text)


def _guidance_in(body: Any) -> Optional[float]:
    for candidate in _mappings(body):
        for key, scale in GUIDANCE_FIELDS:
            stated = _stated_seconds(candidate.get(key))
            if stated is not None:
                return stated * scale
    _, message = _error_detail(body)
    prose = _GUIDANCE_MESSAGE.search(message)
    if prose is None:
        return None
    unit = _unit_seconds(prose.group(2))
    return None if unit is None else float(prose.group(1)) * unit


def _mappings(body: Any) -> list[Mapping[str, Any]]:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return []
    if not isinstance(body, Mapping):
        return []
    nested = body.get("error")
    found = [body]
    if isinstance(nested, Mapping):
        found.append(nested)
        inner = nested.get("details")
        if isinstance(inner, (list, tuple)):
            found.extend(item for item in inner if isinstance(item, Mapping))
    return found


def retry_guidance(body: Any = None, headers: Any = None) -> Optional[float]:
    stated = _guidance_in(body)
    if stated is not None:
        return stated
    get = getattr(headers, "get", None)
    if get is None:
        return None
    for name, scale in GUIDANCE_HEADERS:
        found = _stated_seconds(get(name))
        if found is not None:
            return found * scale
    return None


def next_wait(
    *,
    kind: Optional[str],
    guidance: Optional[float],
    attempt: int,
    waited: float,
    max_retries: int,
    max_wait_seconds: float,
) -> Optional[float]:
    if kind == "quota":
        return None
    remaining = max_retries - attempt
    if kind == LIMIT_UNCLASSIFIED and attempt >= 1:
        remaining = 0
    if remaining < 1:
        return None
    left = max_wait_seconds - waited
    if left <= 0:
        return None
    if kind == "rate" and guidance is None:
        return left / remaining
    if guidance is not None:
        return guidance if guidance <= left else None
    return min(BACKOFF_STEP_SECONDS * (attempt + 1), left) or None


def patience_note(
    guidance: Optional[float], *, waited: float, max_wait_seconds: float, sends: int
) -> str:
    if guidance is not None and guidance > max_wait_seconds:
        return (
            f" - it asked for {guidance:g}s, which is more than the {max_wait_seconds:g}s "
            f"{ENV_MAX_WAIT} allows, so nothing further was sent"
        )
    if waited:
        return f" - {waited:g}s was waited over {sends} send(s) before it was given up on"
    return " - nothing further was sent"


def load_dotenv_file(path: Optional[str] = None) -> bool:
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
    provider: str = "openai_compatible"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False, compare=False)

    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    temperature: float = 0.0
    max_tokens: int = 4096
    reasoning_effort: Optional[str] = None

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "LLMConfig":
        if environ is not None:
            source: Mapping[str, str] = dict(environ)
        else:
            load_dotenv_file()
            source = dict(os.environ)

        def value(name: str) -> Optional[str]:
            raw = source.get(name)
            return raw if raw not in (None, "") else None

        provider = (value(ENV_PROVIDER) or "openai_compatible").strip()
        return cls(
            provider=provider,
            model=value(ENV_MODEL),
            base_url=value(ENV_BASE_URL),
            api_key=value(ENV_API_KEY) or value(VENDOR_KEY_VARS.get(provider, "")),
            timeout_seconds=_float(value(ENV_TIMEOUT), 60.0, ENV_TIMEOUT),
            max_retries=_int(value(ENV_MAX_RETRIES), 2, ENV_MAX_RETRIES),
            max_wait_seconds=_float(
                value(ENV_MAX_WAIT), DEFAULT_MAX_WAIT_SECONDS, ENV_MAX_WAIT
            ),
            temperature=_float(value(ENV_TEMPERATURE), 0.0, ENV_TEMPERATURE),
            max_tokens=_int(value(ENV_MAX_TOKENS), 4096, ENV_MAX_TOKENS),
            reasoning_effort=_effort(value(ENV_REASONING_EFFORT)),
        )

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return ALIAS_BASE_URLS.get(self.provider, ALIAS_BASE_URLS["openai_compatible"])

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.resolved_base_url(),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_wait_seconds": self.max_wait_seconds,
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
    payload: dict[str, Any]
    response: LLMResponse


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], tuple[int, str]]


class LLMProvider(ABC):
    name: str = "llm"
    requires_api_key: bool = False
    accepts_transport: bool = False

    @property
    @abstractmethod
    def model(self) -> str:
        ...

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        ...

    def generate_text(self, request: LLMRequest) -> str:
        response = self.complete(request)
        if response.text is None:
            raise StructuredOutputError(
                f"{self.name} returned no text at all; nothing was invented in its place"
            )
        return response.text

    def generate_structured(self, request: LLMRequest) -> StructuredOutput:
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
    if text is None:
        raise StructuredOutputError(f"{provider} returned nothing to parse")
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1:
        raise StructuredOutputError(
            f"{provider} returned no JSON object anywhere in its answer: {candidate[:200]!r}"
        )
    if end <= start:
        raise StructuredOutputError(
            f"{provider} opened a JSON object and never closed it - the answer stopped after "
            f"{len(candidate)} characters, so it was cut off rather than refused: {TRUNCATION_HINT}"
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


GEMINI_SCHEMA_DROPPED = frozenset(
    {
        "$defs",
        "$schema",
        "additionalProperties",
        "default",
        "examples",
        "propertyOrdering",
        "title",
    }
)


def gemini_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    root = dict(schema)
    defs = dict(root.pop("$defs", None) or {})
    return _gemini_node(root, defs, ())


def _gemini_node(node: Any, defs: dict[str, Any], trail: tuple[str, ...]) -> Any:
    if isinstance(node, (list, tuple)):
        return [_gemini_node(item, defs, trail) for item in node]
    if not isinstance(node, Mapping):
        return node
    reference = str(node.get("$ref", ""))
    if reference:
        name = reference.rsplit("/", 1)[-1]
        if name in trail or name not in defs:
            raise StructuredOutputError(
                f"{reference} cannot be inlined into one Gemini schema"
            )
        return _gemini_node(defs[name], defs, trail + (name,))
    shaped: dict[str, Any] = {}
    branches: Any = None
    for key, value in node.items():
        if key in ("$ref",) or key in GEMINI_SCHEMA_DROPPED:
            continue
        if key == "properties":
            shaped["properties"] = {
                name: _gemini_node(subschema, defs, trail)
                for name, subschema in dict(value).items()
            }
            continue
        if key in ("anyOf", "oneOf"):
            branches = _gemini_node(value, defs, trail)
            continue
        shaped[key] = _gemini_node(value, defs, trail)
    if branches is None:
        return shaped
    alternatives = [item for item in branches if isinstance(item, Mapping)]
    absent = [item for item in alternatives if item.get("type") == "null"]
    present = [item for item in alternatives if item.get("type") != "null"]
    if len(present) == 1 and len(absent) == 1:
        only = present[0]
        kind = only.get("type")
        if isinstance(kind, str):
            for key, value in only.items():
                if key != "type":
                    shaped.setdefault(key, value)
            shaped["type"] = [kind, "null"]
            return shaped
    shaped["anyOf"] = branches
    return shaped


def _schema_name(schema: Mapping[str, Any]) -> str:
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
    name = "openai_compatible"
    accepts_transport = True

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

    def _endpoint(self) -> str:
        return self.config.resolved_base_url() + "/chat/completions"

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
            raise EndpointUnreachableError(f"{url} could not be reached: {error}") from error

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._endpoint()
        attempts = max(1, self.config.max_retries + 1)
        last_status, last_body, last_kind, last_guidance = 0, "", None, None
        waited, sent = 0.0, 0
        for attempt in range(attempts):
            status, body = self._transport(
                url, self._headers(), payload, self.config.timeout_seconds
            )
            sent += 1
            if status // 100 == 2:
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    raise TransportError(
                        f"{url} answered {status} with a body that is not JSON: {body[:200]!r}"
                    ) from error
            last_status, last_body, last_kind = status, body, limit_state(status, body)
            if last_kind == "quota":
                raise QuotaExhaustedError(
                    f"{url} answered {status}: {body[:300]} - the provider refused on its allowance, "
                    "so nothing further was sent to it"
                )
            fatal = failure_kind(status, body)
            if fatal in ("auth", "model"):
                raise refusal_error(url, status, body)
            if status not in _RETRY_STATUS:
                break
            last_guidance = retry_guidance(body)
            wait = next_wait(
                kind=last_kind,
                guidance=last_guidance,
                attempt=attempt,
                waited=waited,
                max_retries=self.config.max_retries,
                max_wait_seconds=self.config.max_wait_seconds,
            )
            if wait is None:
                break
            self._sleeper(wait)
            waited += wait
        refused: type[TransportError] = TransportError
        if last_status in LIMIT_STATUSES:
            refused = RateLimitedError
        final = failure_kind(last_status, last_body)
        if final == "auth":
            refused = ProviderAuthError
        elif final == "model":
            refused = ModelUnavailableError
        raise refused(
            f"{url} answered {last_status}: {last_body[:300]}"
            + (f" (after {sent} send(s))" if sent > 1 else "")
            + patience_note(
                last_guidance,
                waited=waited,
                max_wait_seconds=self.config.max_wait_seconds,
                sends=sent,
            )
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        limit = request.max_tokens or self.config.max_tokens
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(request.messages()),
            "temperature": (
                self.config.temperature if request.temperature is None else request.temperature
            ),
            "max_tokens": limit,
        }
        effort = self.config.reasoning_effort
        if effort in EFFORT_VALUES:
            payload["reasoning_effort"] = effort
        elif effort == "none":
            payload["reasoning"] = {"enabled": False}
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
            usage = raw.get("usage")
            raise StructuredOutputError(
                f"{self.name} ran out of completion budget: "
                f"{budget_note(usage if isinstance(usage, Mapping) else {}, limit=limit, effort=effort)}"
                f"{TRUNCATION_HINT}"
            )
        data = None
        if request.json_schema:
            if not isinstance(text, str) or not text.strip():
                raise StructuredOutputError(
                    answer_diagnosis(self.name, text, message)
                    or f"{self.name} returned nothing to read as the JSON object it was asked for"
                )
            data = parse_json_object(text, provider=self.name)
        return LLMResponse(
            provider=self.name,
            model=str(raw.get("model") or self.model),
            text=text if isinstance(text, str) else None,
            data=data,
            latency_ms=round(latency, 3),
            finish_reason=finish if isinstance(finish, str) else None,
        )


GEMINI_MINIMAL_THINKING = "minimal"


def _step_text(step: Any) -> Optional[str]:
    if not isinstance(step, Mapping):
        return None
    content = step.get("content")
    if isinstance(content, str):
        return content if content.strip() else None
    if isinstance(content, (list, tuple)):
        pieces = [
            item.get("text")
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        joined = "".join(pieces)
        return joined if joined.strip() else None
    return None


def gemini_answer(body: Mapping[str, Any]) -> tuple[Optional[str], str]:
    text = body.get("output_text")
    if isinstance(text, str) and text.strip():
        return text, "output_text"
    steps = body.get("steps")
    if isinstance(steps, list):
        for index in range(len(steps) - 1, -1, -1):
            found = _step_text(steps[index])
            if found is not None:
                return found, f"steps[{index}].content"
        return None, f"{len(steps)} step(s), none holding text"
    return None, "neither output_text nor steps"


class GeminiProvider(OpenAICompatibleProvider):
    name = "gemini"
    requires_api_key = True

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Optional[Transport] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(config, transport=transport, sleeper=sleeper)
        if not config.api_key:
            raise ProviderNotConfigured(
                f"neither {ENV_GEMINI_API_KEY} nor {ENV_API_KEY} holds a credential, so "
                "Gemini cannot be asked. A chain file names the variable, never a key."
            )

    def _endpoint(self) -> str:
        return self.config.resolved_base_url().removesuffix("/v1beta") + "/v1beta/interactions"

    def _headers(self) -> dict[str, str]:
        return {"content-type": "application/json", "x-goog-api-key": str(self.config.api_key)}

    def complete(self, request: LLMRequest) -> LLMResponse:
        limit = request.max_tokens or self.config.max_tokens
        effort = self.config.reasoning_effort
        generation: dict[str, Any] = {
            "max_output_tokens": limit,
            "temperature": (
                self.config.temperature if request.temperature is None else request.temperature
            ),
        }
        if effort in EFFORT_VALUES:
            generation["thinking_level"] = effort
        elif effort == "none":
            generation["thinking_level"] = GEMINI_MINIMAL_THINKING
        payload: dict[str, Any] = {
            "model": self.model,
            "system_instruction": request.system,
            "input": request.user,
            "generation_config": generation,
        }
        if request.json_schema:
            payload["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": gemini_schema(request.json_schema),
            }

        started = time.monotonic()
        raw = self._post(payload)
        latency = (time.monotonic() - started) * 1000.0
        status = raw.get("status")
        text, where = gemini_answer(raw)
        if status == "incomplete":
            usage = raw.get("usage")
            raise StructuredOutputError(
                f"{self.name} ran out of completion budget: "
                f"{budget_note(usage if isinstance(usage, Mapping) else {}, limit=limit, effort=effort)}"
                f"{TRUNCATION_HINT}; this endpoint counts thinking and answer in that one budget and "
                "offers no level below 'minimal'"
            )
        data = None
        if request.json_schema:
            if not isinstance(text, str) or not text.strip():
                raise StructuredOutputError(
                    f"{self.name} answered with no text to read as the JSON object it was asked for - "
                    f"looked in {where}, status "
                    f"{status if isinstance(status, str) else 'unstated'}"
                )
            data = parse_json_object(text, provider=self.name)
        return LLMResponse(
            provider=self.name,
            model=str(raw.get("model") or self.model),
            text=text,
            data=data,
            latency_ms=round(latency, 3),
            finish_reason=status if isinstance(status, str) else None,
        )


def _groq_base_url(config: LLMConfig) -> str:
    parts = urlsplit(config.resolved_base_url())
    return f"{parts.scheme}://{parts.netloc}"


def _groq_failure(error: BaseException) -> str:
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
    name = "groq"
    requires_api_key = True

    def __init__(
        self,
        config: LLMConfig,
        *,
        client: Any = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        _require_model(config)
        if client is None:
            if not config.api_key:
                raise ProviderNotConfigured(
                    f"neither {ENV_GROQ_API_KEY} nor {ENV_API_KEY} holds a credential, so groq "
                    "cannot be asked. A chain file names the variable, never a key."
                )
            from groq import Groq

            client = Groq(
                api_key=config.api_key,
                base_url=_groq_base_url(config),
                timeout=config.timeout_seconds,
                max_retries=0,
            )
        self.config = config
        self._client = client
        self._sleeper = sleeper

    @property
    def model(self) -> str:
        return str(self.config.model)

    def _send(self, kwargs: dict[str, Any]) -> Any:
        from groq import GroqError

        attempt, sent, waited = 0, 0, 0.0
        while True:
            sent += 1
            try:
                return self._client.chat.completions.create(**kwargs)
            except GroqError as error:
                status = getattr(error, "status_code", None)
                body = getattr(error, "body", None)
                kind = limit_state(status, body)
                if kind == "quota":
                    raise QuotaExhaustedError(
                        f"{self.name} refused on its allowance: {_groq_failure(error)} - nothing "
                        "further was sent to it"
                    ) from error
                transient = status is None or status in LIMIT_STATUSES or status in _RETRY_STATUS
                guidance = None
                if transient:
                    headers = getattr(getattr(error, "response", None), "headers", None)
                    guidance = retry_guidance(body, headers)
                wait = next_wait(
                    kind=kind,
                    guidance=guidance,
                    attempt=attempt,
                    waited=waited,
                    max_retries=self.config.max_retries,
                    max_wait_seconds=self.config.max_wait_seconds,
                ) if transient else None
                if wait is None:
                    raise self._refusal(
                        error,
                        transient=transient,
                        kind=kind,
                        guidance=guidance,
                        waited=waited,
                        sent=sent,
                    ) from error
                self._sleeper(wait)
                waited += wait
                attempt += 1

    def _refusal(
        self,
        error: BaseException,
        *,
        transient: bool,
        kind: Optional[str],
        guidance: Optional[float],
        waited: float,
        sent: int,
    ) -> TransportError:
        reason = _groq_failure(error)
        status = getattr(error, "status_code", None)
        spent = (f" (after {sent} send(s))" if sent > 1 else "") + patience_note(
            guidance,
            waited=waited,
            max_wait_seconds=self.config.max_wait_seconds,
            sends=sent,
        )
        if not transient:
            fatal = failure_kind(status, getattr(error, "body", None))
            if fatal == "auth":
                return ProviderAuthError(
                    f"{self.name} call failed: {reason} - the credential was refused, so nothing "
                    "further was sent"
                )
            if fatal == "model":
                return ModelUnavailableError(
                    f"{self.name} call failed: {reason} - the provider says it does not serve this "
                    "model, so nothing further was sent to it"
                )
            return TransportError(f"{self.name} call failed: {reason}")
        if kind is not None:
            return RateLimitedError(f"{self.name} call failed: {reason}" + spent)
        if status is None:
            return EndpointUnreachableError(f"{self.name} call failed: {reason}" + spent)
        return TransportError(f"{self.name} call failed: {reason}" + spent)

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

        started = time.monotonic()
        completion = self._send(kwargs)
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
        if request.json_schema:
            if not isinstance(text, str) or not text.strip():
                raise StructuredOutputError(
                    answer_diagnosis(self.name, text, message)
                    or f"{self.name} returned nothing to read as the JSON object it was asked for"
                )
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
    "gemini": GeminiProvider,
    "vllm": OpenAICompatibleProvider,
    "ollama": OpenAICompatibleProvider,
}


def build_provider(
    config: Optional[LLMConfig] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
) -> LLMProvider:
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
