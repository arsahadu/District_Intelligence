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