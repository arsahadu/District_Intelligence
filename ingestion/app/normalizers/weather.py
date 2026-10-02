from datetime import datetime, timezone

from app.models.common_record import CommonRecord, Location
from app.models.source_weather import SourceWeather


def weather_to_common_record(
    weather: SourceWeather,
    index: int
) -> CommonRecord:

    # IMD gives dates such as "02-Oct"
    forecast_date = datetime.strptime(
        f"{weather.forecast_date}-{datetime.now().year}",
        "%d-%b-%Y"
    ).date()

    return CommonRecord(
        record_id=f"WEATHER-MDU-{weather.forecast_date}",
        source_id=weather.source,
        source_type="weather",
        record_type="forecast",

        title=(
            f"Madurai Weather Forecast - "
            f"{weather.forecast_date}"
        ),

        event_time=forecast_date,

        location=Location(
            raw_text="Madurai",
            district="Madurai",
            state="Tamil Nadu"
        ),

        data={
            "min_temp_c": weather.min_temp_c,
            "max_temp_c": weather.max_temp_c,
            "forecast": weather.forecast,
            "warning": weather.warning,
            "relative_humidity_0830": (
                weather.relative_humidity_0830
            ),
            "relative_humidity_1730": (
                weather.relative_humidity_1730
            ),
            "station_id": weather.station_id
        },

        source_url=weather.source_url,

        retrieved_at=datetime.now(
            timezone.utc
        ).isoformat(),

        raw_reference=weather.source_url
    )