"""CommonRecord fixtures shaped like the real October 2026 Madurai capture."""
# Copied field for field from the Dinamalar capture, stamps and commentbox twin included; nothing is read from disk.
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel
NEWS_SOURCE_ID = "dinamalar"
NEWS_SOURCE_TYPE = "news"
DISTRICT = "Madurai"
STATE = "Tamil Nadu"
RETRIEVED_AT = "2026-10-03T08:52:26.195856"
CAPTURE_RETRIEVED_AT = "2026-10-03T08:52:26.196864"
ARTICLE_URL = (
    "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/it-s-a-good-idea-to-run-northern-state-trains-via-madurai/4338218"
)
TWIN_URL = (
    "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/it-s-a-good-idea-to-run-northern-state-trains-via-madurai/4338218#commentbox"
)
CLOSURE_URL = (
    "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/pre-locking-at-madurai-meenakshi-temple-protesters-at-south-tower-gate-devotees/4338123"
)
OUTAGE_URL = (
    "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/power-outage-today-3/4338913"
)
LISTING_URL = (
    "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/todays-program--june-2nd/4338906"
)
ARTICLE_TITLE = (
    "இது நல்ல ஐடியா! வடமாநில ரயில்களை மதுரை வழியாக இயக்க"
)
CLOSURE_TITLE = (
    "மதுரை மீனாட்சி கோயிலில் முன்கூட்டியே கதவு அடைப்பு: தெற்கு கோபுரம் வாசலில் "
    "போராட்டத்தில் ஈடுபட்ட பக்தர்கள்"
)
OUTAGE_TITLE = "இன்று (அக்.3) மின்தடை"
LISTING_TITLE = "மதுரை மாவட்டம் நிகழ்ச்சி"
ENGLISH_TITLE = (
    "Madurai Corporation to widen East Madurai road after the July flooding"
)
ARTICLE_CONTENT = (
    "டைம்லைன் தற்போதைய செய்தி டிவி ப்ரீமியம் தமிழகம் இந்தியா உலகம் வர்த்தகம் விளையாட்டு "
    "கல்விமலர் டீ கடை பெஞ்ச் தினம் தினம் ஜோசியம் காலண்டர் ஆன்மிகம் வாராவாரம் இணைப்பு மலர் "
    "போட்டோ உலக தமிழர் ஸ்பெஷல் மாவட்டம் 1 UPDATED : அக் 03, 2026 12:00 AM ADDED : அக் 02, "
    "2026 05:45 PM UPDATED : அக் 03, 2026 12:00 AM ADDED : அக் 02, 2026 05:45 PM 1 மதுரை: "
    "வடமாநிலங்களின் ஆன்மிக, தொழிற்புற நகரங்களில் இருந்து ராமேஸ்வரம் செல்லும் ரயில்களை "
    "மதுரை வழியாக இயக்கினால் மருத்துவம் மற்றும் ஆன்மிக சுற்றுலாவும், தென்மாவட்ட "
    "பொருளாதாரமும் வளர்ச்சியடையும் என பயணிகளும் வர்த்தகர்களும் எதிர்ப்பார்க்கின்றனர். "
    "தென் தமிழகத்தின் முக்கிய ஆன்மிகம், கலாசாரம், வணிக நகராகத் திகழும் மதுரையை மையமாக "
    "கொண்டு, வடமாநில ரயில் சேவைகளை ஏற்படுத்த வேண்டும். காரணம், மதுரை மீனாட்சியம்மன், "
    "ராமேஸ்வரம் ராமநாதசுவாமி கோயில்கள் தென்னிந்திய ஆன்மிகச் சுற்றுலாவின் இரு முக்கிய "
    "துாண்களாக விளங்குகின்றன. வடமாநிலங்களில் இருந்து தினமும் ஆயிரக்கணக்கான பக்தர்கள் "
    "ராமேஸ்வரம் வந்து செல்கின்றனர். ஒடிசா மாநிலம் புவனேஸ்வர், பஞ்சாப் மாநிலம் பெரோஸ்பூர் "
    "கன்டோன்மென்ட், உத்திரபிரதேச மாநிலம் அயோத்தி, வாரணாசி உள்ளிட்ட வெளி மாநிலங்களில் "
    "இருந்து ராமேஸ்வரத்திற்கு வாராந்திர ரயில்கள் இயக்கப்படுகின்றன. மேலும், தாம்பரம், "
    "சென்னை எழும்பூர் (போட் மெயில், சேது எக்ஸ்பிரஸ்), கோவை ஆகிய மாநில எல்லை நகரங்களில் "
    "இருந்தும் ரயில்கள் இயக்கப்படுகின்றன. இவை அனைத்தும் மானாமதுரை, காரைக்குடி வழியாக "
    "இயக்கப்படுகின்றன. தோப்பூரில் எய்ம்ஸ் மருத்துவமனை கட்டுமான பணிகள் நிறைவடைந்து "
    "விரைவில் செயல்பாட்டுக்கு வரவுள்ளது. இதன் மூலம் தமிழகம் மட்டுமின்றி, வெளிமாநிலங்களை "
    "சேர்ந்த நோயாளிகளும் உயர் மருத்துவ சிகிச்சைக்காக மதுரைக்கு வருவது அதிகரிக்கும். "
    "இந்நிலையில், வடமாநில ரயில்கள் மதுரை வழியாக இயக்கினால் சிகிச்சைக்கு வரும் நோயாளிகள், "
    "அவர்களுடன் வரும் உதவியாளர்கள் சிரமமின்றி வந்து போக முடியும். வடமாநில பக்தர்களும் ஒரே "
    "பயணத்தில் மீனாட்சியம்மன், ராமநாதசுவாமி கோயில்களை தரிசிக்க முடியும். இது சுற்றுலாத் "
    "துறைக்கும், உள்ளூர் வியாபாரிகளுக்கும் புத்துயிர் கொடுக்கும் என்பதோடு, "
    "தென்னிந்தியாவின் முதன்மை மருத்துவம் மற்றும் ஆன்மிக சுற்றுலா மையமாக மதுரையை மாற்ற "
    "உதவும். பயணிகள், வர்த்தக அமைப்பினர் கூறுகையில், ‘‘ஆன்மிக ஸ்தலங்களான ராமேஸ்வரம், "
    "மதுரை, பழநியை இணைக்கும் வகையில் ராமேஸ்வரத்தில் இருந்து கோவைக்கு புதிய ரயில் சேவையை "
    "தொடங்க வேண்டும். இதனால், ஆன்மிக பக்தர்களுக்கு மட்டுமன்றி, தொழில் நிமித்தமாக கோவை "
    "செல்லும் வியாபாரிகள், வெளி மாநில பயணிகள் மற்றும் மீனவர்களுக்கு வரப்பிரசாதமாக "
    "அமையும். புதிதாக இயக்கப்படவுள்ள சென்னை – ராமேஸ்வரம் வந்தே பாரத் ரயிலையும் மதுரை "
    "வழியாக இயக்க வேண்டும்’’ என்றனர்."
)
CLOSURE_CONTENT = (
    "டைம்லைன் தற்போதைய செய்தி டிவி ப்ரீமியம் தமிழகம் இந்தியா உலகம் வர்த்தகம் விளையாட்டு "
    "கல்விமலர் டீ கடை பெஞ்ச் தினம் தினம் ஜோசியம் காலண்டர் ஆன்மிகம் வாராவாரம் இணைப்பு மலர் "
    "போட்டோ உலக தமிழர் ஸ்பெஷல் மாவட்டம் UPDATED : அக் 03, 2026 12:00 AM ADDED : அக் 02, "
    "2026 04:45 PM UPDATED : அக் 03, 2026 12:00 AM ADDED : அக் 02, 2026 04:45 PM மதுரை: "
    "மதுரை மீனாட்சி அம்மன் கோயிலில் கூட்ட நெரிசலை கட்டுப்படுத்த எவ்வித "
    "முன்னறிவிப்பின்றியும் முன்கூட்டியே கோபுர கதவுகள் அடைக்கப்பட்டதால் பக்தர்கள் "
    "போராட்டத்தில் ஈடுபட்டனர். மீனாட்சி அம்மன் கோயில் கும்பாபிஷேகம் செப்.,17ல் நடந்தது. "
    "நேற்றோடு மண்டல பூஜை நிறைவடைந்தது. செப்.,28 ல் முதல்வர் விஜய் தரிசனம் செய்தார். "
    "கும்பாபிஷேகம் நடந்தது முதலே பக்தர்கள் கூட்டம் தினமும் அதிகரித்து வருகிறது. கூட்டத்தை "
    "சமாளிக்க முடியாமல் போலீசாரும், கோயில் நிர்வாகத்தினரும் திணறி வருகின்றனர். "
    "கூட்டத்தால் கோயில் நடைஅடைப்பதில் காலதாமதம் ஆகிறது. கோயிலுக்குள் இருக்கும் பக்தர்கள் "
    "கூட்டம் வெளியேறுவதில் தாமதம் ஆவதால் நெரிசல் ஏற்படுகிறது. இதைதவிர்க்க சில நாட்களாக "
    "எவ்வித முன்னறிவிப்பின்றி கோயில் கதவுகளை முன்கூட்டியே அடைத்து வருகின்றனர். நேற்று "
    "முன்தினம் இரவு 7:45 மணிக்கு கோபுர வாசல்கள் அடைக்கப்பட்டு பக்தர்கள் உள்ளே "
    "அனுமதிக்கப்படவில்லை. இதனால் கொதித்தெழுந்த பக்தர்கள் தெற்கு கோபுரம் முன் "
    "போராட்டத்தில் ஈடுபட்டனர். ‘கோயிலுக்குள் கூட்ட நெரிசல் உள்ளது. உங்கள் பாதுகாப்பு "
    "கருதியே இந்நடவடிக்கை எடுக்கப்பட்டது’ என்று போலீசார் தெரிவித்தனர். இதை ஏற்க மறுத்த "
    "பக்தர்கள், ‘நாங்கள் வெளியூர்களில் இருந்து வந்துள்ளோம். மீண்டும் எப்போது வந்து அம்மனை "
    "நாங்கள் பார்க்க முடியும். முதலிலேயே அறிவிப்பாக வெளியிட்டிருந்தால் அதற்கேற்ப நாங்கள் "
    "கோயிலுக்கு வந்திருப்போம்’ என்றனர். தெற்கு கோபுரத்தில் இருந்து பக்தர்கள் நகராததால், "
    "வேறு வழியின்றி 200 பேரை மட்டும் போலீசார் உள்ளே அனுமதித்தனர். நேற்றும் விடுமுறை தினம் "
    "என்பதால் கூட்டம் அதிகம் இருந்தது. போலீஸ் கமிஷனர் ராஜேந்திரன் நேரில் "
    "ஒழுங்குப்படுத்தும் பணியை கண்காணித்தார். தினமும் தொடர்கதையாக நடந்துகொண்டிருக்கும் "
    "இப்பிரச்னைக்கு முற்றுப்புள்ளி வைக்க போலீசாரும், கோயில் நிர்வாகமும் நடவடிக்கை எடுக்க "
    "வேண்டும்."
)
OUTAGE_CONTENT = (
    "டைம்லைன் தற்போதைய செய்தி டிவி ப்ரீமியம் தமிழகம் இந்தியா உலகம் வர்த்தகம் விளையாட்டு "
    "கல்விமலர் டீ கடை பெஞ்ச் தினம் தினம் ஜோசியம் காலண்டர் ஆன்மிகம் வாராவாரம் இணைப்பு மலர் "
    "போட்டோ உலக தமிழர் ஸ்பெஷல் மாவட்டம் ADDED : அக் 03, 2026 07:47 AM ADDED : அக் 03, "
    "2026 07:47 AM (காலை 10:00 மணி முதல் மதியம் 2:00 மணி வரை) * சம்பட்டிபுரம் மெயின் "
    "ரோடு, ஜெர்மானூஸ் சில பகுதிகள், ஸ்ரீராம் நகர், எச்.எம்.எஸ்., காலனி, டோக் நகர் 4 முதல் "
    "15 தெருக்கள் வரை, தேனி மெயின் ரோடு, ஜானகி நகர், புது வாழ்வு நகர், எம்.எம். நகர் "
    "முதல் 4 தெருக்கள், இருளாண்டி தேவர் காலனி, ஜெய் நகர் முதல் மூன்று தெருக்கள்."
)
LISTING_CONTENT = (
    "டைம்லைன் தற்போதைய செய்தி டிவி ப்ரீமியம் தமிழகம் இந்தியா உலகம் வர்த்தகம் விளையாட்டு "
    "கல்விமலர் டீ கடை பெஞ்ச் தினம் தினம் ஜோசியம் காலண்டர் ஆன்மிகம் வாராவாரம் இணைப்பு மலர் "
    "போட்டோ உலக தமிழர் ஸ்பெஷல் மாவட்டம் ADDED : அக் 03, 2026 07:46 AM ADDED : அக் 03, "
    "2026 07:46 AM கோயில் * பெருமாள் கோயில் தென்மாடவீதி ஆஞ்சநேயர் கோயில் சார்பில் "
    "புரட்டாசி சனி அன்னதானம்: சுந்தரராஜ் பாகவதர் சத்திரம், கூடலழகர் பெருமாள் கோவில் "
    "சன்னதி தெரு, மதுரை, மதியம் 12:00 மணி * சிருங்கேரி 35வது பீடாதிபதி அபிநவ "
    "வித்யாகீர்த்த மஹா சுவாமிகள் ஆராதனை:"
)
ENGLISH_CONTENT = (
    "UPDATED : Oct 03, 2026 07:20 AM ADDED : Oct 02, 2026 06:10 PM Madurai: The Madurai "
    "Corporation has sanctioned widening of the East Madurai main road after the July "
    "flooding left commuters stranded for three days. Executive Engineer K. Rajendran "
    "said the work will begin on Friday and the sewer line will be relaid before the "
    "monsoon review."
)
FORECAST_TEXT = (
    "Multi cloud with thunder lightning and light rain over Madurai district"
)
ADVERTISEMENT_TITLE = "விளம்பரதாரர்கள் விவரங்கள்"
ADVERTISEMENT_CONTENT = (
    "விளம்பரம் கொடுக்க தொடர்பு கொள்ளவும். விளம்பரதாரர்கள் பட்டியல் இங்கு "
    "வெளியிடப்பட்டுள்ளது. Advertisement charges apply for the classified column; "
    "subscribe to the daily supplement."
)
MIXED_TITLE = "மதுரையில் மின்தடை: K.K. Nagar பகுதியில் power outage"
MIXED_CONTENT = (
    "மதுரை: K.K. Nagar மற்றும் அண்ணா நகர் பகுதிகளில் இன்று காலை 10 மணி முதல் "
    "மாலை 4 மணி வரை power outage தொடர்ந்தது. மின்தடை காரணமாக தையல் கடைகள் "
    "மூடப்பட்டன என்று வியாபாரிகள் தெரிவித்தனர்."
)
TIE_TITLE = "மதுரையில் வெள்ளம்: மின்தடை தொடர்கிறது"
TIE_CONTENT = (
    "மதுரை: தொடர் மழையால் கீழவாசல் தெருக்களில் வெள்ளம் தேங்கியது. அதே நேரத்தில் "
    "நான்கு மணி நேர மின்தடை காரணமாக பம்ப் ஹவுஸ் இயங்கவில்லை. நகராட்சி "
    "பணியாளர்கள் நீக்க முயன்றனர்."
)
KEYWORD_BAIT_TITLE = "மதுரை மருந்தகம் குறித்து குழு கூட்டம்"
KEYWORD_BAIT_CONTENT = (
    "மதுரை: நகர மருத்துவமனையில் டெங்கு சிகிச்சைக்காக தனி வார்டு ஏற்படுத்தப்பட்டுள்ளது. "
    "இச்சம்பவம் குறித்து போலீஸ் நிலையத்தில் புகார் அளிக்கப்பட்டுள்ளது. The incident "
    "was booked as a case, and the official said there is no problem in the matter."
)
CYCLONE_WARNING_TEXT = (
    "Orange alert: cyclonic circulation over Madurai district. "
    "Cyclone with gale winds and heavy rain likely to continue"
)
RAIN_WARNING_TEXT = "Generally cloudy sky with Light rain over Madurai district"
CROP_DISTRESS_TITLE = "வெள்ளத்தில் பயிர் நஷ்டம்: விவசாயிகள் கண்ணீர்"
CROP_DISTRESS_CONTENT = (
    "மதுரை: அழகர் கூடும் விராங்கிப்பகுதியில் வெள்ளத்தில் பயிர் சேதமடைந்தது. "
    "நட்ட ஈடு கேட்டு விவசாயிகள் மறியலில் ஈடுபட்டனர்."
)
class Place(BaseModel):
    raw_text: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
