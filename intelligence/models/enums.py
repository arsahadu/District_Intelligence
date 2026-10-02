"""Controlled vocabularies for Intelligence contracts."""

from __future__ import annotations

from enum import Enum


class ExtractionMethod(str, Enum):
    """How a value came to exist. Separates source facts from inferences."""

    SOURCE_METADATA = "source_metadata"
    REGEX = "regex"
    RULE = "rule"
    DICTIONARY = "dictionary"
    STATISTICAL = "statistical"
    LLM = "llm"
    HUMAN = "human"
    UNRESOLVED = "unresolved"


class SpanValidation(str, Enum):
    """Whether an evidence span has been re-checked against the source text."""

    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    MISMATCH = "mismatch"
    NOT_APPLICABLE = "not_applicable"


class Modality(str, Enum):
    """What the source record actually carried. Keeps OCR out of the core path."""

    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    SCANNED_DOCUMENT = "scanned_document"
    AUDIO_TRANSCRIPT = "audio_transcript"
    STRUCTURED_DATA = "structured_data"
    UNKNOWN = "unknown"


class TextRole(str, Enum):
    """Why a representation of the text exists."""

    SOURCE = "source"
    NORMALIZED = "normalized"
    TRANSLITERATED = "transliterated"
    TRANSLATION = "translation"
    OCR_OUTPUT = "ocr_output"
    UNRESOLVED = "unresolved"


class ScriptType(str, Enum):
    TAMIL = "tamil"
    LATIN = "latin"
    MIXED = "mixed"
    OTHER = "other"
    UNKNOWN = "unknown"


class SummaryKind(str, Enum):
    """A summary is inferred text, so its construction method is declared."""

    EXTRACTIVE = "extractive"
    ABSTRACTIVE = "abstractive"
    STRUCTURED = "structured"
    UNRESOLVED = "unresolved"


