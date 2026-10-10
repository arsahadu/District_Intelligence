"""The six Madurai news records the live batch read with spans it could not ground, replayed offline.

Each body below is a sanitized article of the same shape as the real one, holding the phrases the real
ones repeat and the places the real ones named twice. Each answer is scripted or served by an injected
transport, so no request leaves this process. What is asserted is the offset arithmetic, the grounding,
the routing and the honest uncertainty - not what the correct classification of a real article would
be, which is not this file's to decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from intelligence.checkpoint import Checkpoint
from intelligence.context import build_context
from intelligence.contract import (
    EventStatus,
    IncidentCategory,
    IssueCode,
    ReviewState,
)
from intelligence.intelligence import extract_incident, prompt_text
from intelligence.llm import LLMProvider, LLMRequest, LLMResponse
from intelligence.models.enums import EventType, ExtractionMethod, SeverityLevel
from intelligence.pipeline import parse_args, run_pipeline
from intelligence.router import Mode, route
from intelligence.spans import compute_field_hash, find_spans, verify_evidence
from intelligence.tests.builders import CONTENT, Scripted, quote, responder, listing, single
from intelligence.tests.record_fixtures import base_record
from intelligence.tests.test_gemini import answer as gemini_body
from intelligence.tests.test_gemini import provider as gemini

BASE = "http://backend.test"

UTSAV_ID = "NEWS-MDU-fbb1b8b1bd809612"
CASE_ID = "NEWS-MDU-3eda6936fb2742e5"
DEAD_ID = "NEWS-MDU-caf0c933c1a793e2"
INSPECT_ID = "NEWS-MDU-d7e026050541d904"
WALK_ID = "NEWS-MDU-37de736e0fb1c6cc"
HILL_ID = "NEWS-MDU-cc93365b11a62ad1"

UTSAV_TITLE = "காஞ்சி மஹா பெரியவரின் அனுஷ உற்ஸவம்"
CASE_TITLE = "பள்ளி மாணவர் இறந்த வழக்கு சி.பி.சி.ஐ.டி., க்கு மாற்றம்"
DEAD_TITLE = "விபத்தில் ஒருவர் பலி: மதுரை அருகே லாரி மோதல்"
INSPECT_TITLE = "மாவட்ட ஆட்சியர் நேரில் ஆய்வு: குடிநீர் இணைப்பு பணி"
WALK_TITLE = "தண்ணீரில் நடக்கிறோம் தரையிலே மிதக்கிறோம்: ஐந்தாண்டுகளாக அவலம்"
HILL_TITLE = "திருப்பரங்குன்றம் மலை மேல் குமாரருக்கு வேல் எடுக்கும் திருவிழா"

UTSAV_BODY = (
    "UPDATED : அக் 03, 2026 12:00 AM ADDED : அக் 02, 2026 05:45 PM மதுரை: மதுரை கூடல் நகர் "
    "மஹா பெரியவா கோயிலில் அனுஷத்தின் அனுகிரகம் அமைப்பு சார்பில் காஞ்சி மஹா பெரியவரின் அனுஷ "
    "உற்ஸவம் நடந்தது. அனுஷத்தின் அனுகிரகம் சார்பில் பல்லக்கு ஊர்வலம் நடந்தது. மதுரை மாவட்ட "
    "போலீசார் பாதுகாப்பு ஏற்பாடுகளை செய்திருந்தனர். சண்முக திருக்குமரன் கலந்து கொண்டு பக்தர்களுக்கு "
    "ஆசிர்வாதம் அளித்தார். இறுதி நிகழ்ச்சி மிகவும் சிறப்பாக நடந்தது."
)
CASE_BODY = (
    "ADDED : அக் 03, 2026 07:40 AM மதுரை: மதுரை கூடல் நகரை சேர்ந்த பள்ளி மாணவர் இறந்த நிலையில் "
    "மீட்கப்பட்டார். மாணவர் கொலை செய்யப்பட்டு இருக்கலாம் என்று சந்தேகிக்கப்படுகிறது. கமிஷனர் "
    "ராஜேந்திரன் பரிந்துரையை ஏற்று வழக்கை சி.பி.சி.ஐ.டி., க்கு மாற்றி டி.ஜி.பி., மகேஷ்குமார் "
    "அகர்வால் உத்தரவிட்டுள்ளார்."
)
DEAD_BODY = (
    "ADDED : அக் 03, 2026 06:15 AM மதுரை அருகே திருப்பரங்குன்றம் வெளியசரவக்கணம் பகுதியில் லாரி "
    "மோதிய விபத்தில் ஒருவர் சம்பவ இடத்திலேயே பலியானார்; மற்றொருவர் படுகாயமடைந்தார். தீயணைப்பு "
    "படையினர் உடனை மீட்டு அரசு மருத்துவமனைக்கு அனுப்பி வைத்தனர்."
)
INSPECT_BODY = (
    "ADDED : அக் 03, 2026 08:05 AM மதுரை: கீழவாசல் 100 அடி தெருவில் நடைபெறும் குடிநீர் இணைப்பு "
    "பணிகளை மாவட்ட ஆட்சியர் செந்தில்நாதன் நேரில் ஆய்வு செய்தார். தாமதத்திற்கு காரணமான நிறுவனத்திற்கு "
    "நோட்டீஸ் அனுப்ப உத்தரவிட்டார். பணி முடியாத நிலையில் உள்ளது என்று அப்பகுதி மக்கள் கூறினர்."
)
WALK_BODY = (
    "ADDED : அக் 03, 2026 07:20 AM மதுரை அருகே நாகமலைபுதுக்கோட்டை ஊராட்சி என்.ஜி.ஓ., காலனியில் "
    "5 ஆண்டுகளாக தேங்கி நிற்கும் தண்ணீரால் அப்பகுதியினர் அவதியுறுகின்றனர். என்.ஜி.ஓ., காலனி மக்கள் "
    "குழந்தைகள் பள்ளிக்கு செல்ல தண்ணீரில் நடக்க வேண்டிய அவலம் தொடர்கிறது. ராஜேந்திர பிரசாத் தெரு "
    "மற்றும் கீழக்குயில்குடி மெயின் ரோடு பகுதிகளில் தண்ணீர் தேங்கியுள்ளது. ராஜேந்திர பிரசாத் தெரு "
    "மக்கள் மாவட்ட நிர்வாகத்திடம் பலமுறை புகார் அளித்தனர். முறையான வடிகால் அமைக்காதது, ஆக்கிரமிப்பு "
    "காரணங்களால் தண்ணீர் தேங்கியுள்ளது என்று அதிகாரிகளிடம் கூறப்பட்டது."
)
HILL_BODY = (
    "ADDED : அக் 02, 2026 05:30 PM மதுரை மாவட்டம் திருப்பரங்குன்றத்தில் மலை மேல் கதம்ப சாதம் "
    "தயாரித்து பக்தர்களுக்கு வழங்குவதற்கு தடை விதிக்கப்பட்டது. அதனால் கிராம மக்கள் ஆர்ப்பாட்டத்தில் "
    "ஈடுபட்டனர். மத்திய தொல்லியல் துறை அனுமதி மறுத்ததாக குற்றம் சாட்டப்பட்டது. கோயில் அலுவலகத்தை "
    "முற்றுகையிட்டனர். நீதிமன்ற உத்தரவை தொடர்ந்து மக்கள் மலைமேல் வேல் கொண்டு சென்றனர். மத்திய "
    "தொல்லியல் துறை அதிகாரி இது குறித்து விளக்கம் அளித்தார். திருப்பரங்குன்றத்தில் போலீசார் "
    "குவிக்கப்பட்டனர். இதை தொடர்ந்து பொதுமக்கள் மீண்டும் ஆர்ப்பாட்டத்தில் ஈடுபட்டனர். மத்திய "
    "தொல்லியல் துறை சார்பில் பாதுகாப்பு அளவிடப்பட்டது."
)

#: The phrases the live articles kept repeating, counted in the bodies that stand in for them.
REPEATED = {
    UTSAV_ID: {"நடந்தது": 3, "மதுரை": 3, "அனுஷத்தின் அனுகிரகம்": 2},
    CASE_ID: {"மதுரை": 2},
    DEAD_ID: {},
    INSPECT_ID: {},
    WALK_ID: {"என்.ஜி.ஓ., காலனி": 2, "ராஜேந்திர பிரசாத் தெரு": 2},
    HILL_ID: {"ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்": 2, "திருப்பரங்குன்றத்தில்": 2, "மத்திய தொல்லியல் துறை": 3},
}


def _news(record_id: str, title: str, body: str, *, stamp: str, slug: str) -> dict[str, Any]:
    url = f"https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/{slug}"
    return base_record(
        record_id=record_id,
        title=title,
        event_time=stamp,
        source_url=url,
        raw_reference=url,
        data={"content": body, "language": "ta"},
    )


SIX: dict[str, dict[str, Any]] = {
    UTSAV_ID: _news(UTSAV_ID, UTSAV_TITLE, UTSAV_BODY, stamp="2026-10-02T17:45:00",
                    slug="anusha-utsavam/4340185"),
    CASE_ID: _news(CASE_ID, CASE_TITLE, CASE_BODY, stamp="2026-10-03T07:40:00",
                   slug="schoolboy-death-case/4340201"),
    DEAD_ID: _news(DEAD_ID, DEAD_TITLE, DEAD_BODY, stamp="2026-10-03T06:15:00", slug="one-dead/4340240"),
    INSPECT_ID: _news(INSPECT_ID, INSPECT_TITLE, INSPECT_BODY, stamp="2026-10-03T08:05:00",
                      slug="collector-s-inspection/4340256"),
    WALK_ID: _news(WALK_ID, WALK_TITLE, WALK_BODY, stamp="2026-10-03T07:20:00",
                   slug="we-walk-on-water/4340288"),
    HILL_ID: _news(HILL_ID, HILL_TITLE, HILL_BODY, stamp="2026-10-02T17:30:00",
                   slug="tirupparankundram-hill/4340190"),
}

BODY = {record_id: record["data"]["content"] for record_id, record in SIX.items()}


def utsav_answer() -> dict[str, Any]:
    """The live reading: a headline copied, everything else pointed at a word the article says again."""
    body = BODY[UTSAV_ID]
    return {
        "record_kind": "incident",
        "title": {"value": UTSAV_TITLE, "field": "title", "quote": UTSAV_TITLE, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(body, "காஞ்சி மஹா பெரியவரின் அனுஷ உற்ஸவம் நடந்தது"),
        "event_status": {"value": "action_taken", "field": CONTENT, "quote": "நடந்தது",
                         "char_start": 328, "confidence": 0.7},
        "locations": [{"text": "மதுரை", "location_type": "district", "role": "event_container",
                       "field": CONTENT, "quote": "மதுரை", "char_start": 227, "confidence": 0.8}],
        "entities": [{"text": "அனுஷத்தின் அனுகிரகம் அமைப்பு", "entity_type": "organization",
                      "role": "other", "field": CONTENT, "quote": "அனுஷத்தின் அனுகிரகம்",
                      "char_start": 272, "confidence": 0.7}],
    }


def case_answer() -> dict[str, Any]:
    body = BODY[CASE_ID]
    return {
        "record_kind": "incident",
        "title": {"value": CASE_TITLE, "field": "title", "quote": CASE_TITLE, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(body, "மாணவர் கொலை செய்யப்பட்டு இருக்கலாம்"),
        "incident_type": quote(body, "வழக்கை சி.பி.சி.ஐ.டி., க்கு மாற்றி")
        | {"value": "property_crime"},
        "locations": [
            {"text": "கூடல் நகர்", "location_type": "locality", "role": "event_location",
             "field": CONTENT, "quote": "கூடல் நகரை", "confidence": 0.85},
            {"text": "மதுரை", "location_type": "district", "role": "event_container",
             "field": CONTENT, "quote": "மதுரை", "char_start": 259, "confidence": 0.8},
        ],
    }


def dead_answer() -> dict[str, Any]:
    """The live reading of the lorry crash: the record's own headline, handed back with no field named."""
    body = BODY[DEAD_ID]
    return {
        "record_kind": "incident",
        "title": {"value": DEAD_TITLE, "confidence": 0.9},
        "description": quote(body, "லாரி மோதிய விபத்தில் ஒருவர் சம்பவ இடத்திலேயே பலியானார்"),
        "incident_type": quote(body, "படுகாயமடைந்தார்") | {"value": "vehicle_accident"},
    }