class CommonRecordShape(BaseModel):
    """ingestion's CommonRecord field for field, so ingestion is never imported here."""
    record_id: str
    source_id: str
    source_type: str
    record_type: str
    title: str
    event_time: Optional[datetime] = None
    location: Place
    data: dict[str, Any]
    severity: Optional[str] = None
    status: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: datetime
    raw_reference: Optional[str] = None
def as_record_model(payload: dict[str, Any]) -> CommonRecordShape:
    return CommonRecordShape(**payload)
def base_record(**fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_id": "NEWS-MDU-0001",
        "source_id": NEWS_SOURCE_ID,
        "source_type": NEWS_SOURCE_TYPE,
        "record_type": "article",
        "title": ARTICLE_TITLE,
        "event_time": "2026-10-02T17:45:00",
        "location": {"raw_text": DISTRICT, "district": DISTRICT, "state": STATE},
        "data": {"content": ARTICLE_CONTENT, "language": "ta"},
        "severity": None,
        "status": None,
        "source_url": ARTICLE_URL,
        "retrieved_at": RETRIEVED_AT,
        "raw_reference": ARTICLE_URL,
    }
    payload.update(fields)
    return payload
def _location_for(district: Optional[str], location: dict[str, Any]) -> dict[str, Any]:
    return {**location, "district": district, "raw_text": location["raw_text"] if district else None}