class TimePrecision(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    DECADE = "decade"
    UNKNOWN = "unknown"


class TimeQualifier(str, Enum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    BEFORE = "before"
    AFTER = "after"
    ONGOING = "ongoing"
    RECURRING = "recurring"
    UNKNOWN = "unknown"


class TimeSemantics(str, Enum):
    """Which moment a timestamp actually denotes."""

    EVENT_TIME = "event_time"
    PUBLICATION_TIME = "publication_time"
    RETRIEVAL_TIME = "retrieval_time"
    REPORTED_TIME = "reported_time"
    UNKNOWN = "unknown"


class GranularityLevel(str, Enum):
    """Administrative/physical specificity a mention claims for itself."""

    COUNTRY = "country"
    STATE = "state"
    DISTRICT = "district"
    SUB_DIVISION = "sub_division"
    TALUK = "taluk"
    BLOCK = "block"
    MUNICIPALITY = "municipality"
    CORPORATION = "corporation"
    TOWN = "town"
    VILLAGE = "village"
    WARD = "ward"
    STREET = "street"
    LANDMARK = "landmark"
    ADDRESS = "address"
    UNKNOWN = "unknown"


class MentionType(str, Enum):
    DISTRICT = "district"
    TALUK = "taluk"
    BLOCK = "block"
    VILLAGE = "village"
    TOWN = "town"
    LOCALITY = "locality"
    STREET = "street"
    WARD = "ward"
    INSTITUTION = "institution"
    GOVERNMENT_BODY = "government_body"
    WATER_BODY = "water_body"
    AGRICULTURAL_FIELD = "agricultural_field"
    PROJECT = "project"
    OTHER = "other"
    UNKNOWN = "unknown"


class MentionRole(str, Enum):
    """What a place name is doing in the sentence."""

    EVENT_LOCATION = "event_location"
    EVENT_CONTAINER = "event_container"
    INSTITUTION_NAME = "institution_name"
    ACTOR_AFFILIATION = "actor_affiliation"
    REPORTING_ORIGIN = "reporting_origin"
    MENTIONED_ONLY = "mentioned_only"
    UNRESOLVED = "unresolved"


class ResolutionState(str, Enum):
    """GIS hand-off state. Intelligence only ever produces the first three."""

    NOT_ATTEMPTED = "not_attempted"
    PENDING_GIS = "pending_gis"
    AMBIGUOUS = "ambiguous"
    RESOLVED = "resolved"  # set by GIS, not by Intelligence
    REJECTED = "rejected"  # set by GIS, not by Intelligence


class DistrictHintAuthority(str, Enum):
    """Where a district guess came from, so its trust level is explicit."""

    TEXT_EVIDENCE = "text_evidence"
    SOURCE_CONFIGURATION = "source_configuration"
    FEED_URL = "feed_url"
    MANUAL = "manual"
    NONE = "none"


class IncidentStatus(str, Enum):
    CANDIDATE = "candidate"
    OPEN = "open"
    NEEDS_REVIEW = "needs_review"
    DISMISSED = "dismissed"
    MERGED = "merged"
    ARCHIVED = "archived"


class RelevanceState(str, Enum):
    INCIDENT = "incident"
    NOT_INCIDENT = "not_incident"
    UNSURE = "unsure"
    UNRESOLVED = "unresolved"


class EventType(str, Enum):
    """Language-independent incident taxonomy. Extend via code review only."""

    CEREMONIAL_OR_AWARD_EVENT = "ceremonial_or_award_event"
    ANNOUNCEMENT_ONLY = "announcement_only"
    PERSONNEL_TRANSFER = "personnel_transfer"
    CULTURAL_OR_SPORTS_EVENT = "cultural_or_sports_event"

    FLOOD = "flood"
    URBAN_WATERLOGGING = "urban_waterlogging"
    CYCLONE_STORM_DAMAGE = "cyclone_storm_damage"
    HEAT_WAVE = "heat_wave"
    COLD_WAVE = "cold_wave"
    DROUGHT = "drought"
    LANDSLIDE = "landslide"
    TREE_FALL = "tree_fall"
    FIRE = "fire"
    POLLUTION = "pollution"
    WASTE_MANAGEMENT = "waste_management"
    DISASTER_RELIEF_OPERATION = "disaster_relief_operation"

    DRINKING_WATER_SHORTAGE = "drinking_water_shortage"
    IRRIGATION_WATER_ISSUE = "irrigation_water_issue"
    CANAL_OR_WATER_BODY_ISSUE = "canal_or_water_body_issue"
    BOREWELL_OR_WELL_ISSUE = "borewell_or_well_issue"

    DISEASE_OUTBREAK = "disease_outbreak"
    HOSPITAL_SERVICE_GAP = "hospital_service_gap"
    MEDICINE_SHORTAGE = "medicine_shortage"
    FOOD_SAFETY = "food_safety"
    VETERINARY_DISEASE = "veterinary_disease"
    MALNUTRITION = "malnutrition"
    SANITATION_HYGIENE = "sanitation_hygiene"

    SCHOOL_INFRASTRUCTURE = "school_infrastructure"
    TEACHER_OR_STAFF_SHORTAGE = "teacher_or_staff_shortage"
    EXAM_OR_ADMISSION_DISRUPTION = "exam_or_admission_disruption"
    STUDENT_WELFARE_ISSUE = "student_welfare_issue"

    ROAD_DAMAGE = "road_damage"
    BRIDGE_OR_CULVERT_SAFETY = "bridge_or_culvert_safety"
    PUBLIC_BUILDING_DAMAGE = "public_building_damage"
    POWER_SUPPLY_DISRUPTION = "power_supply_disruption"
    STREETLIGHT_FAILURE = "streetlight_failure"
    TELECOM_DISRUPTION = "telecom_disruption"
    TRAFFIC_CONGESTION = "traffic_congestion"
    ROAD_SAFETY_HAZARD = "road_safety_hazard"
    TRANSPORT_SERVICE_ISSUE = "transport_service_issue"
    VEHICLE_ACCIDENT = "vehicle_accident"

    CROP_DAMAGE = "crop_damage"
    PEST_OR_DISEASE_OUTBREAK = "pest_or_disease_outbreak"
    AGRICULTURAL_COMPENSATION_ISSUE = "agricultural_compensation_issue"
    MARKET_PRICE_DISTRESS = "market_price_distress"
    FARM_LABOUR_DISPUTE = "farm_labour_dispute"

    VIOLENT_CRIME = "violent_crime"
    PROPERTY_CRIME = "property_crime"
    SUBSTANCE_ABUSE_CASE = "substance_abuse_case"
    WOMEN_SAFETY_ISSUE = "women_safety_issue"
    PUBLIC_DISTURBANCE = "public_disturbance"
    COMMUNAL_TENSION = "communal_tension"
    CYBER_OR_FINANCIAL_FRAUD = "cyber_or_financial_fraud"
    PROTEST_OR_STRIKE = "protest_or_strike"
    LEGAL_PROCEEDING_OR_ORDER = "legal_proceeding_or_order"

    LAND_DISPUTE = "land_dispute"
    ILLEGAL_ENCROACHMENT = "illegal_encroachment"
    UNAUTHORISED_CONSTRUCTION = "unauthorised_construction"
    TENANCY_OR_EVICTION_ISSUE = "tenancy_or_eviction_issue"
    PAYOUT_OR_RECORDS_ISSUE = "payout_or_records_issue"
    FOREST_LAND_ISSUE = "forest_land_issue"

    SERVICE_DELIVERY_DELAY = "service_delivery_delay"
    PUBLIC_SCHEME_GRIEVANCE = "public_scheme_grievance"
    PENSION_OR_WAGE_DISPUTE = "pension_or_wage_dispute"
    EMPLOYMENT_DISPUTE = "employment_dispute"
    ELECTION_PROCESS_ISSUE = "election_process_issue"
    TRANSPARENCY_OR_ACCOUNTABILITY = "transparency_or_accountability"
    RELIEF_FUND_MISUSE = "relief_fund_misuse"

    OTHER = "other"
    UNRESOLVED = "unresolved"


class Department(str, Enum):
    """Tamil Nadu district administration departments, as hint targets."""

    COLLECTORATE = "collectorate"
    REVENUE = "revenue"
    RURAL_DEVELOPMENT_PANCHAYAT_RAJ = "rural_development_panchayat_raj"
    URBAN_LOCAL_BODIES = "urban_local_bodies"
    DISTRICT_DISASTER_MANAGEMENT = "district_disaster_management"
    WATER_RESOURCES = "water_resources"
    PUBLIC_HEALTH = "public_health"
    EDUCATION = "education"
    AGRICULTURE = "agriculture"
    HORTICULTURE = "horticulture"
    ANIMAL_HUSBANDRY = "animal_husbandry"
    FISHERIES = "fisheries"
    FOREST = "forest"
    ENVIRONMENT_CLIMATE_CHANGE = "environment_climate_change"
    PWD_HIGHWAYS_RURAL_ROADS = "pwd_highways_rural_roads"
    HOUSING_COMMUNITY_DEVELOPMENT = "housing_community_development"
    POWER_TANGEDCO = "power_tangedco"
    TRANSPORT_MOTOR_WAYS = "transport_motor_ways"
    POLICE = "police"
    PROHIBITION_SUBORDINATE_SERVICE = "prohibition_subordinate_service"
    SOCIAL_WELFARE = "social_welfare"
    BACKWARD_CLASSES_TBCT = "backward_classes_tbct"
    TRIBAL_WELFARE = "tribal_welfare"
    WOMEN_CHILD_DEVELOPMENT = "women_child_development"
    LABOUR_EMPLOYMENT = "labour_employment"
    COOPERATION = "cooperation"
    FOOD_CIVIL_SUPPLIES = "food_civil_supplies"
    MSME_TIRE = "msme_tire"
    INFORMATION_PUBLIC_RELATIONS = "information_public_relations"
    ELECTIONS = "elections"
    YOUTH_SERVICES_SPORTS = "youth_services_sports"
    OTHER = "other"
    UNRESOLVED = "unresolved"


class DepartmentHintBasis(str, Enum):
    TAXONOMY = "taxonomy"
    TEXT_MENTION = "text_mention"
    BOTH = "both"
    UNRESOLVED = "unresolved"


class ActorType(str, Enum):
    PERSON = "person"
    GOVERNMENT_OFFICIAL = "government_official"
    GOVERNMENT_BODY = "government_body"
    COURT = "court"
    POLICE_STATION = "police_station"
    HOSPITAL = "hospital"
    EDUCATIONAL_INSTITUTION = "educational_institution"
    LOCAL_BODY = "local_body"
    COMPANY = "company"
    NGO_OR_TRUST = "ngo_or_trust"
    COMMUNITY_GROUP = "community_group"
    POLITICIAN_OR_PARTY = "politician_or_party"
    MEDIA_OUTLET = "media_outlet"
    OTHER = "other"
    UNKNOWN = "unknown"


class ActorRole(str, Enum):
    """Semantic role an actor plays. Deliberately free of verdicts."""

    REPORTED_BY = "reported_by"
    RESPONDING_AUTHORITY = "responding_authority"
    DECISION_MAKER = "decision_maker"
    AFFECTED_PARTY = "affected_party"
    BENEFICIARY = "beneficiary"
    PETITIONER_OR_COMPLAINANT = "petitioner_or_complainant"
    ACTION_SUBJECT = "action_subject"
    REPRESENTATIVE_OR_UNION = "representative_or_union"
    WITNESS = "witness"
    OTHER = "other"
    UNKNOWN = "unknown"


class ObservationKind(str, Enum):
    """Typed quantities. Absence is encoded by simply having no Observation."""

    AFFECTED_PERSONS = "affected_persons"
    AFFECTED_HOUSEHOLDS = "affected_households"
    AFFECTED_AREA_AGRICULTURE = "affected_area_agriculture"
    FATALITIES = "fatalities"
    INJURIES = "injuries"
    DISPLACED_PERSONS = "displaced_persons"
    DETENTIONS = "detentions"
    PETITION_COUNT = "petition_count"
    PROTEST_PARTICIPANTS = "protest_participants"
    SENTENCE_YEARS = "sentence_years"
    FINE_AMOUNT = "fine_amount"
    COMPENSATION_AMOUNT = "compensation_amount"
    CLAIMED_AMOUNT = "claimed_amount"
    FUNDS_ALLOCATED = "funds_allocated"
    AREA_COVERED = "area_covered"
    RAINFALL = "rainfall"
    WATER_LEVEL = "water_level"
    DISTANCE = "distance"
    POWER_OUTAGE_DURATION = "power_outage_duration"
    SERVICE_BACKLOG_COUNT = "service_backlog_count"
    ROAD_LENGTH = "road_length"
    BUILDING_COUNT = "building_count"
    TREES_FALLED = "trees_felled"
    VEHICLES_INVOLVED = "vehicles_involved"
    AGE = "age"
    DATE_REFERENCE = "date_reference"
    COUNT_GENERIC = "count_generic"
    OTHER = "other"
    UNRESOLVED = "unresolved"


class ObservationQualifier(str, Enum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    REPORTED_UNVERIFIED = "reported_unverified"
    UNKNOWN = "unknown"


class SeverityLevel(str, Enum):
    INFO = "info"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"
    UNRESOLVED = "unresolved"


class SeverityCueCategory(str, Enum):
    """Evidence categories that can justify a severity level."""

    FATALITY = "fatality"
    INJURY = "injury"
    AFFECTED_POPULATION = "affected_population"
    AFFECTED_AREA = "affected_area"
    ECONOMIC_LOSS = "economic_loss"
    SERVICE_DISRUPTION = "service_disruption"
    ESCALATION_OR_SPREAD = "escalation_or_spread"
    CONTINUING_HAZARD = "continuing_hazard"
    LEGAL_OR_LIBERTY_RESTRICTION = "legal_or_liberty_restriction"
    AUTHORITY_ORDER = "authority_order"
    RESPONSE_MOBILISED = "response_mobilised"
    OTHER = "other"
    UNRESOLVED = "unresolved"


class DedupDecision(str, Enum):
    UNIQUE = "unique"
    DUPLICATE = "duplicate"
    MERGE_CANDIDATE = "merge_candidate"
    LINKED = "linked"
    CONTRADICTS = "contradicts"
    UNRESOLVED = "unresolved"


class ReviewReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    UNRESOLVED_EVENT_TYPE = "unresolved_event_type"
    UNRESOLVED_RELEVANCE = "unresolved_relevance"
    UNRESOLVED_SEVERITY = "unresolved_severity"
    SEVERITY_WITHOUT_EVIDENCE = "severity_without_evidence"
    AMBIGUOUS_LOCATION = "ambiguous_location"
    NO_EVENT_LOCATION_CANDIDATE = "no_event_location_candidate"
    DISTRICT_CONFLICT = "district_conflict"
    PENDING_GIS_RESOLUTION = "pending_gis_resolution"
    PUBLICATION_TIME_ONLY = "publication_time_only"
    NO_SUPPORTING_EVIDENCE = "no_supporting_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    LLM_ASSISTED = "llm_assisted"
    NON_TAMIL_UNVERIFIED = "non_tamil_unverified"
    OTHER = "other"


class DataOrigin(str, Enum):
    """How an Incident draft came to exist. Guards against fabricated inputs."""

    PIPELINE = "pipeline"
    MANUAL = "manual"
    IMPORTED = "imported"
    UNKNOWN = "unknown"