def inspect_answer() -> dict[str, Any]:
    """The collector inspection, its headline rewritten in English and still named no field."""
    body = BODY[INSPECT_ID]
    return {
        "record_kind": "incident",
        "title": {"value": "District collector inspects drinking water works", "confidence": 0.9},
        "description": quote(body, "குடிநீர் இணைப்பு பணிகளை மாவட்ட ஆட்சியர் செந்தில்நாதன் நேரில் "
                                   "ஆய்வு செய்தார்"),
        "event_status": quote(body, "நோட்டீஸ் அனுப்ப உத்தரவிட்டார்") | {"value": "action_taken"},
    }


def walk_answer() -> dict[str, Any]:
    """Two colonies named twice, one street named twice, and an actor the article never called this."""
    body = BODY[WALK_ID]
    return {
        "record_kind": "incident",
        "title": {"value": WALK_TITLE, "field": "title", "quote": WALK_TITLE, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(body, "தேங்கி நிற்கும் தண்ணீரால் அப்பகுதியினர் அவதியுறுகின்றனர்"),
        "incident_type": quote(body, "முறையான வடிகால் அமைக்காதது")
        | {"value": "drinking_water_shortage"},
        "severity": quote(body, "5 ஆண்டுகளாக") | {"value": "moderate"},
        "locations": [
            {"text": "என்.ஜி.ஓ., காலனி", "location_type": "locality", "role": "event_location",
             "field": CONTENT, "quote": "என்.ஜி.ஓ., காலனி", "char_start": 296, "confidence": 0.8},
            {"text": "ராஜேந்திர பிரசாத் தெரு", "location_type": "street", "role": "event_location",
             "field": CONTENT, "quote": "ராஜேந்திர பிரசாத் தெரு", "confidence": 0.8},
        ],
        "entities": [{"text": "ஒன்றிய அதிகாரிகள்", "entity_type": "government_official",
                      "role": "responding_authority", "field": CONTENT,
                      "quote": "ஒன்றிய அதிகாரிகள்", "confidence": 0.7}],
    }


def hill_answer() -> dict[str, Any]:
    """The festival protest: every classification pointed at the same repeated act-phrase."""
    body = BODY[HILL_ID]
    return {
        "record_kind": "incident",
        "title": {"value": HILL_TITLE, "field": "title", "quote": HILL_TITLE, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(body, "மலை மேல் கதம்ப சாதம் தயாரித்து பக்தர்களுக்கு வழங்குவதற்கு தடை "
                                   "விதிக்கப்பட்டது"),
        "incident_type": {"value": "protest_or_strike", "field": CONTENT,
                          "quote": "ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்", "confidence": 0.8},
        "category": {"value": "law_and_order", "field": CONTENT, "quote": "ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்",
                     "confidence": 0.7},
        "event_status": quote(body, "கோயில் அலுவலகத்தை முற்றுகையிட்டனர்") | {"value": "ongoing"},
        "locations": [{"text": "திருப்பரங்குன்றம்", "location_type": "town", "role": "event_location",
                       "field": CONTENT, "quote": "திருப்பரங்குன்றத்தில்", "confidence": 0.85}],
        "entities": [{"text": "மத்திய தொல்லியல் துறை", "entity_type": "government_body",
                      "role": "decision_maker", "field": CONTENT, "quote": "மத்திய தொல்லியல் துறை",
                      "char_start": 639, "confidence": 0.8}],
    }


ANSWERS = {
    UTSAV_ID: utsav_answer,
    CASE_ID: case_answer,
    DEAD_ID: dead_answer,
    INSPECT_ID: inspect_answer,
    WALK_ID: walk_answer,
    HILL_ID: hill_answer,
}

#: The grounding codes each live reading recorded, field by field.
LIVE_FAILURES: dict[str, set[tuple[str, str]]] = {
    UTSAV_ID: {("event_status", "ambiguous_quote"), ("locations[loc-1]", "ambiguous_quote"),
               ("entities[ent-1]", "ambiguous_quote")},
    CASE_ID: {("locations[loc-2]", "ambiguous_quote")},
    DEAD_ID: {("title", "unknown_field"), ("title", "unsupported_claim")},
    INSPECT_ID: {("title", "unknown_field"), ("title", "unsupported_claim")},
    WALK_ID: {("locations[loc-1]", "ambiguous_quote"), ("locations[loc-2]", "ambiguous_quote"),
              ("entities[ent-1]", "span_mismatch")},
    HILL_ID: {("incident_type", "ambiguous_quote"), ("category", "ambiguous_quote"),
              ("locations[loc-1]", "ambiguous_quote"), ("entities[ent-1]", "ambiguous_quote")},
}

GROUNDING_CODES = {
    IssueCode.AMBIGUOUS_QUOTE,
    IssueCode.SPAN_MISMATCH,
    IssueCode.UNKNOWN_FIELD,
    IssueCode.UNSUPPORTED_CLAIM,
}


def read(payload: dict[str, Any], record: dict[str, Any]):
    return extract_incident(record, provider=Scripted(payload=payload))


def run(store: Checkpoint, provider: LLMProvider, records: list[dict[str, Any]]):
    return run_pipeline(
        all_records=True,
        base_url=BASE,
        store=store,
        provider=provider,
        transport=responder(listing(*records)),
    )


def grounding(found) -> set[tuple[str, str]]:
    return {(item.field, item.code.value) for item in found.validation.issues
            if item.code in GROUNDING_CODES}


def replayed(found, record: dict[str, Any]) -> None:
    """Every accepted claim that cites a span cites one the field still reproduces."""
    sources = {item.field: item.text for item in build_context(record).fields}
    index = found.evidence_by_id()
    for claim in found.claims:
        if claim.review is not ReviewState.ACCEPTED or not claim.evidence_ids:
            continue
        for evidence_id in claim.evidence_ids:
            cited = index[evidence_id]
            if cited.char_start is None:
                continue
            assert sources[cited.field][cited.char_start : cited.char_end] == cited.quote
            assert cited.char_end - cited.char_start == len(cited.quote)


def test_the_sanitized_bodies_repeat_the_phrases_the_live_articles_repeated():
    for record_id, phrases in REPEATED.items():
        for phrase, count in phrases.items():
            assert len(find_spans(BODY[record_id], phrase)) == count, (record_id, phrase)


def test_every_one_of_the_six_is_prose_the_router_sends_to_a_model():
    assert len(SIX) == 6
    assert set(ANSWERS) == set(SIX)
    for record in SIX.values():
        assert route(record) is Mode.SEMANTIC


@pytest.mark.parametrize("record_id", sorted(LIVE_FAILURES))
def test_the_answer_that_failed_the_live_run_fails_the_same_way_offline(record_id):
    found = read(ANSWERS[record_id](), SIX[record_id])

    assert grounding(found) == LIVE_FAILURES[record_id]
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert found.review_required is True
    replayed(found, SIX[record_id])


@pytest.mark.parametrize("record_id", sorted(LIVE_FAILURES))
def test_nothing_a_refusal_struck_out_stays_a_fact(record_id):
    found = read(ANSWERS[record_id](), SIX[record_id])
    index = found.evidence_by_id()
    claimed = {item.field: item for item in found.claims}
    refused = {item.field for item in found.validation.issues if item.code in GROUNDING_CODES}

    for name in refused:
        claim = claimed.get(name)
        if claim is not None and claim.method is ExtractionMethod.LLM:
            assert claim.review is not ReviewState.ACCEPTED, name
    for evidence in found.evidence:
        assert evidence.method is not ExtractionMethod.UNRESOLVED
        if evidence.char_start is not None:
            assert evidence.span_validation.value == "validated"
        assert index[evidence.evidence_id] is evidence


def test_an_ambiguous_refusal_names_the_offsets_the_phrase_really_sits_at():
    body = BODY[HILL_ID]
    found = read(hill_answer(), SIX[HILL_ID])
    issue = next(item for item in found.validation.issues
                 if item.field == "incident_type" and item.code is IssueCode.AMBIGUOUS_QUOTE)
    real = [span.char_start for span in find_spans(body, "ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்")]

    assert f"occurs {len(real)} times in data.content" in issue.detail
    assert all(str(start) in issue.detail for start in real)
    assert "the answer named no char_start" in issue.detail
    assert found.incident_type is EventType.UNRESOLVED
    assert found.claim("incident_type").evidence_ids == []


def test_an_offset_that_is_nearly_right_is_refused_rather_than_snapped_to_the_nearest_copy():
    body = BODY[UTSAV_ID]
    real = [span.char_start for span in find_spans(body, "நடந்தது")]
    payload = utsav_answer()
    payload["event_status"]["char_start"] = real[0] - 53

    found = read(payload, SIX[UTSAV_ID])
    issue = next(item for item in found.validation.issues if item.field == "event_status")

    assert issue.code is IssueCode.AMBIGUOUS_QUOTE
    assert "which is none of them" in issue.detail
    assert found.event_status is EventStatus.UNKNOWN
    assert found.description is not None


def test_naming_the_offset_of_one_copy_grounds_the_phrase_at_that_offset():
    body = BODY[HILL_ID]
    protest = "ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்"
    payload = hill_answer()
    payload["incident_type"] = quote(body, protest, occurrence=1) | {"value": "protest_or_strike"}
    payload["category"] = quote(body, protest, occurrence=0) | {"value": "law_and_order"}
    payload["locations"] = [
        {**payload["locations"][0],
         "char_start": find_spans(body, "திருப்பரங்குன்றத்தில்")[1].char_start},
    ]
    payload["entities"] = [
        {**payload["entities"][0],
         "char_start": find_spans(body, "மத்திய தொல்லியல் துறை")[2].char_start},
    ]

    found = read(payload, SIX[HILL_ID])

    assert found.validation.issues == []
    assert found.incident_type is EventType.PROTEST_OR_STRIKE
    assert found.category is IncidentCategory.LAW_AND_ORDER
    for field, occurrence in (("incident_type", 1), ("category", 0)):
        cited = found.evidence_by_id()[found.claim(field).evidence_ids[0]]
        assert cited.char_start == find_spans(body, protest)[occurrence].char_start
        assert cited.quote == protest
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False


def test_offsets_are_unicode_characters_of_the_field_and_not_its_bytes():
    body = BODY[WALK_ID]
    phrase = "என்.ஜி.ஓ., காலனி"
    payload = walk_answer()
    payload["locations"] = [{**payload["locations"][0],
                             "char_start": find_spans(body, phrase)[0].char_start}]

    found = read(payload, SIX[WALK_ID])
    cited = found.evidence_by_id()[found.claim("description").evidence_ids[0]]
    as_bytes = len(body[: cited.char_start].encode("utf-8"))

    assert cited.field == CONTENT
    assert body[cited.char_start : cited.char_end] == cited.quote
    assert cited.char_end - cited.char_start == len(cited.quote)
    assert len(cited.quote.encode("utf-8")) != len(cited.quote)
    assert as_bytes != cited.char_start
    assert body.encode("utf-8")[as_bytes : as_bytes + len(cited.quote.encode("utf-8"))].decode(
        "utf-8"
    ) == cited.quote
    assert cited.field_text_hash == compute_field_hash(body)
    assert verify_evidence(cited, body).ok


def test_an_offset_before_a_character_utf16_counts_twice_is_still_one_character():
    body = "செய்தி \U0001F600 நாகமலைபுதுக்கோட்டை ஊராட்சி"
    phrase = "நாகமலைபுதுக்கோட்டை"
    record = _news("NEWS-MDU-ASTRAL-0001", "தண்ணீர் அவலம்", body,
                   stamp="2026-10-03T07:20:00", slug="astral/1")
    found = read({
        "record_kind": "incident",
        "description": quote(body, phrase),
        "locations": [{"text": phrase, "location_type": "village", "role": "event_location",
                       "field": CONTENT, "quote": phrase, "confidence": 0.8}],
    }, record)

    cited = found.evidence_by_id()[found.claim("description").evidence_ids[0]]
    utf16_units = len(body[: cited.char_start].encode("utf-16-le")) // 2

    assert cited.char_start == body.index(phrase)
    assert utf16_units != cited.char_start
    assert body[cited.char_start : cited.char_end] == phrase


def test_a_quote_the_article_inflected_differently_is_refused_and_never_rewritten_to_fit():
    body = BODY[WALK_ID]
    found = read(walk_answer(), SIX[WALK_ID])
    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.SPAN_MISMATCH)
    entity = found.entities[0]

    assert issue.field == "entities[ent-1]"
    assert "ஒன்றிய அதிகாரிகள்" in issue.detail
    assert "is not verbatim in data.content" in issue.detail
    assert "ஒன்றிய அதிகாரிகள்" not in body
    assert "அதிகாரிகளிடம்" in body
    assert entity.review is not ReviewState.ACCEPTED
    assert entity.evidence_ids == []
    assert all(item.quote != "ஒன்றிய அதிகாரிகள்" for item in found.evidence)
    assert "entities[ent-1]" in found.unaccepted_fields()


def test_a_field_the_answer_named_but_the_record_does_not_carry_says_which_field_to_name():
    body = BODY[UTSAV_ID]
    payload = utsav_answer()
    payload["severity"] = {"value": "low", "field": "data.summary",
                           "quote": "சிறப்பாக நடந்தது", "confidence": 0.6}

    found = read(payload, SIX[UTSAV_ID])
    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.UNKNOWN_FIELD and item.field == "severity")

    assert "quoted 'data.summary', which this record does not carry" in issue.detail
    assert "those words are verbatim in data.content, which is the field to name" in issue.detail
    assert found.severity is SeverityLevel.UNRESOLVED