def tamil_article(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """The lead article of the capture, stamps and all."""
    payload = base_record(
        record_id="NEWS-MDU-0001",
        title=ARTICLE_TITLE,
        event_time="2026-10-02T17:45:00",
        source_url=ARTICLE_URL,
        raw_reference=ARTICLE_URL,
        retrieved_at=RETRIEVED_AT,
        data={"content": ARTICLE_CONTENT, "language": language_hint},
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def twin_article(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """The same page scraped a second time through its comment anchor."""
    payload = base_record(
        record_id="NEWS-MDU-0002",
        title=ARTICLE_TITLE,
        event_time="2026-10-02T17:45:00",
        source_url=TWIN_URL,
        raw_reference=TWIN_URL,
        retrieved_at=CAPTURE_RETRIEVED_AT,
        data={"content": ARTICLE_CONTENT, "language": language_hint},
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def temple_closure(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """Carries the only relative event date in the capture."""
    payload = base_record(
        record_id="NEWS-MDU-0004",
        title=CLOSURE_TITLE,
        event_time="2026-10-02T16:45:00",
        source_url=CLOSURE_URL,
        raw_reference=CLOSURE_URL,
        retrieved_at=CAPTURE_RETRIEVED_AT,
        data={"content": CLOSURE_CONTENT, "language": language_hint},
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def power_outage(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """Clock times with no date attached to any of them."""
    payload = base_record(
        record_id="NEWS-MDU-0006",
        title=OUTAGE_TITLE,
        event_time="2026-10-03T07:47:00",
        source_url=OUTAGE_URL,
        raw_reference=OUTAGE_URL,
        retrieved_at=CAPTURE_RETRIEVED_AT,
        data={"content": OUTAGE_CONTENT, "language": language_hint},
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def events_listing(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """A diary listing: bare weekday names and dozens of clock times."""
    payload = base_record(
        record_id="NEWS-MDU-0007",
        title=LISTING_TITLE,
        event_time="2026-10-03T07:46:00",
        source_url=LISTING_URL,
        raw_reference=LISTING_URL,
        retrieved_at=CAPTURE_RETRIEVED_AT,
        data={"content": LISTING_CONTENT, "language": language_hint},
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def english_article(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-EN-0001",
        title=ENGLISH_TITLE,
        event_time="2026-10-02T18:10:00",
        data={"content": ENGLISH_CONTENT, "language": "en"},
    )
    payload.update(fields)
    return payload
def title_only(**fields: Any) -> dict[str, Any]:
    payload = base_record(data={"content": "", "language": "ta"})
    payload.update(fields)
    return payload
def no_text(**fields: Any) -> dict[str, Any]:
    payload = base_record(title="", data={"language": "ta"})
    payload.update(fields)
    return payload
def orange_alert(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-0006",
        title=OUTAGE_TITLE,
        event_time="2026-10-03T07:47:00",
        source_url=OUTAGE_URL,
        raw_reference=OUTAGE_URL,
        retrieved_at=CAPTURE_RETRIEVED_AT,
        data={"content": OUTAGE_CONTENT, "language": "ta"},
        severity="Orange Alert",
        status="UPDATED",
    )
    payload.update(fields)
    return payload
def weather_forecast(*, district: Optional[str] = DISTRICT, **fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="WEATHER-MDU-03-Oct",
        source_id="imd",
        source_type="weather",
        record_type="forecast",
        title="Madurai Weather Forecast - 03-Oct",
        event_time="2026-10-03T00:00:00",
        source_url="https://mausam.imd.gov.in/chennai/693769",
        raw_reference="https://mausam.imd.gov.in/chennai/693769",
        retrieved_at="2026-10-03T03:22:26.195856+00:00",
        data={
            "min_temp_c": 24.5,
            "max_temp_c": 33.1,
            "forecast": FORECAST_TEXT,
            "warning": None,
            "relative_humidity_0830": 71.0,
            "relative_humidity_1730": 63.0,
            "station_id": "MDU",
        },
    )
    if district != payload["location"]["district"]:
        payload["location"] = _location_for(district, payload["location"])
    payload.update(fields)
    return payload
def capture() -> tuple[dict[str, Any], ...]:
    """The seven distinct records, one per incident id."""
    return (
        tamil_article(),
        twin_article(),
        temple_closure(),
        power_outage(),
        events_listing(),
        english_article(),
        weather_forecast(),
    )
AGRI_RETRIEVED_AT = "2026-10-03T14:03:29.086508+00:00"
IMD_RETRIEVED_AT = "2026-10-03T14:03:22.923707+00:00"
def market_price(
    *,
    market: str = "Anna nagar",
    market_id: str = "125",
    commodity: str = "Tomato",
    min_price: float = 46.0,
    max_price: float = 50.0,
    district: Optional[str] = DISTRICT,
    **fields: Any,
) -> dict[str, Any]:
    """One Uzhavar Santhai price line, field for field as the marketing feed states it."""
    place = f"{market} Uzhavar Santhai"
    url = f"https://agrimark.tn.gov.in/home/getPrice_dir/2026-10-03/21/{market_id}"
    payload = base_record(
        record_id=f"AGRI-MDU-{market.replace(' ', '_').upper()}-{commodity.upper()}-2026-10-03",
        source_id="tn_agri_marketing",
        source_type="agriculture",
        record_type="market_price",
        title=f"{commodity} price - {place}",
        event_time="2026-10-03T00:00:00",
        location={
            "raw_text": f"{place}, {district}" if district else None,
            "district": district,
            "state": STATE,
        },
        data={
            "market": market,
            "market_id": market_id,
            "commodity": commodity,
            "quantity": 800.0,
            "min_price": min_price,
            "max_price": max_price,
            "unit": "kg",
            "district_id": "21",
        },
        source_url=url,
        raw_reference=url,
        retrieved_at=AGRI_RETRIEVED_AT,
    )
    payload.update(fields)
    return payload
def weather_warning(
    *,
    text: str = RAIN_WARNING_TEXT,
    record_type: str = "warning",
    station_id: str = "43360",
    district: Optional[str] = DISTRICT,
    **fields: Any,
) -> dict[str, Any]:
    """The city weather page's warning line, with the forecast field left as empty as the capture held it."""
    url = f"https://city.imd.gov.in/citywx/city_weather_test_try_warnings.php?id={station_id}"
    payload = base_record(
        record_id=f"WEATHER-{station_id}-{record_type}",
        source_id="imd_city_madurai",
        source_type="weather",
        record_type=record_type,
        title="Madurai Weather Warning - 03-Oct",
        event_time="2026-10-03T00:00:00",
        location={
            "raw_text": district or None,
            "district": district,
            "state": STATE,
        },
        data={
            "min_temp_c": 26.0,
            "max_temp_c": 36.0,
            "forecast": "",
            "warning": text,
            "relative_humidity_0830": None,
            "relative_humidity_1730": None,
            "station_id": station_id,
        },
        source_url=url,
        raw_reference=url,
        retrieved_at=IMD_RETRIEVED_AT,
    )
    payload.update(fields)
    return payload
def crop_distress_report(
    *,
    district: Optional[str] = DISTRICT,
    language_hint: Optional[str] = "ta",
    **fields: Any,
) -> dict[str, Any]:
    """An agriculture record whose own prose states what happened."""
    payload = base_record(
        record_id="AGRI-MDU-DISTRESS-0001",
        source_id="tn_agri_marketing",
        source_type="agriculture",
        record_type="distress_report",
        title=CROP_DISTRESS_TITLE,
        event_time="2026-10-03T06:30:00",
        location={"raw_text": DISTRICT, "district": district, "state": STATE},
        data={"content": CROP_DISTRESS_CONTENT, "language": language_hint},
        source_url="https://agrimark.tn.gov.in/home/distress/2026-10-03/21",
        raw_reference="https://agrimark.tn.gov.in/home/distress/2026-10-03/21",
        retrieved_at=AGRI_RETRIEVED_AT,
    )
    payload.update(fields)
    return payload
def advertisement(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-AD-0001",
        title=ADVERTISEMENT_TITLE,
        event_time="2026-10-03T07:00:00",
        data={"content": ADVERTISEMENT_CONTENT, "language": "ta"},
        source_url="https://www.dinamalar.com/classified/advertisers/4338999",
        raw_reference="https://www.dinamalar.com/classified/advertisers/4338999",
    )
    payload.update(fields)
    return payload
def mixed_language_article(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-MIXED-0001",
        title=MIXED_TITLE,
        event_time="2026-10-03T09:00:00",
        data={"content": MIXED_CONTENT, "language": "ta"},
        source_url="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/power-cut/4338950",
        raw_reference="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/power-cut/4338950",
    )
    payload.update(fields)
    return payload
def two_hazard_article(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-TIE-0001",
        title=TIE_TITLE,
        event_time="2026-10-03T09:15:00",
        data={"content": TIE_CONTENT, "language": "ta"},
        source_url="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/flood-and-outage/4338960",
        raw_reference="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/flood-and-outage/4338960",
    )
    payload.update(fields)
    return payload
def keyword_bait_article(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id="NEWS-MDU-BAIT-0001",
        title=KEYWORD_BAIT_TITLE,
        event_time="2026-10-03T09:30:00",
        data={"content": KEYWORD_BAIT_CONTENT, "language": "ta"},
        source_url="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/ward-meeting/4338970",
        raw_reference="https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/ward-meeting/4338970",
    )
    payload.update(fields)
    return payload
