"""Serialisation: contracts must survive JSON and disk without Tamil damage."""

from __future__ import annotations

import json
from pathlib import Path

from intelligence.models.enums import EventType, SeverityLevel, TextRole
from intelligence.models.incident import Incident
from intelligence.tests.builders import CONTENT_TA, RECORD_ID, TITLE_TA


def test_json_round_trip_is_lossless(protest):
    restored = Incident.model_validate_json(protest.model_dump_json())

    assert restored == protest
    assert restored.language.text_representations[0].text == CONTENT_TA
    assert restored.observations[0].raw_text == "4800 மனுக்களை"
    assert restored.event_time.raw_text == "அக் 01, 2026 09:46 PM"


def test_dict_round_trip_keeps_enum_semantics(protest):
    document = protest.model_dump(mode="json")
    restored = Incident.model_validate(document)

    assert restored.classification.event_type is EventType.PROTEST_OR_STRIKE
    assert restored.severity.level is SeverityLevel.UNRESOLVED
    assert document["classification"]["event_type"] == "protest_or_strike"
    assert document["language"]["text_representations"][0]["role"] == "source"
    assert restored.language.text_representations[0].role is TextRole.SOURCE


def test_document_is_plain_json_serialisable(protest):
    """No datetime, Decimal or enum object should block json.dumps downstream."""
    document = protest.to_storage_document()

    encoded = json.dumps(document, ensure_ascii=False)
    assert isinstance(encoded, str)
    assert json.loads(encoded)["incident_id"] == protest.incident_id


def test_tamil_is_written_as_tamil_not_escape_sequences(protest):
    encoded = protest.model_dump_json()

    assert "ஆர்ப்பாட்டத்தில்" in encoded
    assert "\\u0b85" not in encoded.lower()


def test_tamil_survives_a_file_round_trip(tmp_path: Path, protest):
    target = tmp_path / "incident.json"
    target.write_text(protest.model_dump_json(), encoding="utf-8")

    raw_bytes = target.read_bytes()
    restored = Incident.model_validate_json(target.read_text(encoding="utf-8"))

    assert CONTENT_TA.encode("utf-8") in raw_bytes
    assert restored == protest
    assert restored.title.text.source == TITLE_TA


def test_unresolved_values_serialise_explicitly(empty_incident):
    document = empty_incident.model_dump(mode="json")

    assert document["fingerprint"] is None
    assert document["classification"]["event_type"] == "unresolved"
    assert document["severity"]["level"] == "unresolved"
    assert document["event_time"]["value"] is None
    assert document["spatial"]["gis"] is None
    assert document["language"]["primary_language"] == "und"
    assert Incident.model_validate(document) == empty_incident


def test_evidence_offsets_survive_serialisation(protest):
    restored = Incident.model_validate_json(protest.model_dump_json())
    evidence = restored.evidence_by_id()["ev-petitions"]
    source_text = restored.language.text_representations[0].text

    assert source_text[evidence.char_start : evidence.char_end] == evidence.quote


def test_record_identity_survives_serialisation(protest):
    restored = Incident.model_validate_json(protest.model_dump_json())

    assert [e.record_id for e in restored.evidence] == [RECORD_ID] * len(restored.evidence)
    assert restored.supporting_record_ids == [RECORD_ID]
