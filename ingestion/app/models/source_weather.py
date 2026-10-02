from pydantic import BaseModel
from typing import Optional


class SourceWeather(BaseModel):
    source: str
    source_url: str
    station_id: str
    forecast_date: str

    min_temp_c: Optional[float] = None
    max_temp_c: Optional[float] = None
    forecast: Optional[str] = None
    warning: Optional[str] = None

    relative_humidity_0830: Optional[float] = None
    relative_humidity_1730: Optional[float] = None