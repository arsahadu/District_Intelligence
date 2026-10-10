"""HTTP client for posting validated Incidents to the platform API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from intelligence.contract import Incident

DEFAULT_TIMEOUT_SECONDS = 15.0

# (url, UTF-8 request body, timeout_seconds) -> (status, response body)
PostTransport = Callable[[str, bytes, float], tuple[int, str]]


class IncidentAPIError(RuntimeError):
    """The API could not create an Incident or return a valid response."""


@dataclass(frozen=True)
class PostResult:
    incident_id: str
    status_code: int
    duplicate: bool = False


def _http_post(url: str, body: bytes, timeout_seconds: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def post_incident(
    incident: Incident,
    *,
    base_url: str,
    transport: Optional[PostTransport] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> PostResult:
    """Post one validated Incident, distinguishing creation from an existing id."""
    url = base_url.rstrip("/") + "/incidents"
    body = json.dumps(incident.to_storage_document(), ensure_ascii=False).encode("utf-8")
    post = transport or _http_post
    try:
        status_code, response_body = post(url, body, timeout_seconds)
    except (OSError, TimeoutError) as error:
        reason = getattr(error, "reason", None) or error
        raise IncidentAPIError(f"{url} could not be reached ({reason})") from error

    if status_code == 409:
        return PostResult(incident.incident_id, status_code, duplicate=True)

    if status_code == 201:
        try:
            created = Incident.model_validate_json(response_body)
        except (ValueError, TypeError) as error:
            raise IncidentAPIError(
                f"{url} answered HTTP 201 with an invalid Incident response: "
                f"{response_body[:300]}"
            ) from error
        if created.incident_id != incident.incident_id:
            raise IncidentAPIError(
                f"{url} created {created.incident_id!r} instead of "
                f"{incident.incident_id!r}"
            )
        if created.to_storage_document() != incident.to_storage_document():
            raise IncidentAPIError(
                f"{url} changed the Incident contract while creating "
                f"{incident.incident_id!r}"
            )
        return PostResult(incident.incident_id, status_code)

    if status_code in (400, 422):
        raise IncidentAPIError(f"{url} rejected the Incident with HTTP {status_code}: {response_body[:500]}")
    if status_code >= 500:
        raise IncidentAPIError(f"{url} failed with HTTP {status_code}: {response_body[:500]}")
    raise IncidentAPIError(f"{url} returned unexpected HTTP {status_code}: {response_body[:500]}")
