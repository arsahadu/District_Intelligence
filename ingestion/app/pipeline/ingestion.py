from datetime import datetime
from pathlib import Path

from app.connectors.news.dinamalar import (
    fetch_page as fetch_news_page,
    extract_article_links,
    extract_article
)

from app.connectors.weather.imd import (
    fetch_page as fetch_weather_page,
    extract_forecast
)

from app.connectors.agriculture.agriculture import (
    extract_market_prices
)

from app.normalizers.news import (
    article_to_common_record
)

from app.normalizers.weather import (
    weather_to_common_record
)

from app.normalizers.agriculture import (
    agriculture_to_common_record
)

from app.storage.raw_storage import save_json


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]


RAW_DIR = PROJECT_ROOT / "data" / "raw"

NORMALIZED_DIR = (
    PROJECT_ROOT / "data" / "normalized"
)


# ============================================================
# NEWS
# ============================================================

def ingest_news(timestamp):

    print("\n" + "=" * 60)
    print("NEWS INGESTION")
    print("=" * 60)

    print("\n[1] Fetching Dinamalar Madurai...")

    html = fetch_news_page(
        "https://www.dinamalar.com/district/291"
    )

    links = extract_article_links(html)

    print(f"    Articles found: {len(links)}")

    articles = []

    for url in links:

        try:
            article_html = fetch_news_page(url)

            article = extract_article(
                article_html,
                url
            )

            if article:
                articles.append(article)

        except Exception as e:

            print(
                f"    Error: {url} → {e}"
            )

    print(
        f"    Articles extracted: "
        f"{len(articles)}"
    )

    # RAW

    raw_dir = RAW_DIR / "dinamalar"

    raw_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    raw_file = raw_dir / f"{timestamp}.json"

    raw_data = [
        article.model_dump(mode="json")
        for article in articles
    ]

    save_json(
        raw_data,
        str(raw_file),
        "dinamalar"
    )

    # NORMALIZE

    normalized = []

    for index, article in enumerate(articles):

        record = article_to_common_record(
            article,
            index
        )

        normalized.append(
            record.model_dump(mode="json")
        )

    normalized_dir = (
        NORMALIZED_DIR / "dinamalar"
    )

    normalized_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    normalized_file = (
        normalized_dir / f"{timestamp}.json"
    )

    save_json(
        normalized,
        str(normalized_file),
        "dinamalar"
    )

    print(
        f"\n    Raw: {raw_file}"
    )

    print(
        f"    Normalized: {normalized_file}"
    )


# ============================================================
# WEATHER
# ============================================================

def ingest_weather(timestamp):

    print("\n" + "=" * 60)
    print("WEATHER INGESTION")
    print("=" * 60)

    print("\n[1] Fetching IMD...")

    html = fetch_weather_page()

    weather_records = extract_forecast(
        html
    )

    print(
        f"    Records extracted: "
        f"{len(weather_records)}"
    )

    # RAW

    raw_dir = RAW_DIR / "imd"

    raw_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    raw_file = raw_dir / f"{timestamp}.json"

    raw_data = [
        record.model_dump(mode="json")
        for record in weather_records
    ]

    save_json(
        raw_data,
        str(raw_file),
        "imd"
    )

    # NORMALIZE

    normalized = []

    for index, weather in enumerate(
        weather_records
    ):

        record = weather_to_common_record(
            weather,
            index
        )

        normalized.append(
            record.model_dump(mode="json")
        )

    normalized_dir = (
        NORMALIZED_DIR / "imd"
    )

    normalized_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    normalized_file = (
        normalized_dir / f"{timestamp}.json"
    )

    save_json(
        normalized,
        str(normalized_file),
        "imd"
    )

    print(
        f"\n    Raw: {raw_file}"
    )

    print(
        f"    Normalized: {normalized_file}"
    )


# ============================================================
# AGRICULTURE
# ============================================================

def ingest_agriculture(timestamp):

    print("\n" + "=" * 60)
    print("AGRICULTURE INGESTION")
    print("=" * 60)

    print("\n[1] Fetching Madurai markets...")

    records = extract_market_prices()

    print(
        f"    Records extracted: "
        f"{len(records)}"
    )

    # RAW

    raw_dir = (
        RAW_DIR / "agriculture"
    )

    raw_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    raw_file = (
        raw_dir / f"{timestamp}.json"
    )

    raw_data = [
        record.model_dump(mode="json")
        for record in records
    ]

    save_json(
        raw_data,
        str(raw_file),
        "agriculture"
    )

    # NORMALIZE

    normalized = []

    for index, agriculture in enumerate(
        records
    ):

        record = agriculture_to_common_record(
            agriculture,
            index
        )

        normalized.append(
            record.model_dump(mode="json")
        )

    normalized_dir = (
        NORMALIZED_DIR / "agriculture"
    )

    normalized_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    normalized_file = (
        normalized_dir / f"{timestamp}.json"
    )

    save_json(
        normalized,
        str(normalized_file),
        "agriculture"
    )

    print(
        f"\n    Raw: {raw_file}"
    )

    print(
        f"    Normalized: {normalized_file}"
    )


# ============================================================
# MASTER INGESTION
# ============================================================

def run_all_sources():

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    print("\n")
    print("=" * 70)
    print("DISTRICT INTELLIGENCE - INGESTION")
    print("=" * 70)

    print(
        f"\nRun timestamp: {timestamp}"
    )

    # --------------------------------------------------------
    # SOURCE 1
    # --------------------------------------------------------

    ingest_news(timestamp)

    # --------------------------------------------------------
    # SOURCE 2
    # --------------------------------------------------------

    ingest_weather(timestamp)

    # --------------------------------------------------------
    # SOURCE 3
    # --------------------------------------------------------

    ingest_agriculture(timestamp)

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("ALL SOURCE INGESTION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":

    run_all_sources()