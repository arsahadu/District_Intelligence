"""Provider abstraction. Intelligence asks a model for structured output; it does not choose which one."""

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

#: What a reasoning endpoint will spend its completion budget on before the JSON starts.
REASONING_EFFORTS = ("none", "default", "low", "medium", "high")

#: The values an OpenAI-style ``reasoning_effort`` is given. ``none`` and ``default`` are not among
#: them: the first asks for no thinking at all and the other asks for the model's own choice, and a
#: chat endpoint that honours those reads the unified ``reasoning`` object, which is where they are
#: said. Sending ``reasoning_effort=none`` would be a value the endpoint has no vocabulary for.
EFFORT_VALUES = ("low", "medium", "high")

#: How long one request may ever spend waiting on a provider's say-so before it is given up on.
#: A busy window is worth waiting on; a wait of hours is a reason to stop and resume later.
DEFAULT_MAX_WAIT_SECONDS = 60.0

#: The step between sends when a provider refused without saying how long to wait.
BACKOFF_STEP_SECONDS = 0.2

#: A schema-constrained answer that ran out of budget is a sizing decision, not a model failure.
TRUNCATION_HINT = (
    "the answer ran out of completion budget before the JSON finished, so raise "
    f"{ENV_MAX_TOKENS} or set {ENV_REASONING_EFFORT}=low to spend less of it on reasoning"
)

#: The repository's local settings file. Intelligence reads it and never writes it.
DOTENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)

#: Endpoint names, so a deployment reads as a deployment rather than as a vendor SDK.
ALIAS_BASE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "groq": "https://api.groq.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "vllm": "http://127.0.0.1:8000/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}

#: The variable each provider's own documentation names for its credential, for a deployment that uses
#: that spelling instead of ``INTELLIGENCE_LLM_API_KEY``. The pipeline's own variable is asked first, so
#: a configuration that already sets it keeps working, and a chain entry's ``api_key_env`` still wins.
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
VENDOR_KEY_VARS = {"gemini": ENV_GEMINI_API_KEY}
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """Base for every provider failure. Callers catch this, not a vendor exception."""


class ProviderNotConfigured(LLMError):
    """No provider could be built from the environment. Never answered with a stub."""


class TransportError(LLMError):
    """The endpoint could not be reached or answered with what the protocol expects."""


class RateLimitedError(TransportError):
    """The endpoint said this window is busy, not that the record cannot be read.

    It is raised only after the bounded waiting and sends ``next_wait`` allowed, so a caller that sees
    one knows another send now would cost a request for an answer the provider already refused. The
    scheduler defers such a record rather than failing it, and a later run reads it.
    """


class QuotaExhaustedError(TransportError):
    """The endpoint said the account's allowance is spent. Another request would only spend another retry.

    Nothing here infers when it might reset: the scheduler stops asking this provider and defers what
    it did not read, so a later run can take them.
    """


class StructuredOutputError(LLMError):
    """The model answered, but not with JSON this contract can read."""


class ModelUnavailableError(TransportError):
    """The endpoint said it will not serve this model. Another model may; this one will not.

    This is a statement about the deployment, so the same candidate is asked for no further record
    in the run, and the chain moves to the next one.
    """


class ProviderAuthError(TransportError):
    """The endpoint refused the credential before it read anything. Never sent again.

    A key that was refused this minute will be refused the next, so this ends the run with a
    configuration failure rather than a deferred record.
    """


class EndpointUnreachableError(TransportError):
    """Nothing answered: the connection was refused, the host could not be found, or it timed out.

    Unlike a refused credential this may fix itself, so a record the chain could not place because an
    endpoint was down is deferred for a later run rather than failed.
    """


#: Codes that mean the allowance itself is gone, not merely this moment's request rate.
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

#: Codes that mean a window is full and the same request may be worth sending again.
RATE_LIMIT_CODES = frozenset(
    {"rate_limit_exceeded", "requests_limit_exceeded", "requests_exceeded", "model_rate_limited"}
)

#: Statuses an endpoint uses to refuse on limits. A 402 is an account-level refusal.
LIMIT_STATUSES = frozenset({402, 429})

#: A refusal on a limit that named neither an allowance nor a window: worth one more send, no more.
LIMIT_UNCLASSIFIED = "unknown"


def _named(holder: Mapping[str, Any]) -> Optional[str]:
    """The name a refusal gave itself, from whichever of its fields states one.

    An endpoint may spell its refusal a code, a type, a status word or the ``reason`` of a detail
    block, and a detail reason is the most specific of those, so it is read before a status word that
    covers every limit the account has. An HTTP status repeated as a number is not a name at all.
    """
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
    """``(code, message)`` from an error body, however the endpoint wrapped it."""
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
    """``"quota"``, ``"rate"``, ``"unknown"`` for a refusal on limits, or None for anything else.

    ``unknown`` is what an endpoint that refused with a bare ``Too Many Requests`` returns: a limit
    was hit, but nothing in the response says whether the allowance is gone or the window is merely
    busy. It is never read as an exhausted allowance, because treating every refusal that way would
    defer work the provider would have answered a moment later.

    Only what the response itself says: a code, or a message that names a quota or a rate limit.
    """
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


