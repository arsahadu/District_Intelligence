from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.records import router as records_router
from app.api.sources import router as sources_router

app = FastAPI(
    title="District Intelligence API",
    version="0.1.0",
    description="FastAPI backend for the District Intelligence platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(records_router)
app.include_router(incidents_router)
app.include_router(sources_router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Welcome to District Intelligence API"}
