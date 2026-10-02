"""Extraction layer: deterministic primitives that future stages build on.

Stage 2 adds the evidence span primitive only. Language detection,
normalisation, time, location, actor and event extraction arrive in later
stages and will all produce Evidence through :mod:`intelligence.extraction.spans`.
"""

from __future__ import annotations

from intelligence.extraction.spans import (
    AmbiguousQuoteError,
    EmptyQuoteError,
    OccurrenceOutOfBoundsError,
    PROBABILISTIC_METHODS,
    QuoteNotFoundError,
    Span,
    SpanCheck,
    SpanError,
    SourceField,
    build_evidence,
    build_evidence_at,
    compute_field_hash,
    derive_evidence_id,
    find_spans,
    locate,
    revalidate,
    verify_evidence,
)

__all__ = [
    "AmbiguousQuoteError",
    "EmptyQuoteError",
    "OccurrenceOutOfBoundsError",
    "PROBABILISTIC_METHODS",
    "QuoteNotFoundError",
    "Span",
    "SpanCheck",
    "SpanError",
    "SourceField",
    "build_evidence",
    "build_evidence_at",
    "compute_field_hash",
    "derive_evidence_id",
    "find_spans",
    "locate",
    "revalidate",
    "verify_evidence",
]
