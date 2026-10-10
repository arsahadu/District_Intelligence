# Collector dashboard

## Development

```powershell
npm install
npm run dev
```

The Vite app uses `VITE_API_BASE_URL` for its API origin (default: `http://localhost:8000`). It reads incidents from `GET /incidents` and requests selected details from `GET /incidents/{incident_id}`. API errors remain visible in the interface; the dashboard has no mock-data fallback.

## Validation

```powershell
npm run lint
npm run build
```