def test_a_title_that_named_no_field_no_longer_blanks_the_headline():
    record = SIX[DEAD_ID]
    found = read(dead_answer(), record)
    claim = found.claim("title")
    issue = next(item for item in found.validation.issues if item.code is IssueCode.UNKNOWN_FIELD)

    assert found.title == DEAD_TITLE
    assert claim.method is ExtractionMethod.SOURCE_METADATA
    assert claim.review is ReviewState.ACCEPTED
    assert "named no field" in issue.detail
    assert "quoted None" not in issue.detail
    assert any(item.code is IssueCode.UNSUPPORTED_CLAIM
                and "this record's own words" in item.detail for item in found.validation.issues)
    assert found.review_required is True
    replayed(found, record)


def test_a_title_the_model_rewrote_is_dropped_and_the_record_s_own_characters_stand():
    record = SIX[INSPECT_ID]
    found = read(inspect_answer(), record)
    claim = found.claim("title")
    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.UNSUPPORTED_CLAIM)

    assert found.title == INSPECT_TITLE
    assert "District collector inspects" not in found.title
    assert claim.method is ExtractionMethod.SOURCE_METADATA
    assert "never summarised, rewritten or translated" in issue.detail
    assert found.event_status is EventStatus.ACTION_TAKEN
    assert found.description is not None
    replayed(found, record)


