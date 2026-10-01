from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.source import SourceCreate, SourceRead
from app.services.record_service import create_source, list_sources

router = APIRouter()


@router.get("/sources", response_model=list[SourceRead])
def get_sources(db: Session = Depends(get_db)) -> list[SourceRead]:
    return list_sources(db)


@router.post("/sources", response_model=SourceRead)
def register_source(source: SourceCreate, db: Session = Depends(get_db)) -> SourceRead:
    try:
        return create_source(db, source.model_dump(mode="python"))
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Source '{source.source_id}' already exists.",
        ) from None
