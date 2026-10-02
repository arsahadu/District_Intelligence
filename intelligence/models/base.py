"""Shared primitives for Intelligence contracts."""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict
from pydantic import AfterValidator, BeforeValidator

EVIDENCE_REFERENCE_FIELD = "evidence_ids"


def _reject_non_numeric(value: Any) -> Any:
    """Block coercion before pydantic quietly turns "0.9" into 0.9."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "must be a real number in [0.0, 1.0], "
            f"got {type(value).__name__}; strings and booleans are not accepted"
        )
    return value


def _validate_unit_interval(value: Any) -> float:
    """Accept only finite numbers in [0, 1]."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("must be finite, not NaN or infinity")
    if not 0.0 <= number <= 1.0:
        raise ValueError("must be within [0.0, 1.0]")
    return number


Ratio = Annotated[
    float,
    BeforeValidator(_reject_non_numeric),
    AfterValidator(_validate_unit_interval),
]

Confidence = Ratio

OptionalConfidence = Confidence | None


class StrictModel(BaseModel):
    """Base for every Intelligence contract."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
        str_min_length=1,
    )


def collect_evidence_references(obj: Any, *, acc: set[str] | None = None) -> set[str]:
    """Walk nested contracts and return every referenced evidence id."""
    if acc is None:
        acc = set()
    if isinstance(obj, dict):
        for value in obj.values():
            collect_evidence_references(value, acc=acc)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for value in obj:
            collect_evidence_references(value, acc=acc)
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            value = getattr(obj, name, None)
            if value is None:
                continue
            if name == EVIDENCE_REFERENCE_FIELD:
                if isinstance(value, (list, tuple)):
                    acc.update(str(item) for item in value)
                continue
            collect_evidence_references(value, acc=acc)
    return acc


def unique_ids(items: Any, id_field: str) -> list[str]:
    """Return duplicate id values in ``items``; empty list means all unique."""
    seen: list[str] = []
    duplicates: list[str] = []
    for item in items:
        value = getattr(item, id_field, None)
        if value is None:
            continue
        if value in seen:
            duplicates.append(str(value))
        seen.append(str(value))
    return duplicates
