"""flumine strategies that record market data without placing any orders.

``TickLoggerStrategy`` writes queryable rows to the database.
``RawStreamRecorder`` writes the untouched stream to files, in the same format
as Betfair's historical data, so recorded races can be replayed in a backtest.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path
from typing import IO, Any

from flumine import BaseStrategy
from loguru import logger

from src.tick_storage import TickStorage
from src.ticks import market_info, runner_infos, ticks_from_market_book


class TickLoggerStrategy(BaseStrategy):
    """Stores every price change for subscribed markets.

    Args:
        storage: Destination for ticks and metadata.
        include_in_play: Also record after the race has started.
        **kwargs: Passed to flumine's ``BaseStrategy`` (``stream`` etc.).
    """

    def __init__(
        self, storage: TickStorage, include_in_play: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.storage = storage
        self.include_in_play = include_in_play
        # market_id -> True once runner names have been stored
        self._metadata: dict[str, bool] = {}

    def check_market_book(self, market: Any, market_book: Any) -> bool:
        if market_book.status == "CLOSED":
            return False
        return self.include_in_play or not market_book.inplay

    def process_market_book(self, market: Any, market_book: Any) -> None:
        if market.market_id not in self._metadata:
            self._save_metadata(market, market_book)
        self.storage.add_ticks(ticks_from_market_book(market_book))

    def process_market_catalogue(self, market: Any, market_catalogue: Any) -> None:
        # The catalogue brings runner names, which the stream itself lacks
        self._save_metadata(market, market.market_book)

    def process_closed_market(self, market: Any, market_book: Any) -> None:
        self._metadata.pop(market.market_id, None)
        self.storage.flush()

    def finish(self, flumine: Any) -> None:
        self.storage.close()
        logger.info(
            "Tick logger finished: {} ticks written, {} dropped",
            self.storage.ticks_written,
            self.storage.ticks_dropped,
        )

    def _save_metadata(self, market: Any, market_book: Any) -> None:
        runners = runner_infos(market, market_book)
        has_names = bool(runners) and all(r.runner_name for r in runners)
        if self._metadata.get(market.market_id) and not has_names:
            return
        saved = self.storage.upsert_market(market_info(market))
        saved = self.storage.upsert_runners(runners) and saved
        if saved:
            self._metadata[market.market_id] = has_names
            logger.info(
                "Recording market {} ({} {}), {} runners",
                market.market_id,
                market.venue,
                market.market_start_datetime,
                len(runners),
            )


class RawStreamRecorder(BaseStrategy):
    """Writes raw stream messages to one file per market.

    Must be attached to a flumine ``BetfairDataStream``. Each line is a
    complete ``mcm`` message, which is the format flumine's simulator reads.
    When a market closes its file is gzipped.

    Args:
        directory: Where recordings are written.
        **kwargs: Passed to flumine's ``BaseStrategy`` (``stream`` etc.).
    """

    def __init__(self, directory: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.directory = Path(directory)
        self._files: dict[str, IO[str]] = {}

    def add(self, flumine: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def process_raw_data(self, clk: str, publish_time: int, datum: dict) -> None:
        market_id = datum.get("id")
        if not market_id:
            return
        handle = self._files.get(market_id)
        if handle is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle = open(self.directory / market_id, "a", encoding="utf-8")
            self._files[market_id] = handle
        message = {"op": "mcm", "clk": clk, "pt": publish_time, "mc": [datum]}
        handle.write(json.dumps(message, separators=(",", ":")) + "\n")

        definition = datum.get("marketDefinition")
        if definition and definition.get("status") == "CLOSED":
            self._close_market(market_id)

    def finish(self, flumine: Any) -> None:
        for handle in self._files.values():
            handle.close()
        self._files.clear()

    def _close_market(self, market_id: str) -> None:
        handle = self._files.pop(market_id, None)
        if handle is not None:
            handle.close()
        raw = self.directory / market_id
        compressed = self.directory / f"{market_id}.gz"
        with open(raw, "rb") as src, gzip.open(compressed, "wb") as dst:
            shutil.copyfileobj(src, dst)
        raw.unlink()
        logger.info("Market {} closed; recording saved to {}", market_id, compressed)
