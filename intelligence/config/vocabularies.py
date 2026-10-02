"""Event taxonomy: families, department mapping and integrity checks.

The codes are language-independent on purpose. ``DEPARTMENT_LABELS`` exists so
display strings can be attached later (Tamil or English) without touching the
internal vocabulary that classifiers emit.
"""

from __future__ import annotations

from intelligence.models.enums import Department, EventType, SeverityLevel

TAXONOMY_VERSION = "2026.10-stage1"

#: Family -> event types. Families group types for reporting and priority;
#: they are not a classification target of their own.
EVENT_TYPE_FAMILIES: dict[str, tuple[EventType, ...]] = {
    "informational": (
        EventType.CEREMONIAL_OR_AWARD_EVENT,
        EventType.ANNOUNCEMENT_ONLY,
        EventType.PERSONNEL_TRANSFER,
        EventType.CULTURAL_OR_SPORTS_EVENT,
    ),
    "disaster": (
        EventType.FLOOD,
        EventType.URBAN_WATERLOGGING,
        EventType.CYCLONE_STORM_DAMAGE,
        EventType.HEAT_WAVE,
        EventType.COLD_WAVE,
        EventType.DROUGHT,
        EventType.LANDSLIDE,
        EventType.TREE_FALL,
        EventType.FIRE,
        EventType.DISASTER_RELIEF_OPERATION,
    ),
    "environment": (
        EventType.POLLUTION,
        EventType.WASTE_MANAGEMENT,
    ),
    "water": (
        EventType.DRINKING_WATER_SHORTAGE,
        EventType.IRRIGATION_WATER_ISSUE,
        EventType.CANAL_OR_WATER_BODY_ISSUE,
        EventType.BOREWELL_OR_WELL_ISSUE,
    ),
    "health": (
        EventType.DISEASE_OUTBREAK,
        EventType.HOSPITAL_SERVICE_GAP,
        EventType.MEDICINE_SHORTAGE,
        EventType.FOOD_SAFETY,
        EventType.VETERINARY_DISEASE,
        EventType.MALNUTRITION,
        EventType.SANITATION_HYGIENE,
    ),
    "education": (
        EventType.SCHOOL_INFRASTRUCTURE,
        EventType.TEACHER_OR_STAFF_SHORTAGE,
        EventType.EXAM_OR_ADMISSION_DISRUPTION,
        EventType.STUDENT_WELFARE_ISSUE,
    ),
    "infrastructure": (
        EventType.ROAD_DAMAGE,
        EventType.BRIDGE_OR_CULVERT_SAFETY,
        EventType.PUBLIC_BUILDING_DAMAGE,
        EventType.STREETLIGHT_FAILURE,
    ),
    "utilities": (
        EventType.POWER_SUPPLY_DISRUPTION,
        EventType.TELECOM_DISRUPTION,
    ),
    "transport": (
        EventType.TRAFFIC_CONGESTION,
        EventType.ROAD_SAFETY_HAZARD,
        EventType.TRANSPORT_SERVICE_ISSUE,
        EventType.VEHICLE_ACCIDENT,
    ),
    "agriculture": (
        EventType.CROP_DAMAGE,
        EventType.PEST_OR_DISEASE_OUTBREAK,
        EventType.AGRICULTURAL_COMPENSATION_ISSUE,
        EventType.MARKET_PRICE_DISTRESS,
        EventType.FARM_LABOUR_DISPUTE,
    ),
    "law_and_order": (
        EventType.VIOLENT_CRIME,
        EventType.PROPERTY_CRIME,
        EventType.SUBSTANCE_ABUSE_CASE,
        EventType.WOMEN_SAFETY_ISSUE,
        EventType.PUBLIC_DISTURBANCE,
        EventType.COMMUNAL_TENSION,
        EventType.CYBER_OR_FINANCIAL_FRAUD,
        EventType.PROTEST_OR_STRIKE,
        EventType.LEGAL_PROCEEDING_OR_ORDER,
    ),
    "revenue_land": (
        EventType.LAND_DISPUTE,
        EventType.ILLEGAL_ENCROACHMENT,
        EventType.UNAUTHORISED_CONSTRUCTION,
        EventType.TENANCY_OR_EVICTION_ISSUE,
        EventType.PAYOUT_OR_RECORDS_ISSUE,
        EventType.FOREST_LAND_ISSUE,
    ),
    "governance": (
        EventType.SERVICE_DELIVERY_DELAY,
        EventType.PUBLIC_SCHEME_GRIEVANCE,
        EventType.PENSION_OR_WAGE_DISPUTE,
        EventType.EMPLOYMENT_DISPUTE,
        EventType.ELECTION_PROCESS_ISSUE,
        EventType.TRANSPARENCY_OR_ACCOUNTABILITY,
        EventType.RELIEF_FUND_MISUSE,
    ),
    "other": (EventType.OTHER,),
    "unresolved": (EventType.UNRESOLVED,),
}