#: Statuses that mean the credential, not the record, was the problem.
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

#: Statuses at which an endpoint says it does not carry the model it was asked for.
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
#: Only read at 404/410, where a whole phrase about the model being absent is the endpoint's own words.
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
    """True when the refusal was about the credential. Only what the response itself says."""
    if status in AUTH_STATUSES:
        return True
    code, _ = _error_detail(body)
    return code in AUTH_CODES


def model_unavailable(status: Optional[int], body: Any) -> bool:
    """True when the refusal named the model as the thing the endpoint will not serve.

    Phrases are only read at 404 and 410: a 400 whose body happens to say "model" next to "not
    found" for some other reason must not end a candidate's run.
    """
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
    """What an endpoint's refusal was about: ``quota``, ``rate``, ``auth``, ``model`` or ``limit``.

    ``limit`` is a refusal on a limit that explained nothing further, and ``None`` is a failure that
    stated none of these - a server error, or a body that said nothing recognisable. Order matters:
    ``Rate limit reached for model X`` names a model and means a window.
    """
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
    """The typed exception for a refusal, naming the status and the body that stated it."""
    detail = f"{url} {prefix} {status}: {str(body)[:300]}"
    kind = failure_kind(status, body)
    if kind == "auth":
        return ProviderAuthError(f"{detail} - the credential was refused, so nothing further was sent")
    if kind == "model":
        return ModelUnavailableError(
            f"{detail} - the endpoint says it does not serve this model, so nothing further was sent to it"
        )
    return TransportError(detail)


#: The fields a chat endpoint may carry a model's thinking in instead of its answer.
REASONING_FIELDS = ("reasoning", "reasoning_content", "thinking")


def answer_diagnosis(provider: str, content: Any, message: Any) -> str:
    """Why there was no answer to read, in the words the response itself supports - or "" if there was.

    A model that spends the completion on thinking and leaves the message empty, and a model whose
    answer was cut off mid-object, both reach a parser as nothing to read. They are different
    failures, and the sentence that names them has to come from the response, not from a guess.
    """
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
    """One whole number from a usage block, from whichever key this endpoint happened to use."""
    for holder in (usage, usage.get("completion_tokens_details"), usage.get("output_tokens_details")):
        if not isinstance(holder, Mapping):
            continue
        for key in keys:
            value = holder.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    return None


def budget_note(usage: Mapping[str, Any], *, limit: int, effort: Optional[str]) -> str:
    """Where a cut-off completion went, from the numbers the response itself carried.

    A truncation says only that the JSON did not finish. The endpoint's own token counts say whether
    the thinking did it or the answer did, and that is the difference between raising the budget and
    asking for less thinking - so the difference is stated rather than left to a guess.
    """
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


#: The fields and headers an endpoint states a required wait in, as the provider's own SDK reads them.
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

#: A duration written next to a number: ``47.5s``, ``2m``, ``1h``, ``30 days``.
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]*)$", re.I)
#: One piece of a duration a provider wrote in parts: ``1m30s``, ``2h 5m``.
_DURATION_PART = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)", re.I)
#: The wait a refusal asks for in prose rather than a field: "try again in 30s", "will reset after 2m".
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
    """A unit written beside a number, as seconds. Longest spellings first, so ``ms`` is not ``m``."""
    text = raw.casefold()
    for spelling, seconds in _UNIT_SECONDS:
        if text.startswith(spelling):
            return seconds
    return None


def _compound_seconds(text: str) -> Optional[float]:
    """A wait a provider wrote in parts, as seconds. None if any character was not a part."""
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
    """Seconds from what a provider wrote: a number, ``47.5s``, ``1m30s``, or an HTTP date."""
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
    """The wait an error body stated, in a field or in the prose of its message."""
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
    """The error body, whatever wrapper it put its own fields inside, and any detail blocks beside them."""
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
    """How long the provider itself asked for, or None when it said nothing.

    Read only from what the refusal carried: a stated duration field, a message that names an
    interval, or a response header the SDK handed us alongside the error. Never inferred from the
    status code, and never guessed at an interval the provider did not state.
    """
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
    """Seconds to wait before sending this request again, or None when it must not be sent again.

    An exhausted allowance is never sent again. A busy window is, and it waits what the provider
    asked for when the whole remaining budget covers that; a wait of less than the stated interval
    would ask again exactly when the provider said not to, so an over-long ask stops the send and
    says so instead. When a busy window stated no interval, the budget is spread over the sends
    still permitted, which is the most waiting the configuration allows and no more. A refusal that
    explained nothing gets one further send after a short step, and a failure that was not a limit
    at all keeps the short growing step it always had.
    """
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
    """Why nothing further was sent, in the numbers the provider and the configuration used."""
    if guidance is not None and guidance > max_wait_seconds:
        return (
            f" - it asked for {guidance:g}s, which is more than the {max_wait_seconds:g}s "
            f"{ENV_MAX_WAIT} allows, so nothing further was sent"
        )
    if waited:
        return f" - {waited:g}s was waited over {sends} send(s) before it was given up on"
    return " - nothing further was sent"


