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

from app.api_client import submit_common_record
from app.storage.raw_storage import save_json


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]


RAW_DIR = PROJECT_ROOT / "data" / "raw"

NORMALIZED_DIR = (
    PROJECT_ROOT / "data" / "normalized"
)


def _submit_normalized_records(source_name: str, normalized_records):
    stored = 0
    duplicates = 0
    failed = 0

    for record in normalized_records:
        try:
            result = submit_common_record(record)
            if result == "stored":
                stored += 1
            elif result == "duplicate":
                duplicates += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"    {source_name} submission failed for {record.record_id}: {exc}")

    return {
        "stored": stored,
        "duplicates": duplicates,
        "failed": failed,
    }


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
            article = extract_article(article_html, url)
            if article:
                articles.append(article)
        except Exception as exc:
            print(f"    Error: {url} → {exc}")

    extracted_count = len(articles)
    print(f"    Articles extracted: {extracted_count}")

    raw_dir = RAW_DIR / "dinamalar"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / f"{timestamp}.json"
    raw_data = [article.model_dump(mode="json") for article in articles]
    save_json(raw_data, str(raw_file), "dinamalar")

    normalized_records = [article_to_common_record(article, index) for index, article in enumerate(articles)]
    normalized_payloads = [record.model_dump(mode="json") for record in normalized_records]

    normalized_dir = NORMALIZED_DIR / "dinamalar"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_file = normalized_dir / f"{timestamp}.json"
    save_json(normalized_payloads, str(normalized_file), "dinamalar")

    submission = _submit_normalized_records("Dinamalar", normalized_records)

    print(f"\n    Raw: {raw_file}")
    print(f"    Normalized: {normalized_file}")
    print(f"    Extracted: {extracted_count}")
    print(f"    Normalized: {len(normalized_records)}")
    print(f"    Stored: {submission['stored']}")
    print(f"    Duplicates: {submission['duplicates']}")
    print(f"    Failed: {submission['failed']}")

    return {
        "extracted": extracted_count,
        "normalized": len(normalized_records),
        "stored": submission["stored"],
        "duplicates": submission["duplicates"],
        "failed": submission["failed"],
    }


# ============================================================
# WEATHER
# ============================================================

def ingest_weather(timestamp):

    print("\n" + "=" * 60)
    print("WEATHER INGESTION")
    print("=" * 60)

    print("\n[1] Fetching IMD...")

    html = fetch_weather_page()
    weather_records = extract_forecast(html)
    extracted_count = len(weather_records)

    print(f"    Records extracted: {extracted_count}")

    raw_dir = RAW_DIR / "imd"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / f"{timestamp}.json"
    raw_data = [record.model_dump(mode="json") for record in weather_records]
    save_json(raw_data, str(raw_file), "imd")

    normalized_records = [weather_to_common_record(weather, index) for index, weather in enumerate(weather_records)]
    normalized_payloads = [record.model_dump(mode="json") for record in normalized_records]

    normalized_dir = NORMALIZED_DIR / "imd"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_file = normalized_dir / f"{timestamp}.json"
    save_json(normalized_payloads, str(normalized_file), "imd")

    submission = _submit_normalized_records("IMD", normalized_records)

    print(f"\n    Raw: {raw_file}")
    print(f"    Normalized: {normalized_file}")
    print(f"    Extracted: {extracted_count}")
    print(f"    Normalized: {len(normalized_records)}")
    print(f"    Stored: {submission['stored']}")
    print(f"    Duplicates: {submission['duplicates']}")
    print(f"    Failed: {submission['failed']}")

    return {
        "extracted": extracted_count,
        "normalized": len(normalized_records),
        "stored": submission["stored"],
        "duplicates": submission["duplicates"],
        "failed": submission["failed"],
    }


# ============================================================
# AGRICULTURE
# ============================================================

def ingest_agriculture(timestamp):

    print("\n" + "=" * 60)
    print("AGRICULTURE INGESTION")
    print("=" * 60)

    print("\n[1] Fetching Madurai markets...")

    records = extract_market_prices()
    extracted_count = len(records)

    print(f"    Records extracted: {extracted_count}")

    raw_dir = RAW_DIR / "agriculture"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / f"{timestamp}.json"
    raw_data = [record.model_dump(mode="json") for record in records]
    save_json(raw_data, str(raw_file), "agriculture")

    normalized_records = [agriculture_to_common_record(agriculture, index) for index, agriculture in enumerate(records)]
    normalized_payloads = [record.model_dump(mode="json") for record in normalized_records]

    normalized_dir = NORMALIZED_DIR / "agriculture"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_file = normalized_dir / f"{timestamp}.json"
    save_json(normalized_payloads, str(normalized_file), "agriculture")

    submission = _submit_normalized_records("Agriculture", normalized_records)

    print(f"\n    Raw: {raw_file}")
    print(f"    Normalized: {normalized_file}")
    print(f"    Extracted: {extracted_count}")
    print(f"    Normalized: {len(normalized_records)}")
    print(f"    Stored: {submission['stored']}")
    print(f"    Duplicates: {submission['duplicates']}")
    print(f"    Failed: {submission['failed']}")

    return {
        "extracted": extracted_count,
        "normalized": len(normalized_records),
        "stored": submission["stored"],
        "duplicates": submission["duplicates"],
        "failed": submission["failed"],
    }


# ============================================================
# MASTER INGESTION
# ============================================================

def run_all_sources():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    totals = {
        "extracted": 0,
        "normalized": 0,
        "stored": 0,
        "duplicates": 0,
        "failed": 0,
    }

    print("\n")
    print("=" * 70)
    print("DISTRICT INTELLIGENCE - INGESTION")
    print("=" * 70)
    print(f"\nRun timestamp: {timestamp}")

    for source_ingest in (ingest_news, ingest_weather, ingest_agriculture):
        result = source_ingest(timestamp)
        for key in totals:
            totals[key] += result[key]

    print("\n")
    print("=" * 70)
    print("INGESTION COMPLETE")
    print("=" * 70)
    print(f"Total normalized: {totals['normalized']}")
    print(f"Total stored: {totals['stored']}")
    print(f"Total duplicates: {totals['duplicates']}")
    print(f"Total failures: {totals['failed']}")


if __name__ == "__main__":
    run_all_sources()