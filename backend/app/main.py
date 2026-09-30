from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router

app = FastAPI(
    title="District Intelligence API",
    version="0.1.0",
    description="Backend service for District Intelligence project"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health_router)


@app.get("/")
def read_root():
    return {"message": "Welcome to District Intelligence API"}
