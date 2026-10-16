"""Madurai District GIS API for saved incidents and source records."""

from threading import Lock
from datetime import date
from typing import Any, Dict, List, Optional

import uvicorn
from config import GIS_CORS_ORIGINS, SERVER_HOST, SERVER_PORT
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from services.data_loader import GISDataUnavailable, load_gis_dataset
from services.geojson_builder import build_geojson_feature_collection
from services.taluka_service import get_taluka_geojson_with_severity, load_taluka_boundaries

app = FastAPI(
    title="Madurai District GIS API",
    description="Geographic processing API for persisted intelligence incidents.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=GIS_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory dataset cache
_cached_dataset: Optional[Dict[str, Any]] = None
_dataset_lock = Lock()


def get_dataset(force_reload: bool = False) -> Dict[str, Any]:
    """Return the cached live dataset, or explicitly refresh it."""
    global _cached_dataset
    with _dataset_lock:
        if force_reload:
            _cached_dataset = None
        if _cached_dataset is None:
            try:
                _cached_dataset = load_gis_dataset()
            except GISDataUnavailable as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
    return _cached_dataset


def _filter_records(
    records: List[Dict[str, Any]],
    *,
    category: Optional[str] = None,
    source_type: Optional[str] = None,
    record_kind: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    taluk: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    selected = records
    filters = (
        ("category", category),
        ("source_type", source_type),
        ("record_kind", record_kind),
        ("severity", severity),
        ("status", status),
        ("taluk", taluk),
    )
    for field, expected in filters:
        if expected and expected.casefold() != "all":
            selected = [
                record for record in selected
                if str(record.get(field) or "").casefold() == expected.casefold()
            ]
    if start_date:
        selected = [
            record for record in selected
            if record.get("date_only") and record["date_only"] >= start_date.isoformat()
        ]
    if end_date:
        selected = [
            record for record in selected
            if record.get("date_only") and record["date_only"] <= end_date.isoformat()
        ]
    if search and search.strip():
        term = search.strip().casefold()
        selected = [
            record for record in selected
            if term in record.get("search_string", "")
        ]
    return selected


@app.get("/", response_class=JSONResponse)
async def service_info() -> JSONResponse:
    """Identify the GIS API and link to its interactive documentation."""
    return JSONResponse(content={
        "service": "Madurai District GIS API",
        "documentation": "/docs",
        "map": "Open Map View in the District Intelligence Collector.",
    })


@app.get("/api/incidents", response_class=JSONResponse)
async def get_incidents(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    taluk: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
) -> JSONResponse:
    """Return normalized, persisted intelligence incidents only."""
    dataset = get_dataset()
    records = _filter_records(
        dataset["incident_records"],
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        severity=severity,
        status=status,
        taluk=taluk,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    return JSONResponse(content=records)


@app.get("/api/geojson", response_class=JSONResponse)
async def get_geojson(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    taluk: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
) -> JSONResponse:
    """Return RFC 7946 GeoJSON for mapped persisted incidents."""
    dataset = get_dataset()
    records = _filter_records(
        dataset.get("incident_mapped_records", dataset["mapped_records"]),
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        severity=severity,
        status=status,
        taluk=taluk,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    geojson_data = build_geojson_feature_collection(records)
    return JSONResponse(content=geojson_data)


@app.get("/api/unmapped", response_class=JSONResponse)
async def get_unmapped_records(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
) -> JSONResponse:
    """Return persisted incidents without a reliable map location."""
    dataset = get_dataset()
    records = _filter_records(
        dataset.get("incident_unmapped_records", dataset["unmapped_records"]),
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        search=search,
    )
    return JSONResponse(content=records)


@app.get("/api/map/records", response_class=JSONResponse)
async def get_map_records(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    taluk: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
) -> JSONResponse:
    """Return incidents and contextual source records as distinct map records."""
    dataset = get_dataset()
    records = _filter_records(
        dataset["all_records"],
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        severity=severity,
        status=status,
        taluk=taluk,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    return JSONResponse(content=records)


@app.get("/api/map/geojson", response_class=JSONResponse)
async def get_map_geojson(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    taluk: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
) -> JSONResponse:
    """Return mapped incidents and contextual records in one collision-safe layer."""
    dataset = get_dataset()
    records = _filter_records(
        dataset["mapped_records"],
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        severity=severity,
        status=status,
        taluk=taluk,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    return JSONResponse(content=build_geojson_feature_collection(records))


@app.get("/api/map/unmapped", response_class=JSONResponse)
async def get_map_unmapped_records(
    category: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    record_kind: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
) -> JSONResponse:
    """Return every incident or source record without a reliable map point."""
    dataset = get_dataset()
    records = _filter_records(
        dataset["unmapped_records"],
        category=category,
        source_type=source_type,
        record_kind=record_kind,
        search=search,
    )
    return JSONResponse(content=records)


@app.get("/api/talukas", response_class=JSONResponse)
async def get_talukas(
    mode: str = Query("aggregated", description="Severity aggregation from saved incidents."),
) -> JSONResponse:
    """Return administrative boundaries with severity aggregated from incidents."""
    if mode != "aggregated":
        raise HTTPException(status_code=422, detail="Only persisted-incident aggregation is supported.")
    dataset = get_dataset()
    records = dataset["incident_records"]
    taluka_data = get_taluka_geojson_with_severity(mode=mode, records=records)
    return JSONResponse(content=taluka_data)


@app.get("/api/stats", response_class=JSONResponse)
async def get_stats() -> JSONResponse:
    """Returns current dataset summary statistics."""
    dataset = get_dataset()
    return JSONResponse(content=dataset["stats"])


@app.post("/api/reload", response_class=JSONResponse)
async def reload_dataset() -> JSONResponse:
    """Forces a fresh load from the persisted incidents API."""
    dataset = get_dataset(force_reload=True)
    load_taluka_boundaries(force_reload=True)
    return JSONResponse(content={
        "status": "success",
        "message": "GIS dataset successfully refreshed",
        "stats": dataset["stats"],
    })


@app.get("/api/health", response_class=JSONResponse)
async def health_check() -> JSONResponse:
    """Report actual upstream data status rather than only process liveness."""
    try:
        dataset = get_dataset()
    except HTTPException as error:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "status": "unavailable",
                "service": "Madurai District GIS API",
                "dataSourceType": "live",
                "cached": False,
                "error": error.detail,
            },
        )
    return JSONResponse(content={
        "status": "healthy",
        "service": "Madurai District GIS API",
        "dataSourceType": dataset["data_source_type"],
        "cached": _cached_dataset is not None,
        "sourceEndpoints": dataset["source_endpoints"],
        "lastRefreshed": dataset["stats"]["lastRefreshed"],
    })


if __name__ == "__main__":
    print(f"Madurai District GIS API: http://{SERVER_HOST}:{SERVER_PORT}/docs")
    uvicorn.run("app:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
