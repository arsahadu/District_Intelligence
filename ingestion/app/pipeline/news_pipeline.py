from app.connectors.news.dinamalar import (
    fetch_page,
    extract_article_links,
    extract_article
)

from app.normalizers.news import (
    article_to_common_record
)

from app.storage.raw_storage import save_json


DINAMALAR_URL = "https://www.dinamalar.com/district/291"


def run():

    print("\n" + "=" * 60)
    print("DINAMALAR NEWS INGESTION PIPELINE")
    print("=" * 60)

    # -----------------------------------------------------
    # 1. Fetch district page
    # -----------------------------------------------------

    print("\n[1] Fetching Dinamalar Madurai page...")

    html = fetch_page(DINAMALAR_URL)

    print(
        f"    Downloaded: {len(html):,} characters"
    )

    # -----------------------------------------------------
    # 2. Extract article links
    # -----------------------------------------------------

    print("\n[2] Extracting Madurai article links...")

    article_links = extract_article_links(html)

    print(
        f"    Articles found: {len(article_links)}"
    )

    # -----------------------------------------------------
    # 3. Fetch articles
    # -----------------------------------------------------

    print("\n[3] Fetching articles...")

    articles = []
    errors = []

    for index, url in enumerate(
        article_links,
        start=1
    ):

        try:

            article_html = fetch_page(url)

            article = extract_article(
                article_html,
                url
            )

            articles.append(article)

            print(
                f"    [{index}/{len(article_links)}] "
                f"{article.title[:70]}"
            )

        except Exception as error:

            errors.append({
                "url": url,
                "error": str(error)
            })

            print(
                f"    [{index}/{len(article_links)}] "
                f"FAILED: {url}"
            )

    # -----------------------------------------------------
    # 4. Save raw SourceArticle data
    # -----------------------------------------------------

    print("\n[4] Saving raw data...")

    raw_records = [
        article.model_dump(mode="json")
        for article in articles
    ]

    raw_path = save_json(
        raw_records,
        category="raw",
        source="dinamalar"
    )

    print(
        f"    Raw data saved: {raw_path}"
    )

    # -----------------------------------------------------
    # 5. Normalize → CommonRecord
    # -----------------------------------------------------

    print(
        "\n[5] Converting to CommonRecord..."
    )

    common_records = [
        article_to_common_record(
            article,
            index
        ).model_dump(mode="json")

        for index, article
        in enumerate(articles, start=1)
    ]

    print(
        f"    Normalized: {len(common_records)} records"
    )

    # -----------------------------------------------------
    # 6. Save normalized data
    # -----------------------------------------------------

    normalized_path = save_json(
        common_records,
        category="normalized",
        source="dinamalar"
    )

    print(
        f"    Normalized data saved: "
        f"{normalized_path}"
    )

    # -----------------------------------------------------
    # 7. Save errors
    # -----------------------------------------------------

    if errors:

        error_path = save_json(
            errors,
            category="errors",
            source="dinamalar"
        )

        print(
            f"    Errors saved: {error_path}"
        )

    # -----------------------------------------------------
    # 8. Summary
    # -----------------------------------------------------

    print("\n" + "=" * 60)
    print("DINAMALAR NEWS PIPELINE COMPLETE")
    print("=" * 60)

    print(
        f"Articles extracted : {len(articles)}"
    )

    print(
        f"Articles failed    : {len(errors)}"
    )

    print(
        f"Normalized records : {len(common_records)}"
    )

    print("=" * 60)


if __name__ == "__main__":
    run()