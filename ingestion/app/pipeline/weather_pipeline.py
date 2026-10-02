from app.connectors.weather.imd import (
    fetch_page,
    extract_forecast
)

from app.normalizers.weather import (
    weather_to_common_record
)

from app.storage.raw_storage import save_json


def run():

    print("\n" + "=" * 60)
    print("IMD WEATHER INGESTION PIPELINE")
    print("=" * 60)

    # 1. Fetch
    print("\n[1] Fetching IMD...")

    html = fetch_page()

    print(f"    Downloaded: {len(html):,} characters")

    # 2. Extract
    print("\n[2] Extracting SourceWeather...")

    weather_records = extract_forecast(html)

    print(
        f"    Extracted: {len(weather_records)} records"
    )

    # 3. Save raw
    raw_records = [
        weather.model_dump(mode="json")
        for weather in weather_records
    ]

    raw_path = save_json(
        raw_records,
        category="raw",
        source="imd"
    )

    print(f"    Raw saved: {raw_path}")

    # 4. Normalize
    print("\n[3] Normalizing → CommonRecord...")

    common_records = [
        weather_to_common_record(
            weather,
            index
        ).model_dump(mode="json")

        for index, weather
        in enumerate(weather_records, start=1)
    ]

    print(
        f"    Normalized: {len(common_records)} records"
    )

    # 5. Save normalized
    normalized_path = save_json(
        common_records,
        category="normalized",
        source="imd"
    )

    print(
        f"    Normalized saved: {normalized_path}"
    )

    print("\n" + "=" * 60)
    print("IMD PIPELINE COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    run()