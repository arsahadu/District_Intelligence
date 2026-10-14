from functools import lru_cache

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, File, HTTPException, UploadFile

from .service import DocumentIntelligenceService, DocumentNotFoundError
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/document-intelligence", tags=["Document Intelligence"])

from .insights import generate_document_insights, InsightsError


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

@lru_cache(maxsize=1)
def get_service() -> DocumentIntelligenceService:
    return DocumentIntelligenceService()


@router.post("/documents", status_code=202)
async def upload_document(file: UploadFile = File(...)):
    """Upload a government document to private S3 and start asynchronous Textract OCR."""
    try:
        service = get_service()
        content = await file.read(service.settings.max_document_bytes + 1)
        metadata = service.submit_document(file.filename or "uploaded-document", content)
        return {
            "message": "Document uploaded; Textract OCR job started.",
            **metadata,
            "poll_url": f"/document-intelligence/jobs/{metadata['job_id']}",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=502,
            detail="AWS document processing failed. Check region, credentials, S3 permissions, and Textract permissions.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_document_job(job_id: str):
    """Poll Textract processing; returns extracted text once complete."""
    try:
        return get_service().get_job(job_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Document upload / Textract start failed")
        raise HTTPException(
            status_code=502,
            detail=(
                "AWS document processing failed. "
                "Check the backend terminal logs."
            ),
        ) from exc
