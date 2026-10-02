"""Pytest setup for the Intelligence contract tests."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from intelligence.tests.builders import (  # noqa: E402
    BENCH_CONTENT_TA,
    CONTENT_TA,
    TITLE_TA,
    bench_incident,
    protest_evidence,
    protest_incident,
    unresolved_incident,
)


@pytest.fixture
def tamil_content() -> str:
    return CONTENT_TA


@pytest.fixture
def protest() -> "object":
    return protest_incident()


@pytest.fixture
def empty_incident() -> "object":
    return unresolved_incident()


@pytest.fixture
def ambiguous_incident() -> "object":
    return bench_incident()


@pytest.fixture
def protest_evidence_list() -> list:
    return protest_evidence()


@pytest.fixture
def bench_content() -> str:
    return BENCH_CONTENT_TA


__all__ = [
    "TITLE_TA",
    "bench_incident",
    "protest_incident",
    "unresolved_incident",
]
