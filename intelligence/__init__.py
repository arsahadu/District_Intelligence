"""Collector District Intelligence - AI / Intelligence module.

Reads the external `CommonRecord` contract produced by the ingestion layer and
builds structured, evidence-anchored `Incident` objects.

Stage 1 exposes contracts only: `intelligence.models` (structure and validation)
and `intelligence.config` (controlled vocabularies). No extraction, storage or
network behaviour lives here yet.
"""

__all__ = ["models", "config"]
__version__ = "0.1.0"
