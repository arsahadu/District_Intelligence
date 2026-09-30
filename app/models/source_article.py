from pydantic import BaseModel
from typing import Optional


class SourceArticle(BaseModel):
    source: str
    url: str
    title: str
    published_at: Optional[str] = None
    content: str