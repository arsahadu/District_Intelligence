import requests
from bs4 import BeautifulSoup
from datetime import datetime

from app.models.source_weather import SourceWeather
from app.storage.raw_storage import save_json


IMD_CITY_URL = (
    "https://city.imd.gov.in/"
    "citywx/city_weather_test_try_warnings.php?id=43360"
)

SOURCE_ID = "imd_city_madurai"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

REQUEST_TIMEOUT = 60


def fetch_page():
    response = requests.get(
        IMD_CITY_URL,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.text


def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split())


def extract_forecast(html):

    soup = BeautifulSoup(html, "html.parser")

    forecasts = []

    heading = soup.find(
        string=lambda text: text and "7 Day's Forecast" in text
    )

    if not heading:
        raise ValueError("7-day forecast section not found")

    table = heading.find_parent("table")

    if not table:
        raise ValueError("Forecast table not found")

    rows = table.find_all("tr")

    for row in rows:

        cells = row.find_all(["td", "th"])

        values = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in cells
        ]

        if len(values) < 7:
            continue

        if values[0] == "Date":
            continue

        date_value = values[0]

        if "-" not in date_value:
            continue

        try:
            min_temp = float(values[1])
        except ValueError:
            min_temp = None

        try:
            max_temp = float(values[2])
        except ValueError:
            max_temp = None

        try:
            rh_0830 = float(values[5])
        except ValueError:
            rh_0830 = None

        try:
            rh_1730 = float(values[6])
        except ValueError:
            rh_1730 = None

        forecasts.append(
            SourceWeather(
                source=SOURCE_ID,
                source_url=IMD_CITY_URL,
                station_id="43360",
                forecast_date=date_value,
                min_temp_c=min_temp,
                max_temp_c=max_temp,
                forecast=values[3],
                warning=values[4],
                relative_humidity_0830=rh_0830,
                relative_humidity_1730=rh_1730
            )
        )

    return forecasts


def main():

    print("\n" + "=" * 60)
    print("IMD MADURAI WEATHER CONNECTOR")
    print("=" * 60)

    # --------------------------------
    # 1. Fetch
    # --------------------------------

    print("\n[1] Fetching IMD Madurai page...")

    html = fetch_page()

    print(f"    Downloaded: {len(html):,} characters")

    # --------------------------------
    # 2. Extract
    # --------------------------------

    print("\n[2] Extracting 7-day forecast...")

    forecasts = extract_forecast(html)

    print(f"    Forecast records: {len(forecasts)}")

    # --------------------------------
    # 3. Save RAW SourceWeather
    # --------------------------------

    raw_records = [
        forecast.model_dump(mode="json")
        for forecast in forecasts
    ]

    raw_path = save_json(
        raw_records,
        category="raw",
        source="imd"
    )

    print(f"\n    Raw data saved: {raw_path}")

    # --------------------------------
    # 4. Display
    # --------------------------------

    print("\n" + "=" * 60)
    print("IMD SOURCE WEATHER RECORDS")
    print("=" * 60)

    for forecast in forecasts:

        print(f"\n{forecast.forecast_date}")
        print(f"Min Temp : {forecast.min_temp_c}°C")
        print(f"Max Temp : {forecast.max_temp_c}°C")
        print(f"Forecast : {forecast.forecast}")
        print(f"Warning  : {forecast.warning}")

    print("\n" + "=" * 60)
    print(
        f"Successfully extracted {len(forecasts)} SourceWeather records"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()