"""Strict, registry-backed location resolution for Madurai District."""

import math
import re
from typing import Any, Dict

MADURAI_DISTRICT_BOUNDS = {
    "center": [9.9252, 78.1198],
    "default_zoom": 11,
    "min_zoom": 9,
    "max_zoom": 18,
    "bounds": [[9.55, 77.65], [10.35, 78.55]],
}

MADURAI_GEO_REGISTRY: Dict[str, Dict[str, Any]] = {
    "anna_nagar": {
        "id": "anna_nagar", "name": "Anna Nagar Uzhavar Santhai",
        "lat": 9.9179, "lng": 78.1504, "taluk": "Madurai East",
        "administrative_level": "Corporation Ward / East Zone",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "chokkikulam": {
        "id": "chokkikulam", "name": "Chokkikulam Uzhavar Santhai",
        "lat": 9.9363, "lng": 78.1384, "taluk": "Madurai North",
        "administrative_level": "Corporation Ward / North Zone",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "palanganatham": {
        "id": "palanganatham", "name": "Palanganatham Uzhavar Santhai",
        "lat": 9.9042, "lng": 78.0967, "taluk": "Madurai South",
        "administrative_level": "Corporation Ward / South Zone",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "usilampatti": {
        "id": "usilampatti", "name": "Usilampatti Uzhavar Santhai",
        "lat": 9.9701, "lng": 77.7928, "taluk": "Usilampatti",
        "administrative_level": "Usilampatti Municipality",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "thirumangalam": {
        "id": "thirumangalam", "name": "Thirumangalam Uzhavar Santhai",
        "lat": 9.8237, "lng": 77.9894, "taluk": "Thirumangalam",
        "administrative_level": "Thirumangalam Municipality",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "melur": {
        "id": "melur", "name": "Melur Uzhavar Santhai",
        "lat": 10.0347, "lng": 78.3370, "taluk": "Melur",
        "administrative_level": "Melur Municipality",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "anaiyur": {
        "id": "anaiyur", "name": "Anaiyur Uzhavar Santhai",
        "lat": 9.9632, "lng": 78.1065, "taluk": "Madurai North",
        "administrative_level": "Anaiyur Urban Zone",
        "category": "agriculture", "facility_type": "Direct Farmer Market",
    },
    "imd_madurai": {
        "id": "imd_madurai", "name": "IMD Meteorological Observatory (Station 43360)",
        "lat": 9.8344, "lng": 78.0934, "taluk": "Thiruparankundram",
        "administrative_level": "Madurai Airport Sector",
        "category": "weather", "facility_type": "Synoptic Weather Station",
    },
    "madurai_corporation": {
        "id": "madurai_corporation", "name": "Madurai Municipal Corporation HQ (Anna Maligai)",
        "lat": 9.9272, "lng": 78.1215, "taluk": "Madurai North",
        "administrative_level": "Tallakulam Civic Central",
        "category": "news", "facility_type": "Municipal Administration",
    },
    "thiruvathavur": {
        "id": "thiruvathavur", "name": "Thiruvathavur Primary Health Centre",
        "lat": 10.0163, "lng": 78.3182, "taluk": "Melur",
        "administrative_level": "Thiruvathavur Revenue Village",
        "category": "news", "facility_type": "Primary Health Centre",
    },
    "thirupparankundram": {
        "id": "thirupparankundram", "name": "Thirupparankundram Hill & Shrine Precinct",
        "lat": 9.8821, "lng": 78.0734, "taluk": "Thiruparankundram",
        "administrative_level": "Heritage Zone",
        "category": "news", "facility_type": "Sacred Complex & Hill",
    },
    "nagamalai_pudukkottai": {
        "id": "nagamalai_pudukkottai", "name": "Nagamalai Pudukkottai (NGO Colony)",
        "lat": 9.9238, "lng": 78.0416, "taluk": "Madurai West",
        "administrative_level": "Nagamalai Panchayat",
        "category": "news", "facility_type": "Residential Locality",
    },
    "melur_highway": {
        "id": "melur_highway", "name": "Melur National Highway Corridor",
        "lat": 10.0347, "lng": 78.3370, "taluk": "Melur",
        "administrative_level": "NH-38 Safety Inspection Sector",
        "category": "news", "facility_type": "Traffic Safety Inspection Area",
    },
    "sholavandan": {
        "id": "sholavandan", "name": "Sholavandan Agricultural Demonstration Farm",
        "lat": 10.0219, "lng": 78.0076, "taluk": "Vadipatti",
        "administrative_level": "Sholavandan Town Panchayat",
        "category": "news", "facility_type": "Agricultural Extension Farm",
    },
}

_PLACE_ALIASES = {
    "anna_nagar": ("anna nagar", "அண்ணா நகர்"),
    "chokkikulam": ("chokkikulam", "சொக்கிகுளம்"),
    "palanganatham": ("palanganatham", "பழங்காநத்தம்"),
    "usilampatti": ("usilampatti", "உசிலம்பட்டி"),
    "thirumangalam": ("thirumangalam", "திருமங்கலம்"),
    "melur": ("melur", "மேலூர்", "மேலுார்"),
    "anaiyur": ("anaiyur", "ஆனையூர்", "அனையூர்"),
    "thiruvathavur": ("thiruvathavur", "திருவாதவூர்"),
    "thirupparankundram": (
        "thirupparankundram", "thiruparankundram", "திருப்பரங்குன்றம்",
    ),
    "nagamalai_pudukkottai": (
        "nagamalai pudukkottai", "nagamalai", "நாகமலை",
    ),
    "melur_highway": ("melur national highway", "melur highway", "மேலூர் நெடுஞ்சாலை"),
    "sholavandan": ("sholavandan", "சோழவந்தான்"),
    "madurai_corporation": ("madurai municipal corporation hq", "anna maligai"),
}

_TALUK_ALIASES = {
    "madurai north": "Madurai North",
    "madurai south": "Madurai South",
    "madurai east": "Madurai East",
    "madurai west": "Madurai West",
    "melur": "Melur",
    "thirumangalam": "Thirumangalam",
    "tirumangalam": "Thirumangalam",
    "thiruparankundram": "Thiruparankundram",
    "thirupparankundram": "Thiruparankundram",
    "usilampatti": "Usilampatti",
    "vadipatti": "Vadipatti",
    "vadippatti": "Vadipatti",
    "peraiyur": "Peraiyur",
    "kallikudi": "Kallikudi",
    "kalligudi": "Kallikudi",
}

_DISTRICT_ALIASES = ("madurai", "மதுரை")


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("raw_text") or value.get("text") or ""
    return str(value or "").strip()


def _contains_alias(text: str, alias: str) -> bool:
    if any(ord(character) > 127 for character in alias):
        return alias.casefold() in text.casefold()
    return re.search(rf"(?<![a-z0-9]){re.escape(alias.casefold())}(?![a-z0-9])", text.casefold()) is not None


def _unresolved(status: str, reason: str, raw_text: str = "") -> Dict[str, Any]:
    return {
        "is_geocoded": False,
        "lat": None,
        "lng": None,
        "taluk": "Unassigned",
        "resolved_name": raw_text or "Unresolved location",
        "administrative_level": "Unresolved",
        "resolution_status": status,
        "reason": reason,
        "coordinate_origin": None,
        "location_precision": "area_only" if status == "UNRESOLVED_AREA_ONLY" else "unavailable",
    }


def resolve_record_location(record: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve source coordinates or explicit place references from the known registry.

    Registry coordinates are approximate display anchors for named facilities, not
    coordinates asserted by the source. Generic district/taluk text is not a point.
    """
    location = record.get("location")
    if not isinstance(location, dict):
        location = {}
    latitude = record.get("latitude", record.get("lat", location.get("latitude")))
    longitude = record.get("longitude", record.get("lng", location.get("longitude")))
    data = record.get("data")
    if isinstance(data, dict):
        latitude = latitude if latitude is not None else data.get("latitude", data.get("lat"))
        longitude = longitude if longitude is not None else data.get("longitude", data.get("lng"))
    raw_text = (
        _text_value(location)
        or _text_value(record.get("raw_location_text"))
        or _text_value(location.get("district"))
    )

    if latitude is not None or longitude is not None:
        try:
            lat = float(latitude)
            lng = float(longitude)
        except (TypeError, ValueError):
            return _unresolved(
                "INVALID_SOURCE_COORDINATES",
                "Source coordinates must contain numeric latitude and longitude values.",
                raw_text,
            )
        if not math.isfinite(lat) or not math.isfinite(lng) or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return _unresolved(
                "INVALID_SOURCE_COORDINATES",
                "Source coordinates are non-finite or outside valid latitude/longitude ranges.",
                raw_text,
            )
        southwest, northeast = MADURAI_DISTRICT_BOUNDS["bounds"]
        if southwest[1] <= lat <= northeast[1] and southwest[0] <= lng <= northeast[0]:
            return _unresolved(
                "INVALID_SOURCE_COORDINATES",
                "Source coordinates appear to have latitude and longitude reversed for this district; the values were not changed.",
                raw_text,
            )
        return {
            "is_geocoded": True,
            "lat": lat,
            "lng": lng,
            "taluk": record.get("taluk") or "Unassigned",
            "resolved_name": raw_text or "Source coordinates",
            "administrative_level": "Direct source coordinates",
            "resolution_status": "RESOLVED_EXPLICIT",
            "reason": "Latitude and longitude were supplied directly by the source.",
            "coordinate_origin": "source",
            "location_precision": "source_coordinates",
        }

    canonical_id = location.get("canonical_location_id")
    if canonical_id:
        for key, item in MADURAI_GEO_REGISTRY.items():
            if str(canonical_id) in {key, item["id"]}:
                return {
                    "is_geocoded": True,
                    "lat": item["lat"],
                    "lng": item["lng"],
                    "taluk": item["taluk"],
                    "resolved_name": item["name"],
                    "administrative_level": item["administrative_level"],
                    "resolution_status": "RESOLVED_REGISTRY",
                    "reason": f"Matched the canonical location ID to the supported {item['facility_type']} registry.",
                    "coordinate_origin": "registry_approximate",
                    "location_precision": "approximate_named_place",
                }

    source_type = str(record.get("source_type") or "").strip().casefold()
    data = record.get("data")
    if not isinstance(data, dict):
        data = {}

    if source_type == "weather":
        station_id = str(data.get("station_id") or record.get("station_id") or "").strip()
        source_id = str(record.get("source_id") or "").strip().casefold()
        if station_id == "43360" or source_id == "imd_city_madurai":
            item = MADURAI_GEO_REGISTRY["imd_madurai"]
            return {
                "is_geocoded": True,
                "lat": item["lat"],
                "lng": item["lng"],
                "taluk": item["taluk"],
                "resolved_name": item["name"],
                "administrative_level": item["administrative_level"],
                "resolution_status": "RESOLVED_REGISTRY",
                "reason": "Matched the explicit IMD Madurai station identifier 43360.",
                "coordinate_origin": "registry_approximate",
                "location_precision": "approximate_weather_station",
            }

    candidate = ""
    if source_type == "agriculture":
        candidate = _text_value(data.get("market") or record.get("market")) or raw_text
    else:
        candidate = raw_text

    for key, aliases in _PLACE_ALIASES.items():
        if key == "melur" and source_type == "news":
            continue
        if any(_contains_alias(candidate, alias) for alias in aliases):
            item = MADURAI_GEO_REGISTRY[key]
            return {
                "is_geocoded": True,
                "lat": item["lat"],
                "lng": item["lng"],
                "taluk": item["taluk"],
                "resolved_name": item["name"],
                "administrative_level": item["administrative_level"],
                "resolution_status": "RESOLVED_REGISTRY",
                "reason": (
                    f"Matched the named place to the supported {item['facility_type']} registry. "
                    "Coordinates are an approximate administrative/facility anchor."
                ),
                "coordinate_origin": "registry_approximate",
                "location_precision": "approximate_named_place",
            }

    if (
        location.get("district")
        and raw_text.casefold() == str(location["district"]).strip().casefold()
    ) or any(_contains_alias(raw_text, alias) for alias in _DISTRICT_ALIASES):
        return _unresolved(
            "UNRESOLVED_AREA_ONLY",
            "The source provides a district-level location only; no point location or centroid is asserted.",
            raw_text,
        )

    if raw_text:
        for alias, taluk in _TALUK_ALIASES.items():
            if _contains_alias(raw_text, alias):
                return _unresolved(
                    "UNRESOLVED_AREA_ONLY",
                    f"The source names {taluk} Taluk but provides no point location; no centroid is used.",
                    raw_text,
                )

    return _unresolved(
        "UNRESOLVED_INSUFFICIENT_DATA",
        "No valid source coordinates or supported named place were available.",
        raw_text,
    )
