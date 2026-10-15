# Collector dashboard

## Development

```powershell
npm install
npm run dev
```

The Vite app uses `VITE_API_BASE_URL` for its API origin (default: `http://localhost:8000`). It reads incidents from `GET /incidents` and requests selected details from `GET /incidents/{incident_id}`. API errors remain visible in the interface; the dashboard has no mock-data fallback.

## GIS map

Map View is implemented natively in React with React Leaflet and consumes the
Python GIS service. Its default API origin is `http://127.0.0.1:8080`; override
it with `VITE_GIS_API_BASE_URL` in `frontend/.env`. The GIS service, in turn,
reads persisted Incident v2.0 documents from the existing backend, filters to
`record_kind: "incident"`, and does not trigger the intelligence pipeline or
read raw source records. See [the GIS service README](../gis/README.md) for the
workflow, backend configuration, routes, and run commands.

## Government documents

The Government Documents workspace uploads PDF files (maximum 20 MB) through the FastAPI document-intelligence routes. It polls OCR status, then lets the user deliberately request and review the backend-generated insights. OCR text is hidden until expanded; insights remain drafts requiring human verification. The feature does not maintain a document list or write document data to PostgreSQL.

The backend must mount `/document-intelligence` and be configured for its existing S3, Textract, and Gemini integrations. Set `VITE_API_BASE_URL` to the backend origin if it is not running at the default URL. The frontend never accesses AWS or Gemini directly.

## Validation

```powershell
npm run lint
npm run build
```
