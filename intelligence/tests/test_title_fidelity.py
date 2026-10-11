from __future__ import annotations

from typing import Any

from intelligence.context import build_context
from intelligence.contract import (
    EventStatus,
    IncidentCategory,
    IssueCode,
    RecordKind,
    ReviewState,
)
from intelligence.intelligence import extract_incident
from intelligence.models.enums import (
    ExtractionMethod,
    EventType,
    SeverityLevel,
    SpanValidation,
)
from intelligence.router import Mode, route
from intelligence.spans import compute_field_hash, find_spans, verify_evidence
from intelligence.tests.builders import CONTENT, Scripted
from intelligence.tests.record_fixtures import base_record

RECORD_ID = "NEWS-MDU-d7e026050541d904"
URL = ("https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/"
       "collector-s-inspection-to-prevent-accident/4344646")

HEADLINE = "விபத்தை தடுக்க கலெக்டர் ஆய்வு"
NAV = ("டைம்லைன் தற்போதைய செய்தி டிவி ப்ரீமியம் தமிழகம் இந்தியா உலகம் வர்த்தகம் "
       "விளையாட்டு கல்விமலர் டீ கடை பெஞ்ச் தினம் தினம் ஜோசியம் காலண்டர் ஆன்மிகம் "
       "வாராவாரம் இணைப்பு மலர் போட்டோ உலக தமிழர் ஸ்பெஷல் மாவட்டம் ")
STAMPS = ("UPDATED : அக் 10, 2026 05:00 AM ADDED : அக் 09, 2026 06:16 PM "
          "UPDATED : அக் 10, 2026 05:00 AM ADDED : அக் 09, 2026 06:16 PM ")
BODY = ("மேலுார்: மேலுார் தேசிய நெடுஞ்சாலையில் அடிக்கடி ஏற்படும் வாகன விபத்தால் உயிரிழப்பு "
        "ஏற்பட்டது. இதையடுத்து கலெக்டர் நிஷாந்த் கிருஷ்ணா புதுப்பட்டி, வெள்ளமலை பட்டி, "
        "வலைச்சேரி பட்டி, பாண்டாங்குடி ஆய்வு செய்தார்.")
ARTICLE = NAV + STAMPS + BODY

INSPECTION = "கலெக்டர் நிஷாந்த் கிருஷ்ணா புதுப்பட்டி, வெள்ளமலை பட்டி, வலைச்சேரி பட்டி, " \
             "பாண்டாங்குடி ஆய்வு செய்தார்."
COLLECTOR = "கலெக்டர் நிஷாந்த் கிருஷ்ணா"
FATALITY = "உயிரிழப்பு ஏற்பட்டது"
CRASH = "வாகன விபத்தால் உயிரிழப்பு ஏற்பட்டது"
ACT_TAKEN = "ஆய்வு செய்தார்."
HIGHWAY = "மேலுார் தேசிய நெடுஞ்சாலையில்"
MELUR = "மேலுார்"

LEAD = BODY.split(". ")[0] + "."
BLOB = ("Collector inspects highway to prevent accidents in Melur area following fatal mishaps and "
        f"directs safety improvements, Madurai district, Tamil Nadu; title: {HEADLINE}; field: "
        f"title, quote: {HEADLINE}; confidence: 0.95; precision: second; char_start: 0; "
        "description: Collector inspects Melur national highway; incident_type: road_safety_hazard, "
        "value: road_safety_hazard; severity: high; event_status: action_taken; record_kind: "
        'incident; locations: [{"text": "மேலுார்"}]; entities: []; context_facts: []')


def scraped_record(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id=RECORD_ID,
        title=HEADLINE,
        event_time="2026-10-09T18:16:00",
        location={"raw_text": "Madurai", "district": "Madurai", "state": "Tamil Nadu"},
        source_url=URL,
        raw_reference=URL,
        retrieved_at="2026-10-10T07:46:34.988674",
        data={"content": ARTICLE, "language": "ta"},
    )
    payload.update(fields)
    return payload


