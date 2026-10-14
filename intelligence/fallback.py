from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from pydantic import ValidationError

from intelligence.intelligence import InvalidExtraction
from intelligence.llm import (
    ENV_API_KEY,
    ENV_PREFIX,
    REASONING_EFFORTS,
    LLMConfig,
    LLMError,
    LLMProvider,
    ModelUnavailableError,
    EndpointUnreachableError,
    ProviderAuthError,
    ProviderNotConfigured,
    PROVIDERS,
    QuotaExhaustedError,
    RateLimitedError,
    StructuredOutputError,
    TransportError,
    Transport,
    load_dotenv_file,
)
from intelligence.records import MappingError

ENV_CHAIN_FILE = ENV_PREFIX + "CHAIN_FILE"

MAX_CANDIDATES = 4

CANDIDATE_KEYS = (
    "provider",
    "model",
    "base_url",
    "api_key_env",
    "timeout_seconds",
    "max_retries",
    "max_wait_seconds",
    "temperature",
    "max_tokens",
    "reasoning_effort",
)
INHERITED_KEYS = tuple(key for key in CANDIDATE_KEYS if key not in ("provider", "model", "api_key_env"))
NUMBER_KEYS = ("timeout_seconds", "max_wait_seconds", "temperature")
INTEGER_KEYS = ("max_retries", "max_tokens")

STOP_KINDS = ("quota", "auth", "rate", "unreachable", "model")


