"""Stage 7: the per-feed reading a record cannot declare for itself."""

from __future__ import annotations

from intelligence.mapping.assembly import MappingPolicy
from intelligence.mapping.record_input import CONTENT_PATH

DEFAULT = MappingPolicy(provider="intelligence.mapping.default")

NEWS = MappingPolicy(
    text_keys=(CONTENT_PATH,),
    provider="intelligence.mapping.news",
)

#: A weather feed states its own valid instant, and puts the prose in a field per publisher.
WEATHER_RECORD_TYPES = frozenset({"alert", "forecast", "nowcast", "observation", "warning"})
WEATHER = MappingPolicy(
    text_keys=(CONTENT_PATH, "data.forecast", "data.warning", "data.observation"),
    extra_keys=(
        "data.min_temp_c",
        "data.max_temp_c",
        "data.station_id",
        "data.relative_humidity_0830",
        "data.relative_humidity_1730",
    ),
    time_trusted_record_types=WEATHER_RECORD_TYPES,
    provider="intelligence.mapping.weather",
)

#: A market feed states prices as numbers: they are cited, never read as prose.
#: Its event_time is the price date the feed publishes, not a page stamp, so it stands.
AGRICULTURE = MappingPolicy(
    text_keys=(CONTENT_PATH,),
    extra_keys=(
        "data.market",
        "data.market_id",
        "data.commodity",
        "data.quantity",
        "data.min_price",
        "data.max_price",
        "data.unit",
        "data.district_id",
    ),
    time_trusted_record_types=frozenset({"market_price"}),
    provider="intelligence.mapping.agriculture",
)

FEED_POLICIES: dict[str, MappingPolicy] = {
    "news": NEWS,
    "weather": WEATHER,
    "agriculture": AGRICULTURE,
}


def policy_for(source_type: str) -> MappingPolicy:
    """The reading this feed needs, or the plain one for a feed nobody has tuned yet."""
    return FEED_POLICIES.get(source_type, DEFAULT)


__all__ = [
    "AGRICULTURE",
    "CONTENT_PATH",
    "DEFAULT",
    "FEED_POLICIES",
    "NEWS",
    "WEATHER",
    "WEATHER_RECORD_TYPES",
    "policy_for",
]
