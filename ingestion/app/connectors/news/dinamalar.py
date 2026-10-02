import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime

from app.models.source_article import SourceArticle
from app.storage.raw_storage import save_json


DINAMALAR_URL = "https://www.dinamalar.com/district/291"

SOURCE_ID = "dinamalar"
SOURCE_TYPE = "news"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

REQUEST_TIMEOUT = 20


# ---------------------------------------------------------
# Fetch
# ---------------------------------------------------------

def fetch_page(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.text


# ---------------------------------------------------------
# Cleaning
# ---------------------------------------------------------

def clean_text(value):

    if not value:
        return ""

    return " ".join(value.split())


# ---------------------------------------------------------
# Dinamalar date parser
# ---------------------------------------------------------

DINAMALAR_MONTHS = {
    "ஜன": 1,
    "பிப்": 2,
    "மார்": 3,
    "ஏப்": 4,
    "மே": 5,
    "ஜூன்": 6,
    "ஜூலை": 7,
    "ஆக": 8,
    "செப்": 9,
    "அக்": 10,
    "நவ": 11,
    "டிச": 12,
}


def parse_dinamalar_date(text):

    if not text:
        return None

    text = clean_text(text)

    match = re.search(
        r"ADDED\s*:\s*"
        r"([^\s]+)\s+"
        r"(\d{1,2}),\s*"
        r"(\d{4})\s+"
        r"(\d{1,2}):(\d{2})\s*"
        r"(AM|PM)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    month_text = match.group(1)
    day = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    am_pm = match.group(6).upper()

    month = DINAMALAR_MONTHS.get(month_text)

    if month is None:
        return None

    # Convert 12-hour clock → 24-hour clock
    if am_pm == "PM" and hour != 12:
        hour += 12

    if am_pm == "AM" and hour == 12:
        hour = 0

    return datetime(
        year,
        month,
        day,
        hour,
        minute
    )


# ---------------------------------------------------------
# Extract article links
# ---------------------------------------------------------

def extract_article_links(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []

    for anchor in soup.find_all(
        "a",
        href=True
    ):

        href = anchor["href"]

        # Only Madurai district article URLs
        if (
            "/news/tamil-nadu-district-news-madurai/"
            not in href
        ):
            continue

        if href.startswith("/"):

            href = (
                "https://www.dinamalar.com"
                + href
            )

        if href not in links:

            links.append(href)

    return links


# ---------------------------------------------------------
# Extract individual article
# ---------------------------------------------------------

def extract_article(html, url):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # -------------------------
    # Title
    # -------------------------

    title = ""

    title_tag = soup.find("h1")

    if title_tag:

        title = clean_text(
            title_tag.get_text(
                " ",
                strip=True
            )
        )

    # -------------------------
    # Published date
    # -------------------------

    published_at = None

    added_text = soup.find(
        string=lambda text:
            text and "ADDED" in text
    )

    if added_text:

        published_at = parse_dinamalar_date(
            added_text
        )

    # -------------------------
    # Content
    # -------------------------

    paragraphs = []

    for paragraph in soup.find_all("p"):

        text = clean_text(
            paragraph.get_text(
                " ",
                strip=True
            )
        )

        if text:

            paragraphs.append(text)

    content = " ".join(paragraphs)

    # -------------------------
    # SourceArticle
    # -------------------------

    return SourceArticle(
        source=SOURCE_ID,
        url=url,
        title=title,
        published_at=published_at,
        content=content
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    print("\n" + "=" * 60)
    print("DINAMALAR MADURAI NEWS CONNECTOR")
    print("=" * 60)

    # -----------------------------------------------------
    # 1. Fetch district page
    # -----------------------------------------------------

    print(
        "\n[1] Fetching Dinamalar Madurai page..."
    )

    html = fetch_page(
        DINAMALAR_URL
    )

    print(
        f"    Downloaded: {len(html):,} characters"
    )

    # -----------------------------------------------------
    # 2. Extract article links
    # -----------------------------------------------------

    print(
        "\n[2] Extracting Madurai article links..."
    )

    article_links = extract_article_links(
        html
    )

    print(
        f"    Articles found: {len(article_links)}"
    )

    # -----------------------------------------------------
    # 3. Fetch articles
    # -----------------------------------------------------

    print(
        "\n[3] Fetching articles..."
    )

    articles = []

    errors = []

    for index, url in enumerate(
        article_links,
        start=1
    ):

        try:

            article_html = fetch_page(
                url
            )

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

    print(
        "\n[4] Saving raw SourceArticle data..."
    )

    raw_records = [
        article.model_dump(
            mode="json"
        )
        for article in articles
    ]

    raw_path = save_json(
        raw_records,
        category="raw",
        source=SOURCE_ID
    )

    print(
        f"    Raw data saved: {raw_path}"
    )

    # -----------------------------------------------------
    # 5. Save errors
    # -----------------------------------------------------

    if errors:

        error_path = save_json(
            errors,
            category="errors",
            source=SOURCE_ID
        )

        print(
            f"    Errors saved: {error_path}"
        )

    # -----------------------------------------------------
    # 6. Summary
    # -----------------------------------------------------

    print("\n" + "=" * 60)
    print("DINAMALAR CONNECTOR COMPLETE")
    print("=" * 60)

    print(
        f"Articles extracted : {len(articles)}"
    )

    print(
        f"Articles failed    : {len(errors)}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()