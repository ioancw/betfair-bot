"""Tests for the flumine builders and the live-trading safety gate."""

import importlib.util
from pathlib import Path

import pytest
from flumine.streams.betfairdatastream import BetfairDataStream
from flumine.streams.betfairmarketstream import BetfairMarketStream

from src.flumine_setup import (
    create_framework,
    create_trading_client,
    horse_racing_filter,
    price_stream,
    recording_stream,
)


def test_horse_racing_filter_defaults_to_gb_win_markets():
    assert horse_racing_filter() == {
        "eventTypeIds": ["7"],
        "countryCodes": ["GB"],
        "marketTypes": ["WIN"],
    }


def test_horse_racing_filter_accepts_other_countries():
    assert horse_racing_filter(["GB", "IE"])["countryCodes"] == ["GB", "IE"]


def test_price_stream_requests_best_prices_and_traded_volume():
    stream = price_stream(["GB"], ladder_levels=3)

    assert isinstance(stream, BetfairMarketStream)
    assert stream.market_filter["eventTypeIds"] == ["7"]
    assert stream.market_data_filter["ladderLevels"] == 3
    assert {"EX_BEST_OFFERS", "EX_TRADED", "EX_LTP", "EX_MARKET_DEF"} <= set(
        stream.market_data_filter["fields"]
    )


def test_recording_stream_is_raw_and_requests_full_depth():
    stream = recording_stream(["GB", "IE"])

    assert isinstance(stream, BetfairDataStream)
    assert stream.market_filter["countryCodes"] == ["GB", "IE"]
    assert "EX_ALL_OFFERS" in stream.market_data_filter["fields"]


def test_trading_client_uses_cert_directory(tmp_path):
    client = create_trading_client("user", "pass", "key", str(tmp_path))
    assert client.username == "user"
    assert client.app_key == "key"
    assert client.certs == str(tmp_path)


def test_framework_paper_trades_by_default(tmp_path):
    trading = create_trading_client("user", "pass", "key", str(tmp_path))
    framework = create_framework(trading)
    client = framework.clients.get_default()
    assert client.paper_trade is True


def test_data_only_framework_opens_no_order_stream(tmp_path):
    trading = create_trading_client("user", "pass", "key", str(tmp_path))
    framework = create_framework(trading, place_orders=False)
    assert framework.clients.get_default().order_stream is False
    assert len(framework.streams) == 0


@pytest.fixture
def run_strategy_script():
    path = Path(__file__).parent.parent / "scripts" / "run_strategy.py"
    spec = importlib.util.spec_from_file_location("run_strategy_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLiveTradingGate:
    def test_paper_trading_without_flag(self, run_strategy_script, monkeypatch):
        monkeypatch.setenv("BETFAIR_LIVE_TRADING", "yes")
        assert run_strategy_script.live_trading_allowed(False, 2.0) is False

    def test_flag_alone_is_refused(self, run_strategy_script, monkeypatch):
        monkeypatch.delenv("BETFAIR_LIVE_TRADING", raising=False)
        with pytest.raises(SystemExit, match="BETFAIR_LIVE_TRADING"):
            run_strategy_script.live_trading_allowed(True, 2.0)

    def test_flag_and_env_var_allow_live(self, run_strategy_script, monkeypatch):
        monkeypatch.setenv("BETFAIR_LIVE_TRADING", "yes")
        assert run_strategy_script.live_trading_allowed(True, 2.0) is True

    def test_large_live_stake_is_refused(self, run_strategy_script, monkeypatch):
        monkeypatch.setenv("BETFAIR_LIVE_TRADING", "yes")
        with pytest.raises(SystemExit, match="exceeds"):
            run_strategy_script.live_trading_allowed(True, 50.0)
