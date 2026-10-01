import uuid

from fastapi.testclient import TestClient

from app.main import app   # if you run pytest from inside backend/


client = TestClient(app)


def test_records_round_trip_contract():
    record_id = f"NEWS-MDU-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "record_id": record_id,
        "source_id": "dinamalar",
        "source_type": "news",
        "record_type": "article",
        "title": "Example district news",
        "event_time": "2026-10-01T10:30:00",
        "location": {
            "raw_text": "Madurai",
            "district": "Madurai",
            "state": "Tamil Nadu",
        },
        "data": {"content": "Example content", "language": "ta"},
        "severity": None,
        "status": None,
        "source_url": "https://example.com/article",
        "retrieved_at": "2026-10-01T11:00:00",
        "raw_reference": "data/raw/dinamalar/example.json",
    }

    first = client.post("/records", json=payload)
    assert first.status_code == 200, first.text

    second = client.post("/records", json=payload)
    assert second.status_code == 409, second.text

    all_records = client.get("/records")
    assert all_records.status_code == 200, all_records.text
    items = all_records.json()
    assert len(items) >= 1
    assert items[0]["record_id"] == payload["record_id"]

    one_record = client.get(f"/records/{payload['record_id']}")
    assert one_record.status_code == 200, one_record.text
    assert one_record.json()["record_id"] == payload["record_id"]
