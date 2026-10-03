"""Stage 6 word lists. These are words, not geography: no entry carries a coordinate or a canonical id."""

from __future__ import annotations

from dataclasses import dataclass

from intelligence.models.enums import ActorRole, ActorType, GranularityLevel, MentionType

LEXICON_VERSION = "2026.10-stage6"

MAX_MODIFIERS = 3
CONTEXT_WINDOW_CHARACTERS = 120
MAX_PERSON_NAME_TOKENS = 3


@dataclass(frozen=True)
class PlaceWord:
    surface: str
    mention_type: MentionType = MentionType.UNKNOWN
    granularity: GranularityLevel = GranularityLevel.UNKNOWN
    latin: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class EntityWord:
    surface: str
    actor_type: ActorType = ActorType.UNKNOWN
    latin: tuple[str, ...] = ()
    note: str = ""


#: A word that says what kind of place this is, so a type may be claimed for the mention.
PLACE_TYPES: tuple[PlaceWord, ...] = (
    PlaceWord("மாவட்டம்", MentionType.DISTRICT, GranularityLevel.DISTRICT, ("Maavattam",), "district"),
    PlaceWord("வட்டம்", MentionType.TALUK, GranularityLevel.TALUK, ("Vattam",), "taluk"),
    PlaceWord("தாலுகா", MentionType.TALUK, GranularityLevel.TALUK, ("Thaluk",), "taluk"),
    PlaceWord("வட்டாரம்", MentionType.BLOCK, GranularityLevel.BLOCK, ("Vattaram",), "block"),
    PlaceWord("உதவிமாவட்டம்", MentionType.DISTRICT, GranularityLevel.SUB_DIVISION, (), "sub-district"),
    PlaceWord("நகரம்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Nagar",), "town or colony"),
    PlaceWord("மாநகரம்", MentionType.TOWN, GranularityLevel.CORPORATION, ("Metro city",), "metro city"),
    PlaceWord("நகர்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Nagar",), "named colony"),
    PlaceWord("காலனி", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Colony",), "colony"),
    PlaceWord("பகுதி", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Pakuthi",), "area"),
    PlaceWord("வார்டு", MentionType.WARD, GranularityLevel.WARD, ("Ward",), "ward"),
    PlaceWord("ஊர்", MentionType.VILLAGE, GranularityLevel.VILLAGE, ("Oor",), "town or village"),
    PlaceWord("கிராமம்", MentionType.VILLAGE, GranularityLevel.VILLAGE, ("Gramam",), "village"),
    PlaceWord("தெரு", MentionType.STREET, GranularityLevel.STREET, ("Theru",), "street"),
    PlaceWord("வீதி", MentionType.STREET, GranularityLevel.STREET, ("Veedhi",), "street"),
    PlaceWord("சாலை", MentionType.STREET, GranularityLevel.STREET, ("Road",), "road"),
    PlaceWord("ரோடு", MentionType.STREET, GranularityLevel.STREET, ("Road",), "road"),
    PlaceWord("பாதை", MentionType.STREET, GranularityLevel.STREET, ("Path",), "path"),
    PlaceWord("மாடவீதி", MentionType.STREET, GranularityLevel.STREET, ("Maadaveethi",), "temple street"),
    PlaceWord("கோயில்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Temple",), "temple"),
    PlaceWord("கோவில்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Temple",), "temple"),
    PlaceWord("ஆலயம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Temple",), "temple"),
    PlaceWord("அலுவலகம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Office",), "office"),
    PlaceWord("நிலையம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Station",), "station"),
    PlaceWord("மருத்துவமனை", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Hospital",), "hospital"),
    PlaceWord("பள்ளி", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("School",), "school"),
    PlaceWord("கல்லூரி", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("College",), "college"),
    PlaceWord("கோபுரம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Gopuram",), "temple tower"),
    PlaceWord("வாசல்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Gateway",), "gateway"),
    PlaceWord("மண்டபம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Mandapam",), "pavilion"),
    PlaceWord("சத்திரம்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Matha",), "monastery"),
    PlaceWord("திட்டம்", MentionType.PROJECT, GranularityLevel.UNKNOWN, ("Project",), "project"),
    PlaceWord("ஆறு", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("River",), "river"),
    PlaceWord("நதி", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("River",), "river"),
    PlaceWord("குளம்", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Tank",), "tank"),
    PlaceWord("ஏரி", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Lake",), "lake"),
    PlaceWord("அணை", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Dam",), "dam"),
    PlaceWord("வாய்க்கால்", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Canal",), "canal"),
    PlaceWord("கடல்", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Sea",), "sea"),
    PlaceWord("வயல்", MentionType.AGRICULTURAL_FIELD, GranularityLevel.UNKNOWN, ("Field",), "field"),
    PlaceWord("நிலம்", MentionType.AGRICULTURAL_FIELD, GranularityLevel.UNKNOWN, ("Land",), "land"),
    PlaceWord("மாநிலம்", MentionType.UNKNOWN, GranularityLevel.STATE, ("State",), "state"),
    PlaceWord("நாடு", MentionType.UNKNOWN, GranularityLevel.COUNTRY, ("Country",), "country"),
)

#: Membership licenses the surface as a name; the type still comes from a type word in the text.
PLACE_NAMES: tuple[PlaceWord, ...] = (
    PlaceWord("மதுரை", MentionType.DISTRICT, GranularityLevel.DISTRICT, ("Madurai",), "district"),
    PlaceWord("திருமங்கலம்", MentionType.TALUK, GranularityLevel.TALUK, ("Thirumangalam",), "taluk town"),
    PlaceWord("மேலூர்", MentionType.TALUK, GranularityLevel.TALUK, ("Melur",), "taluk town"),
    PlaceWord("உசிலம்பட்டம்", MentionType.TALUK, GranularityLevel.TALUK, ("Usilampatti",), "taluk town"),
    PlaceWord("திருப்பரங்குறம்", MentionType.TALUK, GranularityLevel.TALUK, ("Thirupparankundram",), "taluk town"),
    PlaceWord("சோழவந்தான்", MentionType.TALUK, GranularityLevel.TALUK, ("Solvandhan",), "taluk town"),
    PlaceWord("அன்னூர்", MentionType.TALUK, GranularityLevel.TALUK, ("Anoor",), "taluk town"),
    PlaceWord("கல்லக்குடி", MentionType.TALUK, GranularityLevel.TALUK, ("Kalakad",), "taluk town"),
    PlaceWord("வெஞ்சி", MentionType.TALUK, GranularityLevel.TALUK, ("Venchi",), "taluk town"),
    PlaceWord("சேவி", MentionType.TALUK, GranularityLevel.TALUK, ("Sevi",), "taluk town"),
    PlaceWord("பட்டிவீரபுரம்", MentionType.TALUK, GranularityLevel.TALUK, ("Pattiveerapuram",), "taluk town"),
    PlaceWord("வைகை", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, ("Vaigai",), "river"),
    PlaceWord("தமிழகம்", MentionType.UNKNOWN, GranularityLevel.STATE, ("Tamil Nadu",), "state"),
    PlaceWord("இந்தியா", MentionType.UNKNOWN, GranularityLevel.COUNTRY, ("India",), "country"),
    PlaceWord("சென்னை", MentionType.TOWN, GranularityLevel.TOWN, ("Chennai",), "city"),
    PlaceWord("தாம்பரம்", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Tambaram",), "town"),
    PlaceWord("எழும்பூர்", MentionType.UNKNOWN, GranularityLevel.UNKNOWN, ("Egmore",), "locality"),
    PlaceWord("கோவை", MentionType.TOWN, GranularityLevel.TOWN, ("Coimbatore",), "city"),
    PlaceWord("மானாமதுரை", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Manamadurai",), "town"),
    PlaceWord("காரைக்குடி", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Karaikudi",), "town"),
    PlaceWord("ராமேஸ்வரம்", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Rameswaram",), "town"),
    PlaceWord("தோப்பூர்", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Thoppur",), "town"),
    PlaceWord("பழநி", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Palani",), "town"),
    PlaceWord("வாரணாசி", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Varanasi",), "city"),
    PlaceWord("அயோத்தி", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Ayodhya",), "city"),
    PlaceWord("புவனேஸ்வர்", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Bhubaneswar",), "city"),
    PlaceWord("பெரோஸ்பூர்", MentionType.TOWN, GranularityLevel.UNKNOWN, ("Ferozepur",), "city"),
    PlaceWord("கன்டோன்மென்ட்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Cantonment",), "cantonment"),
    PlaceWord("ஒடிசா", MentionType.UNKNOWN, GranularityLevel.STATE, ("Odisha",), "state"),
    PlaceWord("பஞ்சாப்", MentionType.UNKNOWN, GranularityLevel.STATE, ("Punjab",), "state"),
    PlaceWord("உத்திரபிரதேச", MentionType.UNKNOWN, GranularityLevel.STATE, ("Uttar Pradesh",), "state"),
    PlaceWord("கர்நாடக", MentionType.UNKNOWN, GranularityLevel.STATE, ("Karnataka",), "state"),
    PlaceWord("கேரள", MentionType.UNKNOWN, GranularityLevel.STATE, ("Kerala",), "state"),
    PlaceWord("மஹாராஷ்டிர", MentionType.UNKNOWN, GranularityLevel.STATE, ("Maharashtra",), "state"),
    PlaceWord("டெல்லி", MentionType.TOWN, GranularityLevel.TOWN, ("Delhi",), "city"),
    PlaceWord("மும்பை", MentionType.TOWN, GranularityLevel.TOWN, ("Mumbai",), "city"),
    PlaceWord("பெங்களூரு", MentionType.TOWN, GranularityLevel.TOWN, ("Bengaluru",), "city"),
    PlaceWord("ஹைதராபாத்", MentionType.TOWN, GranularityLevel.TOWN, ("Hyderabad",), "city"),
    PlaceWord("சம்பட்டிபுரம்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Sambattipuram",), "locality"),
    PlaceWord("ஸ்ரீராம்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Sriram",), "locality name"),
    PlaceWord("ஜெர்மானூஸ்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Germanos",), "locality name"),
    PlaceWord("டோக்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Token",), "locality name"),
    PlaceWord("ஜானகி", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Janaki",), "locality name"),
    PlaceWord("ஜெய்", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Jay",), "locality name"),
    PlaceWord("இருளாண்டி", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Irundandi",), "locality name"),
    PlaceWord("வாழ்வு", MentionType.LOCALITY, GranularityLevel.UNKNOWN, ("Vazhvu",), "locality name"),
    PlaceWord("தேவர்", MentionType.UNKNOWN, GranularityLevel.UNKNOWN, ("Thevar",), "honorific in a name"),
    PlaceWord("அண்ணா", MentionType.UNKNOWN, GranularityLevel.UNKNOWN, ("Anna",), "name element"),
    PlaceWord("எய்ம்ஸ்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("AIIMS",), "hospital"),
    PlaceWord("மீனாட்சி", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Meenakshi",), "temple name"),
    PlaceWord("மீனாட்சியம்மன்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Meenakshi Amman",), "temple name"),
    PlaceWord("ராமநாதசுவாமி", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Ramanathaswamy",), "temple name"),
    PlaceWord("கூடலழகர்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Koodalazhagar",), "temple name"),
    PlaceWord("ஆஞ்சநேயர்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Anjaneyar",), "temple name"),
    PlaceWord("பெருமாள்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Perumal",), "temple name"),
    PlaceWord("சுந்தரராஜ்", MentionType.INSTITUTION, GranularityLevel.LANDMARK, ("Sundararaj",), "temple name"),
    PlaceWord("சன்னதி", MentionType.UNKNOWN, GranularityLevel.UNKNOWN, ("Sannathi",), "sanctum"),
)

#: Latin words that name a place kind, for English and Tanglish surfaces.
LATIN_PLACE_WORDS: tuple[PlaceWord, ...] = (
    PlaceWord("district", MentionType.DISTRICT, GranularityLevel.DISTRICT, (), "district"),
    PlaceWord("taluk", MentionType.TALUK, GranularityLevel.TALUK, (), "taluk"),
    PlaceWord("taluka", MentionType.TALUK, GranularityLevel.TALUK, (), "taluk"),
    PlaceWord("city", MentionType.TOWN, GranularityLevel.TOWN, (), "city"),
    PlaceWord("town", MentionType.TOWN, GranularityLevel.TOWN, (), "town"),
    PlaceWord("village", MentionType.VILLAGE, GranularityLevel.VILLAGE, (), "village"),
    PlaceWord("nagar", MentionType.LOCALITY, GranularityLevel.UNKNOWN, (), "colony"),
    PlaceWord("colony", MentionType.LOCALITY, GranularityLevel.UNKNOWN, (), "colony"),
    PlaceWord("street", MentionType.STREET, GranularityLevel.STREET, (), "street"),
    PlaceWord("road", MentionType.STREET, GranularityLevel.STREET, (), "road"),
    PlaceWord("temple", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "temple"),
    PlaceWord("church", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "church"),
    PlaceWord("mosque", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "mosque"),
    PlaceWord("school", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "school"),
    PlaceWord("college", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "college"),
    PlaceWord("hospital", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "hospital"),
    PlaceWord("office", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "office"),
    PlaceWord("station", MentionType.INSTITUTION, GranularityLevel.LANDMARK, (), "station"),
    PlaceWord("river", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, (), "river"),
    PlaceWord("lake", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, (), "lake"),
    PlaceWord("dam", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, (), "dam"),
    PlaceWord("canal", MentionType.WATER_BODY, GranularityLevel.UNKNOWN, (), "canal"),
    PlaceWord("state", MentionType.UNKNOWN, GranularityLevel.STATE, (), "state"),
    PlaceWord("country", MentionType.UNKNOWN, GranularityLevel.COUNTRY, (), "country"),
)

#: Unlisted, an abstract noun in a locative case would become a place the text never located.
NON_PLACE_WORDS: frozenset[str] = frozenset(
    {
        "இடம்",
        "வேலை",
        "பணி",
        "பணிகள்",
        "கூட்டம்",
        "நிகழ்ச்சி",
        "விழா",
        "தேர்வு",
        "போட்டி",
        "நேரம்",
        "சமயம்",
        "வாரம்",
        "மாதம்",
        "ஆண்டு",
        "வருடம்",
        "காலம்",
        "பொழுது",
        "நிலை",
        "அவசரம்",
        "பிரச்சனை",
        "கோரிக்கை",
        "முடிவு",
        "நடவடிக்கை",
        "அனுமதி",
        "தடை",
        "பாதுகாப்பு",
        "ஒழுங்கு",
        "கல்வி",
        "சிகிச்சை",
        "மருந்து",
        "உணவு",
        "தண்ணீர்",
        "மின்",
        "மின்தடை",
        "சேவை",
        "போக்குவரத்து",
        "விரைவு",
        "தொடர்",
        "முன்னறிவிப்பு",
        "அறிவிப்பு",
        "புகார்",
        "வாக்குமதீ",
        "குறை",
        "இழப்பு",
        "பாதிப்பு",
        "வழக்கு",
        "தீர்ப்பு",
        "சட்டம்",
        "விதி",
        "கட்டுப்பாடு",
        "தீ",
        "புகை",
        "வெள்ளம்",
        "மழை",
        "காற்று",
        "வெயில்",
        "குளிர்",
        "நோய்",
        "ஊழல்",
        "போராட்டம்",
        "முற்றுகை",
        "தரிசனம்",
        "அன்னதானம்",
        "ஆராதனை",
        "பூஜை",
        "அபிஷேகம்",
        "கும்பாபிஷேகம்",
        "நிர்வாகம்",
        "அமைப்பு",
        "துறை",
        "நிறுவனம்",
        "வாரியம்",
        "ஆணையம்",
        "சபை",
        "குழு",
        "கமிட்டி",
        "கட்டுமானம்",
        "ரயில்",
        "பஸ்",
        "வாகனம்",
    }
)

#: Never a name element: postpositions, conjunctions, pronouns, quantifiers, time words, verbs.
NON_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "மற்றும்",
        "மென",
        "என",
        "என்று",
        "என்பது",
        "என்பதால்",
        "ஆகிய",
        "உள்ளிட்ட",
        "மூலம்",
        "வழியாக",
        "சார்ந்த",
        "இருந்து",
        "லிருந்து",
        "விலிருந்து",
        "வரை",
        "முதல்",
        "மீது",
        "முன்",
        "பின்",
        "அருகே",
        "உடன்",
        "மட்டும்",
        "எல்லாம்",
        "சில",
        "பல",
        "அனைத்து",
        "அனைவரும்",
        "மொத்தம்",
        "கூடுதல்",
        "அதிகம்",
        "மிகவும்",
        "எவ்வளவு",
        "எங்கும்",
        "எப்போது",
        "ஏன்",
        "எப்படி",
        "ஆகும்",
        "ஆனது",
        "உள்ளது",
        "இல்லை",
        "வேண்டும்",
        "முடியும்",
        "தான்",
        "ஓர்",
        "ஒரு",
        "இந்த",
        "அந்த",
        "இது",
        "அது",
        "இவை",
        "அவை",
        "இதன்",
        "அதன்",
        "அவரது",
        "தனது",
        "எங்கள்",
        "உங்கள்",
        "நாம்",
        "நான்",
        "அவர்",
        "இவர்",
        "அவர்கள்",
        "இவர்கள்",
        "எனவே",
        "எனினும்",
        "ஆனால்",
        "ஆகவே",
        "அல்லது",
        "இந்நிலையில்",
        "தற்போது",
        "இதுவரை",
        "இனி",
        "பிறகு",
        "கடந்த",
        "வரும்",
        "வந்த",
        "செல்லும்",
        "கொண்டு",
        "வைத்து",
        "எடுத்த",
        "கூறுகையில்",
        "தெரிவித்தார்",
        "தெரிவித்தனர்",
        "என்றார்",
        "என்றனர்",
        "மறுத்த",
        "மணி",
        "மணிகள்",
        "மணிக்கு",
        "மணிக்குள்",
        "காலை",
        "மாலை",
        "மதியம்",
        "நண்பகல்",
        "இரவு",
        "பகல்",
        "நேற்று",
        "இன்று",
        "நாளை",
        "முன்தினம்",
        "நேற்றும்",
        "தினமும்",
        "தொடர்கதையாக",
        "நிரந்தரமாக",
        "தற்காலிகமாக",
        "முன்கூட்டியே",
        "எவ்வித",
        "எந்த",
        "எல்லா",
        "ஒவ்வொரு",
        "மட்டுமின்றி",
        "மட்டுமல்ல",
        "உட்பட",
        "விலகி",
        "நிறைவடைந்து",
        "ஆரம்பித்து",
        "முடித்து",
        "நேரில்",
        "விரைவில்",
        "பயணத்தில்",
        "முடியாமல்",
        "வருகின்றனர்",
        "கண்காணித்தார்",
        "எதிர்ப்பார்க்கின்றனர்",
        "அனுமதிக்க",
        "தேங்கிய",
        "திணறி",
        "பயணம்",
        "போது",
        "ஆக",
        "இருந்தும்",
        "வேறு",
        "மற்ற",
        "சம்பவம்",
        "நிகழ்வு",
        "என்பதால்",
        "காரணம்",
        "பேரை",
        "பேர்",
        "உள்ளிட்டவை",
        "என்பவை",
        "மூவரும்",
        "இருவர்",
        "200",
    }
)

#: Calendar and clock vocabulary: a case-marked one of these is Stage 4's business.
CALENDAR_TOKENS: frozenset[str] = frozenset(
    {
        "அக்",
        "செப்",
        "ஆக",
        "ஜன",
        "பிப்",
        "மார்",
        "ஏப்",
        "நவ",
        "டிச",
        "ஜூ",
        "சனி",
        "ஞாயிறு",
        "திங்கடள்",
        "செவ்வாய்",
        "புதன்",
        "வியாழன்",
        "வெள்ளி",
        "சனிக்கிழமை",
        "பௌர்ணமி",
        "அமாவாசை",
        "புரட்டாசி",
        "ஆவணி",
    }
)

#: Locative-family labels that can hold a place. Dative and genitive attach to people and verbs, not places.
LOCATIVE_LABELS: frozenset[str] = frozenset(
    {"locative", "locative plural", "ablative", "superessive", "dative locative"}
)

#: Postpositions that state a route or a proximity, so the word before one is a place.
LOCATIONAL_POSTPOSITIONS: frozenset[str] = frozenset(
    {
        "வழியாக",
        "மூலம்",
        "சார்ந்த",
        "அருகே",
        "இருந்து",
        "லிருந்து",
        "விலிருந்து",
        "வரை",
        "உட்பட",
        "nearer",
        "near",
        "via",
        "through",
        "from",
        "up to",
        "towards",
        "around",
    }
)

#: Tail -> the SUFFIX_RULES label for the word it leaves behind, keyed by tail so the two cannot drift apart.
TAIL_LABELS: dict[str, str] = {
    "த்தினரும்": "genitive plural",
    "த்தினர்": "genitive plural",
    "தினரும்": "genitive plural",
    "தியர்": "genitive plural",
    "ினரும்": "nominative plural",
    "ினர்": "nominative plural",
    "வர்களும்": "nominative plural",
    "வர்கள்": "nominative plural",
    "களுக்கும்": "dative plural",
    "ளுக்குள்": "locative plural",
    "உக்குள்": "locative",
    "க்குள்": "locative",
    "களையும்": "accusative plural",
    "களும்": "nominative plural",
    "களே": "nominative plural",
    "கள": "nominative plural",
    "க்கள": "nominative plural",
    "ாரும்": "nominative plural",
    # A ம்-final noun writes மு before the clitic, so நிர்வாகமும் must lose all of "மும்" to reach நிர்வாக.
    "மும்": "emphasis",
    "உம்": "emphasis",
    "ும்": "emphasis",
    "இடை": "locative",
}

#: Tails SUFFIX_RULES does not carry. Longest first: a peel that removes too little leaves a stem no lexicon matches.
EXTRA_TAILS: tuple[str, ...] = tuple(
    sorted(TAIL_LABELS, key=lambda tail: (-len(tail), tail))
)

#: Derived, so a locative tail is spelled one way.
LOCATIVE_TAILS: frozenset[str] = frozenset(
    tail for tail, label in TAIL_LABELS.items() if label in LOCATIVE_LABELS
)

#: Words a phrase may run through to reach its head. A general adjective here would quote the sentence verb as place.
PLACE_QUALIFIERS: frozenset[str] = frozenset(
    {"மெயின்", "மேயின்", "புது", "புதிய", "பழைய", "அம்மன்", "main", "new", "old"}
)

#: Direction and sequence words that legitimately qualify a place name.
DIRECTION_QUALIFIERS: frozenset[str] = frozenset(
    {
        "தெற்கு",
        "வடக்கு",
        "கிழக்கு",
        "மேற்கு",
        "தென்",
        "வட",
        "மேல்",
        "கீழ்",
        "நடு",
        "மத்திய",
        "central",
        "east",
        "west",
        "north",
        "south",
    }
)

LATIN_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "and", "in", "on", "at", "for", "to", "from", "after",
        "before", "with", "under", "over", "near", "via", "through", "said", "stated",
        "has", "have", "had", "will", "would", "is", "are", "was", "were", "it", "its",
        "this", "that", "which", "who", "during", "about", "as", "by", "left",
    }
)

#: Body heads: a noun that says an organisation is being named.
ACTOR_HEADS: tuple[EntityWord, ...] = (
    EntityWord("மாநகராட்சி", ActorType.LOCAL_BODY, ("Corporation",), "city corporation"),
    EntityWord("நகராட்சி", ActorType.LOCAL_BODY, ("Municipality",), "municipality"),
    EntityWord("பேரூராட்சி", ActorType.LOCAL_BODY, ("Town Panchayat",), "town panchayat"),
    EntityWord("ஊராட்சி", ActorType.LOCAL_BODY, ("Village Panchayat",), "village panchayat"),
    EntityWord("பஞ்சாயத்து", ActorType.LOCAL_BODY, ("Panchayat",), "panchayat"),
    EntityWord("நிர்வாகம்", ActorType.OTHER, ("Administration",), "administration"),
    EntityWord("அமைப்பினர்", ActorType.OTHER, ("Members",), "body members"),
    EntityWord("அமைப்பு", ActorType.NGO_OR_TRUST, ("Organisation",), "organisation"),
    EntityWord("சங்கம்", ActorType.NGO_OR_TRUST, ("Association",), "association"),
    EntityWord("கழகம்", ActorType.OTHER, ("Kazhagam",), "body or party"),
    EntityWord("சபை", ActorType.OTHER, ("Council",), "council or assembly"),
    EntityWord("யூனியன்", ActorType.OTHER, ("Union",), "union"),
    EntityWord("நிறுவனம்", ActorType.COMPANY, ("Institute",), "institution or company"),
    EntityWord("துறை", ActorType.GOVERNMENT_BODY, ("Department",), "department"),
    EntityWord("வாரியம்", ActorType.GOVERNMENT_BODY, ("Board",), "board"),
    EntityWord("ஆணையம்", ActorType.GOVERNMENT_BODY, ("Commission",), "commission"),
    EntityWord("அலுவலகம்", ActorType.GOVERNMENT_BODY, ("Office",), "office"),
    EntityWord("அரசு", ActorType.GOVERNMENT_BODY, ("Government",), "government"),
    EntityWord("கமிட்டி", ActorType.OTHER, ("Committee",), "committee"),
    EntityWord("குழு", ActorType.OTHER, ("Team",), "committee or group"),
    EntityWord("நீதிமன்றம்", ActorType.COURT, ("Court",), "court"),
    EntityWord("மருத்துவமனை", ActorType.HOSPITAL, ("Hospital",), "hospital"),
    EntityWord("பள்ளி", ActorType.EDUCATIONAL_INSTITUTION, ("School",), "school"),
    EntityWord("கல்லூரி", ActorType.EDUCATIONAL_INSTITUTION, ("College",), "college"),
    EntityWord("பல்கலைக்கழகம்", ActorType.EDUCATIONAL_INSTITUTION, ("University",), "university"),
    EntityWord("காவல்", ActorType.GOVERNMENT_BODY, ("Police",), "police"),
    EntityWord("போலீஸ்", ActorType.GOVERNMENT_BODY, ("Police",), "police"),
    EntityWord("ஊடகம்", ActorType.MEDIA_OUTLET, ("Media",), "media"),
    EntityWord("corporation", ActorType.LOCAL_BODY, ("Corporation",), "corporation"),
    EntityWord("council", ActorType.LOCAL_BODY, ("Council",), "council"),
    EntityWord("municipality", ActorType.LOCAL_BODY, ("Municipality",), "municipality"),
    EntityWord("panchayat", ActorType.LOCAL_BODY, ("Panchayat",), "panchayat"),
    EntityWord("department", ActorType.GOVERNMENT_BODY, ("Department",), "department"),
    EntityWord("board", ActorType.GOVERNMENT_BODY, ("Board",), "board"),
    EntityWord("commission", ActorType.GOVERNMENT_BODY, ("Commission",), "commission"),
    EntityWord("authority", ActorType.GOVERNMENT_BODY, ("Authority",), "authority"),
    EntityWord("court", ActorType.COURT, ("Court",), "court"),
    EntityWord("hospital", ActorType.HOSPITAL, ("Hospital",), "hospital"),
    EntityWord("school", ActorType.EDUCATIONAL_INSTITUTION, ("School",), "school"),
    EntityWord("college", ActorType.EDUCATIONAL_INSTITUTION, ("College",), "college"),
    EntityWord("police", ActorType.GOVERNMENT_BODY, ("Police",), "police"),
    EntityWord("committee", ActorType.OTHER, ("Committee",), "committee"),
    EntityWord("association", ActorType.NGO_OR_TRUST, ("Association",), "association"),
    EntityWord("trust", ActorType.NGO_OR_TRUST, ("Trust",), "trust"),
    EntityWord("company", ActorType.COMPANY, ("Company",), "company"),
)

#: Collective human heads: people described by what they share, not by a named body.
COLLECTIVE_HEADS: tuple[EntityWord, ...] = (
    EntityWord("பக்தர்", ActorType.COMMUNITY_GROUP, ("Devotee",), "devotees"),
    EntityWord("பயணி", ActorType.COMMUNITY_GROUP, ("Traveller",), "travellers"),
    EntityWord("வர்த்தகர்", ActorType.COMMUNITY_GROUP, ("Trader",), "traders"),
    EntityWord("வியாபாரி", ActorType.COMMUNITY_GROUP, ("Businessperson",), "traders"),
    EntityWord("நோயாளி", ActorType.COMMUNITY_GROUP, ("Patient",), "patients"),
    EntityWord("விவசாயி", ActorType.COMMUNITY_GROUP, ("Farmer",), "farmers"),
    EntityWord("தொழிலாளர்", ActorType.COMMUNITY_GROUP, ("Labourer",), "workers"),
    EntityWord("ஊழியர்", ActorType.COMMUNITY_GROUP, ("Employee",), "employees"),
    EntityWord("மாணவன்", ActorType.COMMUNITY_GROUP, ("Student",), "students"),
    EntityWord("மாணவி", ActorType.COMMUNITY_GROUP, ("Student",), "students"),
    EntityWord("மக்கள்", ActorType.COMMUNITY_GROUP, ("People",), "the public"),
    EntityWord("மீனவன்", ActorType.COMMUNITY_GROUP, ("Fisherman",), "fishers"),
    EntityWord("மீனவர்", ActorType.COMMUNITY_GROUP, ("Fisherman",), "fishers"),
    EntityWord("அதிகாரி", ActorType.GOVERNMENT_OFFICIAL, ("Officer",), "officers"),
    EntityWord("போலீசார்", ActorType.GOVERNMENT_BODY, ("Police",), "the police"),
    EntityWord("devotees", ActorType.COMMUNITY_GROUP, ("Devotees",), "devotees"),
    EntityWord("travellers", ActorType.COMMUNITY_GROUP, ("Travellers",), "travellers"),
    EntityWord("commuters", ActorType.COMMUNITY_GROUP, ("Commuters",), "commuters"),
    EntityWord("farmers", ActorType.COMMUNITY_GROUP, ("Farmers",), "farmers"),
    EntityWord("traders", ActorType.COMMUNITY_GROUP, ("Traders",), "traders"),
    EntityWord("residents", ActorType.COMMUNITY_GROUP, ("Residents",), "residents"),
    EntityWord("passengers", ActorType.COMMUNITY_GROUP, ("Passengers",), "passengers"),
    EntityWord("officials", ActorType.GOVERNMENT_BODY, ("Officials",), "officials"),
)

#: Office titles. A title licenses a following bare token as that person's name.
OFFICIAL_TITLES: tuple[EntityWord, ...] = (
    EntityWord("கமிஷனர்", ActorType.GOVERNMENT_OFFICIAL, ("Commissioner",), "police commissioner"),
    EntityWord("கலெக்டர்", ActorType.GOVERNMENT_OFFICIAL, ("Collector",), "district collector"),
    EntityWord("ஆய்வாளர்", ActorType.GOVERNMENT_OFFICIAL, ("Inspector",), "inspector"),
    EntityWord("மேயர்", ActorType.GOVERNMENT_OFFICIAL, ("Mayor",), "mayor"),
    EntityWord("துணைமேயர்", ActorType.GOVERNMENT_OFFICIAL, ("Deputy Mayor",), "deputy mayor"),
    EntityWord("ஆணையர்", ActorType.GOVERNMENT_OFFICIAL, ("Commissioner",), "commissioner"),
    EntityWord("முதல்வர்", ActorType.GOVERNMENT_OFFICIAL, ("Chief Minister",), "chief minister"),
    EntityWord("அமைச்சர்", ActorType.GOVERNMENT_OFFICIAL, ("Minister",), "minister"),
    EntityWord("இயக்குநர்", ActorType.GOVERNMENT_OFFICIAL, ("Director",), "director"),
    EntityWord("செயலாளர்", ActorType.OTHER, ("Secretary",), "secretary"),
    EntityWord("தலைவர்", ActorType.OTHER, ("Chairman",), "chairman or president"),
    EntityWord("உதவித்தலைவர்", ActorType.OTHER, ("Vice President",), "vice president"),
    EntityWord("பொறியாளர்", ActorType.OTHER, ("Engineer",), "engineer"),
    EntityWord("பீடாதிபதி", ActorType.OTHER, ("Peethadhipathi",), "pontiff"),
    EntityWord("சுவாமிகள்", ActorType.OTHER, ("Swamigal",), "religious head"),
    EntityWord("பாகவதர்", ActorType.OTHER, ("Bhagavathar",), "religious title"),
    EntityWord("கவுன்சிலர்", ActorType.LOCAL_BODY, ("Councillor",), "ward councillor"),
    EntityWord("collector", ActorType.GOVERNMENT_OFFICIAL, ("Collector",), "collector"),
    EntityWord("commissioner", ActorType.GOVERNMENT_OFFICIAL, ("Commissioner",), "commissioner"),
    EntityWord("mayor", ActorType.GOVERNMENT_OFFICIAL, ("Mayor",), "mayor"),
    EntityWord("engineer", ActorType.OTHER, ("Engineer",), "engineer"),
    EntityWord("director", ActorType.OTHER, ("Director",), "director"),
    EntityWord("secretary", ActorType.OTHER, ("Secretary",), "secretary"),
    EntityWord("chairman", ActorType.OTHER, ("Chairman",), "chairman"),
    EntityWord("officer", ActorType.GOVERNMENT_OFFICIAL, ("Officer",), "officer"),
    EntityWord("minister", ActorType.GOVERNMENT_OFFICIAL, ("Minister",), "minister"),
    EntityWord("superintendent", ActorType.GOVERNMENT_OFFICIAL, ("Superintendent",), "superintendent"),
)

CUE_SPEECH = "speech"
CUE_RESPONSE = "response"
CUE_DEMAND = "demand"
CUE_HARM = "harm"

#: Verbs and nouns that tie an entity to what happened. A cue licenses exactly one role.
EVENT_CUES: dict[str, tuple[str, ...]] = {
    CUE_SPEECH: (
        "தெரிவி",
        "கூறுக",
        "என்ற",
        "அறிவித்த",
        "கேட்ட",
        "வாக்குமதீ",
        "coined",
        "said",
        "stated",
        "informed",
        "told",
        "added",
        "alleged",
        "clarified",
        "appealed",
        "condemned",
        "praised",
        "warned",
    ),
    CUE_RESPONSE: (
        "கண்காணி",
        "நடவடிக்கை",
        "அனுமதி",
        "பார்வையி",
        "ஏற்பாடு",
        "ஒழுங்கு",
        "கட்டுப்பா",
        "மீட்ட",
        "வழங்கி",
        "தொடங்கி",
        "நிறுத்தி",
        "சரிசெய்",
        "responded",
        "arranged",
        "ordered",
        "allowed",
        "inspected",
        "reviewed",
        "relieved",
        "restored",
        "rescued",
        "closed",
        "opened",
        "arrested",
        "registered",
        "sanctioned",
        "approved",
        "decided",
        "announced",
        "scheduled",
        "launched",
        "began",
        "will begin",
    ),
    CUE_DEMAND: (
        "போராட்ட",
        "முற்றுகை",
        "மன்றாட",
        "கோரிக்கை",
        "மனு",
        "தர்ணா",
        "கண்டன",
        "மறுத்த",
        "எதிர்பார்க்க",
        "எதிர்ப்பார்க்க",
        "protested",
        "demanded",
        "striking",
        "blocked",
    ),
    CUE_HARM: (
        "இழப்பு",
        "பாதிப்பு",
        "உயிரிழ",
        "காயம்",
        "சேதம்",
        "வெள்ள",
        "விழுந்த",
        "எரிந்த",
        "stranded",
        "damaged",
        "flooded",
        "collapsed",
        "injured",
        "killed",
        "affected",
        "disrupted",
        "cut off",
    ),
}

CUE_ROLES: dict[str, ActorRole] = {
    CUE_SPEECH: ActorRole.REPORTED_BY,
    CUE_RESPONSE: ActorRole.RESPONDING_AUTHORITY,
    CUE_DEMAND: ActorRole.PETITIONER_OR_COMPLAINANT,
    CUE_HARM: ActorRole.AFFECTED_PARTY,
}

#: A response cue is only credited to a body that can actually respond.
AUTHORITY_ACTOR_TYPES: frozenset[ActorType] = frozenset(
    {
        ActorType.GOVERNMENT_BODY,
        ActorType.GOVERNMENT_OFFICIAL,
        ActorType.LOCAL_BODY,
        ActorType.COURT,
        ActorType.POLICE_STATION,
        ActorType.HOSPITAL,
        ActorType.EDUCATIONAL_INSTITUTION,
    }
)

#: Only these types may be credited with demanding something: a corporation does not protest.
DEMAND_ACTOR_TYPES: frozenset[ActorType] = frozenset(
    {
        ActorType.COMMUNITY_GROUP,
        ActorType.NGO_OR_TRUST,
        ActorType.PERSON,
        ActorType.POLITICIAN_OR_PARTY,
        ActorType.OTHER,
        ActorType.UNKNOWN,
    }
)

#: Whose word is kept when one surface is cued in more than one sentence: acting on the event outranks being quoted.
ROLE_PRECEDENCE: tuple[ActorRole, ...] = (
    ActorRole.RESPONDING_AUTHORITY,
    ActorRole.PETITIONER_OR_COMPLAINANT,
    ActorRole.AFFECTED_PARTY,
    ActorRole.REPORTED_BY,
    ActorRole.UNKNOWN,
)

#: Coarser first: how specific a mention claims to be decides which one is the event place.
GRANULARITY_SPECIFICITY: dict[GranularityLevel, int] = {
    level: index
    for index, level in enumerate(
        (
            GranularityLevel.ADDRESS,
            GranularityLevel.STREET,
            GranularityLevel.LANDMARK,
            GranularityLevel.WARD,
            GranularityLevel.VILLAGE,
            GranularityLevel.TOWN,
            GranularityLevel.MUNICIPALITY,
            GranularityLevel.CORPORATION,
            GranularityLevel.BLOCK,
            GranularityLevel.TALUK,
            GranularityLevel.SUB_DIVISION,
            GranularityLevel.DISTRICT,
            GranularityLevel.STATE,
            GranularityLevel.COUNTRY,
            GranularityLevel.UNKNOWN,
        )
    )
}

_PLACE_LEXICONS: tuple[tuple[str, tuple[PlaceWord, ...]], ...] = (
    ("PLACE_TYPES", PLACE_TYPES),
    ("PLACE_NAMES", PLACE_NAMES),
    ("LATIN_PLACE_WORDS", LATIN_PLACE_WORDS),
)
_ACTOR_LEXICONS: tuple[tuple[str, tuple[EntityWord, ...]], ...] = (
    ("ACTOR_HEADS", ACTOR_HEADS),
    ("COLLECTIVE_HEADS", COLLECTIVE_HEADS),
    ("OFFICIAL_TITLES", OFFICIAL_TITLES),
)
_BLOCKLISTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("NON_PLACE_WORDS", NON_PLACE_WORDS),
    ("NON_NAME_TOKENS", NON_NAME_TOKENS),
    ("CALENDAR_TOKENS", CALENDAR_TOKENS),
    ("LATIN_STOP_WORDS", LATIN_STOP_WORDS),
    ("LOCATIONAL_POSTPOSITIONS", LOCATIONAL_POSTPOSITIONS),
)

#: NON_PLACE_WORDS only says a noun is not a place, and an administration is exactly that kind of noun.
_ENTITY_BLOCKLISTS: tuple[tuple[str, frozenset[str]], ...] = tuple(
    entry for entry in _BLOCKLISTS if entry[0] != "NON_PLACE_WORDS"
)


def _is_latin(surface: str) -> bool:
    return surface.isascii()


def mention_lexicon_integrity_errors() -> list[str]:
    """Every way these word lists could contradict each other, checked rather than assumed."""
    errors: list[str] = []

    def add(message: str) -> None:
        errors.append(message)

    for name, words in _PLACE_LEXICONS + _ACTOR_LEXICONS:
        seen: dict[str, list[str]] = {}
        for word in words:
            surface = word.surface
            if not surface or not surface.strip():
                add(f"{name} holds a blank surface")
                continue
            if surface != surface.strip():
                add(f"{name} surface {surface!r} carries surrounding whitespace")
            seen.setdefault(surface.strip().casefold(), []).append(surface)
            if _is_latin(surface) and surface != surface.lower():
                add(f"{name} Latin surface {surface!r} is not lowercase; folds make it unreachable")
            for latin in getattr(word, "latin", ()):
                if not latin or not latin.strip():
                    add(f"{name} surface {surface!r} states a blank Latin rendering")
                if not latin.isascii():
                    add(f"{name} surface {surface!r} renders as {latin!r}, which is not Latin script")
        for key, surfaces in seen.items():
            if len(surfaces) > 1:
                add(f"{name} lists {surfaces[0]!r} more than once as {surfaces}")

    for lexicons, blockers, reads in (
        (_PLACE_LEXICONS, _BLOCKLISTS, "place"),
        (_ACTOR_LEXICONS, _ENTITY_BLOCKLISTS, "actor"),
    ):
        for name, words in lexicons:
            for word in words:
                for block_name, blocked in blockers:
                    if word.surface in blocked:
                        add(
                            f"{word.surface!r} is a {name} entry and a {block_name} entry, "
                            f"so no {reads} lexicon reaches it"
                        )

    for (left_name, left), (right_name, right) in (
        (pair, other)
        for index, pair in enumerate(_ACTOR_LEXICONS)
        for other in _ACTOR_LEXICONS[index + 1 :]
    ):
        shared = sorted({word.surface for word in left} & {word.surface for word in right})
        if shared:
            add(f"{left_name} and {right_name} both claim {', '.join(repr(s) for s in shared)}")

    for (left_name, left), (right_name, right) in (
        (pair, other)
        for index, pair in enumerate(_PLACE_LEXICONS)
        for other in _PLACE_LEXICONS[index + 1 :]
    ):
        shared = sorted({word.surface for word in left} & {word.surface for word in right})
        if shared:
            add(f"{left_name} and {right_name} both claim {', '.join(repr(s) for s in shared)}")

    listed = [level for level, index in sorted(GRANULARITY_SPECIFICITY.items(), key=lambda item: item[1])]
    missing = sorted(level.value for level in GranularityLevel if level not in GRANULARITY_SPECIFICITY)
    if missing:
        add(f"GRANULARITY_SPECIFICITY does not rank {', '.join(missing)}")
    if len(listed) != len(GranularityLevel):
        add(f"GRANULARITY_SPECIFICITY ranks {len(listed)} levels, not {len(GranularityLevel)}")

    if set(EVENT_CUES) != set(CUE_ROLES):
        add(f"EVENT_CUES classes {sorted(EVENT_CUES)} and CUE_ROLES {sorted(CUE_ROLES)} disagree")
    owner: dict[str, str] = {}
    for cue_class, stems in EVENT_CUES.items():
        if not stems:
            add(f"{cue_class} states no cue stem")
        for stem in stems:
            if not stem or not stem.strip():
                add(f"{cue_class} holds a blank cue stem")
            if stem != stem.strip():
                add(f"{cue_class} cue stem {stem!r} carries surrounding whitespace")
            if stem in owner and owner[stem] != cue_class:
                add(f"cue {stem!r} is both {owner[stem]} and {cue_class}; one stem cannot license two roles")
            owner.setdefault(stem, cue_class)
    unranked = sorted(role.value for role in set(CUE_ROLES.values()) if role not in ROLE_PRECEDENCE)
    if unranked:
        add(f"ROLE_PRECEDENCE does not rank {', '.join(unranked)}")

    lengths = [len(tail) for tail in EXTRA_TAILS]
    if lengths != sorted(lengths, reverse=True):
        add("EXTRA_TAILS must run longest first, or a short tail is peeled before a long one")

    surfaces = {
        word.surface
        for _, words in _PLACE_LEXICONS + _ACTOR_LEXICONS
        for word in words
    }
    swallowed = sorted(set(EXTRA_TAILS) & surfaces)
    if swallowed:
        add(f"tails that are also vocabulary words, so a peel eats the word: {swallowed}")

    silent = sorted(
        f"{tail!r} as {label!r}"
        for tail, label in TAIL_LABELS.items()
        if label != "emphasis" and label not in LOCATIVE_LABELS and not label.endswith("plural")
    )
    if silent:
        add(f"peel labels neither locative, plural nor silent: {', '.join(silent)}")

    overlap = AUTHORITY_ACTOR_TYPES & DEMAND_ACTOR_TYPES
    if overlap:
        add(f"actor types both trusted to respond and expected to demand: {sorted(t.value for t in overlap)}")

    confused = sorted(DIRECTION_QUALIFIERS & NON_NAME_TOKENS)
    if confused:
        add(f"qualifiers that are also banned as name tokens: {confused}")

    for name, words in _BLOCKLISTS:
        inside = sorted(PLACE_QUALIFIERS & words)
        if inside:
            add(f"PLACE_QUALIFIERS are also {name}: {inside}")
    doubled = sorted(
        {surface for surface in PLACE_QUALIFIERS if surface in surfaces} | set(DIRECTION_QUALIFIERS & PLACE_QUALIFIERS)
    )
    if doubled:
        add(f"PLACE_QUALIFIERS that already name or qualify something: {doubled}")

    return errors
