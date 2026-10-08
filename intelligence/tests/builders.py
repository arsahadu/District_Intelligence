"""Shared test doubles: source text, evidence factories, and the scripted provider answers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from intelligence.llm import LLMProvider, LLMRequest, LLMResponse
from intelligence.models.enums import ExtractionMethod, SpanValidation
from intelligence.models.evidence import Evidence
from intelligence.spans import compute_field_hash
from intelligence.tests.record_fixtures import waterlogging

CONTENT = "data.content"

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


@dataclass
class Scripted(LLMProvider):
    """Answers with exactly what the test wrote. No determinism is hiding behind it."""

    payload: Any
    name: str = "scripted"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload
        return LLMResponse(
            provider=self.name,
            model=self.model_id,
            text=body,
            data=None if isinstance(self.payload, str) else self.payload,
            latency_ms=12.5,
        )


def quote(content: str, text: str, occurrence: int = 0) -> dict[str, Any]:
    """A real span: the offset of the nth copy of the text, taken from the field itself."""
    position = -1
    for _ in range(occurrence + 1):
        position = content.index(text, position + 1)
    return {
        "value": text,
        "field": CONTENT,
        "quote": text,
        "char_start": position,
        "confidence": 0.85,
    }


class NeverCalled(LLMProvider):
    """A provider that fails the test the moment anything asks it a question."""

    @property
    def model(self) -> str:
        raise AssertionError("a structured record never needs a model")

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise AssertionError("a structured record never reaches a provider")

RAIN = "நேற்று மாலை பெய்த கனமழையால் சில பகுதிகளில் சாலைகளில் தண்ணீர் தேங்கியது"
STILL_WATCHING = "கண்காணித்து வருகின்றனர்"
OFFICIALS = "அதிகாரிகள்"


def water_answer() -> dict[str, Any]:
    content = waterlogging()["data"]["content"]
    title = waterlogging()["title"]
    return {
        "record_kind": "incident",
        "title": {"value": title, "field": "title", "quote": title, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(content, RAIN),
        "incident_type": {"value": "urban_waterlogging", "field": CONTENT,
                          "quote": "தண்ணீர் தேங்கியது",
                          "char_start": content.index("தண்ணீர் தேங்கியது"), "confidence": 0.8},
        "severity": {"value": "moderate", "field": CONTENT, "quote": "கனமழையால்",
                     "char_start": content.index("கனமழையால்"), "confidence": 0.7},
        "event_status": {"value": "ongoing", "field": CONTENT, "quote": STILL_WATCHING,
                         "char_start": content.index(STILL_WATCHING), "confidence": 0.9},
        "event_time": {"value": "2026-10-03T08:30:00", "precision": "minute", "confidence": 0.8},
        "locations": [{"text": "மதுரை", "normalized_name": "Madurai", "location_type": "district",
                       "role": "event_container", "district": "Madurai",
                       "field": CONTENT, "quote": "மதுரை மாவட்டத்தில்",
                       "char_start": content.index("மதுரை மாவட்டத்தில்"), "confidence": 0.85}],
        "entities": [{"text": OFFICIALS, "entity_type": "government_official",
                      "role": "responding_authority", "field": CONTENT, "quote": OFFICIALS,
                      "char_start": content.index(OFFICIALS), "confidence": 0.8}],
        "relationships": [{"kind": "responds_to", "subject": OFFICIALS, "object": "Madurai",
                           "field": CONTENT, "quote": STILL_WATCHING,
                           "char_start": content.index(STILL_WATCHING), "confidence": 0.7}],
    }
