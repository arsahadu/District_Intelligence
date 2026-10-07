"""Collector District Intelligence - AI / Intelligence module.

Stage 1 of the LLM-first redesign: ``pipeline.process_record`` turns a CommonRecord into the
stable ``contract.Incident`` through one provider-neutral LLM call and deterministic evidence
checking. ``python -m intelligence.pipeline`` drives that boundary over the records the platform
API serves and reports the batch. Everything under ``models``, ``config``, ``extraction`` and
``mapping`` is the deterministic chain from the earlier stages. It still runs and is still reused
(spans, record reading, enums, evidence); it is frozen for new semantic rules and is replaced
stage by stage as the LLM path takes over classification, severity, status and department.

Intelligence stays inside this package: nothing here imports ingestion, backend or frontend.
"""

from __future__ import annotations

from typing import Any

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
from intelligence.context import RecordContext, build_context
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
    "pipeline",
    "intelligence",
    "llm",
    "context",
    "contract",
    "models",
    "config",
    "extraction",
    "mapping",
    "Claim",
    "ContextFact",
    "ContextType",
    "Entity",
    "EntityType",
    "EventStatus",
    "Evidence",
    "Generation",
    "Incident",
    "IncidentCategory",
    "Issue",
    "IssueCode",
    "Location",
    "PriorityLevel",
    "Provenance",
    "RecordContext",
    "RecordKind",
    "Relationship",
    "ReviewState",
    "SCHEMA_VERSION",
    "Validation",
    "GroqProvider",
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "OpenAICompatibleProvider",
    "ProviderNotConfigured",
    "StructuredOutputError",
    "TransportError",
    "build_context",
    "build_provider",
    "extract_incident",
    "extraction_request",
    "load_dotenv_file",
    "process_record",
    "validate_extraction",
]
__version__ = "0.8.0"

#: Stage 1 of the LLM-first redesign: foundation and output contract, no features yet.
INTELLIGENCE_STAGE = 1

#: Later stages replace these deterministic seams with LLM calls over the same contract.
LEGACY_DETERMINISTIC_SEAMS = (
    "intelligence.mapping.assembly",
    "intelligence.mapping.enrichment",
    "intelligence.mapping.classifier",
    "intelligence.mapping.operations",
    "intelligence.mapping.deduplication",
)
