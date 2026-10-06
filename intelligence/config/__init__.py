"""Controlled vocabulary data for the deterministic chain.

Cue lists here assign event types, severities and statuses by wording. That semantic work moves
to the LLM (see intelligence/README.md), so these tables are frozen: fix a defect, do not extend
a list. The enumerations the output contract shares live in intelligence.models.
"""

from intelligence.config.event_type_cues import (
    CUE_LEXICON_VERSION,
    EVENT_TYPE_CUES,
    cue_lexicon_integrity_errors,
)
from intelligence.config.mention_words import (
    LEXICON_VERSION,
    mention_lexicon_integrity_errors,
)
from intelligence.config.operations_cues import (
    OPERATIONS_LEXICON_VERSION,
    operations_integrity_errors,
)
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
    "CUE_LEXICON_VERSION",
    "DEPARTMENT_LABELS",
    "EVENT_TYPE_CUES",
    "EVENT_TYPE_DEPARTMENTS",
    "EVENT_TYPE_FAMILIES",
    "EVENT_TYPE_FAMILY",
    "INFORMATIONAL_EVENT_TYPES",
    "LEXICON_VERSION",
    "OPERATIONS_LEXICON_VERSION",
    "SEVERITY_LEVEL_ORDER",
    "TAXONOMY_VERSION",
    "UNRESOLVED_EVENT_TYPES",
    "departments_for",
    "cue_lexicon_integrity_errors",
    "family_of",
    "informational_event_types",
    "is_informational",
    "mention_lexicon_integrity_errors",
    "operations_integrity_errors",
    "primary_department_for",
    "severity_rank",
    "unresolved_event_types",
    "vocabulary_integrity_errors",
]
