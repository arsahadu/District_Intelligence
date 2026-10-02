"""Controlled vocabulary data for Intelligence.

``intelligence.models`` defines *structure*; this package defines *knowledge
data* - which event types exist as a taxonomy, which families they belong to,
and which departments usually own them. Later stages read these mappings
instead of hard-coding them inside extractors and classifiers, so a taxonomy
revision stays a config change rather than a code rewrite.
"""

from intelligence.config.vocabularies import (
    DEPARTMENT_LABELS,
    EVENT_TYPE_DEPARTMENTS,
    EVENT_TYPE_FAMILIES,
    EVENT_TYPE_FAMILY,
    INFORMATIONAL_EVENT_TYPES,
    SEVERITY_LEVEL_ORDER,
    TAXONOMY_VERSION,
    UNRESOLVED_EVENT_TYPES,
    departments_for,
    family_of,
    informational_event_types,
    is_informational,
    primary_department_for,
    severity_rank,
    unresolved_event_types,
    vocabulary_integrity_errors,
)

__all__ = [
    "DEPARTMENT_LABELS",
    "EVENT_TYPE_DEPARTMENTS",
    "EVENT_TYPE_FAMILIES",
    "EVENT_TYPE_FAMILY",
    "INFORMATIONAL_EVENT_TYPES",
    "SEVERITY_LEVEL_ORDER",
    "TAXONOMY_VERSION",
    "UNRESOLVED_EVENT_TYPES",
    "departments_for",
    "family_of",
    "informational_event_types",
    "is_informational",
    "primary_department_for",
    "severity_rank",
    "unresolved_event_types",
    "vocabulary_integrity_errors",
]
