from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("AKSHARE_PP_DATA_DIR", str(Path(tempfile.gettempdir()) / "akshare-pp-service-tests"))

from bridge import BridgeHandler, FundDataStore, OverseasFundDataStore, StockDataStore, YahooDataStore


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        self.server.fund_store = FundDataStore(Path(self.temp.name), 3600)
        self.server.stock_store = StockDataStore(Path(self.temp.name))
        self.server.overseas_fund_store = OverseasFundDataStore(Path(self.temp.name), 3600)
        self.server.yahoo_store = YahooDataStore(Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_contract(self) -> None:
        result = self.get_json("/health")
        self.assertEqual(result["status"], "ok")
        self.assertIn("bridge_version", result)
        self.assertIn("akshare_version", result)
        self.assertIn("started_at", result)

    def test_stock_latest_contract(self) -> None:
        payload = {
            "ticker": "300308.SZ",
            "code": "300308",
            "currency": "CNY",
            "source": "test",
            "stale": False,
            "prices": [{"date": "2026-08-12", "close": 916.27}],
        }
        with mock.patch.object(self.server.stock_store, "get_latest", return_value=payload):
            result = self.get_json("/stock/300308.SZ/latest.json")
        self.assertEqual(result["prices"][0]["close"], 916.27)

    def test_hong_kong_stock_route(self) -> None:
        payload = {
            "ticker": "0700.HK",
            "currency": "HKD",
            "source": "test",
            "stale": False,
            "prices": [{"date": "2026-08-13", "close": 612.5, "high": 618.0, "low": 603.0, "volume": 123}],
        }
        with mock.patch.object(self.server.yahoo_store, "fetch", return_value=payload) as fetch:
            result = self.get_json("/stock/hk/0700/latest.json")
        fetch.assert_called_once_with("hk", "0700", "latest", force_refresh=False)
        self.assertEqual(result["ticker"], "0700.HK")

    def test_united_states_fund_route(self) -> None:
        payload = {
            "ticker": "SPY",
            "currency": "USD",
            "source": "test",
            "stale": False,
            "prices": [{"date": "2026-08-13", "close": 650.0}],
        }
        with mock.patch.object(self.server.yahoo_store, "fetch", return_value=payload) as fetch:
            result = self.get_json("/fund/us/SPY.json")
        fetch.assert_called_once_with("us", "SPY", "history", force_refresh=False)
        self.assertEqual(result["currency"], "USD")

    def test_hong_kong_fund_latest_returns_one_price(self) -> None:
        payload = {
            "ticker": "1002200683.HKF",
            "currency": "HKD",
            "source": "test",
            "stale": False,
            "prices": [
                {"date": "2026-08-12", "close": 12.0},
                {"date": "2026-08-13", "close": 12.1},
            ],
        }
        with mock.patch.object(self.server.overseas_fund_store, "get", return_value=payload):
            result = self.get_json("/fund/hk/1002200683/latest.json")
        self.assertEqual(result["prices"], [{"date": "2026-08-13", "close": 12.1}])

    def test_hong_kong_fund_isin_route(self) -> None:
        payload = {
            "ticker": "HK0000294535",
            "name": "Taikang Kaitai Overseas Short Tenor Bond Fund - Class A-HKD-DIST",
            "currency": "HKD",
            "source": "test",
            "stale": False,
            "prices": [{"date": "2026-08-12", "close": 11.086}],
        }
        with mock.patch.object(self.server.overseas_fund_store, "get", return_value=payload) as get:
            result = self.get_json("/fund/hk/HK0000294535.json")
        get.assert_called_once_with("hk", "HK0000294535", force_refresh=False)
        self.assertEqual(result["currency"], "HKD")


if __name__ == "__main__":
    unittest.main()
