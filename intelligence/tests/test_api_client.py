from __future__ import annotations

import json

import pytest

from intelligence.api_client import IncidentAPIError, post_incident
from intelligence.structured import process_record as process_structured
from intelligence.tests.record_fixtures import market_price
from intelligence.tests.test_contract import incident, provenance as contract_provenance


def test_incident_is_posted_as_utf8_and_201_response_is_contract_validated():
    generated = incident()
    calls: list[tuple[str, bytes, float]] = []

    def transport(url: str, body: bytes, timeout: float) -> tuple[int, str]:
        calls.append((url, body, timeout))
        return 201, json.dumps(generated.to_storage_document(), ensure_ascii=False)

    result = post_incident(generated, base_url="http://platform.test/", transport=transport)

    assert result.incident_id == generated.incident_id
    assert result.status_code == 201
    assert result.duplicate is False
    assert calls[0][0] == "http://platform.test/incidents"
    assert json.loads(calls[0][1].decode("utf-8")) == generated.to_storage_document()
    assert calls[0][1].decode("utf-8").encode("utf-8") == calls[0][1]


def test_duplicate_response_is_reported_without_overwriting():
    generated = incident()
    result = post_incident(
        generated,
        base_url="http://platform.test",
        transport=lambda *_: (409, '{"detail":"already exists"}'),
    )

    assert result.status_code == 409
    assert result.duplicate is True


@pytest.mark.parametrize("status_code", [400, 422, 500, 503, 204])
def test_api_errors_are_explicit(status_code: int):
    with pytest.raises(IncidentAPIError, match=f"HTTP {status_code}"):
        post_incident(
            incident(),
            base_url="http://platform.test",
            transport=lambda *_: (status_code, "request failed"),
        )


def test_connection_errors_are_explicit():
    def unreachable(*_):
        raise OSError("connection refused")

    with pytest.raises(IncidentAPIError, match="could not be reached"):
        post_incident(incident(), base_url="http://platform.test", transport=unreachable)


def test_invalid_or_mismatched_success_responses_are_not_reported_as_created():
    with pytest.raises(IncidentAPIError, match="invalid Incident response"):
        post_incident(
            incident(),
            base_url="http://platform.test",
            transport=lambda *_: (201, '{"not":"an Incident"}'),
        )

    different = incident(incident_id="INC-DIFFERENT")
    with pytest.raises(IncidentAPIError, match="instead of"):
        post_incident(
            incident(),
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(different.to_storage_document())),
        )

    changed = incident(
        provenance=contract_provenance(source_url="https://changed.example.test/report")
    )
    with pytest.raises(IncidentAPIError, match="changed the Incident contract"):
        post_incident(
            incident(),
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(changed.to_storage_document())),
        )


def test_a_posted_price_line_sends_its_values_and_provenance_as_the_record_gave_them():
    record = market_price()
    generated = process_structured(record)
    sent: list[dict] = []

    def transport(url: str, body: bytes, timeout: float) -> tuple[int, str]:
        document = json.loads(body.decode("utf-8"))
        sent.append(document)
        return 201, json.dumps(document, ensure_ascii=False)

    result = post_incident(generated, base_url="http://platform.test", transport=transport)

    assert (result.status_code, result.duplicate) == (201, False)
    assert sent[0]["record_kind"] == "context"
    assert sent[0]["context_type"] == "market_price"
    assert {item["field"]: item["value"] for item in sent[0]["context_facts"]} == {
        "data.commodity": "Tomato",
        "data.market": "Anna nagar",
        "data.market_id": "125",
        "data.min_price": "46.0",
        "data.max_price": "50.0",
        "data.quantity": "800.0",
        "data.unit": "kg",
        "data.district_id": "21",
    }
    assert [item["field"] for item in sent[0]["source_geography"]] == [
        "location.raw_text", "location.district", "location.state",
    ]
    provenance = sent[0]["provenance"]
    assert provenance["source_url"] == record["source_url"]
    assert provenance["record_id"] == record["record_id"]
    assert provenance["retrieved_at"].startswith("2026-10-03T14:03:29")
    assert provenance["declared_event_time"] == "2026-10-03T00:00:00"
    assert provenance["declared_event_time_precision"] == "unknown"
    assert provenance["fields_without_value"] == []


@pytest.mark.parametrize(
    "field,wording",
    [
        ("record_kind", "invalid Incident response"),
        ("context_type", "changed the Incident contract"),
        ("context_facts", "changed the Incident contract"),
        ("source_geography", "changed the Incident contract"),
    ],
)
def test_the_round_trip_notices_a_field_the_backend_did_not_store(field, wording):
    generated = process_structured(market_price())
    document = generated.to_storage_document()
    del document[field]

    with pytest.raises(IncidentAPIError, match=wording):
        post_incident(
            generated,
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(document, ensure_ascii=False)),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_kind", "forecast"),
        ("context_type", "other"),
        ("context_facts", []),
        ("source_geography", []),
    ],
)
def test_the_round_trip_notices_a_field_the_backend_rewrote(field, value):
    generated = process_structured(market_price())
    document = generated.to_storage_document()
    document[field] = value

    with pytest.raises(IncidentAPIError, match="changed the Incident contract"):
        post_incident(
            generated,
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(document, ensure_ascii=False)),
        )


def test_a_document_the_backend_returned_without_its_provenance_is_not_a_created_incident():
    generated = process_structured(market_price())
    document = generated.to_storage_document()
    del document["provenance"]

    with pytest.raises(IncidentAPIError, match="invalid Incident response"):
        post_incident(
            generated,
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(document, ensure_ascii=False)),
        )


def test_a_band_the_backend_invented_for_a_price_line_is_reported_not_swallowed():
    generated = process_structured(market_price())
    document = generated.to_storage_document()
    document["provenance"]["declared_severity"] = "high"

    with pytest.raises(IncidentAPIError, match="changed the Incident contract"):
        post_incident(
            generated,
            base_url="http://platform.test",
            transport=lambda *_: (201, json.dumps(document, ensure_ascii=False)),
        )
