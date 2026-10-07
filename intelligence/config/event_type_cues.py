"""Stage 7 cue vocabulary: printed words that carry an event type, and words that must carry none."""

from __future__ import annotations

from dataclasses import dataclass

from intelligence.config.vocabularies import EVENT_TYPE_FAMILY
from intelligence.models.enums import ActorType, Department, EventType

CUE_LEXICON_VERSION = "2026.10-stage7"

PRIMARY = "primary"
SUPPORTING = "supporting"
STRENGTHS = (PRIMARY, SUPPORTING)


@dataclass(frozen=True)
class Cue:
    surface: str
    event_type: EventType
    strength: str = PRIMARY
    latin: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class NonEventCue:
    surface: str
    reason: str


#: Event words that carry no type of their own; matching one refuses rather than decides.
NON_CUE_WORDS: frozenset[str] = frozenset(
    {
        "சம்பவம்",
        "நிகழ்வு",
        "பிரச்னை",
        "சிக்கல்",
        "விஷயம்",
        "incident",
        "event",
        "matter",
        "issue",
        "problem",
        "case",
    }
)

#: Surfaces that state this record carries no event at all.
NON_EVENT_CUES: tuple[NonEventCue, ...] = (
    NonEventCue("விளம்பரம்", "advertisement space, states no event"),
    NonEventCue("விளம்பரதாரர்கள்", "list of advertisers, states no event"),
    NonEventCue("advertisement", "paid advertisement space"),
    NonEventCue("advertorial", "paid promotional copy"),
    NonEventCue("sponsored", "paid promotional copy"),
    NonEventCue("subscribe", "subscription prompt"),
    NonEventCue("classified", "paid classified notice"),
)

#: Feeds this platform tracks, and the record types inside them that carry an event or an observation.
TRACKED_FEEDS: dict[str, frozenset[str]] = {
    "news": frozenset({"article", "press_release", "bulletin"}),
    "weather": frozenset({"forecast", "warning", "nowcast", "alert", "observation"}),
    "agriculture": frozenset({"market_price", "crop_report", "distress_report"}),
    "disaster": frozenset({"event", "warning", "relief_operation"}),
    "grievance": frozenset({"complaint", "ticket", "petition"}),
    "municipal": frozenset({"work_order", "notice", "tender"}),
    "traffic": frozenset({"incident", "alert"}),
    "health": frozenset({"outbreak_report", "surveillance", "dashboard_row"}),
    "revenue": frozenset({"land_case", "relief_report"}),
}

#: Bodies whose name in the text states which department answers for them.
ACTOR_DEPARTMENT_HINTS: dict[ActorType, Department] = {
    ActorType.POLICE_STATION: Department.POLICE,
    ActorType.HOSPITAL: Department.PUBLIC_HEALTH,
    ActorType.EDUCATIONAL_INSTITUTION: Department.EDUCATION,
}

#: A body name that could answer to two offices is refused rather than guessed.
AMBIGUOUS_ACTOR_TYPES: frozenset[ActorType] = frozenset(
    {
        ActorType.LOCAL_BODY,
        ActorType.GOVERNMENT_BODY,
        ActorType.COMPANY,
        ActorType.COMMUNITY_GROUP,
        ActorType.OTHER,
        ActorType.UNKNOWN,
    }
)

#: Record kinds whose whole content is the district observation itself, so there is no event word to find.
OBSERVATION_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "alert",
        "crop_report",
        "dashboard_row",
        "distress_report",
        "forecast",
        "market_price",
        "nowcast",
        "observation",
        "outbreak_report",
        "warning",
    }
)

#: What a record kind says it is about, stated by the feed. A candidate, never a verdict.
RECORD_TYPE_CANDIDATES: dict[str, tuple[EventType, ...]] = {
    "market_price": (EventType.MARKET_PRICE_DISTRESS,),
    "crop_report": (EventType.CROP_DAMAGE,),
    "distress_report": (EventType.MARKET_PRICE_DISTRESS, EventType.CROP_DAMAGE),
    "outbreak_report": (EventType.DISEASE_OUTBREAK,),
    "land_case": (EventType.LAND_DISPUTE,),
    "complaint": (EventType.SERVICE_DELIVERY_DELAY,),
    "ticket": (EventType.SERVICE_DELIVERY_DELAY,),
    "petition": (EventType.SERVICE_DELIVERY_DELAY,),
}

#: Types this vocabulary is deliberately silent about, so an untested surface never fires.
UNCUED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.MALNUTRITION,
        EventType.SANITATION_HYGIENE,
        EventType.STUDENT_WELFARE_ISSUE,
        EventType.FARM_LABOUR_DISPUTE,
        EventType.TENANCY_OR_EVICTION_ISSUE,
        EventType.PAYOUT_OR_RECORDS_ISSUE,
        EventType.FOREST_LAND_ISSUE,
        EventType.PUBLIC_SCHEME_GRIEVANCE,
        EventType.RELIEF_FUND_MISUSE,
        EventType.DISASTER_RELIEF_OPERATION,
        EventType.UNRESOLVED,
        EventType.OTHER,
    }
)

