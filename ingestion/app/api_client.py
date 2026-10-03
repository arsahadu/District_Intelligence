from __future__ import annotations

import time

import requests

from app.config import PLATFORM_API_URL
from app.models.common_record import CommonRecord


def submit_common_record(record: CommonRecord) -> str:
    payload = record.model_dump(mode="json")
    url = f"{PLATFORM_API_URL}/records"

    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code in {200, 201}:
                return "stored"

            if response.status_code == 409:
                return "duplicate"

            retryable_status_codes = {429, 500, 502, 503, 504}
            if response.status_code in retryable_status_codes and attempt == 0:
                time.sleep(1)
                continue

            raise RuntimeError(
                f"POST {url} failed with status {response.status_code}: {response.text.strip() or response.reason}"
            )
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(1)
                continue
            raise RuntimeError(f"POST {url} failed: {exc}") from exc

    raise RuntimeError(f"POST {url} failed after retry attempts")
