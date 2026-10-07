"""Collector District Intelligence - AI / Intelligence module.

``pipeline.process_record`` turns a CommonRecord into the stable ``contract.Incident`` through one
provider-neutral LLM call and deterministic evidence checking. ``python -m intelligence.pipeline``
drives that boundary over the records the platform API serves and reports the batch.

``records`` reads a CommonRecord, ``context`` makes it citable, ``spans`` grounds every quote,
``intelligence`` builds and validates the one LLM answer, ``contract`` is the output shape and
``models`` is the pydantic vocabulary they share.

Intelligence stays inside this package: nothing here imports ingestion, backend or frontend.
"""

from __future__ import annotations

from typing import Any

from intelligence.context import RecordContext, build_context
from intelligence.contract import (
    Claim,
    ContextFact,
    ContextType,
    Entity,
    EntityType,
    EventStatus,
    Evidence,
    Generation,
    Incident,
    IncidentCategory,
    Issue,
    IssueCode,
    Location,
    PriorityLevel,
    Provenance,
    RecordKind,
    Relationship,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.intelligence import extract_incident, extraction_request, validate_extraction
from intelligence.llm import (
    GroqProvider,
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAICompatibleProvider,
    ProviderNotConfigured,
    StructuredOutputError,
    TransportError,
    build_provider,
    load_dotenv_file,
)


def __getattr__(name: str) -> Any:
    """``process_record`` is resolved lazily: importing it here would make
    ``python -m intelligence.pipeline`` re-import the module it is about to run and warn about it."""
    if name == "process_record":
        from intelligence.pipeline import process_record

        return process_record
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Claim",
    "ContextFact",
    "ContextType",
    "Entity",
    "EntityType",
    "EventStatus",
    "Evidence",
    "Generation",
    "GroqProvider",
    "Incident",
    "IncidentCategory",
    "Issue",
    "IssueCode",
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "Location",
    "OpenAICompatibleProvider",
    "PriorityLevel",
    "Provenance",
    "ProviderNotConfigured",
    "RecordContext",
    "RecordKind",
    "Relationship",
    "ReviewState",
    "SCHEMA_VERSION",
    "StructuredOutputError",
    "TransportError",
    "Validation",
    "build_context",
    "build_provider",
    "context",
    "contract",
    "extract_incident",
    "extraction_request",
    "intelligence",
    "llm",
    "load_dotenv_file",
    "models",
    "pipeline",
    "process_record",
    "records",
    "spans",
    "validate_extraction",
]
__version__ = "0.9.0"