def test_a_title_the_model_never_answered_is_left_as_the_abstention_it_was():
    found = read({"description": quote(BODY[DEAD_ID], "பலியானார்")}, SIX[DEAD_ID])

    assert found.title is None
    assert found.claim("title") is None
    assert "title" not in found.unaccepted_fields()


def test_a_span_free_answer_survives_the_provider_it_would_have_been_sent_to():
    endpoint, recorder = gemini([gemini_body(hill_answer())])

    found = extract_incident(SIX[HILL_ID], provider=endpoint)

    assert recorder.sends == 1
    assert grounding(found) == LIVE_FAILURES[HILL_ID]
    assert found.generation.provider == "gemini"
    replayed(found, SIX[HILL_ID])


def test_the_prompt_states_the_offset_unit_and_that_a_protest_is_not_an_ending():
    prompt = prompt_text()

    assert "An offset counts in Unicode characters of the field you named" in prompt
    assert "an offset that is nearly right is refused as flatly as one that is far wrong" in prompt
    assert "long enough to be found once in that field" in prompt
    assert "never `resolved`" in prompt
    assert "protest, a strike, a court hearing, an inspection or a meeting" in prompt


@dataclass
class AskNothing(LLMProvider):
    """Names itself as the reader an earlier run used, and fails the test if anything asks it."""

    name: str = "scripted"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise AssertionError("a record an earlier run read is never sent again")