#: Reverse of EVENT_TYPE_FAMILIES, built once at import so the two cannot drift.
EVENT_TYPE_FAMILY: dict[EventType, str] = {
    event_type: family
    for family, event_types in EVENT_TYPE_FAMILIES.items()
    for event_type in event_types
}

#: Default department hints per event type. The first entry is the usual
#: nodal office; the rest are commonly co-responsible. These remain *hints*:
#: the Collector assigns, this table does not.
EVENT_TYPE_DEPARTMENTS: dict[EventType, tuple[Department, ...]] = {
    # informational - collectorate/PR only, never an incident queue
    EventType.CEREMONIAL_OR_AWARD_EVENT: (Department.INFORMATION_PUBLIC_RELATIONS,),
    EventType.ANNOUNCEMENT_ONLY: (Department.INFORMATION_PUBLIC_RELATIONS,),
    EventType.PERSONNEL_TRANSFER: (Department.COLLECTORATE,),
    EventType.CULTURAL_OR_SPORTS_EVENT: (Department.YOUTH_SERVICES_SPORTS,),
    # disaster
    EventType.FLOOD: (
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.WATER_RESOURCES,
        Department.RURAL_DEVELOPMENT_PANCHAYAT_RAJ,
    ),
    EventType.URBAN_WATERLOGGING: (
        Department.URBAN_LOCAL_BODIES,
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.PWD_HIGHWAYS_RURAL_ROADS,
    ),
    EventType.CYCLONE_STORM_DAMAGE: (
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.POWER_TANGEDCO,
        Department.HOUSING_COMMUNITY_DEVELOPMENT,
    ),
    EventType.HEAT_WAVE: (
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.PUBLIC_HEALTH,
        Department.LABOUR_EMPLOYMENT,
    ),
    EventType.COLD_WAVE: (Department.DISTRICT_DISASTER_MANAGEMENT, Department.PUBLIC_HEALTH),
    EventType.DROUGHT: (
        Department.REVENUE,
        Department.AGRICULTURE,
        Department.WATER_RESOURCES,
        Department.DISTRICT_DISASTER_MANAGEMENT,
    ),
    EventType.LANDSLIDE: (
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.FOREST,
        Department.PWD_HIGHWAYS_RURAL_ROADS,
    ),
    EventType.TREE_FALL: (
        Department.PWD_HIGHWAYS_RURAL_ROADS,
        Department.URBAN_LOCAL_BODIES,
        Department.FOREST,
    ),
    EventType.FIRE: (Department.POLICE, Department.URBAN_LOCAL_BODIES, Department.PUBLIC_HEALTH),
    EventType.DISASTER_RELIEF_OPERATION: (
        Department.DISTRICT_DISASTER_MANAGEMENT,
        Department.REVENUE,
        Department.FOOD_CIVIL_SUPPLIES,
    ),
    # environment
    EventType.POLLUTION: (Department.ENVIRONMENT_CLIMATE_CHANGE, Department.URBAN_LOCAL_BODIES),
    EventType.WASTE_MANAGEMENT: (Department.URBAN_LOCAL_BODIES, Department.RURAL_DEVELOPMENT_PANCHAYAT_RAJ),
    # water
    EventType.DRINKING_WATER_SHORTAGE: (
        Department.RURAL_DEVELOPMENT_PANCHAYAT_RAJ,
        Department.URBAN_LOCAL_BODIES,
        Department.WATER_RESOURCES,
    ),
    EventType.IRRIGATION_WATER_ISSUE: (Department.WATER_RESOURCES, Department.REVENUE),
    EventType.CANAL_OR_WATER_BODY_ISSUE: (Department.WATER_RESOURCES, Department.REVENUE),
    EventType.BOREWELL_OR_WELL_ISSUE: (Department.REVENUE, Department.RURAL_DEVELOPMENT_PANCHAYAT_RAJ),
    # health
    EventType.DISEASE_OUTBREAK: (Department.PUBLIC_HEALTH, Department.COLLECTORATE),
    EventType.HOSPITAL_SERVICE_GAP: (Department.PUBLIC_HEALTH,),
    EventType.MEDICINE_SHORTAGE: (Department.PUBLIC_HEALTH, Department.FOOD_CIVIL_SUPPLIES),
    EventType.FOOD_SAFETY: (Department.PUBLIC_HEALTH, Department.FOOD_CIVIL_SUPPLIES),
    EventType.VETERINARY_DISEASE: (Department.ANIMAL_HUSBANDRY, Department.PUBLIC_HEALTH),
    EventType.MALNUTRITION: (Department.WOMEN_CHILD_DEVELOPMENT, Department.PUBLIC_HEALTH),
    EventType.SANITATION_HYGIENE: (Department.URBAN_LOCAL_BODIES, Department.PUBLIC_HEALTH),
    # education
    EventType.SCHOOL_INFRASTRUCTURE: (Department.EDUCATION, Department.PWD_HIGHWAYS_RURAL_ROADS),
    EventType.TEACHER_OR_STAFF_SHORTAGE: (Department.EDUCATION,),
    EventType.EXAM_OR_ADMISSION_DISRUPTION: (Department.EDUCATION, Department.POLICE),
    EventType.STUDENT_WELFARE_ISSUE: (Department.EDUCATION, Department.SOCIAL_WELFARE),
    # infrastructure
    EventType.ROAD_DAMAGE: (Department.PWD_HIGHWAYS_RURAL_ROADS, Department.URBAN_LOCAL_BODIES),
    EventType.BRIDGE_OR_CULVERT_SAFETY: (Department.PWD_HIGHWAYS_RURAL_ROADS, Department.DISTRICT_DISASTER_MANAGEMENT),
    EventType.PUBLIC_BUILDING_DAMAGE: (Department.PWD_HIGHWAYS_RURAL_ROADS, Department.COLLECTORATE),
    EventType.STREETLIGHT_FAILURE: (Department.URBAN_LOCAL_BODIES, Department.POWER_TANGEDCO),
    # utilities
    EventType.POWER_SUPPLY_DISRUPTION: (Department.POWER_TANGEDCO,),
    EventType.TELECOM_DISRUPTION: (Department.COLLECTORATE,),
    # transport
    EventType.TRAFFIC_CONGESTION: (Department.TRANSPORT_MOTOR_WAYS, Department.POLICE),
    EventType.ROAD_SAFETY_HAZARD: (Department.TRANSPORT_MOTOR_WAYS, Department.POLICE),
    EventType.TRANSPORT_SERVICE_ISSUE: (Department.TRANSPORT_MOTOR_WAYS,),
    EventType.VEHICLE_ACCIDENT: (Department.POLICE, Department.PUBLIC_HEALTH, Department.TRANSPORT_MOTOR_WAYS),
    # agriculture
    EventType.CROP_DAMAGE: (Department.AGRICULTURE, Department.REVENUE, Department.DISTRICT_DISASTER_MANAGEMENT),
    EventType.PEST_OR_DISEASE_OUTBREAK: (Department.AGRICULTURE, Department.HORTICULTURE),
    EventType.AGRICULTURAL_COMPENSATION_ISSUE: (Department.REVENUE, Department.AGRICULTURE),
    EventType.MARKET_PRICE_DISTRESS: (Department.COOPERATION, Department.AGRICULTURE),
    EventType.FARM_LABOUR_DISPUTE: (Department.LABOUR_EMPLOYMENT, Department.AGRICULTURE),
    # law and order
    EventType.VIOLENT_CRIME: (Department.POLICE, Department.SOCIAL_WELFARE),
    EventType.PROPERTY_CRIME: (Department.POLICE,),
    EventType.SUBSTANCE_ABUSE_CASE: (Department.PROHIBITION_SUBORDINATE_SERVICE, Department.POLICE),
    EventType.WOMEN_SAFETY_ISSUE: (Department.WOMEN_CHILD_DEVELOPMENT, Department.POLICE),
    EventType.PUBLIC_DISTURBANCE: (Department.POLICE, Department.COLLECTORATE),
    EventType.COMMUNAL_TENSION: (Department.POLICE, Department.COLLECTORATE),
    EventType.CYBER_OR_FINANCIAL_FRAUD: (Department.POLICE, Department.COOPERATION),
    EventType.PROTEST_OR_STRIKE: (Department.POLICE, Department.LABOUR_EMPLOYMENT, Department.COLLECTORATE),
    EventType.LEGAL_PROCEEDING_OR_ORDER: (Department.COLLECTORATE, Department.REVENUE),
    # revenue and land
    EventType.LAND_DISPUTE: (Department.REVENUE, Department.POLICE),
    EventType.ILLEGAL_ENCROACHMENT: (Department.REVENUE, Department.FOREST, Department.HOUSING_COMMUNITY_DEVELOPMENT),
    EventType.UNAUTHORISED_CONSTRUCTION: (Department.HOUSING_COMMUNITY_DEVELOPMENT, Department.URBAN_LOCAL_BODIES),
    EventType.TENANCY_OR_EVICTION_ISSUE: (Department.REVENUE, Department.TRIBAL_WELFARE),
    EventType.PAYOUT_OR_RECORDS_ISSUE: (Department.REVENUE, Department.SOCIAL_WELFARE),
    EventType.FOREST_LAND_ISSUE: (Department.FOREST, Department.TRIBAL_WELFARE),
    # governance
    EventType.SERVICE_DELIVERY_DELAY: (Department.COLLECTORATE,),
    EventType.PUBLIC_SCHEME_GRIEVANCE: (Department.SOCIAL_WELFARE, Department.COLLECTORATE),
    EventType.PENSION_OR_WAGE_DISPUTE: (Department.LABOUR_EMPLOYMENT, Department.SOCIAL_WELFARE),
    EventType.EMPLOYMENT_DISPUTE: (Department.LABOUR_EMPLOYMENT,),
    EventType.ELECTION_PROCESS_ISSUE: (Department.ELECTIONS, Department.COLLECTORATE),
    EventType.TRANSPARENCY_OR_ACCOUNTABILITY: (Department.COLLECTORATE, Department.INFORMATION_PUBLIC_RELATIONS),
    EventType.RELIEF_FUND_MISUSE: (Department.COLLECTORATE, Department.SOCIAL_WELFARE),
    # fallbacks
    EventType.OTHER: (Department.OTHER,),
    EventType.UNRESOLVED: (Department.UNRESOLVED,),
}

