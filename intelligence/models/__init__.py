"""Public contract surface for the Intelligence module.

Stage 1 deliberately contains structure and validation only. No extraction,
storage, network or model-calling code belongs in this package.
"""

from intelligence.models.actors import Actor
from intelligence.models.base import Confidence, OptionalConfidence, Ratio, StrictModel
from intelligence.models.classification import ClassificationInfo, DepartmentHint, RelevanceInfo
from intelligence.models.evidence import Evidence
from intelligence.models.incident import SCHEMA_VERSION, Incident
from intelligence.models.language import (
    MULTILINGUAL,
    UNKNOWN_LANGUAGE,
    LanguageDetection,
    LanguageInfo,
    LocalizedText,
    SummaryInfo,
    TextRepresentation,
    TitleInfo,
)
from intelligence.models.metadata import (
    ConfidenceSummary,
    DedupMetadata,
    ProcessingMetadata,
    ReviewInfo,
)
from intelligence.models.quantities import Observation
from intelligence.models.severity import Severity, SeveritySignal
from intelligence.models.spatial import GisResolution, LocationMention, SpatialHint
from intelligence.models.temporal import TimeValue

__all__ = [
    "SCHEMA_VERSION",
    "Actor",
    "ClassificationInfo",
    "Confidence",
    "ConfidenceSummary",
    "DedupMetadata",
    "DepartmentHint",
    "Evidence",
    "GisResolution",
    "Incident",
    "LanguageDetection",
    "LanguageInfo",
    "LocationMention",
    "LocalizedText",
    "MULTILINGUAL",
    "Observation",
    "OptionalConfidence",
    "ProcessingMetadata",
    "Ratio",
    "RelevanceInfo",
    "ReviewInfo",
    "Severity",
    "SeveritySignal",
    "SpatialHint",
    "StrictModel",
    "SummaryInfo",
    "TextRepresentation",
    "TimeValue",
    "TitleInfo",
    "UNKNOWN_LANGUAGE",
]
