from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault("AKSHARE_PP_DATA_DIR", str(Path(tempfile.gettempdir()) / "akshare-pp-tests"))

from bridge import (
    FundDataStore,
    OverseasFundDataStore,
    StockDataStore,
    YahooDataStore,
    payload_to_csv,
    stock_quote_date,
)
from update_manager import parse_version


class TickerTests(unittest.TestCase):
    def test_explicit_suffixes(self) -> None:
        self.assertEqual(StockDataStore.normalize_ticker("300308.SZ"), ("300308", "sz300308", "300308.SZ"))
        self.assertEqual(StockDataStore.normalize_ticker("600519.SS"), ("600519", "sh600519", "600519.SH"))

    def test_exchange_inference(self) -> None:
        self.assertEqual(StockDataStore.normalize_ticker("000858")[2], "000858.SZ")
        self.assertEqual(StockDataStore.normalize_ticker("600519")[2], "600519.SH")
        self.assertEqual(StockDataStore.normalize_ticker("920001")[2], "920001.BJ")

    def test_invalid_ticker(self) -> None:
        with self.assertRaises(ValueError):
            StockDataStore.normalize_ticker("AAPL")

    def test_overseas_symbol_normalization(self) -> None:
        self.assertEqual(YahooDataStore.normalize_symbol("hk", "700"), ("0700.HK", "0700.HK"))
        self.assertEqual(YahooDataStore.normalize_symbol("hk", "0700.HK"), ("0700.HK", "0700.HK"))
        self.assertEqual(YahooDataStore.normalize_symbol("us", "brk-b"), ("BRK-B", "BRK-B"))


class QuoteDateTests(unittest.TestCase):
    def test_market_day_uses_current_day(self) -> None:
        current = datetime(2026, 8, 12, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(stock_quote_date("2026-08-11", current), "2026-08-12")

    def test_before_market_uses_latest_history(self) -> None:
        current = datetime(2026, 8, 12, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(stock_quote_date("2026-08-11", current), "2026-08-11")

    def test_weekend_uses_latest_history(self) -> None:
        current = datetime(2026, 8, 9, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(stock_quote_date("2026-08-07", current), "2026-08-07")

    def test_exchange_holiday_uses_latest_history(self) -> None:
        current = datetime(2026, 10, 1, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(
            stock_quote_date(
                "2026-09-30",
                current,
                trading_dates={"2026-09-30", "2026-12-31"},
            ),
            "2026-09-30",
        )

    def test_stale_calendar_falls_back_to_weekday(self) -> None:
        current = datetime(2026, 8, 12, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(
            stock_quote_date("2026-08-11", current, trading_dates={"2025-12-31"}),
            "2026-08-12",
        )


class CacheTests(unittest.TestCase):
    def test_fresh_and_expired_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory), 60)
            fresh = {"fetched_at": datetime.now(timezone.utc).isoformat()}
            expired = {"fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()}
            self.assertTrue(store.cache_is_fresh(fresh))
            self.assertFalse(store.cache_is_fresh(expired))

    def test_stale_cache_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory), 0)
            cached = {
                "code": "000305",
                "kind": "open",
                "fetched_at": "2020-01-01T00:00:00+00:00",
                "stale": False,
                "prices": [{"date": "2020-01-01", "close": 1.0}],
            }
            store.write_cache("000305", "open", cached)
            with mock.patch.object(store, "fetch_upstream", side_effect=RuntimeError("offline")):
                result = store.get("000305", kind="open", force_refresh=True)
            self.assertTrue(result["stale"])
            self.assertEqual(result["prices"][0]["close"], 1.0)


class YahooTests(unittest.TestCase):
    @staticmethod
    def chart_payload() -> dict:
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "currency": "HKD",
                            "exchangeTimezoneName": "Asia/Hong_Kong",
                            "longName": "Tencent Holdings Limited",
                            "regularMarketPrice": 612.5,
                            "regularMarketTime": 1786665600,
                            "regularMarketDayHigh": 618.0,
                            "regularMarketDayLow": 603.0,
                            "regularMarketVolume": 12345678,
                        },
                        "timestamp": [1786579200, 1786665600],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [607.5, 612.5],
                                    "high": [610.0, 618.0],
                                    "low": [600.0, 603.0],
                                    "volume": [10000000, 12345678],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }

    def test_yahoo_history_has_ohlcv_and_full_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = YahooDataStore(Path(directory))
            response = io.BytesIO(json.dumps(self.chart_payload()).encode("utf-8"))
            with mock.patch("bridge.urlopen", return_value=response) as mocked:
                result = store.fetch("hk", "700", "history", force_refresh=True)
            self.assertEqual(result["ticker"], "0700.HK")
            self.assertEqual(result["name"], "Tencent Holdings Limited")
            self.assertEqual(result["prices"][-1]["high"], 618.0)
            self.assertEqual(result["prices"][-1]["low"], 603.0)
            self.assertEqual(result["prices"][-1]["volume"], 12345678)
            self.assertIn("period1=0", mocked.call_args.args[0].full_url)
            self.assertIn("period2=", mocked.call_args.args[0].full_url)

    def test_yahoo_latest_uses_regular_market_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = YahooDataStore(Path(directory))
            response = io.BytesIO(json.dumps(self.chart_payload()).encode("utf-8"))
            with mock.patch("bridge.urlopen", return_value=response):
                result = store.fetch("hk", "0700.HK", "latest", force_refresh=True)
            self.assertEqual(len(result["prices"]), 1)
            self.assertEqual(result["prices"][0]["close"], 612.5)

    def test_yahoo_failure_returns_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = YahooDataStore(Path(directory))
            cached = {
                "ticker": "MSFT",
                "fetched_at": "2020-01-01T00:00:00+00:00",
                "stale": False,
                "prices": [{"date": "2020-01-01", "close": 100.0}],
            }
            store.write_cache("us", "MSFT", "history", cached)
            with mock.patch("bridge.urlopen", side_effect=OSError("offline")):
                result = store.fetch("us", "MSFT", "history", force_refresh=True)
            self.assertTrue(result["stale"])
            self.assertEqual(result["prices"][0]["close"], 100.0)


