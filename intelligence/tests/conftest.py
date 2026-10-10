"""Pytest setup: the repository root is importable, and no request can leave this process."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: What every blocked path says. A test asserts on this text rather than on one library's exception.
BLOCKED = "network access is blocked for the intelligence test session"


def _blocked(*args, **kwargs):
    raise RuntimeError(BLOCKED)


@pytest.fixture(autouse=True, scope="session")
def no_network():
    """Close every outbound path, not only the ones this suite happens to use.

    Removing the credentials is not a boundary: a test that hands a provider a key of its own would
    still send, and one vendor's key can reach another vendor's endpoint. These are the funnels every
    client in this stack passes through - name resolution, socket creation, ``connect``,
    ``connect_ex`` and datagram sends - so a live Gemini, Groq, OpenRouter, OpenAI or backend request
    cannot happen by accident whatever transport it arrived on. A test that needs an answer builds
    one: every response here is a constructed object, and no test binds or listens on a socket, so
    nothing legitimate is caught by this.
    """
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
    """Take the deployment out of the environment for the whole session.

    A call made with no ``environ`` reads the repository's ``.env`` and then the real variables, which
    on a configured machine means the billed endpoint this suite is never supposed to touch - so the
    variables are removed and ``.env`` is pointed at a file that does not exist. A test that wants an
    env file builds one in its own ``tmp_path`` and points ``DOTENV_PATH`` at it; reading that file is
    what ``environment_restored`` below undoes. ``no_network`` is the backstop: these fixtures remove
    the reason a call would be billed, that one removes the call.
    """
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
    """Put the environment back the way the session found it after every test.

    The session fixture above cannot be the whole boundary: reading an env file is ``load_dotenv``,
    which writes its keys into the real ``os.environ``, and a ``monkeypatch.delenv`` made beforehand
    records nothing for a key that was absent, so it has nothing to restore afterwards. A test that
    points ``DOTENV_PATH`` at a file of its own therefore leaves that file's deployment - endpoint,
    model and all - in the process for every test that follows it, in whatever order they ran. This
    snapshot is taken after the session fixtures have cleared the deployment, so what every test is
    handed back is the clean state, whatever it did to reach another one.
    """
    before = dict(os.environ)
    yield
    for name in [key for key in os.environ if key not in before]:
        del os.environ[name]
    for name, value in before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value