def read(payload: dict[str, Any], record: dict[str, Any]):
    return extract_incident(record, provider=Scripted(payload=payload))


def replayed(found, record: dict[str, Any]) -> None:
    sources = {item.field: item.text for item in build_context(record).fields}
    index = found.evidence_by_id()
    for claim in found.claims:
        if claim.review is not ReviewState.ACCEPTED or not claim.evidence_ids:
            continue
        for evidence_id in claim.evidence_ids:
            cited = index[evidence_id]
            assert cited.field in sources, f"evidence cites {cited.field!r}, which is not a field"
            if cited.char_start is None:
                continue
            held = sources[cited.field]
            assert held[cited.char_start : cited.char_end] == cited.quote
            assert cited.char_end - cited.char_start == len(cited.quote)
            assert cited.field_text_hash == compute_field_hash(held)


def headline_claim() -> dict[str, Any]:
    return {"value": HEADLINE, "field": "title", "quote": HEADLINE, "char_start": 0,
            "confidence": 0.9}


def cited(span: str, **overrides: Any) -> dict[str, Any]:
    payload = {"value": span, "field": CONTENT, "quote": span,
               "char_start": ARTICLE.index(span), "confidence": 0.9}
    payload.update(overrides)
    return payload


def good_answer() -> dict[str, Any]:
    return {
        "record_kind": "incident",
        "title": headline_claim(),
        "description": cited(INSPECTION),
        "incident_type": cited(CRASH, value="road_safety_hazard"),
        "severity": cited(FATALITY, value="high"),
        "event_status": cited(ACT_TAKEN, value="action_taken"),
        "locations": [{"text": MELUR, "location_type": "town", "role": "event_location",
                       "field": CONTENT, "quote": HIGHWAY, "char_start": ARTICLE.index(HIGHWAY),
                       "confidence": 0.85}],
        "entities": [{"text": COLLECTOR, "entity_type": "government_official",
                      "role": "responding_authority", "field": CONTENT, "quote": COLLECTOR,
                      "char_start": ARTICLE.index(COLLECTOR), "confidence": 0.9}],
    }


def test_the_scraped_field_opens_with_navigation_and_a_repeated_stamp():
    record = scraped_record()
    context = build_context(record)

    assert route(record) is Mode.SEMANTIC
    assert context.paths == ("title", CONTENT)
    assert context.text_of("title") == HEADLINE
    assert ARTICLE.startswith(NAV) and NAV.strip() in ARTICLE
    assert HEADLINE not in ARTICLE
    assert ARTICLE.index(BODY) == len(NAV) + len(STAMPS)
    assert len(find_spans(ARTICLE, "UPDATED")) == 2
    assert all("location" not in item.field for item in context.fields)
    assert [item.field for item in context.geography] == ["location.raw_text",
                                                         "location.district", "location.state"]


def test_page_furniture_that_replays_verbatim_is_still_not_allowed_as_the_title():
    record = scraped_record()
    assert ARTICLE[0 : len(NAV)] == NAV

    found = read({"record_kind": "incident", "title": cited(NAV, char_start=0)}, record)
    claim = found.claim("title")

    assert found.title == HEADLINE
    assert claim.method is ExtractionMethod.SOURCE_METADATA
    assert claim.review is ReviewState.ACCEPTED
    assert claim.evidence_ids
    assert all(found.evidence_by_id()[i].field == "title" for i in claim.evidence_ids)
    assert any(item.code is IssueCode.UNSUPPORTED_CLAIM and item.field == "title"
               and f"from a span of {CONTENT!r}" in item.detail
               for item in found.validation.issues)
    assert found.record_kind is not RecordKind.INCIDENT
    replayed(found, record)


def test_a_body_sentence_copied_as_the_title_is_refused_and_the_headline_stands():
    record = scraped_record()
    assert LEAD in ARTICLE and LEAD != HEADLINE

    found = read({"record_kind": "incident", "title": cited(LEAD)}, record)

    assert found.title == HEADLINE
    assert found.claim("title").method is ExtractionMethod.SOURCE_METADATA
    assert all(item.field != CONTENT for item in found.evidence
               if item.method is ExtractionMethod.LLM)
    replayed(found, record)