def test_a_resumed_run_reports_who_it_asked_and_who_it_only_returned(tmp_path):
    records = [SIX[DEAD_ID], SIX[INSPECT_ID]]

    first = run(Checkpoint(tmp_path / "run"), Scripted(dead_answer()), records)
    second = run(Checkpoint(tmp_path / "run").load(), AskNothing(), records)
    saved = Checkpoint(tmp_path / "run").load()

    assert first.attempted == 2 and len(first.reused) == 0
    assert first.reused_answered_by() == {}
    assert second.outcomes == []
    assert second.attempted == 0
    assert len(second.reused) == 2
    assert second.answered_by() == {}
    assert second.reused_answered_by() == {"scripted/gpt-oss-120b": 2}
    assert second.counts()["Read by an earlier run"] == "scripted/gpt-oss-120b (2)"
    assert "Read by" not in second.counts()
    assert "2 reused" in second.accounting()
    assert all(item.review_required for item in second.reused)
    assert (saved.entries[DEAD_ID].provider, saved.entries[DEAD_ID].model) == (
        "scripted", "gpt-oss-120b",
    )


def test_a_fresh_state_directory_reads_the_same_record_again(tmp_path):
    record = [SIX[UTSAV_ID]]

    first = run(Checkpoint(tmp_path / "one"), Scripted(utsav_answer()), record)
    resumed = run(Checkpoint(tmp_path / "one").load(), AskNothing(), record)
    fresh = run(Checkpoint(tmp_path / "two"), Scripted(utsav_answer()), record)

    assert (first.attempted, len(first.reused)) == (1, 0)
    assert (resumed.attempted, len(resumed.reused)) == (0, 1)
    assert (fresh.attempted, len(fresh.reused)) == (1, 0)
    assert resumed.reused_answered_by() == {"scripted/gpt-oss-120b": 1}
    assert fresh.reused_answered_by() == {}


