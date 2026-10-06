"""Collector District Intelligence - AI / Intelligence module.

Stage 1 of the LLM-first redesign: ``pipeline.process_record`` turns a CommonRecord into the
stable ``contract.Incident`` through one provider-neutral LLM call and deterministic evidence
checking. Everything under ``models``, ``config``, ``extraction`` and ``mapping`` is the
deterministic chain from the earlier stages. It still runs and is still reused (spans, record
reading, enums, evidence); it is frozen for new semantic rules and is replaced stage by stage as
the LLM path takes over classification, severity, status and department.

Intelligence stays inside this package: nothing here imports ingestion, backend or frontend.
"""

from __future__ import annotations

from intelligence.contract import (
    Claim,
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
from intelligence.pipeline import process_record

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
__version__ = "0.7.0"

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