def test_a_headline_cited_from_its_own_field_stays_the_models_own_claim():
    record = scraped_record()

    found = read({"record_kind": "incident", "title": headline_claim(),
                  "description": cited(ACT_TAKEN)}, record)
    claim = found.claim("title")

    assert found.title == HEADLINE
    assert claim.method is ExtractionMethod.LLM
    assert claim.review is ReviewState.ACCEPTED
    cited_row = found.evidence_by_id()[claim.evidence_ids[0]]
    assert (cited_row.field, cited_row.char_start, cited_row.quote) == ("title", 0, HEADLINE)
    assert not any(item.field == "title" for item in found.validation.issues)
    replayed(found, record)


def test_the_scraped_article_is_still_read_as_an_incident_when_the_answer_is_grounded():
    record = scraped_record()

    found = read(good_answer(), record)

    assert found.title == HEADLINE
    assert found.description == INSPECTION
    assert found.record_kind is RecordKind.INCIDENT
    assert found.incident_type is EventType.ROAD_SAFETY_HAZARD
    assert found.severity is SeverityLevel.HIGH
    assert found.event_status is EventStatus.ACTION_TAKEN
    assert found.category is IncidentCategory.UNRESOLVED
    assert [item.text for item in found.locations] == [MELUR]
    assert [item.text for item in found.entities] == [COLLECTOR]
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False
    replayed(found, record)


def test_an_answer_that_carries_only_a_stuffed_title_invents_no_event():
    record = scraped_record()

    found = read({"title": {"value": BLOB}}, record)

    assert BLOB not in ARTICLE
    assert found.title == HEADLINE
    assert found.description is None
    assert found.incident_type is EventType.UNRESOLVED
    assert found.category is IncidentCategory.UNRESOLVED
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert found.record_kind is RecordKind.UNRESOLVED
    assert found.locations == [] and found.entities == []
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert found.review_required is True
    codes = {(item.field, item.code.value) for item in found.validation.issues}
    assert ("title", "unknown_field") in codes
    assert ("title", "unsupported_claim") in codes
    refusal = next(item for item in found.validation.issues
                   if item.code is IssueCode.UNKNOWN_FIELD)
    assert "named no field" in refusal.detail and f"title, {CONTENT}" in refusal.detail
    assert "verbatim in none of them" in refusal.detail
    replayed(found, record)


def test_evidence_would_stop_replaying_if_the_furniture_were_cut_before_grounding():
    record = scraped_record()
    stripped = ARTICLE[len(NAV) + len(STAMPS):]
    rows = [item for item in read(good_answer(), record).evidence if item.field == CONTENT]

    assert rows
    for row in rows:
        assert verify_evidence(row, ARTICLE).validation is SpanValidation.VALIDATED
        check = verify_evidence(row, stripped)
        assert check.validation is SpanValidation.MISMATCH
        assert "no longer anchored" in check.reason
        assert check.expected_hash == compute_field_hash(ARTICLE)
        assert check.actual_hash == compute_field_hash(stripped)


def test_a_place_the_record_declared_about_itself_is_not_a_location_the_model_owes():
    record = scraped_record()

    found = read({"title": headline_claim()}, record)
    geography = found.source_geography

    assert found.locations == []
    assert [(item.field, item.value) for item in geography] == [
        ("location.raw_text", "Madurai"), ("location.district", "Madurai"),
        ("location.state", "Tamil Nadu")]
    assert all(item.method is ExtractionMethod.SOURCE_METADATA for item in geography)
    assert all(item.review is ReviewState.ACCEPTED for item in geography)
    index = found.evidence_by_id()
    for item in geography:
        row = index[item.evidence_ids[0]]
        assert row.char_start is None and row.field == item.field
    assert found.record_kind is RecordKind.UNRESOLVED
    replayed(found, record)