# fmt: off
EVENT_TYPE_CUES: tuple[Cue, ...] = (
    # disaster
    Cue("வெள்ளம்", EventType.FLOOD, PRIMARY, ("Vellam",), "flood water"),
    Cue("வெள்ளப்பெருக்கு", EventType.FLOOD, PRIMARY, (), "flood surge"),
    Cue("வெள்ள நீர்", EventType.FLOOD, PRIMARY, (), "flood water"),
    Cue("flood", EventType.FLOOD, PRIMARY, (), "english flood"),
    Cue("flooding", EventType.FLOOD, PRIMARY, (), "english flood"),
    Cue("மழை", EventType.FLOOD, SUPPORTING, ("Rain",), "rain names a hazard only beside another cue"),
    Cue("நீர்தேக்கம்", EventType.URBAN_WATERLOGGING, PRIMARY, (), "standing water"),
    Cue("நீர்தேங்கும்", EventType.URBAN_WATERLOGGING, PRIMARY, (), "water collects"),
    Cue("waterlogging", EventType.URBAN_WATERLOGGING, PRIMARY, (), "english waterlogging"),
    Cue("waterlogged", EventType.URBAN_WATERLOGGING, PRIMARY, (), "english waterlogged"),
    Cue("புயல்", EventType.CYCLONE_STORM_DAMAGE, PRIMARY, ("Puyal",), "cyclone"),
    Cue("சூறாவளி", EventType.CYCLONE_STORM_DAMAGE, PRIMARY, (), "severe storm"),
    Cue("cyclone", EventType.CYCLONE_STORM_DAMAGE, PRIMARY, (), "english cyclone"),
    Cue("storm", EventType.CYCLONE_STORM_DAMAGE, SUPPORTING, (), "storm wind"),
    Cue("கடுங்கோடை", EventType.HEAT_WAVE, PRIMARY, (), "severe summer heat"),
    Cue("எலுமிச்சை சூடு", EventType.HEAT_WAVE, PRIMARY, (), "summer heat"),
    Cue("வெப்பநிலை உயர்வு", EventType.HEAT_WAVE, PRIMARY, (), "temperature rise"),
    Cue("heat wave", EventType.HEAT_WAVE, PRIMARY, (), "english heat wave"),
    Cue("வெப்பநிலை", EventType.HEAT_WAVE, SUPPORTING, (), "temperature"),
    Cue("படுகுளிர்", EventType.COLD_WAVE, PRIMARY, (), "bitter cold"),
    Cue("cold wave", EventType.COLD_WAVE, PRIMARY, (), "english cold wave"),
    Cue("குளிர்", EventType.COLD_WAVE, SUPPORTING, (), "cold"),
    Cue("வறட்சி", EventType.DROUGHT, PRIMARY, ("Varatchi",), "drought"),
    Cue("drought", EventType.DROUGHT, PRIMARY, (), "english drought"),
    Cue("மண் சரிவு", EventType.LANDSLIDE, PRIMARY, (), "landslip"),
    Cue("landslide", EventType.LANDSLIDE, PRIMARY, (), "english landslide"),
    Cue("மரம் சாய்ந்த", EventType.TREE_FALL, PRIMARY, (), "tree fell"),
    Cue("மரம் விழுந்த", EventType.TREE_FALL, PRIMARY, (), "tree fell on"),
    Cue("மரம் வெட்டப்பட்ட", EventType.TREE_FALL, PRIMARY, (), "tree cut down"),
    Cue("tree fell", EventType.TREE_FALL, PRIMARY, (), "english tree fell"),
    Cue("மரம்", EventType.TREE_FALL, SUPPORTING, (), "tree"),
    Cue("தீப்பிடித்த", EventType.FIRE, PRIMARY, (), "caught fire"),
    Cue("தீ விபத்து", EventType.FIRE, PRIMARY, (), "fire accident"),
    Cue("தீயணைப்பு", EventType.FIRE, SUPPORTING, (), "fire service names a response"),
    Cue("fire", EventType.FIRE, PRIMARY, (), "english fire"),
    # environment
    Cue("மாசு", EventType.POLLUTION, PRIMARY, ("Maasu",), "pollution"),
    Cue("காற்று மாசு", EventType.POLLUTION, PRIMARY, (), "air pollution"),
    Cue("pollution", EventType.POLLUTION, PRIMARY, (), "english pollution"),
    Cue("தொழிற்சாலை", EventType.POLLUTION, SUPPORTING, (), "factory"),
    Cue("குப்பை மேடு", EventType.WASTE_MANAGEMENT, PRIMARY, (), "garbage dump yard"),
    Cue("குப்பை", EventType.WASTE_MANAGEMENT, PRIMARY, ("Kuppai",), "garbage"),
    Cue("garbage", EventType.WASTE_MANAGEMENT, PRIMARY, (), "english garbage"),
    Cue("waste", EventType.WASTE_MANAGEMENT, SUPPORTING, (), "english waste"),
    # water
    Cue("குடிநீர் தட்டுப்பாடு", EventType.DRINKING_WATER_SHORTAGE, PRIMARY, (), "drinking water shortage"),
    Cue("குடிநீர்", EventType.DRINKING_WATER_SHORTAGE, SUPPORTING, ("Kudineer",), "drinking water names a supply"),
    Cue("drinking water", EventType.DRINKING_WATER_SHORTAGE, SUPPORTING, (), "english drinking water"),
    Cue("water shortage", EventType.DRINKING_WATER_SHORTAGE, PRIMARY, (), "english water shortage"),
    Cue("தட்டுப்பாடு", EventType.DRINKING_WATER_SHORTAGE, SUPPORTING, (), "shortage"),
    Cue("பாசன நீர்", EventType.IRRIGATION_WATER_ISSUE, SUPPORTING, (), "irrigation water names a supply"),
    Cue("பாசன தட்டுப்பாடு", EventType.IRRIGATION_WATER_ISSUE, PRIMARY, (), "irrigation shortage"),
    Cue("பாசனம்", EventType.IRRIGATION_WATER_ISSUE, SUPPORTING, ("Paasanam",), "irrigation names a practice"),
    Cue("irrigation", EventType.IRRIGATION_WATER_ISSUE, SUPPORTING, (), "english irrigation"),
    Cue("மேல்நிலை கால்வாய்", EventType.CANAL_OR_WATER_BODY_ISSUE, SUPPORTING, (), "upper canal names a channel"),
    Cue("கால்வாய் அடைப்பு", EventType.CANAL_OR_WATER_BODY_ISSUE, PRIMARY, (), "canal blocked up"),
    Cue("கால்வாய் தூர்வார", EventType.CANAL_OR_WATER_BODY_ISSUE, PRIMARY, (), "canal desilting asked for"),
    Cue("canal blocked", EventType.CANAL_OR_WATER_BODY_ISSUE, PRIMARY, (), "english canal blocked"),
    Cue("கால்வாய்", EventType.CANAL_OR_WATER_BODY_ISSUE, SUPPORTING, ("Kaalvaai",), "canal names a channel"),
    Cue("canal", EventType.CANAL_OR_WATER_BODY_ISSUE, SUPPORTING, (), "english canal"),
    Cue("ஏரி", EventType.CANAL_OR_WATER_BODY_ISSUE, SUPPORTING, ("Lake",), "lake"),
    Cue("அணை", EventType.CANAL_OR_WATER_BODY_ISSUE, SUPPORTING, (), "dam"),
    Cue("ஆழ்துளை கிணறு", EventType.BOREWELL_OR_WELL_ISSUE, SUPPORTING, (), "borewell names a source"),
    Cue("ஆழ்துளை கிணறு வறண்ட", EventType.BOREWELL_OR_WELL_ISSUE, PRIMARY, (), "borewell dried up"),
    Cue("கிணறு வறண்ட", EventType.BOREWELL_OR_WELL_ISSUE, PRIMARY, (), "well dried up"),
    Cue("borewell failed", EventType.BOREWELL_OR_WELL_ISSUE, PRIMARY, (), "english borewell failed"),
    Cue("borewell", EventType.BOREWELL_OR_WELL_ISSUE, SUPPORTING, (), "english borewell"),
    Cue("கிணறு", EventType.BOREWELL_OR_WELL_ISSUE, SUPPORTING, (), "well"),
    # health
    Cue("டெங்கு", EventType.DISEASE_OUTBREAK, SUPPORTING, ("Dengue",), "dengue names a disease, not a spread"),
    Cue("மலேரியா", EventType.DISEASE_OUTBREAK, SUPPORTING, (), "malaria names a disease, not a spread"),
    Cue("காலரா", EventType.DISEASE_OUTBREAK, SUPPORTING, (), "cholera names a disease, not a spread"),
    Cue("டெங்கு காய்ச்சல்", EventType.DISEASE_OUTBREAK, PRIMARY, (), "dengue fever states cases"),
    Cue("நோய் பரவிய", EventType.DISEASE_OUTBREAK, PRIMARY, (), "disease spread"),
    Cue("நோய்த்தொற்று", EventType.DISEASE_OUTBREAK, PRIMARY, (), "infection spreading"),
    Cue("dengue fever", EventType.DISEASE_OUTBREAK, PRIMARY, (), "english dengue fever"),
    Cue("outbreak", EventType.DISEASE_OUTBREAK, PRIMARY, (), "english outbreak"),
    Cue("dengue", EventType.DISEASE_OUTBREAK, SUPPORTING, (), "english dengue"),
    Cue("நோய்", EventType.DISEASE_OUTBREAK, SUPPORTING, (), "illness"),
    Cue("மருத்துவமனை", EventType.HOSPITAL_SERVICE_GAP, SUPPORTING, ("Maruthuvamanei",), "hospital names a building"),
    Cue("hospital", EventType.HOSPITAL_SERVICE_GAP, SUPPORTING, (), "english hospital"),
    Cue("படுக்கை பற்றாக்குறை", EventType.HOSPITAL_SERVICE_GAP, PRIMARY, (), "bed shortage"),
    Cue("சிகிச்சை மறுக்கப்பட்ட", EventType.HOSPITAL_SERVICE_GAP, PRIMARY, (), "treatment refused"),
    Cue("bed shortage", EventType.HOSPITAL_SERVICE_GAP, PRIMARY, (), "english bed shortage"),
    Cue("படுக்கை", EventType.HOSPITAL_SERVICE_GAP, SUPPORTING, (), "bed"),
    Cue("சிகிச்சை", EventType.HOSPITAL_SERVICE_GAP, SUPPORTING, (), "treatment"),
    Cue("மருந்து தட்டுப்பாடு", EventType.MEDICINE_SHORTAGE, PRIMARY, (), "medicine shortage"),
    Cue("medicine shortage", EventType.MEDICINE_SHORTAGE, PRIMARY, (), "english medicine shortage"),
    Cue("மாத்திரை", EventType.MEDICINE_SHORTAGE, SUPPORTING, ("Maathrai",), "tablet"),
    Cue("medicine", EventType.MEDICINE_SHORTAGE, SUPPORTING, (), "english medicine"),
    Cue("மருந்து", EventType.MEDICINE_SHORTAGE, SUPPORTING, (), "medicine"),
    Cue("கலப்படம்", EventType.FOOD_SAFETY, PRIMARY, (), "adulteration"),
    Cue("உணவு பாதுகாப்பு", EventType.FOOD_SAFETY, PRIMARY, (), "food safety"),
    Cue("food safety", EventType.FOOD_SAFETY, PRIMARY, (), "english food safety"),
    Cue("உணவு", EventType.FOOD_SAFETY, SUPPORTING, (), "food"),
    Cue("கால்நடை நோய்", EventType.VETERINARY_DISEASE, PRIMARY, (), "cattle disease"),
    Cue("கால்நடை", EventType.VETERINARY_DISEASE, SUPPORTING, (), "livestock"),
    Cue("poultry", EventType.VETERINARY_DISEASE, SUPPORTING, (), "english poultry"),
    # education
    Cue("பள்ளி கட்டிடம் சரிந்த", EventType.SCHOOL_INFRASTRUCTURE, PRIMARY, (), "school building collapsed"),
    Cue("பள்ளி கட்டிடம் பழுது", EventType.SCHOOL_INFRASTRUCTURE, PRIMARY, (), "school building in disrepair"),
    Cue("பள்ளி கட்டிடம்", EventType.SCHOOL_INFRASTRUCTURE, SUPPORTING, (), "school building names a structure"),
    Cue("பள்ளி", EventType.SCHOOL_INFRASTRUCTURE, SUPPORTING, ("Pally",), "school"),
    Cue("school", EventType.SCHOOL_INFRASTRUCTURE, SUPPORTING, (), "english school"),
    Cue("ஆசிரியர் பற்றாக்குறை", EventType.TEACHER_OR_STAFF_SHORTAGE, PRIMARY, (), "teacher shortage"),
    Cue("காலி பணியிடம்", EventType.TEACHER_OR_STAFF_SHORTAGE, PRIMARY, (), "vacant post"),
    Cue("ஆசிரியர்", EventType.TEACHER_OR_STAFF_SHORTAGE, SUPPORTING, ("Aasiriyar",), "teacher"),
    Cue("teacher", EventType.TEACHER_OR_STAFF_SHORTAGE, SUPPORTING, (), "english teacher"),
    Cue("வினாத்தாள்", EventType.EXAM_OR_ADMISSION_DISRUPTION, PRIMARY, (), "question paper"),
    Cue("தேர்வு ரத்து", EventType.EXAM_OR_ADMISSION_DISRUPTION, PRIMARY, (), "exam cancelled"),
    Cue("தேர்வு", EventType.EXAM_OR_ADMISSION_DISRUPTION, SUPPORTING, ("Thervu",), "exam"),
    Cue("exam", EventType.EXAM_OR_ADMISSION_DISRUPTION, SUPPORTING, (), "english exam"),
    # infrastructure
    Cue("சாலை பள்ளம்", EventType.ROAD_DAMAGE, PRIMARY, (), "road pit"),
    Cue("road damage", EventType.ROAD_DAMAGE, PRIMARY, (), "english road damage"),
    Cue("pothole", EventType.ROAD_DAMAGE, PRIMARY, (), "english pothole"),
    Cue("ரோடு", EventType.ROAD_DAMAGE, SUPPORTING, (), "road"),
    Cue("சாலை", EventType.ROAD_DAMAGE, SUPPORTING, ("Salai",), "road"),
    Cue("பள்ளம்", EventType.ROAD_DAMAGE, SUPPORTING, (), "pit"),
    Cue("ஓடைப் பாலம்", EventType.BRIDGE_OR_CULVERT_SAFETY, SUPPORTING, (), "culvert names a structure"),
    Cue("பாலம் சரிந்த", EventType.BRIDGE_OR_CULVERT_SAFETY, PRIMARY, (), "bridge collapsed"),
    Cue("பாலம் சேதமடைந்த", EventType.BRIDGE_OR_CULVERT_SAFETY, PRIMARY, (), "bridge damaged"),
    Cue("culvert", EventType.BRIDGE_OR_CULVERT_SAFETY, SUPPORTING, (), "english culvert"),
    Cue("பாலம்", EventType.BRIDGE_OR_CULVERT_SAFETY, SUPPORTING, ("Paalam",), "bridge"),
    Cue("கட்டிடம் சரிந்த", EventType.PUBLIC_BUILDING_DAMAGE, PRIMARY, (), "building collapsed"),
    Cue("கட்டிடம் இடிந்த", EventType.PUBLIC_BUILDING_DAMAGE, PRIMARY, (), "building gave way"),
    Cue("building collapsed", EventType.PUBLIC_BUILDING_DAMAGE, PRIMARY, (), "english building collapsed"),
    Cue("கட்டிடம்", EventType.PUBLIC_BUILDING_DAMAGE, SUPPORTING, (), "building"),
    Cue("தெருவிளக்கு வேலை செய்யவில்லை", EventType.STREETLIGHT_FAILURE, PRIMARY, (), "street light not working"),
    Cue("street light not working", EventType.STREETLIGHT_FAILURE, PRIMARY, (), "english street light not working"),
    Cue("தெருவிளக்கு", EventType.STREETLIGHT_FAILURE, SUPPORTING, (), "street light names a fitting"),
    Cue("street light", EventType.STREETLIGHT_FAILURE, SUPPORTING, (), "english street light"),
    Cue("streetlight", EventType.STREETLIGHT_FAILURE, PRIMARY, (), "english streetlight"),
    # utilities
    Cue("மின்தடை", EventType.POWER_SUPPLY_DISRUPTION, PRIMARY, ("Mindhadai",), "power cut"),
    Cue("power cut", EventType.POWER_SUPPLY_DISRUPTION, PRIMARY, (), "english power cut"),
    Cue("power outage", EventType.POWER_SUPPLY_DISRUPTION, PRIMARY, (), "english power outage"),
    Cue("மின்சாரம்", EventType.POWER_SUPPLY_DISRUPTION, SUPPORTING, (), "electricity"),
    Cue("மின்", EventType.POWER_SUPPLY_DISRUPTION, SUPPORTING, (), "electric"),
    # transport
    Cue("போக்குவரத்து நெரிசல்", EventType.TRAFFIC_CONGESTION, PRIMARY, (), "traffic rush"),
    Cue("traffic jam", EventType.TRAFFIC_CONGESTION, PRIMARY, (), "english traffic jam"),
    Cue("congestion", EventType.TRAFFIC_CONGESTION, PRIMARY, (), "english congestion"),
    Cue("போக்குவரத்து", EventType.TRAFFIC_CONGESTION, SUPPORTING, (), "traffic or transport"),
    Cue("நெரிசல்", EventType.TRAFFIC_CONGESTION, SUPPORTING, (), "rush"),
    Cue("blackspot", EventType.ROAD_SAFETY_HAZARD, PRIMARY, (), "english accident blackspot"),
    Cue("speeding", EventType.ROAD_SAFETY_HAZARD, PRIMARY, (), "english speeding"),
    Cue("அபாயம்", EventType.ROAD_SAFETY_HAZARD, SUPPORTING, (), "danger"),
    Cue("பேருந்து", EventType.TRANSPORT_SERVICE_ISSUE, SUPPORTING, ("Perundhu",), "bus"),
    Cue("ரயில்", EventType.TRANSPORT_SERVICE_ISSUE, SUPPORTING, ("Rail",), "train"),
    Cue("விமான நிலையம்", EventType.TRANSPORT_SERVICE_ISSUE, SUPPORTING, (), "airport"),
    Cue("விபத்து", EventType.VEHICLE_ACCIDENT, PRIMARY, ("Vibaththu",), "accident"),
    Cue("மோதல்", EventType.VEHICLE_ACCIDENT, PRIMARY, (), "collision"),
    Cue("accident", EventType.VEHICLE_ACCIDENT, PRIMARY, (), "english accident"),
    Cue("வாகனம்", EventType.VEHICLE_ACCIDENT, SUPPORTING, (), "vehicle"),
    Cue("lorry", EventType.VEHICLE_ACCIDENT, SUPPORTING, (), "english lorry"),
    # agriculture
    Cue("பயிர் சேதம்", EventType.CROP_DAMAGE, PRIMARY, (), "crop damage"),
    Cue("பயிர் நஷ்டம்", EventType.CROP_DAMAGE, PRIMARY, (), "crop loss"),
    Cue("crop damage", EventType.CROP_DAMAGE, PRIMARY, (), "english crop damage"),
    Cue("பயிர்", EventType.CROP_DAMAGE, SUPPORTING, ("Payir",), "crop"),
    Cue("நெல்", EventType.CROP_DAMAGE, SUPPORTING, (), "paddy"),
    Cue("பூச்சி தாக்குதல்", EventType.PEST_OR_DISEASE_OUTBREAK, PRIMARY, (), "pest attack"),
    Cue("pest", EventType.PEST_OR_DISEASE_OUTBREAK, PRIMARY, (), "english pest"),
    Cue("இழப்பீடு", EventType.AGRICULTURAL_COMPENSATION_ISSUE, PRIMARY, (), "compensation"),
    Cue("நட்ட ஈடு", EventType.AGRICULTURAL_COMPENSATION_ISSUE, PRIMARY, (), "loss compensation"),
    Cue("compensation", EventType.AGRICULTURAL_COMPENSATION_ISSUE, PRIMARY, (), "english compensation"),
    Cue("விலை வீழ்ச்சி", EventType.MARKET_PRICE_DISTRESS, PRIMARY, (), "price fall"),
    Cue("விலை குறைந்த", EventType.MARKET_PRICE_DISTRESS, PRIMARY, (), "price dropped"),
    Cue("price crash", EventType.MARKET_PRICE_DISTRESS, PRIMARY, (), "english price crash"),
    Cue("விலை", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, (), "price"),
    Cue("price", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, (), "english price"),
    Cue("market", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, (), "english market"),
    Cue("உழவர் சந்தை", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, ("Uzhavar Santhai",), "farmers market"),
    Cue("மார்க்கெட்", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, (), "market"),
    Cue("சந்தை", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, (), "market"),
    Cue("விவசாயி", EventType.MARKET_PRICE_DISTRESS, SUPPORTING, ("Vivasayi",), "farmer"),
    # law and order
    Cue("கொலை", EventType.VIOLENT_CRIME, PRIMARY, ("Kolai",), "murder"),
    Cue("தாக்குதல்", EventType.VIOLENT_CRIME, PRIMARY, (), "attack"),
    Cue("murder", EventType.VIOLENT_CRIME, PRIMARY, (), "english murder"),
    Cue("assault", EventType.VIOLENT_CRIME, PRIMARY, (), "english assault"),
    Cue("திருட்டு", EventType.PROPERTY_CRIME, PRIMARY, ("Thirattu",), "theft"),
    Cue("களவாணி", EventType.PROPERTY_CRIME, PRIMARY, (), "burglar"),
    Cue("theft", EventType.PROPERTY_CRIME, PRIMARY, (), "english theft"),
    Cue("burglary", EventType.PROPERTY_CRIME, PRIMARY, (), "english burglary"),
    Cue("மதுபான", EventType.SUBSTANCE_ABUSE_CASE, SUPPORTING, (), "liquor"),
    Cue("excise", EventType.SUBSTANCE_ABUSE_CASE, SUPPORTING, (), "english excise"),
    Cue("bootleg", EventType.SUBSTANCE_ABUSE_CASE, PRIMARY, (), "english bootleg"),
    Cue("பாலியல்", EventType.WOMEN_SAFETY_ISSUE, PRIMARY, (), "sexual offence"),
    Cue("rape", EventType.WOMEN_SAFETY_ISSUE, PRIMARY, (), "english rape"),
    Cue("harassment", EventType.WOMEN_SAFETY_ISSUE, PRIMARY, (), "english harassment"),
    Cue("பெண்", EventType.WOMEN_SAFETY_ISSUE, SUPPORTING, (), "woman"),
    Cue("மதக் கலவரம்", EventType.COMMUNAL_TENSION, PRIMARY, (), "communal riot"),
    Cue("கலவரம்", EventType.COMMUNAL_TENSION, PRIMARY, (), "riot"),
    Cue("communal", EventType.COMMUNAL_TENSION, PRIMARY, (), "english communal"),
    Cue("மோசடி", EventType.CYBER_OR_FINANCIAL_FRAUD, PRIMARY, ("Mosadi",), "fraud"),
    Cue("fraud", EventType.CYBER_OR_FINANCIAL_FRAUD, PRIMARY, (), "english fraud"),
    Cue("cyber", EventType.CYBER_OR_FINANCIAL_FRAUD, SUPPORTING, (), "english cyber"),
    Cue("scam", EventType.CYBER_OR_FINANCIAL_FRAUD, SUPPORTING, (), "english scam"),
    Cue("போராட்டம்", EventType.PROTEST_OR_STRIKE, PRIMARY, ("Porattam",), "protest"),
    Cue("ஆர்ப்பாட்டம்", EventType.PROTEST_OR_STRIKE, PRIMARY, (), "agitation"),
    Cue("வேலை நிறுத்தம்", EventType.PROTEST_OR_STRIKE, PRIMARY, (), "strike"),
    Cue("மறியல்", EventType.PROTEST_OR_STRIKE, PRIMARY, (), "dharna"),
    Cue("protest", EventType.PROTEST_OR_STRIKE, PRIMARY, (), "english protest"),
    Cue("strike", EventType.PROTEST_OR_STRIKE, PRIMARY, (), "english strike"),
    Cue("தீர்ப்பு", EventType.LEGAL_PROCEEDING_OR_ORDER, PRIMARY, (), "verdict"),
    Cue("உத்தரவு", EventType.LEGAL_PROCEEDING_OR_ORDER, PRIMARY, (), "order"),
    Cue("தடையுத்தரவு", EventType.LEGAL_PROCEEDING_OR_ORDER, PRIMARY, (), "injunction"),
    Cue("verdict", EventType.LEGAL_PROCEEDING_OR_ORDER, PRIMARY, (), "english verdict"),
    Cue("நீதிமன்றம்", EventType.LEGAL_PROCEEDING_OR_ORDER, SUPPORTING, (), "court"),
    Cue("வழக்கு", EventType.LEGAL_PROCEEDING_OR_ORDER, SUPPORTING, ("Vazhakku",), "case"),
    # revenue and land
    Cue("ஆக்கிரமிப்பு", EventType.ILLEGAL_ENCROACHMENT, PRIMARY, (), "encroachment"),
    Cue("encroachment", EventType.ILLEGAL_ENCROACHMENT, PRIMARY, (), "english encroachment"),
    Cue("அனுமதியின்றி", EventType.UNAUTHORISED_CONSTRUCTION, PRIMARY, (), "without permission"),
    Cue("அனுமதி இல்லாத", EventType.UNAUTHORISED_CONSTRUCTION, PRIMARY, (), "unauthorised"),
    Cue("unauthorised construction", EventType.UNAUTHORISED_CONSTRUCTION, PRIMARY, (), "english unauthorised construction"),
    Cue("நில விவகாரம்", EventType.LAND_DISPUTE, PRIMARY, (), "land dispute"),
    Cue("land dispute", EventType.LAND_DISPUTE, PRIMARY, (), "english land dispute"),
    Cue("நிலம்", EventType.LAND_DISPUTE, SUPPORTING, (), "land"),
    # governance
    Cue("நிலுவையில்", EventType.SERVICE_DELIVERY_DELAY, PRIMARY, (), "lying pending"),
    Cue("delay", EventType.SERVICE_DELIVERY_DELAY, SUPPORTING, (), "english delay"),
    Cue("pending", EventType.SERVICE_DELIVERY_DELAY, SUPPORTING, (), "english pending"),
    Cue("தாமதம்", EventType.SERVICE_DELIVERY_DELAY, SUPPORTING, (), "delay"),
    Cue("ஓய்வூதியம் நிறுத்தப்பட்ட", EventType.PENSION_OR_WAGE_DISPUTE, PRIMARY, (), "pension stopped"),
    Cue("pension stopped", EventType.PENSION_OR_WAGE_DISPUTE, PRIMARY, (), "english pension stopped"),
    Cue("ஓய்வூதியம்", EventType.PENSION_OR_WAGE_DISPUTE, SUPPORTING, (), "pension names a payment"),
    Cue("pension", EventType.PENSION_OR_WAGE_DISPUTE, SUPPORTING, (), "english pension"),
    Cue("சம்பளம்", EventType.PENSION_OR_WAGE_DISPUTE, SUPPORTING, (), "wage"),
    Cue("கூலி", EventType.PENSION_OR_WAGE_DISPUTE, SUPPORTING, (), "wage for labour"),
    Cue("வேலைவாய்ப்பு", EventType.EMPLOYMENT_DISPUTE, SUPPORTING, (), "employment"),
    Cue("நியமனம்", EventType.EMPLOYMENT_DISPUTE, SUPPORTING, (), "appointment"),
    Cue("recruitment", EventType.EMPLOYMENT_DISPUTE, SUPPORTING, (), "english recruitment"),
    Cue("வாக்காளர் பட்டியல்", EventType.ELECTION_PROCESS_ISSUE, PRIMARY, (), "voter roll"),
    Cue("தேர்தல்", EventType.ELECTION_PROCESS_ISSUE, SUPPORTING, ("Thethal",), "election names a process"),
    Cue("election", EventType.ELECTION_PROCESS_ISSUE, SUPPORTING, (), "english election"),
    Cue("வாக்காளர்", EventType.ELECTION_PROCESS_ISSUE, SUPPORTING, (), "voter"),
    Cue("லஞ்சம்", EventType.TRANSPARENCY_OR_ACCOUNTABILITY, PRIMARY, ("Lancham",), "bribe"),
    Cue("ஊழல்", EventType.TRANSPARENCY_OR_ACCOUNTABILITY, PRIMARY, (), "corruption"),
    Cue("bribe", EventType.TRANSPARENCY_OR_ACCOUNTABILITY, PRIMARY, (), "english bribe"),
    Cue("corruption", EventType.TRANSPARENCY_OR_ACCOUNTABILITY, PRIMARY, (), "english corruption"),
    Cue("vigilance", EventType.TRANSPARENCY_OR_ACCOUNTABILITY, SUPPORTING, (), "english vigilance"),
    # informational
    Cue("விருது", EventType.CEREMONIAL_OR_AWARD_EVENT, PRIMARY, ("Viruthu",), "award"),
    Cue("விழா", EventType.CEREMONIAL_OR_AWARD_EVENT, PRIMARY, ("Vizha",), "festival or ceremony"),
    Cue("award", EventType.CEREMONIAL_OR_AWARD_EVENT, PRIMARY, (), "english award"),
    Cue("பரிசு", EventType.CEREMONIAL_OR_AWARD_EVENT, SUPPORTING, (), "prize"),
    Cue("நிகழ்ச்சி", EventType.CEREMONIAL_OR_AWARD_EVENT, SUPPORTING, ("Nigalchi",), "programme"),
    Cue("அறிவிப்பு", EventType.ANNOUNCEMENT_ONLY, SUPPORTING, (), "announcement"),
    Cue("announced", EventType.ANNOUNCEMENT_ONLY, SUPPORTING, (), "english announced"),
    Cue("இடமாற்றம்", EventType.PERSONNEL_TRANSFER, PRIMARY, (), "transfer posting"),
    Cue("transfer", EventType.PERSONNEL_TRANSFER, SUPPORTING, (), "english transfer"),
    Cue("விளையாட்டு", EventType.CULTURAL_OR_SPORTS_EVENT, SUPPORTING, (), "sport"),
    Cue("போட்டி", EventType.CULTURAL_OR_SPORTS_EVENT, SUPPORTING, ("Potti",), "competition"),
    Cue("cricket", EventType.CULTURAL_OR_SPORTS_EVENT, SUPPORTING, (), "english cricket"),
    Cue("stadium", EventType.CULTURAL_OR_SPORTS_EVENT, SUPPORTING, (), "english stadium"),
)
# fmt: on


