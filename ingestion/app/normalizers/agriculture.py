from datetime import datetime, timezone

from app.models.common_record import (
    CommonRecord,
    Location
)

from app.models.source_agriculture import (
    SourceAgriculture
)


def agriculture_to_common_record(
    agriculture: SourceAgriculture,
    index: int
) -> CommonRecord:

    market_slug = (
        agriculture.market
        .upper()
        .replace(" ", "_")
    )

    commodity_slug = (
        agriculture.commodity
        .upper()
        .replace(" ", "_")
    )

    return CommonRecord(

        record_id=(
            f"AGRI-MDU-"
            f"{market_slug}-"
            f"{commodity_slug}-"
            f"{agriculture.price_date}"
        ),

        source_id=agriculture.source,

        source_type="agriculture",

        record_type="market_price",

        title=(
            f"{agriculture.commodity} price - "
            f"{agriculture.market} Uzhavar Santhai"
        ),

        event_time=agriculture.price_date,

        location=Location(
            raw_text=(
                f"{agriculture.market} "
                f"Uzhavar Santhai, Madurai"
            ),

            district="Madurai",

            state="Tamil Nadu"
        ),

        data={

            "market": agriculture.market,

            "market_id": agriculture.market_id,

            "commodity": agriculture.commodity,

            "quantity": agriculture.quantity,

            "min_price": agriculture.min_price,

            "max_price": agriculture.max_price,

            "unit": agriculture.unit,

            "district_id": agriculture.district_id

        },

        severity=None,

        status=None,

        source_url=agriculture.source_url,

        retrieved_at=datetime.now(
            timezone.utc
        ).isoformat(),

        raw_reference=agriculture.source_url
    )