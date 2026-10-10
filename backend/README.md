# District Intelligence Backend

## Run locally

From the `backend` directory, create and activate a virtual environment, then install the dependencies:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start the API:

```powershell
uvicorn app.main:app --reload --port 8000
```

The health endpoint is available at `http://localhost:8000/health`. Interactive API documentation is at `http://localhost:8000/docs`.

The backend resolves the monorepo's stable `intelligence.contract` package from the repository layout, so no
working-directory-specific `PYTHONPATH` setting is required.

## Incidents

The API validates and stores the exact `Incident` v2.0 contract defined in `intelligence/contract.py`.
`POST /incidents` creates an incident and returns `201`; posting an existing `incident_id` returns `409`.
`GET /incidents` supports filters for `incident_type`, `category`, `severity`, `priority`, `event_status`,
`district`, `start_date` and `end_date`. `GET /incidents/{incident_id}` returns the complete contract document,
including its evidence, provenance, claims, validation and generation metadata.

Apply database migrations from the `backend` directory:

```powershell
alembic upgrade head
```