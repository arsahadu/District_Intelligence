# District Intelligence

Collector-facing incident dashboard for Madurai District, built with React, Vite, and the existing FastAPI service.

## Run locally

Start PostgreSQL from the repository root if it is not already running:

```powershell
docker compose up -d postgres
```

Start the backend in one PowerShell terminal. Ensure `backend/.env` has a valid `DATABASE_URL` for the database:

```powershell
Set-Location backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Start the frontend in a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dashboard requests Incident v2.0 records from `GET /incidents` and loads selected record details from `GET /incidents/{incident_id}`. Set `VITE_API_BASE_URL` in `frontend/.env` to point to a different API origin; it defaults to `http://localhost:8000`.

The incident list endpoint currently returns all matching records as a JSON array and has no pagination parameters. The dashboard paginates display rows locally without discarding fetched records. Search and the available category, severity, priority, status, location, and event-date filters are applied to the complete fetched response.
