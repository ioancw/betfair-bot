"""Builders for the flumine framework, Betfair client and stream subscriptions."""

from __future__ import annotations

from typing import Sequence

import betfairlightweight
from betfairlightweight.filters import streaming_market_data_filter, streaming_market_filter
from flumine import Flumine, clients
from flumine.streams.betfairdatastream import BetfairDataStream
from flumine.streams.betfairmarketstream import BetfairMarketStream

HORSE_RACING = "7"

# Best prices, traded volume and market status: enough to store ticks and to
# simulate order matching while paper trading
PRICE_FIELDS = ["EX_BEST_OFFERS", "EX_TRADED", "EX_TRADED_VOL", "EX_LTP", "EX_MARKET_DEF"]

# Everything the exchange publishes, for recordings that will be replayed
RECORDING_FIELDS = [
    "EX_ALL_OFFERS",
    "EX_TRADED",
    "EX_TRADED_VOL",
    "EX_LTP",
    "EX_MARKET_DEF",
    "SP_TRADED",
    "SP_PROJECTED",
]


def create_trading_client(
    username: str, password: str, app_key: str, cert_path: str
) -> betfairlightweight.APIClient:
    """Betfair API client for flumine, which logs in and keeps the session alive."""
    return betfairlightweight.APIClient(
        username=username, password=password, app_key=app_key, certs=cert_path
    )


def create_framework(
    trading: betfairlightweight.APIClient, paper_trade: bool = True, place_orders: bool = True
) -> Flumine:
    """Build the flumine framework.

    Args:
        trading: Betfair API client.
        paper_trade: Simulate order matching locally instead of sending orders
            to the exchange. Only ``paper_trade=False`` risks real money.
        place_orders: Set False for data-only runs; no order stream is opened.
    """
    client = clients.BetfairClient(
        trading, paper_trade=paper_trade, order_stream=place_orders
    )
    return Flumine(client=client)


def horse_racing_filter(
    country_codes: Sequence[str] = ("GB",), market_types: Sequence[str] = ("WIN",)
) -> dict:
    """Stream subscription for horse racing markets."""
    return streaming_market_filter(
        event_type_ids=[HORSE_RACING],
        country_codes=list(country_codes),
        market_types=list(market_types),
    )


def price_stream(
    country_codes: Sequence[str] = ("GB",), ladder_levels: int = 3
) -> BetfairMarketStream:
    """Live market books for strategies and the tick logger."""
    return BetfairMarketStream(
        market_filter=horse_racing_filter(country_codes),
        market_data_filter=streaming_market_data_filter(
            fields=PRICE_FIELDS, ladder_levels=ladder_levels
        ),
    )


def recording_stream(country_codes: Sequence[str] = ("GB",)) -> BetfairDataStream:
    """Raw stream messages for :class:`src.stream_logger.RawStreamRecorder`."""
    return BetfairDataStream(
        market_filter=horse_racing_filter(country_codes),
        market_data_filter=streaming_market_data_filter(fields=RECORDING_FIELDS),
    )
