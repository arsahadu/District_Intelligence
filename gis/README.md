# GIS service

The GIS folder contains the Python API and geographic-processing code used by
the Collector's React Map View. The old standalone HTML/CSS/JavaScript dashboard
has been removed; GIS does not serve a second user interface. It also does not
connect directly to PostgreSQL or run the intelligence pipeline.

## Data flow

1. The ingestion and intelligence pipeline process incoming source material.
2. The pipeline stores its output through the existing backend `POST /incidents`
   API. PostgreSQL is owned and accessed by that backend.
3. The GIS service requests `GET {BACKEND_API_BASE_URL}/incidents`. The backend
   returns persisted intelligence documents as a JSON API response. GIS keeps
   only documents explicitly marked `record_kind: "incident"`; stored forecasts
   and context documents are not treated as GIS incidents.
4. GIS normalizes those saved incidents for location resolution, map display,
   and GeoJSON. It preserves the incident's metadata, provenance, and evidence.
   It never reads `GET /records` or source/sample incident JSON files.
5. The React Map View requests the GIS incident, GeoJSON, statistics, health,
   unmapped-location, and taluk-boundary APIs.

There are no bundled incident/source sample datasets or sample severity
overrides. The only local GeoJSON input is
`data/madurai_talukas.geojson`: it contains administrative taluk polygons, not
incident or intelligence records. The backend's JSON response is only the
transport format for records already persisted by the intelligence workflow;
it is not a separate JSON-file data source.

GIS uses valid coordinates stored on an incident's location or a supported
named-place registry entry. District/taluk-only descriptions remain unmapped,
rather than being assigned a centroid. Registry coordinates are approximate
facility anchors. Co-location offsets are deterministic display-only offsets;
source/base coordinates remain unchanged. Taluk coloring aggregates only saved
incidents. A taluk without mapped incidents remains unassessed (unknown).

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GIS_HOST` | `127.0.0.1` | GIS service bind host |
| `GIS_PORT` | `8080` | GIS service port |
| `BACKEND_API_BASE_URL` | `http://localhost:8000` | Existing backend API origin |
| `BACKEND_TIMEOUT_SECONDS` | `3.0` | Timeout for the backend request |
| `GIS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins, comma-separated |
| `VITE_GIS_API_BASE_URL` | `http://127.0.0.1:8080` | GIS API origin used by React |

The GIS API expects the backend's direct JSON-list response from `/incidents`.
It does not expect an envelope and adds no database connection or authentication
headers. Set `GIS_CORS_ORIGINS` if the Collector uses another browser origin.

## Run locally

Start the backend and its database as documented in the root README. Then run
the Collector and GIS API in separate PowerShell terminals:

```powershell
Set-Location frontend
npm install
npm run dev
```

```powershell
Set-Location gis
.\.venv\Scripts\Activate.ps1
python app.py
```

To create the GIS virtual environment if needed:

```powershell
Set-Location gis
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open the Collector at `http://localhost:5173` and select **Map View**. The
Python service root returns API information; its interactive API documentation
is at `http://127.0.0.1:8080/docs`.

## GIS API

- `GET /api/incidents` — normalized, persisted incidents only.
- `GET /api/unmapped` — persisted incidents without a reliable map location.
- `GET /api/geojson` — RFC 7946 FeatureCollection for mapped incidents;
  coordinates are `[longitude, latitude]`. Supports category, source type,
  severity, status, taluk, date, and search filters.
- `GET /api/talukas?mode=aggregated` — administrative boundaries with severity
  derived only from mapped saved incidents.
- `GET /api/stats` — persisted incident and map-location totals.
- `GET /api/health` — backend incident API availability and refresh timestamp;
  returns HTTP 503 when the backend cannot be reached.
- `POST /api/reload` — invalidate the cache and refetch persisted incidents.

## Tests

From the repository root, use the GIS virtual environment:

```powershell
.\gis\.venv\Scripts\python.exe -m pytest .\gis\tests -v
```

Frontend checks are run in `frontend` with `npm run lint` and `npm run build`.
