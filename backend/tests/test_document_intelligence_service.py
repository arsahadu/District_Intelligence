
from unittest.mock import Mock

from app.document_intelligence.service import DocumentIntelligenceService


def make_service():
    service = DocumentIntelligenceService.__new__(DocumentIntelligenceService)
    service.settings = Mock()
    service.settings.bucket_name = "test-bucket"
    service.settings.jobs_prefix = "documents/jobs/"
    service.settings.results_prefix = "documents/results/"
    service.s3 = Mock()
    service.textract = Mock()
    return service


def test_successful_ocr_saves_result_and_updates_metadata():
    service = make_service()
    metadata = {"job_id": "job-1", "status": "IN_PROGRESS"}
    saved = {}

    def load_json(key):
        if key == "documents/jobs/job-1.json":
            return metadata
        return None

    def save_json(key, payload):
        saved[key] = payload.copy()

    service._load_json = load_json
    service._save_json = save_json
    service.textract.get_document_text_detection.return_value = {
        "JobStatus": "SUCCEEDED",
        "DocumentMetadata": {"Pages": 1},
        "Blocks": [
            {"BlockType": "PAGE", "Page": 1},
            {"BlockType": "LINE", "Text": "Hello", "Confidence": 99, "Page": 1},
        ],
    }

    result = service.get_job("job-1")

    assert result["status"] == "SUCCEEDED"
    assert result["text"] == "Hello"
    assert saved["documents/results/job-1.json"]["status"] == "SUCCEEDED"
    assert saved["documents/jobs/job-1.json"]["status"] == "SUCCEEDED"


def test_failed_ocr_updates_metadata():
    service = make_service()
    metadata = {"job_id": "job-2", "status": "IN_PROGRESS"}
    saved = {}

    service._load_json = lambda key: (
        metadata if key == "documents/jobs/job-2.json" else None
    )
    service._save_json = lambda key, payload: saved.update({key: payload.copy()})
    service.textract.get_document_text_detection.return_value = {
        "JobStatus": "FAILED",
        "StatusMessage": "Test failure",
        "Warnings": [],
    }

    result = service.get_job("job-2")

    assert result["status"] == "FAILED"
    assert saved["documents/results/job-2.json"]["status"] == "FAILED"
    assert saved["documents/jobs/job-2.json"]["status"] == "FAILED"


def test_cached_terminal_result_repairs_stale_metadata():
    service = make_service()
    metadata = {"job_id": "job-3", "status": "IN_PROGRESS"}
    cached = {
        "job_id": "job-3",
        "status": "SUCCEEDED",
        "completed_at": "2026-10-17T00:00:00+00:00",
        "page_count": 1,
        "line_count": 1,
    }
    saved = {}

    service._load_json = lambda key: (
        metadata if key == "documents/jobs/job-3.json"
        else cached if key == "documents/results/job-3.json"
        else None
    )
    service._save_json = lambda key, payload: saved.update({key: payload.copy()})

    result = service.get_job("job-3")

    assert result == cached
    assert saved["documents/jobs/job-3.json"]["status"] == "SUCCEEDED"
    service.textract.get_document_text_detection.assert_not_called()
