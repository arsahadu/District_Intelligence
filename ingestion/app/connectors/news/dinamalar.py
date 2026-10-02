import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime

from ingestion.app.models.source_article import SourceArticle
from ingestion.app.normalizers.news import article_to_common_record
from ingestion.app.storage.raw_storage import save_json


# --------------------------------------------------
# Configuration
# --------------------------------------------------

DINAMALAR_URL = "https://www.dinamalar.com/district/291"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

REQUEST_TIMEOUT = 20


# --------------------------------------------------
# Fetch webpage
# --------------------------------------------------

def fetch_page(url: str) -> str:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.text


# --------------------------------------------------
# Extract Madurai article URLs
# --------------------------------------------------

def extract_articles(html: str) -> list[dict]:

    soup = BeautifulSoup(html, "html.parser")

    articles = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):

        title = link.get_text(" ", strip=True)
        href = link.get("href")

        if not title or not href:
            continue

        url = urljoin(DINAMALAR_URL, href)

        # Only Madurai district news articles
        if "/news/tamil-nadu-district-news-madurai/" not in url:
            continue

        # Remove duplicates
        if url in seen_urls:
            continue

        # Ignore very short titles
        if len(title) < 10:
            continue

        seen_urls.add(url)

        articles.append({
            "source": "dinamalar",
            "title": title,
            "url": url
        })

    return articles


# --------------------------------------------------
# Extract individual article
# --------------------------------------------------

def extract_article(article: dict) -> dict:

    html = fetch_page(article["url"])

    soup = BeautifulSoup(html, "html.parser")

    # ----------------------------------------------
    # Title
    # ----------------------------------------------

    title_tag = soup.find("h1")

    if title_tag:

        title = title_tag.get_text(
            " ",
            strip=True
        )

    else:

        title = article["title"]


    # ----------------------------------------------
    # Published time
    # ----------------------------------------------

    published_at = None

    page_text = soup.get_text(
        " ",
        strip=True
    )

    added_index = page_text.find("ADDED")

    if added_index != -1:

        published_at = page_text[
            added_index:added_index + 80
        ]


    # ----------------------------------------------
    # Article content
    # ----------------------------------------------

    paragraphs = []

    for p in soup.find_all("p"):

        text = p.get_text(
            " ",
            strip=True
        )

        if len(text) > 40:

            paragraphs.append(text)


    content = "\n".join(paragraphs)


    # ----------------------------------------------
    # Return source-specific record
    # ----------------------------------------------

    return {
        "source": "dinamalar",
        "url": article["url"],
        "title": title,
        "published_at": published_at,
        "content": content
    }


# --------------------------------------------------
# Main pipeline
# --------------------------------------------------

def main():

    print("\n" + "=" * 60)
    print("DINAMALAR MADURAI DATA CONNECTOR")
    print("=" * 60)


    # ----------------------------------------------
    # 1. Fetch district page
    # ----------------------------------------------

    print("\n[1] Fetching Madurai district page...")

    html = fetch_page(DINAMALAR_URL)

    print(
        f"    Downloaded: {len(html):,} characters"
    )


    # ----------------------------------------------
    # 2. Find article URLs
    # ----------------------------------------------

    print("\n[2] Extracting Madurai articles...")

    articles = extract_articles(html)

    print(
        f"    Articles found: {len(articles)}"
    )

    if not articles:

        print("    No articles found.")
        return


    # ----------------------------------------------
    # 3. Fetch individual articles
    # ----------------------------------------------

    print("\n[3] Fetching article content...")

    collected_articles = []

    failed_articles = []

    for index, article in enumerate(
        articles,
        start=1
    ):

        print(
            f"\n    [{index}/{len(articles)}] "
            f"{article['title']}"
        )

        try:

            raw_article = extract_article(
                article
            )

            # Validate with Pydantic
            source_article = SourceArticle(
                **raw_article
            )

            collected_articles.append(
                source_article.model_dump()
            )

            print("        ✓ Collected")

        except Exception as error:

            failed_articles.append({
                "url": article["url"],
                "title": article["title"],
                "error": str(error)
            })

            print(
                f"        ✗ Failed: {error}"
            )


    # ----------------------------------------------
    # 4. Summary
    # ----------------------------------------------

    print("\n" + "=" * 60)
    print("COLLECTION SUMMARY")
    print("=" * 60)

    print(
        f"Found      : {len(articles)}"
    )

    print(
        f"Collected  : {len(collected_articles)}"
    )

    print(
        f"Failed     : {len(failed_articles)}"
    )


    if not collected_articles:

        print("\nNo articles were collected.")
        return


    # ----------------------------------------------
    # 5. Save RAW data
    # ----------------------------------------------

    print("\n[4] Saving RAW data...")

    raw_file = save_json(
        data=collected_articles,
        category="raw",
        source="dinamalar"
    )

    print(
        f"    Saved: {raw_file}"
    )


    # ----------------------------------------------
    # 6. Convert → CommonRecord
    # ----------------------------------------------

    print("\n[5] Converting to CommonRecord...")

    common_records = []

    for index, article_data in enumerate(
        collected_articles,
        start=1
    ):

        article = SourceArticle(
            **article_data
        )

        common_record = article_to_common_record(
            article=article,
            index=index
        )

        common_records.append(
            common_record.model_dump()
        )


    print(
        f"    CommonRecords created: "
        f"{len(common_records)}"
    )


    # ----------------------------------------------
    # 7. Save normalized data
    # ----------------------------------------------

    print("\n[6] Saving NORMALIZED data...")

    normalized_file = save_json(
        data=common_records,
        category="normalized",
        source="dinamalar"
    )

    print(
        f"    Saved: {normalized_file}"
    )


    # ----------------------------------------------
    # 8. Failed article log
    # ----------------------------------------------

    if failed_articles:

        failed_file = save_json(
            data=failed_articles,
            category="errors",
            source="dinamalar"
        )

        print(
            f"\n    Failed article log: "
            f"{failed_file}"
        )


    # ----------------------------------------------
    # Finished
    # ----------------------------------------------

    print("\n" + "=" * 60)
    print("DINAMALAR PIPELINE COMPLETED")
    print("=" * 60)

    print(
        f"\nRaw records        : "
        f"{len(collected_articles)}"
    )

    print(
        f"CommonRecords      : "
        f"{len(common_records)}"
    )

    print(
        f"Failed             : "
        f"{len(failed_articles)}"
    )

    print("\nDone.")


# --------------------------------------------------
# Entry point
# --------------------------------------------------

if __name__ == "__main__":
    main()