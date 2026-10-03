import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]

for dotenv_path in (ROOT_DIR / ".env", ROOT_DIR / "ingestion" / ".env"):
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)

PLATFORM_API_URL = os.getenv("PLATFORM_API_URL", "http://localhost:8000").rstrip("/")