#: Event types that are coverage, not incidents. The relevance gate is allowed
#: to resolve straight to NOT_INCIDENT for these.
INFORMATIONAL_EVENT_TYPES: frozenset[EventType] = frozenset(EVENT_TYPE_FAMILIES["informational"])

#: Event types that carry no classification decision at all.
UNRESOLVED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.UNRESOLVED, EventType.OTHER}
)

#: Ordering used for prioritisation. UNRESOLVED is deliberately absent: it is
#: not the lowest severity, it is the absence of a severity judgement.
SEVERITY_LEVEL_ORDER: tuple[SeverityLevel, ...] = (
    SeverityLevel.INFO,
    SeverityLevel.LOW,
    SeverityLevel.MODERATE,
    SeverityLevel.HIGH,
    SeverityLevel.CRITICAL,
)

#: Display labels. Internal codes stay stable; wording is presentation.
DEPARTMENT_LABELS: dict[Department, str] = {
    Department.COLLECTORATE: "Collectorate",
    Department.REVENUE: "Revenue",
    Department.RURAL_DEVELOPMENT_PANCHAYAT_RAJ: "Rural Development / Panchayat Raj",
    Department.URBAN_LOCAL_BODIES: "Urban Local Bodies",
    Department.DISTRICT_DISASTER_MANAGEMENT: "District Disaster Management",
    Department.WATER_RESOURCES: "Water Resources",
    Department.PUBLIC_HEALTH: "Public Health & Preventive Medicine",
    Department.EDUCATION: "School Education",
    Department.AGRICULTURE: "Agriculture",
    Department.HORTICULTURE: "Horticulture",
    Department.ANIMAL_HUSBANDRY: "Animal Husbandry",
    Department.FISHERIES: "Fisheries",
    Department.FOREST: "Environment, Forest & Climate Change",
    Department.ENVIRONMENT_CLIMATE_CHANGE: "Pollution Control",
    Department.PWD_HIGHWAYS_RURAL_ROADS: "Highways & Rural Development Works",
    Department.HOUSING_COMMUNITY_DEVELOPMENT: "Housing & Urban Development",
    Department.POWER_TANGEDCO: "TANGEDCO",
    Department.TRANSPORT_MOTOR_WAYS: "Transport",
    Department.POLICE: "Police",
    Department.PROHIBITION_SUBORDINATE_SERVICE: "Prohibition",
    Department.SOCIAL_WELFARE: "Social Welfare",
    Department.BACKWARD_CLASSES_TBCT: "Backward Classes",
    Department.TRIBAL_WELFARE: "Tribal Welfare",
    Department.WOMEN_CHILD_DEVELOPMENT: "Women & Child Development",
    Department.LABOUR_EMPLOYMENT: "Labour & Employment",
    Department.COOPERATION: "Cooperation",
    Department.FOOD_CIVIL_SUPPLIES: "Food & Civil Supplies",
    Department.MSME_TIRE: "MSME",
    Department.INFORMATION_PUBLIC_RELATIONS: "Information & Public Relations",
    Department.ELECTIONS: "District Election Office",
    Department.YOUTH_SERVICES_SPORTS: "Youth Services & Sports",
    Department.OTHER: "Other",
    Department.UNRESOLVED: "Unresolved",
}