class ChainExhaustedError(LLMError):
    def __init__(self, message: str, *, kind: str, attempts: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = tuple(attempts)


class ChainFailedError(TransportError):
    def __init__(self, message: str, *, attempts: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


def classify(error: BaseException) -> str:
    if isinstance(error, QuotaExhaustedError):
        return "quota"
    if isinstance(error, RateLimitedError):
        return "rate"
    if isinstance(error, ModelUnavailableError):
        return "model"
    if isinstance(error, ProviderAuthError):
        return "auth"
    if isinstance(error, EndpointUnreachableError):
        return "unreachable"
    if isinstance(error, StructuredOutputError):
        return "output"
    if isinstance(error, InvalidExtraction):
        return "shape"
    if isinstance(error, MappingError):
        return "record"
    if isinstance(error, ValidationError):
        return "contract"
    if isinstance(error, TransportError):
        return "transient"
    if isinstance(error, LLMError):
        return "provider"
    return "provider"


def _origin(config: LLMConfig) -> str:
    parts = urlsplit(config.resolved_base_url())
    return f"{parts.scheme}://{parts.netloc}"


class Candidate:
    def __init__(self, config: LLMConfig, *, key_source: str = ENV_API_KEY) -> None:
        self.config = config
        self.key_source = key_source
        self.provider: Optional[LLMProvider] = None
        self.closed: Optional[str] = None
        self.closed_kind = ""

    @property
    def label(self) -> str:
        return f"{self.config.provider}/{self.config.model}"

    @property
    def identity(self) -> tuple[str, str]:
        return str(self.config.provider), str(self.config.model)

    @property
    def scope(self) -> str:
        return f"{_origin(self.config)}|{self.key_source}"

    def build(self, *, transport: Optional[Transport] = None) -> LLMProvider:
        if self.provider is None:
            provider_class = PROVIDERS.get(self.config.provider)
            if provider_class is None:
                raise ProviderNotConfigured(
                    f"{self.label} names provider {self.config.provider!r}; the providers implemented "
                    "here are " + ", ".join(sorted(PROVIDERS))
                )
            if transport is not None and not provider_class.accepts_transport:
                raise ProviderNotConfigured(
                    f"{self.label} is asked through the {self.config.provider} SDK, so an injected "
                    "transport cannot reach it; only a candidate this layer sends itself can be given one"
                )
            self.provider = (
                provider_class(self.config, transport=transport)
                if transport is not None
                else provider_class(self.config)
            )
        return self.provider

    def describe(self) -> str:
        return self.label + (f" (closed: {self.closed})" if self.closed else "")


@dataclass(frozen=True)
class Answered:
    result: Any
    label: str
    identity: tuple[str, str]
    attempts: tuple[str, ...] = ()


class Chain:
    def __init__(
        self,
        candidates: Sequence[Candidate],
        *,
        problems: Sequence[str] = (),
        transport: Optional[Transport] = None,
    ) -> None:
        if not candidates:
            raise ProviderNotConfigured("no candidate to ask: the model chain is empty")
        self.candidates = list(candidates)
        self.problems = list(problems)
        self.transport = transport
        self.answered_by: dict[str, int] = {}
        self._closed_scopes: set[str] = set()
        self._stop_kinds: set[str] = set()
        self._lock = threading.Lock()

    @classmethod
    def single(cls, provider: LLMProvider) -> "Chain":
        config = getattr(provider, "config", None)
        if isinstance(config, LLMConfig) and config.model:
            candidate = Candidate(config)
            candidate.provider = provider
        else:
            candidate = Candidate(
                LLMConfig(
                    provider=str(getattr(provider, "name", "llm") or "llm"),
                    model=_model_of(provider),
                )
            )
            candidate.provider = provider
        return cls([candidate])

    @property
    def identities(self) -> list[tuple[str, str]]:
        return [candidate.identity for candidate in self.candidates]

    def describe(self) -> str:
        return ", ".join(candidate.label for candidate in self.candidates)

    def available(self) -> list[Candidate]:
        with self._lock:
            return [
                candidate
                for candidate in self.candidates
                if candidate.closed is None and candidate.scope not in self._closed_scopes
            ]

    def scopes_note(self) -> str:
        with self._lock:
            rows = []
            for candidate in self.candidates:
                if candidate.closed is not None:
                    state = f"closed: {candidate.closed}"
                elif candidate.scope in self._closed_scopes:
                    state = "closed with its endpoint"
                else:
                    state = "open"
                rows.append(
                    f"{candidate.label} at {_origin(candidate.config)} "
                    f"[{candidate.key_source}]: {state}"
                )
            return "; ".join(rows)

    def read(self, ask: Callable[[LLMProvider], Any]) -> Answered:
        attempts: list[str] = []
        last: Optional[BaseException] = None
        for candidate in self.candidates:
            blocked = self._block(candidate)
            if blocked is not None:
                attempts.append(f"skipped {candidate.label}: {blocked}")
                continue
            try:
                outcome = ask(candidate.build(transport=self.transport))
            except (MappingError, ValidationError) as error:
                raise error
            except Exception as error:
                kind = classify(error)
                attempts.append(f"{candidate.label}: {kind}: {_short(error)}")
                last = error
                self._note(candidate, kind, error)
                continue
            with self._lock:
                self.answered_by[candidate.label] = self.answered_by.get(candidate.label, 0) + 1
            return Answered(
                result=outcome,
                label=candidate.label,
                identity=candidate.identity,
                attempts=tuple(attempts),
            )

        if last is None:
            raise ChainExhaustedError(
                "nothing was asked: every candidate in the chain is already closed" + _tail(attempts),
                kind=self._stop_kind(),
                attempts=tuple(attempts),
            )
        if self.available():
            raise ChainFailedError(
                f"{_short(last)} (every candidate had its turn)" + _tail(attempts),
                attempts=tuple(attempts),
            )
        raise ChainExhaustedError(
            f"{_short(last)} (no candidate left to ask)" + _tail(attempts),
            kind=self._stop_kind(),
            attempts=tuple(attempts),
        )

    def _block(self, candidate: Candidate) -> Optional[str]:
        with self._lock:
            if candidate.closed is not None:
                return candidate.closed
            if candidate.scope in self._closed_scopes:
                return "its endpoint and credential refused another model already"
        return None

    def _note(self, candidate: Candidate, kind: str, error: BaseException) -> None:
        reason = f"{kind}: {_short(error)}"
        with self._lock:
            if kind in ("quota", "rate", "auth", "unreachable"):
                self._closed_scopes.add(candidate.scope)
                self._stop_kinds.add(kind)
            if kind in ("auth", "model"):
                candidate.closed, candidate.closed_kind = reason, kind
                self._stop_kinds.add(kind)

    def _stop_kind(self) -> str:
        with self._lock:
            kinds = set(self._stop_kinds)
        for kind in STOP_KINDS:
            if kind in kinds:
                return kind
        return "unreachable"


def _model_of(provider: LLMProvider) -> str:
    try:
        return str(provider.model or "")
    except Exception:
        return ""


def _short(error: BaseException) -> str:
    return " ".join(str(error).split())[:300]


def _tail(attempts: Sequence[str]) -> str:
    if not attempts:
        return ""
    return " - turns: " + "; ".join(attempt[:220] for attempt in attempts)[:900]


def env_source(environ: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    if environ is not None:
        return dict(environ)
    load_dotenv_file()
    return dict(os.environ)


def _entries(path: str) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as error:
        raise ProviderNotConfigured(
            f"{ENV_CHAIN_FILE}={path} could not be read: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ProviderNotConfigured(
            f"{ENV_CHAIN_FILE}={path} is not JSON: {error}; it must hold a list of candidates or an "
            "object with a candidates list"
        ) from error
    found = payload.get("candidates") if isinstance(payload, Mapping) else payload
    if not isinstance(found, list) or not found:
        raise ProviderNotConfigured(
            f"{ENV_CHAIN_FILE}={path} holds no list of candidates; each entry names a provider and a "
            "model, and the credentials come from the environment, never from this file"
        )
    if len(found) > MAX_CANDIDATES:
        raise ProviderNotConfigured(
            f"{ENV_CHAIN_FILE}={path} names {len(found)} candidates, more than the {MAX_CANDIDATES} "
            "one record may walk; keep the chain to the models actually deployed"
        )
    return [item for item in found]


def _verify(
    entry: Any, number: int, source: Mapping[str, str], base: LLMConfig
) -> tuple[Optional[Candidate], str]:
    where = f"candidate {number}"
    if not isinstance(entry, Mapping):
        return None, f"{where} is {type(entry).__name__}, expected an object naming a provider and a model"
    stray = sorted(str(key) for key in entry if key not in CANDIDATE_KEYS)
    if stray:
        return None, f"{where} states {', '.join(stray)}, which is not a setting this pipeline reads"
    provider = str(entry.get("provider") or "").strip()
    if provider not in PROVIDERS:
        return None, (
            f"{where} names provider {provider!r}; the providers implemented here are "
            + ", ".join(sorted(PROVIDERS))
        )
    model = str(entry.get("model") or "").strip()
    if not model:
        return None, (
            f"{where} on provider {provider} states no model. A chain lists models that are actually "
            "deployed; nothing here guesses one"
        )
    fields: dict[str, Any] = {"provider": provider, "model": model}
    for key in INHERITED_KEYS:
        if key not in entry or entry[key] in (None, ""):
            continue
        raw = entry[key]
        if key in NUMBER_KEYS:
            try:
                fields[key] = float(raw)
            except (TypeError, ValueError) as error:
                return None, f"{where} has {key}={raw!r}, which is not a number: {error}"
            continue
        if key in INTEGER_KEYS:
            try:
                fields[key] = int(raw)
            except (TypeError, ValueError) as error:
                return None, f"{where} has {key}={raw!r}, which is not an integer: {error}"
            continue
        if key == "base_url" and not str(raw).startswith(("http://", "https://")):
            return None, f"{where} has base_url={str(raw)[:60]!r}, which is not an http(s) URL"
        if key == "reasoning_effort":
            effort = str(raw).strip().casefold()
            if effort not in REASONING_EFFORTS:
                return None, f"{where} has reasoning_effort={raw!r}, not one of {', '.join(REASONING_EFFORTS)}"
            fields[key] = effort
            continue
        fields[key] = raw

    key_source = str(entry.get("api_key_env") or ENV_API_KEY)
    api_key = (source.get(key_source) or "").strip() or None
    if provider != base.provider and not str(entry.get("base_url") or "").strip():
        fields["base_url"] = None
    config = replace(base, **fields)
    if PROVIDERS[provider].requires_api_key and not api_key:
        return None, (
            f"{where} ({provider}/{model}) needs a credential and {key_source} holds none, so this "
            "run cannot ask it"
        )
    return Candidate(replace(config, api_key=api_key), key_source=key_source), ""


def chain_named(
    environ: Optional[Mapping[str, str]] = None, chain_path: Optional[str] = None
) -> bool:
    if (chain_path or "").strip():
        return True
    return bool((env_source(environ).get(ENV_CHAIN_FILE) or "").strip())


def chain_configs(
    base: Optional[LLMConfig] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    chain_path: Optional[str] = None,
) -> tuple[list[Candidate], list[str]]:
    source = env_source(environ)
    settings = base or LLMConfig.from_env(None if environ is None else dict(environ))
    path = (chain_path or source.get(ENV_CHAIN_FILE) or "").strip()
    if not path:
        return [Candidate(settings)], []

    candidates: list[Candidate] = []
    problems: list[str] = []
    for number, entry in enumerate(_entries(path), start=1):
        candidate, problem = _verify(entry, number, source, settings)
        if candidate is None:
            problems.append(problem)
            continue
        candidates.append(candidate)
    if not candidates:
        raise ProviderNotConfigured(
            f"{ENV_CHAIN_FILE}={path} leaves no candidate to ask: {'; '.join(problems)}"
        )
    return candidates, problems


def build_chain(
    base: Optional[LLMConfig] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    chain_path: Optional[str] = None,
    transport: Optional[Transport] = None,
) -> Chain:
    candidates, problems = chain_configs(base, environ=environ, chain_path=chain_path)
    return Chain(candidates, problems=problems, transport=transport)


__all__ = [
    "Answered",
    "CANDIDATE_KEYS",
    "Chain",
    "ChainExhaustedError",
    "ChainFailedError",
    "ENV_CHAIN_FILE",
    "MAX_CANDIDATES",
    "Candidate",
    "build_chain",
    "chain_configs",
    "classify",
]
