"""Configuration for the Madurai District GIS API."""

import os
from pathlib import Path

GIS_DIR = Path(__file__).resolve().parent
DATA_DIR = GIS_DIR / "data"

SERVER_HOST = os.getenv("GIS_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("GIS_PORT", "8080"))

BACKEND_API_BASE_URL = os.getenv("BACKEND_API_BASE_URL", "http://localhost:8000")
BACKEND_TIMEOUT_SECONDS = float(os.getenv("BACKEND_TIMEOUT_SECONDS", "3.0"))
GIS_CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "GIS_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

MADURAI_CENTER = [9.9252, 78.1198]
DEFAULT_ZOOM = 11
MIN_ZOOM = 9
MAX_ZOOM = 18

MADURAI_BOUNDS = [
    [9.55, 77.65],  # Southwest
    [10.35, 78.55], # Northeast
]
