from __future__ import annotations

from typing import Any, Optional

import pytest

from intelligence.contract import Incident, IssueCode, ReviewState
from intelligence.intelligence import extract_incident, prompt_text
from intelligence.linking import candidate_pairs, eligible, place_names
from intelligence.models.enums import MentionRole, MentionType, ResolutionState
from intelligence.tests.builders import Scripted, quote
from intelligence.tests.record_fixtures import base_record

FIRE_TA = "மதுரை: மேலூர் அருகே கருங்காலக்குடி கிராமத்தில் வைக்கோல் போரில் தீ பிடித்து எரிந்தது"
FLOOD_TA = "மதுரை: மேலூர் அருகே கருங்காலக்குடி கிராமத்தில் கால்வாய் உடைந்து வெள்ளம் புகுந்தது"
VILLAGE = "கருங்காலக்குடி கிராமத்தில்"
DATELINE = "மதுரை"


def place(content: str, words: str, **fields: Any) -> dict[str, Any]:
    return quote(content, words) | {
        "text": "Karungalakudi",
        "normalized_name": "Karungalakudi",
        "location_type": "village",
        "role": "event_location",
    } | fields


def read(
    locations: list[dict[str, Any]],
    *,
    content: str = FIRE_TA,
    kind: str = "fire",
    kind_quote: str = "தீ பிடித்து",
    record_id: str = "NEWS-LOC-1",
    district: Optional[str] = "Madurai",
) -> Incident:
    record = base_record(
        record_id=record_id,
        title=f"report {record_id}",
        location={"raw_text": district, "district": district, "state": "Tamil Nadu"},
        data={"content": content, "language": "ta"},
        source_url=f"https://news.example.test/{record_id}",
        raw_reference=f"https://news.example.test/{record_id}",
    )
    answer = {
        "record_kind": "incident",
        "description": quote(content, content),
        "incident_type": quote(content, kind_quote) | {"value": kind},
        "event_status": quote(content, kind_quote) | {"value": "reported"},
        "locations": locations,
    }
    return extract_incident(record, provider=Scripted(answer))


def issues_on(incident: Incident, field: str) -> list[IssueCode]:
    return [issue.code for issue in incident.validation.issues if issue.field == field]


def test_a_tamil_village_keeps_the_record_s_words_and_the_level_its_own_quote_names():
    incident = read([place(FIRE_TA, VILLAGE, village="Karungalakudi")])

    found = incident.locations[0]
    assert (found.text, found.normalized_name) == (VILLAGE, "Karungalakudi")
    assert found.village == "Karungalakudi" and found.taluk is None
    assert found.location_type is MentionType.VILLAGE and found.role is MentionRole.EVENT_LOCATION
    assert found.review is ReviewState.ACCEPTED
    assert incident.evidence_by_id()[found.evidence_ids[0]].quote == VILLAGE
    assert incident.validation.state is ReviewState.ACCEPTED


@pytest.mark.parametrize("taluk", ["Vadipatti", "Melur"])
def test_a_taluk_the_text_does_not_print_is_dropped_and_the_reading_goes_to_review(taluk):
    incident = read([place(FIRE_TA, VILLAGE, village="Karungalakudi", taluk=taluk)])

    found = incident.locations[0]
    assert found.taluk is None and found.village == "Karungalakudi"
    assert issues_on(incident, "locations[loc-1].taluk") == [IssueCode.UNSUPPORTED_CLAIM]
    assert incident.review_required


def test_a_level_the_tamil_text_prints_verbatim_is_kept():
    incident = read([place(FIRE_TA, VILLAGE, taluk="மேலூர்")])

    assert incident.locations[0].taluk == "மேலூர்"
    assert issues_on(incident, "locations[loc-1].taluk") == []
    assert incident.validation.state is ReviewState.ACCEPTED


def test_the_district_the_record_declares_supports_a_district_level():
    incident = read([place(FIRE_TA, VILLAGE, district="Madurai")])

    assert incident.locations[0].district == "Madurai"
    assert incident.validation.state is ReviewState.ACCEPTED


def test_a_district_neither_printed_nor_declared_is_not_taken_from_what_is_known_of_the_place():
    incident = read([place(FIRE_TA, VILLAGE, district="Madurai")], district=None)

    assert incident.locations[0].district is None
    assert issues_on(incident, "locations[loc-1].district") == [IssueCode.UNSUPPORTED_CLAIM]
    assert incident.review_required


def test_a_dateline_and_the_incident_village_keep_separate_roles():
    incident = read([
        place(FIRE_TA, DATELINE, text="Madurai", normalized_name="Madurai",
              location_type="district", role="reporting_origin", district="Madurai"),
        place(FIRE_TA, VILLAGE, village="Karungalakudi"),
    ])

    dateline, village = incident.locations
    assert (dateline.text, dateline.role) == (DATELINE, MentionRole.REPORTING_ORIGIN)
    assert (village.text, village.role) == (VILLAGE, MentionRole.EVENT_LOCATION)
    assert incident.validation.state is ReviewState.ACCEPTED
    assert place_names(incident) == {"karungalakudi"}


def test_coordinates_and_canonical_ids_in_an_answer_are_dropped_and_the_place_stays_pending():
    incident = read([place(FIRE_TA, VILLAGE, latitude=9.9252, longitude=78.1198,
                           canonical_location_id="MDU-0042")])

    found = incident.locations[0]
    assert (found.latitude, found.longitude, found.canonical_location_id) == (None, None, None)
    assert found.resolution_state is ResolutionState.PENDING_GIS
    assert issues_on(incident, "locations[loc-1]") == [IssueCode.UNAUTHORISED_GIS_VALUE] * 3
    assert incident.review_required


def test_a_role_outside_the_taxonomy_is_left_unresolved_for_a_person():
    incident = read([place(FIRE_TA, VILLAGE, role="incident_place")])

    assert incident.locations[0].role is MentionRole.UNRESOLVED
    assert issues_on(incident, "locations[loc-1].role") == [IssueCode.INVALID_VALUE]
    assert incident.review_required


def test_a_record_that_names_no_place_carries_no_location():
    incident = read([])

    assert incident.locations == []
    assert incident.validation.state is ReviewState.ACCEPTED


def test_the_prompt_states_the_place_roles_and_that_levels_come_only_from_the_text():
    rules = " ".join(prompt_text().split())

    for phrase in (
        "event_location is the most specific place the text says it happened",
        "reporting_origin a dateline or filing place",
        "a district named in passing or as a dateline is not where it happened",
        "district, taluk, block and village hold only levels the text names",
        "a district mention yields no taluk, town, village or ward",
        "no coordinates, canonical place ids or evidence ids",
    ):
        assert phrase in rules


@pytest.mark.parametrize(
    ("role", "paired"),
    [("event_location", 1), ("event_container", 1), ("reporting_origin", 0), ("mentioned_only", 0)],
)
def test_only_a_place_the_event_happened_in_makes_two_different_reports_candidates(role, paired):
    fire = read([place(FIRE_TA, VILLAGE, role=role)], record_id="NEWS-LOC-1")
    flood = read([place(FLOOD_TA, VILLAGE, role=role)], content=FLOOD_TA, kind="flood",
                 kind_quote="வெள்ளம் புகுந்தது", record_id="NEWS-LOC-2")

    assert len(candidate_pairs(eligible([fire, flood]))) == paired
