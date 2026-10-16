# GIS service

The GIS folder contains the Python API and geographic-processing code used by
the Collector's React Map View. The old standalone HTML/CSS/JavaScript dashboard
has been removed; GIS does not serve a second user interface. It also does not
connect directly to PostgreSQL or run the intelligence pipeline.

## Data flow

1. The ingestion and intelligence pipeline process incoming source material.
2. The pipeline stores its output through the existing backend `POST /incidents`
   API. PostgreSQL is owned and accessed by that backend.
3. The GIS service requests `GET {BACKEND_API_BASE_URL}/incidents` and
   `GET {BACKEND_API_BASE_URL}/records`. It retains only actual
   `record_kind: "incident"` documents in the incident layer and normalizes
   eligible news, weather, and agriculture source records as separate context
   records. Forecasts and market information are never promoted to incidents.
4. GIS preserves each source record's identity, content, timestamp, location,
   source URL, and any associated intelligence validation status. It resolves
   only validated source coordinates, explicit registered places, and the
   registered IMD Madurai station. District/taluk-level descriptions remain
   unmapped instead of being assigned a guessed point.
5. The React Map View requests the backward-compatible incident APIs plus the
   combined map-record, GeoJSON, and unmapped-record APIs. The card pages use
   URL-based map selection to reopen the exact record.

There are no bundled incident/source sample datasets or sample severity
overrides. The only local GeoJSON input is
`data/madurai_talukas.geojson`: it contains administrative taluk polygons, not
incident or intelligence records. The backend's JSON response is only the
transport format for records already persisted by the intelligence workflow;
it is not a separate JSON-file data source.

GIS validates coordinate ranges and rejects incomplete, non-finite, and
district-swapped coordinate pairs without changing the source values. District
or taluk-only descriptions (including Tamil place labels) remain unmapped,
rather than being assigned a centroid. Registry coordinates are approximate
facility or station anchors and are labelled as such. Co-location offsets are
deterministic display-only offsets; source/base coordinates remain unchanged.
Taluk coloring aggregates only saved incidents. A taluk without mapped
incidents remains unassessed (unknown).

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GIS_HOST` | `127.0.0.1` | GIS service bind host |
| `GIS_PORT` | `8080` | GIS service port |
| `BACKEND_API_BASE_URL` | `http://localhost:8000` | Existing backend API origin |
| `BACKEND_TIMEOUT_SECONDS` | `3.0` | Timeout for the backend request |
| `GIS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins, comma-separated |
| `VITE_GIS_API_BASE_URL` | `http://127.0.0.1:8080` | GIS API origin used by React |

The GIS API expects direct JSON-list responses from `/incidents` and `/records`.
It does not expect an envelope and adds no database connection or authentication
headers. A source-record request failure is reported as partial map
availability; it does not make contextual records into incidents. Set
`GIS_CORS_ORIGINS` if the Collector uses another browser origin.

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
- `GET /api/map/records` — incidents and eligible news, weather, and
  agriculture source records with distinct record kinds. Supports category,
  source type, incident kind, severity, status, taluk, date, and search filters.
- `GET /api/map/geojson` — combined mapped-record FeatureCollection with
  collision-resistant feature IDs and RFC 7946 coordinates.
- `GET /api/map/unmapped` — all map records without a reliable point, including
  their location-precision explanation.
- `GET /api/talukas?mode=aggregated` — administrative boundaries with severity
  derived only from mapped saved incidents.
- `GET /api/stats` — incident-only totals plus separate combined map totals.
- `GET /api/health` — incident and source-record availability with refresh time;
  returns HTTP 503 when the incidents service cannot be reached.
- `POST /api/reload` — invalidate the cache and refetch incidents and source
  records.

## Tests

From the repository root, use the GIS virtual environment:

```powershell
.\gis\.venv\Scripts\python.exe -m pytest .\gis\tests -v
```

Frontend checks are run in `frontend` with `npm run lint` and `npm run build`.
