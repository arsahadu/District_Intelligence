"""Shared source text and evidence factory for the span and evidence tests."""

from __future__ import annotations

from intelligence.models.enums import ExtractionMethod, SpanValidation
from intelligence.models.evidence import Evidence
from intelligence.spans import compute_field_hash

RECORD_ID = "NEWS-MDU-TEST-0001"
SOURCE_ID = "dinamalar"
SOURCE_TYPE = "news"
SOURCE_URL = "https://www.dinamalar.com/district/291"

#: Mirrors the real corpus: publish boilerplate, then Tamil body text.
CONTENT_TA = (
    "UPDATED : அக் 02, 2026 12:00 AM ADDED : அக் 01, 2026 09:46 PM "
    "அவர் லேண்ட் நிறுவன ஒப்பந்தத்தை ரத்து செய்யக்கோரி துாய்மைப் பணியாளர்கள் "
    "மதுரையில் உள்ள மாநகராட்சி அலுவலகத்தில் 4800 மனுக்களை கமிஷனரிடம் அளித்தனர். "
    "தொழிலாளர் நல வாரிய ஊழியர்கள் ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்."
)

#: A Kumbakonam incident reported through the Madurai bench of the High Court.
BENCH_CONTENT_TA = (
    "கும்பகோணத்தில் அரசின் மதுபான விடுதியை காலி செய்ய மறுத்ததால் விடுதி "
    "உரிமையாளர் மீது வழக்கு தொடரப்பட்டது; உயர்நீதிமன்ற மதுரைக் கிளை "
    "விசாரணைக்கு இடைக்கால தடை விதித்தது."
)


def span_evidence(
    evidence_id: str,
    *,
    field: str = "data.content",
    source_text: str = CONTENT_TA,
    quote: str,
    method: ExtractionMethod = ExtractionMethod.RULE,
    confidence: float = 0.95,
    record_id: str = RECORD_ID,
    **overrides,
) -> Evidence:
    """Build evidence with offsets computed from the real text."""
    start = source_text.index(quote)
    payload = {
        "evidence_id": evidence_id,
        "record_id": record_id,
        "source_id": SOURCE_ID,
        "source_type": SOURCE_TYPE,
        "field": field,
        "method": method,
        "quote": quote,
        "char_start": start,
        "char_end": start + len(quote),
        "field_text_hash": compute_field_hash(source_text),
        "span_validation": SpanValidation.VALIDATED,
        "source_url": SOURCE_URL,
        "raw_reference": SOURCE_URL,
        "confidence": confidence,
    }
    payload.update(overrides)
    return Evidence(**payload)