class HongKongFundTests(unittest.TestCase):
    def test_taikang_isin_and_share_class_parsing(self) -> None:
        html = "HK0000294535 (Class A – HKD – DIST)<br>HK0000294543 (Class A - USD - DIST)"
        result = OverseasFundDataStore.parse_taikang_isins(html)
        self.assertEqual(result["HK0000294535"], "CLASS A-HKD-DIST")
        self.assertEqual(result["HK0000294543"], "CLASS A-USD-DIST")

    def test_taikang_nav_filters_exact_share_class(self) -> None:
        frame = __import__("pandas").DataFrame(
            {
                "类别": ["Class A-HKD-DIST", "Class A-USD-DIST", "Class A-HKD-DIST"],
                "货币": ["HKD", "USD", "HKD"],
                "交易日期": ["2025-01-13", "2025-01-13", "2026-08-12"],
                "每基金单位资产净值": [11.141, 10.5, 11.086],
            }
        )
        currency, prices = OverseasFundDataStore.normalize_taikang_nav(frame, "Class A – HKD – DIST")
        self.assertEqual(currency, "HKD")
        self.assertEqual(len(prices), 2)
        self.assertEqual(prices[-1], {"date": "2026-08-12", "close": 11.086})

    def test_isin_uses_official_source_and_writes_cache(self) -> None:
        payload = {
            "ticker": "HK0000294535",
            "code": "HK0000294535",
            "currency": "HKD",
            "source": "official",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "prices": [{"date": "2026-08-12", "close": 11.086}],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = OverseasFundDataStore(Path(directory), 3600)
            with mock.patch.object(store, "fetch_taikang_isin", return_value=payload) as fetch:
                result = store.get("hk", "hk0000294535", force_refresh=True)
            fetch.assert_called_once_with("HK0000294535")
            self.assertEqual(result["currency"], "HKD")
            self.assertTrue(store.cache_file("hk", "HK0000294535").exists())


class SerializationTests(unittest.TestCase):
    def test_csv_shape(self) -> None:
        result = payload_to_csv({"prices": [{"date": "2026-08-11", "close": 1.0981}]})
        self.assertEqual(result.decode("utf-8"), "Date,Close\n2026-08-11,1.0981\n")

    def test_version_comparison(self) -> None:
        self.assertGreater(parse_version("1.18.85"), parse_version("1.18.84"))
        self.assertEqual(parse_version("1.18.84"), (1, 18, 84))

    def test_pp_stock_config_contract(self) -> None:
        from gui import BridgeApp

        text = BridgeApp.pp_config(
            "http://127.0.0.1:18765/stock/{TICKER}.json",
            "http://127.0.0.1:18765/stock/{TICKER}/latest.json",
            include_ohlcv=True,
        )
        for path in ("date", "close", "high", "low", "volume"):
            self.assertIn(f"$.prices[*].{path}", text)

    def test_pp_fund_config_can_reuse_history_for_latest(self) -> None:
        from gui import BridgeApp

        text = BridgeApp.pp_config(
            "http://127.0.0.1:18765/fund/{TICKER}.json?kind=open",
            "http://127.0.0.1:18765/fund/{TICKER}/latest.json?kind=open",
            include_ohlcv=False,
            latest_same=True,
        )
        self.assertIn("最新报价：与历史报价相同", text)
        self.assertNotIn("最新报价 URL：", text)


if __name__ == "__main__":
    unittest.main()