def cues_for(event_type: EventType) -> tuple[Cue, ...]:
    return tuple(cue for cue in EVENT_TYPE_CUES if cue.event_type is event_type)


def cue_surfaces() -> tuple[str, ...]:
    return tuple(cue.surface for cue in EVENT_TYPE_CUES)


def feed_is_tracked(source_type: str, record_type: str) -> bool:
    return record_type in TRACKED_FEEDS.get(source_type, frozenset())


def is_observation_feed(source_type: str, record_type: str) -> bool:
    return feed_is_tracked(source_type, record_type) and record_type in OBSERVATION_RECORD_TYPES


def candidates_for(record_type: str) -> tuple[EventType, ...]:
    return RECORD_TYPE_CANDIDATES.get(record_type, ())


def cue_lexicon_integrity_errors() -> list[str]:
    """Report drift between the cue vocabulary and the Stage 1 taxonomy. Empty list is clean."""
    from intelligence.extraction.mention_text import fold

    errors: list[str] = []
    members = set(EventType)
    non_cue_folded = {fold(word) for word in NON_CUE_WORDS}
    non_event_folded = {fold(row.surface) for row in NON_EVENT_CUES}
    claims: dict[str, set[EventType]] = {}

    for cue in EVENT_TYPE_CUES:
        where = f"event cue {cue.surface!r} -> {cue.event_type.value}"
        if not cue.surface.strip() or cue.surface != cue.surface.strip():
            errors.append(f"{where}: surface is blank or carries surrounding whitespace")
        if cue.strength not in STRENGTHS:
            errors.append(f"{where}: strength {cue.strength!r} is not one of {STRENGTHS}")
        if cue.event_type not in members:
            errors.append(f"{where} is not an EventType member")
            continue
        if EVENT_TYPE_FAMILY.get(cue.event_type) is None:
            errors.append(f"{where} has no family in EVENT_TYPE_FAMILIES")
        if cue.event_type in (EventType.UNRESOLVED, EventType.OTHER):
            errors.append(f"{where} claims a type that states no decision")
        if cue.event_type in UNCUED_EVENT_TYPES:
            errors.append(f"{where} claims a type the vocabulary declares deliberately uncueable")
        if not cue.note:
            errors.append(f"{where} carries no gloss for the next reader")

        key = fold(cue.surface)
        if key in non_cue_folded:
            errors.append(f"{where}: surface is also a declared non-cue word")
        if key in non_event_folded:
            errors.append(f"{where}: surface claims an event type and no event at once")
        claims.setdefault(key, set()).add(cue.event_type)

        for latin in cue.latin:
            if not latin.strip():
                errors.append(f"{where}: blank Latin spelling")
            claims.setdefault(fold(latin), set()).add(cue.event_type)

    for key, claimed in sorted(claims.items()):
        if len(claimed) > 1:
            errors.append(
                f"cue key {key!r} claims "
                + ", ".join(sorted(event_type.value for event_type in claimed))
            )

    for row in NON_EVENT_CUES:
        if row.surface.strip() != row.surface or not row.reason:
            errors.append(f"non-event cue {row.surface!r} is blank or states no reason")

    mapped = set(ACTOR_DEPARTMENT_HINTS) & set(AMBIGUOUS_ACTOR_TYPES)
    for actor_type in sorted(mapped, key=lambda item: item.name):
        errors.append(f"actor type {actor_type.name} is both refused and mapped to a department")

    for actor_type, department in ACTOR_DEPARTMENT_HINTS.items():
        if department in (Department.UNRESOLVED, Department.OTHER):
            errors.append(f"actor type {actor_type.name} hints to {department.value}")

    tracked = {record_type for types in TRACKED_FEEDS.values() for record_type in types}
    for record_type in sorted(OBSERVATION_RECORD_TYPES - tracked):
        errors.append(f"observation record kind {record_type!r} is not a tracked feed's type")
    for record_type, candidates in sorted(RECORD_TYPE_CANDIDATES.items()):
        if record_type not in tracked:
            errors.append(f"record kind {record_type!r} states candidates but no feed tracks it")
        for event_type in candidates:
            if event_type not in members:
                errors.append(f"record kind {record_type!r} candidates {event_type!r} is not an EventType")
            elif event_type in (EventType.UNRESOLVED, EventType.OTHER):
                errors.append(f"record kind {record_type!r} candidates {event_type.value}")

    return errors


__all__ = [
    "ACTOR_DEPARTMENT_HINTS",
    "AMBIGUOUS_ACTOR_TYPES",
    "CUE_LEXICON_VERSION",
    "EVENT_TYPE_CUES",
    "NON_CUE_WORDS",
    "NON_EVENT_CUES",
    "NonEventCue",
    "OBSERVATION_RECORD_TYPES",
    "PRIMARY",
    "RECORD_TYPE_CANDIDATES",
    "STRENGTHS",
    "SUPPORTING",
    "TRACKED_FEEDS",
    "UNCUED_EVENT_TYPES",
    "Cue",
    "candidates_for",
    "cue_lexicon_integrity_errors",
    "cue_surfaces",
    "cues_for",
    "feed_is_tracked",
    "is_observation_feed",
]
