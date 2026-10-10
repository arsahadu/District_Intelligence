import os
import subprocess
import sys
from pathlib import Path


def test_backend_imports_repository_incident_contract_without_pythonpath():
    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.main; "
                "from app.api.incidents import Incident as APIIncident; "
                "from intelligence.contract import Incident; "
                "assert APIIncident is Incident; "
                "assert '/incidents' in app.main.app.openapi()['paths']"
            ),
        ],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
