from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BLOCKED = "network access is blocked for the intelligence test session"


def _blocked(*args, **kwargs):
    raise RuntimeError(BLOCKED)


@pytest.fixture(autouse=True, scope="session")
def no_network():
    guard = pytest.MonkeyPatch()
    guard.setattr(socket, "create_connection", _blocked, raising=False)
    guard.setattr(socket, "getaddrinfo", _blocked, raising=False)
    guard.setattr(socket.socket, "connect", _blocked, raising=False)
    guard.setattr(socket.socket, "connect_ex", _blocked, raising=False)
    guard.setattr(socket.socket, "sendto", _blocked, raising=False)
    yield
    guard.undo()


@pytest.fixture(autouse=True, scope="session")
def no_live_deployment(tmp_path_factory):
    from intelligence import llm

    guard = pytest.MonkeyPatch()
    for name in [key for key in os.environ if key.startswith("INTELLIGENCE_") or key.endswith("_API_KEY")]:
        guard.delenv(name, raising=False)
    missing = tmp_path_factory.mktemp("deployment") / "absent.env"
    guard.setattr(llm, "DOTENV_PATH", str(missing))
    yield
    guard.undo()


@pytest.fixture(autouse=True)
def environment_restored(no_live_deployment):
    before = dict(os.environ)
    yield
    for name in [key for key in os.environ if key not in before]:
        del os.environ[name]
    for name, value in before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value
