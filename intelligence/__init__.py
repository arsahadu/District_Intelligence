"""AI / Intelligence module for Collector District Intelligence.

``pipeline.process_record`` turns a CommonRecord into the stable ``contract.Incident``: ``router`` picks
the reading the record's shape calls for, ``structured`` copies a price line's or forecast's field values
with no model, and a narrative record goes through one provider-neutral LLM call plus deterministic
evidence checking. ``geography`` hands the record's own place values to the GIS boundary.
``python -m intelligence.pipeline`` runs that over the records the platform API serves.

Nothing here imports ingestion, backend, frontend or GIS.
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
    SourceGeography,
    Validation,
)
from intelligence.geography import source_geography
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
from intelligence.router import Mode, route
from intelligence.structured import process_record as process_structured


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
__version__ = "0.11.0"