def load_dotenv_file(path: Optional[str] = None) -> bool:
    """Put the local ``.env`` into ``os.environ``, leaving any real variable (even empty) alone."""
    target = path or DOTENV_PATH
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
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
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
        """Safe to log or embed in a fixture: no key, no endpoint secrets."""
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
    #: Whether this provider cannot authenticate at all without a key. A chain asks this before it
    #: asks the endpoint, so a candidate with no credential is reported as a configuration problem.
    requires_api_key: bool = False
    #: Whether a transport handed to the constructor is the one that sends. An SDK-backed provider's
    #: sends go through its own client, so a chain told that one cannot be answered from outside.
    accepts_transport: bool = False

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
    if start == -1:
        raise StructuredOutputError(
            f"{provider} returned no JSON object anywhere in its answer: {candidate[:200]!r}"
        )
    if end <= start:
        #: A brace opened and never closed: the answer was still being written when it stopped.
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
    """Reshape a Pydantic schema into what JSON-Schema strict mode demands.

    Every object closes itself, every property is required, absence is said as null, and references are
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


#: What a Gemini schema reads. A Pydantic schema states more than this - the definitions its
#: references point at, the defaults its fields fall back to, the labels a validator gave them - and
#: sending those as though the endpoint knew them is how a request is refused before it is read.
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
    """Reshape a Pydantic schema into the subset this endpoint reads, asking nothing of the answer that
    the contract does not ask anyway.

    References are inlined because there is no ``$defs`` to point at, and a field that is optional
    because one branch of its ``anyOf`` is null is said as a ``type`` array holding ``"null"``, which is
    how this endpoint writes optional. A shape that cannot be carried across - a self-referencing
    definition, a choice between two objects - is refused here rather than sent in a form the endpoint
    would silently answer differently for.
    """
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
            #: Its keys are the answer's own field names, not schema keywords: a record has a title.
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
            #: A timeout is an OSError here too, and it is what this looks like from the outside.
            raise EndpointUnreachableError(f"{url} could not be reached: {error}") from error

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send, then send again only as often and as long as the limit policy allows.

        The endpoint's own wait is honoured when its error body states one; a header-only wait is not
        visible here, because a transport hands back the status and the body alone. A refusal that
        named the credential or the model ends the sends at once: neither is a condition a further
        send of the same request could change.
        """
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


#: The least thinking this endpoint will be asked for. It has no word for ``none``, so a configuration
#: that asks for none is answered with the floor the endpoint offers rather than a value it would refuse.
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
    """The answer text and the words naming where it was found, or None beside what was searched.

    ``output_text`` is what the endpoint offers as the finished answer. Where a response carries only
    ``steps``, the last one holding text is the answer rather than an earlier turn, and a response with
    neither says which of them was looked for instead of arriving as an empty string that parses to nothing.
    """
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
    """Gemini's interactions endpoint: Google's own request dialect, this layer's send policy.

    Carried over from the OpenAI-compatible provider: the transport, the bounded waiting, the wait a
    refusal asked for, and which refusals end the sending. Replaced: the header the credential travels
    in, the names of the token budget and the thinking level, where the answer sits in the response, and
    the schema subset it is sent in.
    """

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
                    f"{ENV_API_KEY} is not set, so groq cannot authenticate. Export it or put it "
                    "in the local .env, or pass an LLMConfig with a key."
                )
            from groq import Groq

            client = Groq(
                api_key=config.api_key,
                base_url=_groq_base_url(config),
                timeout=config.timeout_seconds,
                # This layer decides how often and how long to send again, so the SDK must not add
                # sends of its own on top of them.
                max_retries=0,
            )
        self.config = config
        self._client = client
        self._sleeper = sleeper

    @property
    def model(self) -> str:
        return str(self.config.model)

    def _send(self, kwargs: dict[str, Any]) -> Any:
        """Ask once, then again only as often and as long as the limit policy allows.

        Groq states its waits in ``retry-after`` and ``x-ratelimit-reset-*`` headers, which the SDK
        hands back on the exception it raises, so they are read here along with the error body.
        """
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
        """How this send ends: what the provider said, plus what this run already spent waiting.

        Which error it becomes is what the chain acts on: a refused credential or an unserved model
        ends this candidate, a busy window or a dead endpoint is a reason to read the record later.
        """
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
