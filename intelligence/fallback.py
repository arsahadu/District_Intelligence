"""The ordered model chain: who else is asked when the configured model cannot answer, and what each
refusal means.

One model is the configuration the project has always had, and it stays the default: with no chain
named, this module hands back exactly that one candidate and behaves exactly as a single provider
does. A chain is only ever a list of *configured* candidates - each one states a provider, a model
and the environment variable holding its credential - because a fallback to a model nobody verified
is a second way to fail, and a fallback to another model behind the same exhausted allowance is a
request that cannot succeed.

What decides whether the next candidate is asked is the kind of refusal, read here and nowhere else:

``quota`` / ``rate``   the endpoint refused on its allowance or its window. Every candidate sharing
                        that endpoint and credential is closed for the run: the refusal was addressed
                        to the account, not to the model. A candidate on another account may still
                        answer, and if none can, the record is deferred for a later run.
``auth``               the credential was refused. Nothing about the next record will change that, and
                        every model behind that credential is unreachable for the same reason.
``model``              this endpoint will not serve this model. The endpoint is fine; the next
                        candidate is asked, and this one is not asked again this run.
``unreachable``        the connection failed or timed out. A later run may find it up, so records the
                        chain could not place are deferred, not failed.
``output`` / ``shape`` the model answered and what it answered with cannot be read. Worth another
                        model's turn on this record: nothing is repaired, and the failure is kept.
``record``             the record itself could not be read. Another model would fail on it the same
                        way, so the chain stops and the orchestrator's own diagnosis stands.
"""

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

#: A JSON file naming the candidates, in the order they are asked. Unset means one candidate: the
#: ``INTELLIGENCE_LLM_*`` configuration exactly as it stands.
ENV_CHAIN_FILE = ENV_PREFIX + "CHAIN_FILE"

#: One record walks the whole chain, so a long chain is a long wait rather than a better answer.
MAX_CANDIDATES = 4

#: The keys one candidate may state. Anything else in the file is a mistake, not an unknown default.
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
#: Carried over from the ``INTELLIGENCE_LLM_*`` configuration unless the candidate states its own.
INHERITED_KEYS = tuple(key for key in CANDIDATE_KEYS if key not in ("provider", "model", "api_key_env"))
NUMBER_KEYS = ("timeout_seconds", "max_wait_seconds", "temperature")
INTEGER_KEYS = ("max_retries", "max_tokens")

#: How a refusal that ended the asking is described, strongest first. A spent allowance outranks a
#: busy window because nothing that helps a window helps an exhausted account, and a refused
#: credential is named before either: no later run reads anything until it is fixed.
STOP_KINDS = ("quota", "auth", "rate", "unreachable", "model")