def family_of(event_type: EventType) -> str | None:
    """Report family for an event type, or None if it is unmapped."""
    return EVENT_TYPE_FAMILY.get(event_type)


def departments_for(event_type: EventType) -> tuple[Department, ...]:
    """Departments usually responsible for an event type. Hints, not assignments."""
    return EVENT_TYPE_DEPARTMENTS.get(event_type, (Department.UNRESOLVED,))


def primary_department_for(event_type: EventType) -> Department | None:
    departments = departments_for(event_type)
    if not departments or departments[0] in (Department.UNRESOLVED, Department.OTHER):
        return None
    return departments[0]


def is_informational(event_type: EventType) -> bool:
    return event_type in INFORMATIONAL_EVENT_TYPES


def informational_event_types() -> frozenset[EventType]:
    return INFORMATIONAL_EVENT_TYPES


def unresolved_event_types() -> frozenset[EventType]:
    return UNRESOLVED_EVENT_TYPES


def severity_rank(level: SeverityLevel) -> int | None:
    """Sort position for a severity level; None for UNRESOLVED."""
    try:
        return SEVERITY_LEVEL_ORDER.index(level)
    except ValueError:
        return None


def vocabulary_integrity_errors() -> list[str]:
    """Report drift between the enums and this taxonomy. Empty list is clean.

    Called from a test so an added ``EventType`` that nobody mapped fails the
    suite instead of silently classifying as unmapped at runtime.
    """
    errors: list[str] = []

    enum_types = set(EventType)
    mapped_types = set(EVENT_TYPE_FAMILY)
    for missing in sorted(enum_types - mapped_types, key=lambda e: e.value):
        errors.append(f"EventType.{missing.name} has no family in EVENT_TYPE_FAMILIES")
    for extra in sorted(mapped_types - enum_types, key=lambda e: str(e)):
        errors.append(f"EVENT_TYPE_FAMILIES references non-member {extra}")

    seen: dict[EventType, str] = {}
    for family, types in EVENT_TYPE_FAMILIES.items():
        for event_type in types:
            if event_type in seen:
                errors.append(
                    f"EventType.{event_type.name} appears in both "
                    f"{seen[event_type]} and {family} families"
                )
            seen[event_type] = family

    for missing in sorted(enum_types - set(EVENT_TYPE_DEPARTMENTS), key=lambda e: e.value):
        errors.append(f"EventType.{missing.name} has no entry in EVENT_TYPE_DEPARTMENTS")
    for extra in sorted(set(EVENT_TYPE_DEPARTMENTS) - enum_types, key=lambda e: str(e)):
        errors.append(f"EVENT_TYPE_DEPARTMENTS references non-member {extra}")

    for event_type, departments in EVENT_TYPE_DEPARTMENTS.items():
        if not departments:
            errors.append(f"{event_type.value} maps to an empty department tuple")
        unknown = [d for d in departments if d not in set(Department)]
        if unknown:
            errors.append(f"{event_type.value} maps to unknown departments: {unknown}")
        if event_type in INFORMATIONAL_EVENT_TYPES and not departments:
            errors.append(f"informational type {event_type.value} maps to no department")

    for department in Department:
        if department not in DEPARTMENT_LABELS:
            errors.append(f"Department.{department.name} has no display label")

    for level in SeverityLevel:
        if level is SeverityLevel.UNRESOLVED:
            if severity_rank(level) is not None:
                errors.append("UNRESOLVED severity must not be rankable")
        elif severity_rank(level) is None:
            errors.append(f"SeverityLevel.{level.name} is missing from SEVERITY_LEVEL_ORDER")

    return errors
