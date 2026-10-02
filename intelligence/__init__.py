"""Collector District Intelligence - AI / Intelligence module.

Reads the external `CommonRecord` contract produced by the ingestion layer and
builds structured, evidence-anchored `Incident` objects.

Stage 1 exposed the contracts: `intelligence.models` (structure and validation)
and `intelligence.config` (controlled vocabularies). Stage 2 adds
`intelligence.extraction.spans`, the evidence span primitive that later
extraction stages must produce Evidence through.

Still no CommonRecord -> Incident pipeline: field resolution and record
iteration are a later stage, and nothing here reads a network, a database or a
file.
"""

__all__ = ["models", "config", "extraction"]
__version__ = "0.2.0"
