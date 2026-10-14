from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "ap-south-1")
    bucket_name: str = os.getenv("DOCUMENTS_S3_BUCKET", "fai-tce-team61-data")
    raw_prefix: str = os.getenv("DOCUMENTS_S3_RAW_PREFIX", "documents/raw/")
    jobs_prefix: str = os.getenv("DOCUMENTS_S3_JOBS_PREFIX", "documents/jobs/")
    results_prefix: str = os.getenv("DOCUMENTS_S3_RESULTS_PREFIX", "documents/results/")
    max_document_bytes: int = int(os.getenv("DOCUMENTS_MAX_BYTES", str(20 * 1024 * 1024)))


ALLOWED_EXTENSIONS = {".pdf", ".tif", ".tiff"}
ALLOWED_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