class ChainExhaustedError(LLMError):
    """No candidate is left that this run could ask, so what was not read is left for a later one.

    ``kind`` names the refusal that ended the asking: a spent allowance, a busy window, an endpoint
    that is down, a credential refused or a model nobody serves. None of them is a statement about
    the record, which is why the records after it are deferred rather than failed.
    """

    def __init__(self, message: str, *, kind: str, attempts: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = tuple(attempts)


class ChainFailedError(TransportError):
    """Every candidate had its turn at this record and none produced an answer that could be read.

    The candidates are still worth asking - the refusal was about this record, not about the account -
    so the run continues and this record is reported as a failure naming each attempt.
    """

    def __init__(self, message: str, *, attempts: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


def classify(error: BaseException) -> str:
    """Which of the documented refusals this is. Order matters: each type is more specific than the last."""
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
    """One configured model, its own turn at a record, and the reason it stopped taking turns."""

    def __init__(self, config: LLMConfig, *, key_source: str = ENV_API_KEY) -> None:
        self.config = config
        self.key_source = key_source
        self.provider: Optional[LLMProvider] = None
        #: Set when this candidate must not be asked again this run, with the reason it was set.
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
        """The account this candidate shares its limits with: one endpoint, one credential.

        Two models behind one key are one allowance, which is what a provider's own refusal says when
        it names the project rather than the model. Nothing here assumes two candidates on different
        endpoints are independent until one of them has refused.
        """
        return f"{_origin(self.config)}|{self.key_source}"

    def build(self, *, transport: Optional[Transport] = None) -> LLMProvider:
        """The provider object, made once. Configuration that cannot produce one is refused by name."""
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
    """What a record came back with, and which candidate the turn was worth crediting."""

    result: Any
    label: str
    identity: tuple[str, str]
    #: Every candidate that was skipped or refused before this one, in order, with the reason.
    attempts: tuple[str, ...] = ()


class Chain:
    """The candidates one run may ask, in order, and what each of them has already refused.

    State is per run: a scope that refused on its allowance stays closed for every later record, so a
    busy endpoint costs one refusal rather than one per record. It is a run's state, not a durable
    one - the next run asks again, which is the whole point of deferring instead of failing.
    """

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
        #: Candidates the configuration named but this run could not verify, so they were dropped.
        self.problems = list(problems)
        self.transport = transport
        self.answered_by: dict[str, int] = {}
        self._closed_scopes: set[str] = set()
        self._stop_kinds: set[str] = set()
        self._lock = threading.Lock()

    @classmethod
    def single(cls, provider: LLMProvider) -> "Chain":
        """One provider, already built: what a caller that passes ``provider=`` means."""
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
        """Each account the chain was configured against and what it has refused so far.

        Names the endpoint and the variable holding the credential, never the credential: two models
        at one endpoint under one variable are one allowance, and that is what a report has to show
        for a reader to see why a second model was not asked.
        """
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
        """Ask each candidate in turn until one produces an answer. One send per candidate, at most.

        ``ask`` is the whole record read - provider call, grounding and contract assembly - because
        whether a candidate succeeded is decided by the validated result, not by the HTTP status.
        """
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
                #: This record's own read, whoever was asked. Another model fails the same way.
                raise error
            except Exception as error:  # noqa: BLE001 - every provider failure is classified, none repaired
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
        """Why this candidate is not asked, from what an earlier record's refusal said."""
        with self._lock:
            if candidate.closed is not None:
                return candidate.closed
            if candidate.scope in self._closed_scopes:
                return "its endpoint and credential refused another model already"
        return None

    def _note(self, candidate: Candidate, kind: str, error: BaseException) -> None:
        """What a refusal means for the rest of the run. Only refusals about the endpoint stick."""
        reason = f"{kind}: {_short(error)}"
        with self._lock:
            if kind in ("quota", "rate", "auth", "unreachable"):
                #: A limit, a credential and a dead socket all belong to the endpoint, not to the
                #: model: every candidate sharing that endpoint and key is refused for this run.
                self._closed_scopes.add(candidate.scope)
                self._stop_kinds.add(kind)
            if kind in ("auth", "model"):
                candidate.closed, candidate.closed_kind = reason, kind
                self._stop_kinds.add(kind)

    def _stop_kind(self) -> str:
        """The refusal to name in the report, strongest first."""
        with self._lock:
            kinds = set(self._stop_kinds)
        for kind in STOP_KINDS:
            if kind in kinds:
                return kind
        return "unreachable"


def _model_of(provider: LLMProvider) -> str:
    try:
        return str(provider.model or "")
    except Exception:  # noqa: BLE001 - a double that cannot name its model names nothing
        return ""


def _short(error: BaseException) -> str:
    """One line of what the provider actually said, with no request and therefore no credential."""
    return " ".join(str(error).split())[:300]


def _tail(attempts: Sequence[str]) -> str:
    """Every turn the chain took, kept in the message that reports it, so the reason for a fallback
    survives into the checkpoint instead of living only in the run that made it."""
    if not attempts:
        return ""
    return " - turns: " + "; ".join(attempt[:220] for attempt in attempts)[:900]


def env_source(environ: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    """The environment the chain is read from: the file first, then the real variables.

    An explicit ``environ`` is used as-is and never touches ``.env``, the same rule ``LLMConfig``
    follows, so a test cannot inherit a live key.
    """
    if environ is not None:
        return dict(environ)
    load_dotenv_file()
    return dict(os.environ)


def _entries(path: str) -> list[Any]:
    """The candidates a chain file names, or the configuration error that stops the run."""
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
    """One entry as a candidate, or the reason this run will not ask it.

    A candidate states what makes it a different reader - provider, model, endpoint, credential - and
    inherits the rest of ``INTELLIGENCE_LLM_*``, so one budget and one timeout govern the whole chain
    unless an entry says otherwise.
    """
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
        #: A candidate on another service is reached at that service's own address. Inheriting the
        #: configured endpoint would send the fallback to the machine that has just refused it.
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
    """Whether a chain file was stated, without reading it.

    A run that was never pointed at one keeps the single reader it always had, so a chain file that
    cannot be read cannot abort a batch whose records need no reading at all.
    """
    if (chain_path or "").strip():
        return True
    return bool((env_source(environ).get(ENV_CHAIN_FILE) or "").strip())


def chain_configs(
    base: Optional[LLMConfig] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    chain_path: Optional[str] = None,
) -> tuple[list[Candidate], list[str]]:
    """The candidates this configuration names, in the order they are asked, plus what was dropped.

    Called before a run decides what it still has to read: it names no endpoint and asks no question,
    so a run that reuses everything it selected never authenticates a model.
    """
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
        #: A chain that asks nothing is a configuration failure, not a run that reads without a model.
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
    """The chain a run asks: the file's candidates, or the one model ``INTELLIGENCE_LLM_*`` names."""
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
