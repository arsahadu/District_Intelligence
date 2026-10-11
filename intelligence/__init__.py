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
    SourceGeography,
    Validation,
)
from intelligence.geography import source_geography
from intelligence.intelligence import extract_incident, extraction_request, validate_extraction
from intelligence.llm import (
    GeminiProvider,
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
from intelligence.router import Mode, route
from intelligence.structured import process_record as process_structured


def __getattr__(name: str) -> Any:
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
    "GeminiProvider",
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
    "Mode",
    "OpenAICompatibleProvider",
    "PriorityLevel",
    "Provenance",
    "ProviderNotConfigured",
    "RecordContext",
    "RecordKind",
    "Relationship",
    "ReviewState",
    "SCHEMA_VERSION",
    "SourceGeography",
    "StructuredOutputError",
    "TransportError",
    "Validation",
    "build_context",
    "build_provider",
    "context",
    "contract",
    "extract_incident",
    "extraction_request",
    "geography",
    "intelligence",
    "llm",
    "load_dotenv_file",
    "models",
    "pipeline",
    "process_record",
    "process_structured",
    "records",
    "router",
    "route",
    "source_geography",
    "spans",
    "structured",
    "validate_extraction",
]
__version__ = "0.16.0"
