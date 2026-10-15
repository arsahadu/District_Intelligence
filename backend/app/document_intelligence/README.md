# Government Document Intelligence — Amazon Textract module

A standalone FastAPI router that uploads government documents to a private S3 bucket and runs asynchronous Amazon Textract text detection. It can be integrated into the existing FastAPI backend without replacing the ingestion pipeline or intelligence pipeline.

## What this MVP does

- Accepts PDF and TIFF uploads.
- Enforces a configurable 20 MB upload limit.
- Stores originals under `documents/raw/` with UUID-based keys.
- Starts asynchronous Textract text detection.
- Stores job metadata under `documents/jobs/` and completed OCR output under `documents/results/`.
- Returns extracted lines, confidence values, page numbers, and joined text.
- Uses the AWS credential chain; no keys are embedded in code.

## 1. Copy into the backend

Copy `document_intelligence/` to `backend/app/document_intelligence/`.

## 2. Install dependencies

From `backend/`, using the project's virtual environment:

```bash
./.venv/bin/python -m pip install boto3 python-multipart
```

Add these lines to `backend/requirements.txt`:

```text
boto3
python-multipart
```

## 3. Configure environment

Use the existing private team bucket and Mumbai region:

```bash
export AWS_REGION="ap-south-1"
export DOCUMENTS_S3_BUCKET="fai-tce-team61-data"
export DOCUMENTS_S3_RAW_PREFIX="documents/raw/"
export DOCUMENTS_S3_JOBS_PREFIX="documents/jobs/"
export DOCUMENTS_S3_RESULTS_PREFIX="documents/results/"
export DOCUMENTS_MAX_BYTES="20971520"
```

Use the already-approved AWS profile or execution role. Do not commit credentials, create a new IAM role/user, or make the bucket public.

## 4. Router registration

The FastAPI application registers this router in `backend/app/main.py`. Do not register it a second time. The module dependencies are included through `backend/requirements.txt`.

Keep these endpoints behind the application's existing authentication/authorization. Before production, restrict uploads and job-result access to authorized Collector users and enforce per-user job ownership. This standalone module intentionally does not invent a replacement auth system.

## 5. AWS permissions

The current approved AWS identity needs access to:

- `s3:PutObject` and `s3:GetObject` for `arn:aws:s3:::fai-tce-team61-data/documents/*`
- `textract:StartDocumentTextDetection`
- `textract:GetDocumentTextDetection`

The identity and bucket policy must also allow Textract to read the input S3 objects under your account's policies. If access is denied, ask the AWS project administrator to update the existing approved policy; do not create a new role or weaken bucket privacy.

## 6. Run and test

Start the backend as usual and open `http://127.0.0.1:8000/docs`.

Upload a document to `POST /document-intelligence/documents` using multipart form field `file`. The response includes `job_id` and `poll_url`.

Poll `GET /document-intelligence/jobs/{job_id}`. While processing, it returns `status: IN_PROGRESS`. Once complete, it returns `status: SUCCEEDED`, extracted `text`, `lines`, `page_count`, and confidence values. Results are cached in S3 so repeated polls do not repeatedly fetch Textract results.

## 7. Frontend integration contract

1. Upload the selected PDF to `POST /document-intelligence/documents`.
2. Keep the returned `job_id` and poll `poll_url` every 4 seconds until OCR succeeds or reaches a terminal status.
3. When OCR is `SUCCEEDED`, call `POST /document-intelligence/jobs/{job_id}/insights`. The backend generates and caches structured Gemini insights.
4. Display insights as drafts with source citations. Keep OCR text hidden by default, and require human confirmation before taking official action.

This feature is independent of the source ingestion pipeline; do not rerun ingestion to upload documents.

## Limitations and production checklist

- This MVP extracts text only. For table/form structure, extend it with `StartDocumentAnalysis` and `GetDocumentAnalysis` using `TABLES`, `FORMS`, and/or `LAYOUT`.
- Textract's published language support does not list Tamil. Validate actual Tamil documents before relying on its OCR; this MVP should first be tested with English documents.
- Gemini insights are available from the document-insights endpoint and are drafts requiring human verification; they do not create official actions.
- Before production, add malware scanning, document retention/deletion policy, per-user access checks, audit events, rate limiting, and tests for oversized/invalid uploads.
