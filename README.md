# District Intelligence Platform

Minimal React and FastAPI foundation for the Collector Dashboard. The current vertical slice checks the backend health endpoint; database and intelligence features are out of scope.

## Run locally

Start the backend in one PowerShell terminal:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Start the frontend in a second terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

The dashboard is at `http://localhost:5173`, the API is at `http://localhost:8000`, and Swagger is at `http://localhost:8000/docs`.
