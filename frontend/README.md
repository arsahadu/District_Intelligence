# Collector dashboard

## Development

```powershell
npm install
npm run dev
```

The Vite app uses `VITE_API_BASE_URL` for its API origin (default: `http://localhost:8000`). It reads incidents from `GET /incidents` and requests selected details from `GET /incidents/{incident_id}`. API errors remain visible in the interface; the dashboard has no mock-data fallback.

## Government documents

The Government Documents workspace uploads PDF files (maximum 20 MB) through the FastAPI document-intelligence routes. It polls OCR status, then lets the user deliberately request and review the backend-generated insights. OCR text is hidden until expanded; insights remain drafts requiring human verification. The feature does not maintain a document list or write document data to PostgreSQL.

The backend must mount `/document-intelligence` and be configured for its existing S3, Textract, and Gemini integrations. Set `VITE_API_BASE_URL` to the backend origin if it is not running at the default URL. The frontend never accesses AWS or Gemini directly.

## Validation

```powershell
npm run lint
npm run build
```
