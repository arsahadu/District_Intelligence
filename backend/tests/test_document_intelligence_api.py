from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.document_intelligence import router as document_intelligence_router
from app.document_intelligence.service import DocumentNotFoundError
from app.main import app


class FakeDocumentService:
    settings = SimpleNamespace(max_document_bytes=1024)

    def submit_document(self, filename, content):
        assert filename == "notice.pdf"
        assert content == b"%PDF-1.7"
        return {
            "job_id": "test-job",
            "status": "IN_PROGRESS",
            "s3_bucket": "private-bucket",
            "s3_key": "documents/raw/private.pdf",
        }

    def get_job(self, job_id):
        assert job_id == "test-job"
        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "text": "Extracted text",
            "s3_bucket": "private-bucket",
            "s3_key": "documents/raw/private.pdf",
        }


def test_upload_does_not_return_private_s3_location(monkeypatch):
    monkeypatch.setattr(document_intelligence_router, "get_service", lambda: FakeDocumentService())

    response = TestClient(app).post(
        "/document-intelligence/documents",
        files={"file": ("notice.pdf", b"%PDF-1.7", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "test-job"
    assert response.json()["poll_url"] == "/document-intelligence/jobs/test-job"
    assert "s3_bucket" not in response.json()
    assert "s3_key" not in response.json()


def test_job_does_not_return_private_s3_location(monkeypatch):
    monkeypatch.setattr(document_intelligence_router, "get_service", lambda: FakeDocumentService())

    response = TestClient(app).get("/document-intelligence/jobs/test-job")

    assert response.status_code == 200
    assert response.json()["text"] == "Extracted text"
    assert "s3_bucket" not in response.json()
    assert "s3_key" not in response.json()


def test_insights_returns_404_for_unknown_job(monkeypatch):
    class MissingJobService:
        def get_job(self, job_id):
            raise DocumentNotFoundError("No document job was found for this job ID")

    monkeypatch.setattr(document_intelligence_router, "get_service", lambda: MissingJobService())

    response = TestClient(app).post("/document-intelligence/jobs/missing-job/insights")

    assert response.status_code == 404
    assert response.json()["detail"] == "No document job was found for this job ID"


def test_cached_insights_skip_gemini_generation(monkeypatch):
    cached = {"document_title": "Cached report", "review_status": "pending_human_review"}

    class CachedInsightsService:
        def get_job(self, job_id):
            return {"job_id": job_id, "status": "SUCCEEDED"}

        def get_cached_insights(self, job_id):
            return cached

        def save_insights(self, job_id, payload):
            raise AssertionError("Cached insights must not be written again")

    def fail_if_generated(ocr_result):
        raise AssertionError("Gemini must not be called for cached insights")

    monkeypatch.setattr(document_intelligence_router, "get_service", lambda: CachedInsightsService())
    monkeypatch.setattr(document_intelligence_router, "generate_document_insights", fail_if_generated)

    response = TestClient(app).post("/document-intelligence/jobs/test-job/insights")

    assert response.status_code == 200
    assert response.json() == cached
