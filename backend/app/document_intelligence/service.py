from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .config import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
    Settings,
) 


class DocumentNotFoundError(Exception):
    """Raised when a Textract job is not one created by this module."""


class DocumentIntelligenceService:
    """Upload private documents to S3 and run asynchronous Amazon Textract OCR.

    Credentials are resolved through the standard AWS credential chain (for example,
    an approved AWS profile or the application's existing execution role).
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        if not self.settings.bucket_name:
            raise RuntimeError("DOCUMENTS_S3_BUCKET must be configured")
        self.s3 = boto3.client("s3", region_name=self.settings.aws_region)
        self.textract = boto3.client("textract", region_name=self.settings.aws_region)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_filename(filename: str | None) -> str:
        # Strip client-supplied directory components and control characters.
        safe = Path(filename or "uploaded-document").name.replace("\x00", "")
        return safe[:180] or "uploaded-document"

    def submit_document(self, filename: str, content: bytes) -> dict[str, Any]:
        if not content:
            raise ValueError("The uploaded document is empty")
        if len(content) > self.settings.max_document_bytes:
            limit_mb = self.settings.max_document_bytes // (1024 * 1024)
            raise ValueError(f"Document exceeds the {limit_mb} MB upload limit")

        safe_name = self._safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported file type. Upload PDF, PNG, JPG, or TIFF.")

        document_id = str(uuid.uuid4())
        s3_key = f"{self.settings.raw_prefix.rstrip('/')}/{document_id}/{safe_name}"
        self.s3.put_object(
            Bucket=self.settings.bucket_name,
            Key=s3_key,
            Body=content,
            ContentType=ALLOWED_CONTENT_TYPES[suffix],
            ServerSideEncryption="AES256",
            Metadata={"document-id": document_id},
        )

        response = self.textract.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": self.settings.bucket_name, "Name": s3_key}}
        )
        job_id = response["JobId"]
        metadata = {
            "document_id": document_id,
            "job_id": job_id,
            "filename": safe_name,
            "s3_bucket": self.settings.bucket_name,
            "s3_key": s3_key,
            "created_at": self._now(),
            "status": "IN_PROGRESS",
            "ocr_service": "amazon-textract",
        }
        self._save_json(
            f"{self.settings.jobs_prefix.rstrip('/')}/{job_id}.json", metadata
        )
        return metadata

    def _load_json(self, key: str) -> dict[str, Any] | None:
        try:
            obj = self.s3.get_object(Bucket=self.settings.bucket_name, Key=key)
            return json.loads(obj["Body"].read().decode("utf-8"))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise

    def _save_json(self, key: str, payload: dict[str, Any]) -> None:
        self.s3.put_object(
            Bucket=self.settings.bucket_name,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
    def get_cached_insights(self, job_id: str) -> dict[str, Any] | None:
        key = f"{self.settings.results_prefix.rstrip('/')}/{job_id}-insights.json"
        return self._load_json(key)

    def save_insights(self, job_id: str, payload: dict[str, Any]) -> None:
        key = f"{self.settings.results_prefix.rstrip('/')}/{job_id}-insights.json"
        self._save_json(key, payload)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job_key = f"{self.settings.jobs_prefix.rstrip('/')}/{job_id}.json"
        metadata = self._load_json(job_key)
        if metadata is None:
            raise DocumentNotFoundError("No document job was found for this job ID")

        result_key = f"{self.settings.results_prefix.rstrip('/')}/{job_id}.json"
        cached = self._load_json(result_key)
        if cached is not None:
            return cached

        first = self.textract.get_document_text_detection(JobId=job_id, MaxResults=1000)
        status = first.get("JobStatus", "UNKNOWN")
        if status == "IN_PROGRESS":
            return {
                **metadata,
                "status": status,
                "message": "Textract is still processing this document. Poll this endpoint again shortly.",
            }
        if status in {"FAILED", "PARTIAL_SUCCESS"}:
            result = {
                **metadata,
                "status": status,
                "status_message": first.get("StatusMessage"),
                "warnings": first.get("Warnings", []),
                "completed_at": self._now(),
            }
            self._save_json(result_key, result)
            return result

        blocks = list(first.get("Blocks", []))
        token = first.get("NextToken")
        while token:
            page = self.textract.get_document_text_detection(
                JobId=job_id, MaxResults=1000, NextToken=token
            )
            blocks.extend(page.get("Blocks", []))
            token = page.get("NextToken")

        lines = [
            {
                "text": block.get("Text", ""),
                "confidence": block.get("Confidence"),
                "page": block.get("Page"),
            }
            for block in blocks
            if block.get("BlockType") == "LINE" and block.get("Text")
        ]
        page_count = sum(1 for block in blocks if block.get("BlockType") == "PAGE")
        result = {
            **metadata,
            "status": "SUCCEEDED",
            "completed_at": self._now(),
            "page_count": page_count,
            "line_count": len(lines),
            "text": "\n".join(item["text"] for item in lines),
            "lines": lines,
            "next_step": "Pass the extracted text to the existing Gemini/intelligence adapter for insights.",
        }
        self._save_json(result_key, result)
        return result

