"""District Intelligence platform backend."""

import sys
from pathlib import Path


_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[2])
if _REPOSITORY_ROOT in sys.path:
    sys.path.remove(_REPOSITORY_ROOT)
sys.path.insert(0, _REPOSITORY_ROOT)