# Gemini document-insights integration

## Important finding from the supplied files
`intelligence/llm.py` currently registers `openai_compatible`, `groq`, `vllm`, and `ollama`; it does not register a native Gemini provider. This patch calls the Gemini REST API from the separate document module so it does not change the existing incident pipeline.

## 1. Copy `insights.py`
Copy this file to `backend/app/document_intelligence/insights.py`.

## 2. Add cache methods to `DocumentIntelligenceService` in `service.py`
Add inside the class:

```python
def get_cached_insights(self, job_id: str) -> dict[str, Any] | None:
    key = f"{self.settings.results_prefix.rstrip('/')}/{job_id}-insights.json"
    return self._load_json(key)

def save_insights(self, job_id: str, payload: dict[str, Any]) -> None:
    key = f"{self.settings.results_prefix.rstrip('/')}/{job_id}-insights.json"
    self._save_json(key, payload)
```

## 3. Configure Gemini in the same terminal that starts FastAPI
Use exact model IDs available in your Google AI Studio/API account; do not assume product display names are API IDs:

```bash
export GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
export DOCUMENT_INSIGHTS_PRIMARY_MODEL="YOUR_VALID_PRIMARY_MODEL_ID"
export DOCUMENT_INSIGHTS_FALLBACK_MODEL="YOUR_VALID_FALLBACK_MODEL_ID"
export DOCUMENT_INSIGHTS_TIMEOUT_SECONDS="60"
export DOCUMENT_INSIGHTS_MAX_CHARS="45000"
```

Never commit the key or paste it into chat.

## 4. Add imports to `backend/app/document_intelligence/router.py`

```python
from .insights import generate_document_insights, InsightsError
```

Append this route after the existing routes:

```python
@router.post("/jobs/{job_id}/insights")
def generate_job_insights(job_id: str):
    """Generate and cache human-reviewable insights from a completed OCR job."""
    try:
        service = get_service()
        ocr_result = service.get_job(job_id)
        if ocr_result.get("status") != "SUCCEEDED":
            raise HTTPException(status_code=409, detail=f"OCR is not complete (status={ocr_result.get('status')})")
        cached = service.get_cached_insights(job_id)
        if cached is not None:
            return cached
        result = generate_document_insights(ocr_result)
        service.save_insights(job_id, result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsightsError as exc:
        logger.exception("Gemini document insights failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Could not retrieve OCR result")
        raise HTTPException(status_code=502, detail="Could not retrieve OCR result; check AWS permissions and backend logs") from exc
```

Your existing router already imports `logger`, `ClientError`, and `BotoCoreError`.

## 5. Restart and test
After restarting FastAPI, first ensure a Textract job has `status: SUCCEEDED`. Then call:

```bash
curl -X POST "http://127.0.0.1:8000/document-intelligence/jobs/YOUR_JOB_ID/insights"
```

The response contains a summary, key facts, deadlines, action items, financial details, evidence quotes, source metadata, and `review_status: pending_human_review`. Repeated requests return the cached S3 JSON instead of charging for another model call.

## Safety and limitations
- Human review remains required; the model output is not an official order or decision.
- Evidence quotes are checked against OCR text, but that alone does not prove legal meaning or page correctness.
- Protect both upload and insights endpoints with the app's existing authentication and document-level authorization before production.
- Textract's published language support does not list Tamil; validate OCR quality with real documents before relying on Tamil output.