def test_the_limit_help_says_which_records_a_limited_run_takes(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--help"])

    out = " ".join(capsys.readouterr().out.split())

    assert "from the front of the order the backend returned them" in out
    assert "which records those are is the API's decision and is not the same set next run" in out


def test_a_limited_run_keeps_the_front_of_the_order_the_backend_returned():
    ordered = [SIX[CASE_ID], SIX[WALK_ID], SIX[HILL_ID], SIX[DEAD_ID]]

    summary = run_pipeline(limit=2, base_url=BASE, transport=responder(listing(*ordered)),
                           provider=Scripted(case_answer()))

    assert [item.record_id for item in summary.outcomes] == [CASE_ID, WALK_ID]
    assert summary.fetched == 4 and summary.selected == 2
    assert "4 fetched = 2 selected + 2 not selected (--limit/--record-id)" in summary.accounting()
    assert sum(summary.buckets().values()) == summary.attempted


def test_a_review_required_reading_keeps_that_state_when_a_later_run_returns_it(tmp_path):
    record = [SIX[HILL_ID]]

    run(Checkpoint(tmp_path / "hill"), Scripted(hill_answer()), record)
    returned = run(Checkpoint(tmp_path / "hill").load(), AskNothing(), record)

    incident = returned.reused[0].incident
    assert incident is not None
    assert returned.reused[0].review_required is True
    assert grounding(incident) == LIVE_FAILURES[HILL_ID]
    assert Checkpoint(tmp_path / "hill").load().entries[HILL_ID].state == "review_required"


def test_the_six_records_read_one_at_a_time_into_one_fresh_directory(tmp_path):
    """What the manual validation does with these ids: one `--record-id` run each, one new state
    directory, every reading kept, and the next run of the same id returning it without asking."""
    directory = tmp_path / "fresh"

    for record_id in sorted(SIX):
        summary = run_pipeline(
            record_id=record_id,
            base_url=BASE,
            transport=responder(single(SIX[record_id])),
            provider=Scripted(ANSWERS[record_id]()),
            store=Checkpoint(directory).load(),
        )
        assert (summary.fetched, summary.selected, summary.attempted) == (1, 1, 1)
        assert len(summary.reused) == 0

    saved = Checkpoint(directory).load()
    assert set(saved.entries) == set(SIX)
    assert all(item.state == "review_required" for item in saved.entries.values())

    returned = run_pipeline(
        record_id=HILL_ID,
        base_url=BASE,
        transport=responder(single(SIX[HILL_ID])),
        provider=AskNothing(),
        store=Checkpoint(directory).load(),
    )
    assert (returned.attempted, len(returned.reused)) == (0, 1)
    assert returned.reused_answered_by() == {"scripted/gpt-oss-120b": 1}
