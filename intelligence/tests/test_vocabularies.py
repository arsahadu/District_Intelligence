"""Taxonomy config: enums and vocabulary data must not drift apart."""

from __future__ import annotations

import pytest

from intelligence.config.vocabularies import (
    DEPARTMENT_LABELS,
    EVENT_TYPE_DEPARTMENTS,
    EVENT_TYPE_FAMILIES,
    EVENT_TYPE_FAMILY,
    SEVERITY_LEVEL_ORDER,
    TAXONOMY_VERSION,
    departments_for,
    family_of,
    informational_event_types,
    is_informational,
    primary_department_for,
    severity_rank,
    vocabulary_integrity_errors,
)
from intelligence.models.enums import Department, EventType, SeverityLevel


def test_taxonomy_has_no_drift_against_the_enums():
    errors = vocabulary_integrity_errors()

    assert errors == [], "vocabulary drift:\n" + "\n".join(errors)


def test_every_event_type_belongs_to_exactly_one_family():
    for event_type in EventType:
        assert family_of(event_type) is not None, event_type
        assert event_type in EVENT_TYPE_FAMILY


def test_family_sizes_match_the_enum_population():
    total = sum(len(members) for members in EVENT_TYPE_FAMILIES.values())

    assert total == len(EventType)
    assert set(EVENT_TYPE_FAMILIES) >= {
        "disaster",
        "health",
        "education",
        "law_and_order",
        "revenue_land",
        "agriculture",
        "informational",
        "unresolved",
    }


def test_every_event_type_maps_to_at_least_one_department():
    for event_type in EventType:
        assert departments_for(event_type), event_type
        assert all(d in Department for d in departments_for(event_type))


def test_primary_department_is_the_nodal_office():
    assert primary_department_for(EventType.FLOOD) is Department.DISTRICT_DISASTER_MANAGEMENT
    assert primary_department_for(EventType.DISEASE_OUTBREAK) is Department.PUBLIC_HEALTH
    assert primary_department_for(EventType.ROAD_DAMAGE) is Department.PWD_HIGHWAYS_RURAL_ROADS
    assert primary_department_for(EventType.PROTEST_OR_STRIKE) is Department.POLICE


def test_unresolved_and_other_never_produce_a_department_hint():
    assert primary_department_for(EventType.UNRESOLVED) is None
    assert primary_department_for(EventType.OTHER) is None
    assert departments_for(EventType.UNRESOLVED) == (Department.UNRESOLVED,)


def test_informational_types_are_flagged_so_the_relevance_gate_can_shortcut():
    informational = informational_event_types()

    assert is_informational(EventType.CEREMONIAL_OR_AWARD_EVENT) is True
    assert is_informational(EventType.FLOOD) is False
    assert EventType.ANNOUNCEMENT_ONLY in informational
    assert all(family_of(e) == "informational" for e in informational)


def test_event_codes_are_language_independent():
    """Codes are snake_case identifiers, never surface-language phrases."""
    for event_type in EventType:
        assert event_type.value == event_type.value.lower()
        assert " " not in event_type.value
        assert event_type.value.replace("_", "").isascii()


def test_severity_ordering_excludes_unresolved():
    assert SeverityLevel.UNRESOLVED not in SEVERITY_LEVEL_ORDER
    assert severity_rank(SeverityLevel.UNRESOLVED) is None
    assert severity_rank(SeverityLevel.CRITICAL) > severity_rank(SeverityLevel.MODERATE)
    assert severity_rank(SeverityLevel.INFO) == 0


def test_departments_all_carry_display_labels():
    assert len(DEPARTMENT_LABELS) == len(Department)
    assert DEPARTMENT_LABELS[Department.PWD_HIGHWAYS_RURAL_ROADS] == (
        "Highways & Rural Development Works"
    )


def test_taxonomy_version_is_pinned_for_reproducibility():
    assert TAXONOMY_VERSION
    assert EVENT_TYPE_DEPARTMENTS[EventType.FLOOD]


@pytest.mark.parametrize(
    "event_type",
    [EventType.FLOOD, EventType.LAND_DISPUTE, EventType.VEHICLE_ACCIDENT],
)
def test_family_lookup_agrees_with_department_lookup(event_type):
    assert family_of(event_type) in {"disaster", "revenue_land", "transport"}
    assert isinstance(departments_for(event_type), tuple)


def test_event_type_family_map_is_the_reverse_of_the_families_map():
    assert EVENT_TYPE_FAMILY[EventType.URBAN_WATERLOGGING] == "disaster"
    assert EVENT_TYPE_FAMILY[EventType.WASTE_MANAGEMENT] == "environment"
    assert EVENT_TYPE_FAMILY[EventType.MALNUTRITION] == "health"
    assert EVENT_TYPE_FAMILY[EventType.SCHOOL_INFRASTRUCTURE] == "education"
    assert EVENT_TYPE_FAMILY[EventType.POWER_SUPPLY_DISRUPTION] == "utilities"
    assert EVENT_TYPE_FAMILY[EventType.CYBER_OR_FINANCIAL_FRAUD] == "law_and_order"
    assert EVENT_TYPE_FAMILY[EventType.ELECTION_PROCESS_ISSUE] == "governance"
    assert EVENT_TYPE_FAMILY[EventType.DRINKING_WATER_SHORTAGE] == "water"
    assert EVENT_TYPE_FAMILY[EventType.BOREWELL_OR_WELL_ISSUE] == "water"
    assert EVENT_TYPE_FAMILY[EventType.TRAFFIC_CONGESTION] == "transport"
    assert EVENT_TYPE_FAMILY[EventType.PUBLIC_BUILDING_DAMAGE] == "infrastructure"
    assert EVENT_TYPE_FAMILY[EventType.EMPLOYMENT_DISPUTE] == "governance"
    assert EVENT_TYPE_FAMILY[EventType.PERSONNEL_TRANSFER] == "informational"
