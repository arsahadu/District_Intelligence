import requests
from datetime import date

from app.models.source_agriculture import SourceAgriculture


BASE_URL = "https://agrimark.tn.gov.in"

DISTRICT_ID = "21"
DISTRICT_NAME = "Madurai"

SOURCE_ID = "tn_agri_marketing"

GET_US_LIST_URL = f"{BASE_URL}/home/getUSList"
GET_PRICE_URL = f"{BASE_URL}/home/getPrice_dir"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest"
}


def get_markets():

    response = requests.post(
        GET_US_LIST_URL,
        data={"district": DISTRICT_ID},
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def get_market_prices(market_id, price_date):

    url = (
        f"{GET_PRICE_URL}/"
        f"{price_date}/"
        f"{DISTRICT_ID}/"
        f"{market_id}"
    )

    response = requests.post(
        url,
        data="",
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def extract_market_prices():

    today = date.today().isoformat()

    markets = get_markets()

    records = []

    for market in markets:

        market_id = market["master_us_id"]
        market_name = market["us_name"]

        print(f"    Fetching: {market_name}")

        data = get_market_prices(
            market_id,
            today
        )

        price_list = data.get(
            "price_list",
            {}
        )

        for commodity, values in price_list.items():

            record = SourceAgriculture(

                source=SOURCE_ID,

                source_url=(
                    f"{GET_PRICE_URL}/"
                    f"{today}/"
                    f"{DISTRICT_ID}/"
                    f"{market_id}"
                ),

                district=DISTRICT_NAME,

                district_id=DISTRICT_ID,

                market=market_name,

                market_id=market_id,

                commodity=commodity.strip(),

                price_date=date.fromisoformat(
                    today
                ),

                quantity=(
                    float(values["qty"])
                    if values.get("qty")
                    else None
                ),

                min_price=(
                    float(values["min"])
                    if values.get("min")
                    else None
                ),

                max_price=(
                    float(values["max"])
                    if values.get("max")
                    else None
                ),

                unit="kg"
            )

            records.append(record)

    return records