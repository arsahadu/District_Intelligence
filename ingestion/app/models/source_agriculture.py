from pydantic import BaseModel
from typing import Optional
from datetime import date


class SourceAgriculture(BaseModel):
    source: str
    source_url: str

    district: str
    district_id: str

    market: str
    market_id: str

    commodity: str
    price_date: date

    quantity: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None

    unit: str = "kg"