"""HTTP write-back for the stable Incident contract."""

from __future__ import annotations

import json

import pytest

from intelligence.api_client import IncidentAPIError, post_incident
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
