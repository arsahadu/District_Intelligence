"""The public entry point: a CommonRecord goes in, a validated Incident comes out."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from intelligence.contract import Incident
from intelligence.intelligence import extract_incident
from intelligence.llm import LLMConfig, LLMProvider, build_provider
from intelligence.mapping.record_input import CONTENT_PATH


def process_record(
    record: Any,
    *,
    provider: Optional[LLMProvider] = None,
    config: Optional[LLMConfig] = None,
    environ: Optional[Mapping[str, str]] = None,
    text_keys: Sequence[str] = (CONTENT_PATH,),
    extra_keys: Sequence[str] = (),
) -> Incident:
    """Run one record through the Stage 1 chain.

    With no provider the configuration comes from ``INTELLIGENCE_LLM_*``, in the environment or
    in the repository ``.env``. If none can be built this raises ``ProviderNotConfigured``: an
    unavailable model is a failure the caller sees, never an Incident invented from nothing.
    """
    if provider is None:
        provider = build_provider(config, environ=environ)
    return extract_incident(record, provider=provider, text_keys=text_keys, extra_keys=extra_keys)


__all__ = ["Incident", "LLMConfig", "LLMProvider", "build_provider", "process_record"]
