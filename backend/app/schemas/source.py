from pydantic import BaseModel, ConfigDict


class SourceCreate(BaseModel):
    source_id: str
    name: str
    source_type: str
    base_url: str | None = None
    status: str = "active"


class SourceRead(SourceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
