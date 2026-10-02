from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SourceArticle(BaseModel):
    source: str
    url: str
    title: str
    published_at: Optional[datetime] = None
    content: str