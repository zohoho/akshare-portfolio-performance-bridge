from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from app_paths import (
    BRIDGE_VERSION,
    SERVICE_HOST,
    SERVICE_PORT,
    active_runtime,
    cache_dir as app_cache_dir,
    service_state_file,
    update_status_file,
    write_json,
    read_json,
)


SERVICE_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


def running_akshare_version() -> str:
    try:
        return metadata.version("akshare")
    except metadata.PackageNotFoundError:
        return str(getattr(ak, "__version__", "unknown"))


CODE_PATTERN = re.compile(r"^\d{6}$")
HK_ISIN_PATTERN = re.compile(r"^HK[A-Z0-9]{10}$")
FETCH_LOCK = threading.Lock()
STOCK_FETCH_LOCK = threading.RLock()
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_STOCK_HISTORY_TTL_SECONDS = 30 * 60
DEFAULT_STOCK_LATEST_TTL_SECONDS = 30
YAHOO_HISTORY_TTL_SECONDS = 6 * 60 * 60
YAHOO_LATEST_TTL_SECONDS = 5 * 60


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_date_filter(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"无法识别日期：{value}")
    return parsed.normalize()


def normalize_prices(frame: pd.DataFrame, date_column: str, close_column: str) -> list[dict]:
    if frame is None or frame.empty:
        return []
    if date_column not in frame.columns or close_column not in frame.columns:
        raise ValueError(
            f"数据列发生变化；当前列为：{', '.join(str(item) for item in frame.columns)}"
        )

    normalized = frame[[date_column, close_column]].copy()
    normalized.columns = ["date", "close"]
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.dropna().drop_duplicates(subset=["date"], keep="last")
    normalized = normalized.sort_values("date")

    return [
        {"date": row.date.strftime("%Y-%m-%d"), "close": float(row.close)}
        for row in normalized.itertuples(index=False)
    ]


def normalize_stock_prices(
    frame: pd.DataFrame,
    date_column: str,
    close_column: str,
    high_column: str | None = None,
    low_column: str | None = None,
    volume_column: str | None = None,
) -> list[dict]:
    if frame is None or frame.empty:
        return []
    required = [date_column, close_column]
    if any(column not in frame.columns for column in required):
        raise ValueError(f"Stock data columns changed: {list(frame.columns)}")

    selected = required + [
        column
        for column in (high_column, low_column, volume_column)
        if column is not None and column in frame.columns
    ]
    normalized = frame[selected].copy()
    rename_map = {date_column: "date", close_column: "close"}
    if high_column in normalized.columns:
        rename_map[high_column] = "high"
    if low_column in normalized.columns:
        rename_map[low_column] = "low"
    if volume_column in normalized.columns:
        rename_map[volume_column] = "volume"
    normalized = normalized.rename(columns=rename_map)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in ("close", "high", "low", "volume"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["date", "close"])
    normalized = normalized.drop_duplicates(subset=["date"], keep="last").sort_values("date")

    prices: list[dict] = []
    for row in normalized.to_dict(orient="records"):
        item = {"date": row["date"].strftime("%Y-%m-%d"), "close": float(row["close"])}
        for column in ("high", "low"):
            if column in row and pd.notna(row[column]):
                item[column] = float(row[column])
        if "volume" in row and pd.notna(row["volume"]):
            item["volume"] = int(float(row["volume"]))
        prices.append(item)
    return prices


class FundDataStore:
    def __init__(self, cache_dir: Path, cache_ttl_seconds: int) -> None:
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fund_names: dict[str, str] | None = None

    def cache_file(self, code: str, kind: str) -> Path:
        return self.cache_dir / f"{code}-{kind}.json"

    def read_cache(self, code: str, kind: str) -> dict | None:
        path = self.cache_file(code, kind)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def cache_is_fresh(self, payload: dict) -> bool:
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return False
        try:
            timestamp = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        age = datetime.now(timezone.utc) - timestamp
        return age.total_seconds() < self.cache_ttl_seconds

    def write_cache(self, code: str, kind: str, payload: dict) -> None:
        path = self.cache_file(code, kind)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def fetch_open_fund(self, code: str) -> tuple[str, list[dict]]:
        frame = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        prices = normalize_prices(frame, "净值日期", "单位净值")
        if not prices:
            raise ValueError("开放式基金接口没有返回净值")
        return "akshare.fund_open_fund_info_em", prices

    def fetch_etf(self, code: str) -> tuple[str, list[dict]]:
        frame = ak.fund_etf_hist_em(
            symbol=code,
            period="daily",
            start_date="19700101",
            end_date="20500101",
            adjust="",
        )
        prices = normalize_prices(frame, "日期", "收盘")
        if not prices:
            raise ValueError("ETF 接口没有返回行情")
        return "akshare.fund_etf_hist_em", prices

    def fund_name(self, code: str) -> str:
        if self._fund_names is None:
            try:
                frame = ak.fund_name_em()
                self._fund_names = {
                    str(row["基金代码"]).zfill(6): str(row["基金简称"])
                    for _, row in frame.iterrows()
                    if pd.notna(row.get("基金代码")) and pd.notna(row.get("基金简称"))
                }
            except Exception:
                self._fund_names = {}
        return self._fund_names.get(code, "")

    def fetch_upstream(self, code: str, kind: str) -> dict:
        errors: list[str] = []
        if kind == "open":
            fetchers = (self.fetch_open_fund,)
        elif kind == "etf":
            fetchers = (self.fetch_etf,)
        elif code.startswith("5") or code.startswith("159"):
            fetchers = (self.fetch_etf, self.fetch_open_fund)
        else:
            fetchers = (self.fetch_open_fund, self.fetch_etf)

        for fetcher in fetchers:
            try:
                source, prices = fetcher(code)
                return {
                    "code": code,
                    "name": self.fund_name(code),
                    "kind": kind,
                    "currency": "CNY",
                    "source": source,
                    "fetched_at": utc_now_text(),
                    "stale": False,
                    "prices": prices,
                }
            except Exception as exc:  # AkShare wraps several changing upstreams.
                errors.append(f"{fetcher.__name__}: {exc}")
        raise RuntimeError("；".join(errors))

    def get(self, code: str, kind: str = "auto", force_refresh: bool = False) -> dict:
        cached = self.read_cache(code, kind)
        if cached and not force_refresh and self.cache_is_fresh(cached):
            cached["stale"] = False
            return cached

        with FETCH_LOCK:
            cached = self.read_cache(code, kind)
            if cached and not force_refresh and self.cache_is_fresh(cached):
                cached["stale"] = False
                return cached
            try:
                payload = self.fetch_upstream(code, kind)
                self.write_cache(code, kind, payload)
                return payload
            except Exception:
                if cached:
                    cached["stale"] = True
                    return cached
                raise


class OverseasFundDataStore:
    """Historical NAV data for overseas funds exposed by AkShare."""

    TAIKANG_BASE_URL = "https://hk.taikangasset.cn"
    TAIKANG_KNOWN_ISINS = {
        "HK0000294535": {
            "fund_code": "20160420",
            "fund_name": "Taikang Kaitai Overseas Short Tenor Bond Fund",
            "share_class": "Class A-HKD-DIST",
            "currency": "HKD",
        },
    }

    def __init__(self, cache_dir: Path, cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_file(self, market: str, code: str) -> Path:
        safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", code)
        return self.cache_dir / f"fund-{market}-{safe_code}-history.json"

    def read_cache(self, market: str, code: str) -> dict | None:
        try:
            return json.loads(self.cache_file(market, code).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_cache(self, market: str, code: str, payload: dict) -> None:
        path = self.cache_file(market, code)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    @staticmethod
    def currency_from_frame(frame: pd.DataFrame) -> str:
        if "单位" not in frame.columns:
            return "HKD"
        values = frame["单位"].dropna().astype(str)
        unit = values.iloc[-1].strip() if not values.empty else ""
        mappings = {
            "港币": "HKD",
            "港元": "HKD",
            "美元": "USD",
            "人民币": "CNY",
            "欧元": "EUR",
            "英镑": "GBP",
            "日元": "JPY",
            "澳元": "AUD",
            "新加坡元": "SGD",
        }
        return next((currency for name, currency in mappings.items() if name in unit), "HKD")

    @staticmethod
    def normalize_share_class(value: object) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[‐‑‒–—−]", "-", text)
        text = re.sub(r"\s*-\s*", "-", text)
        return re.sub(r"\s+", " ", text).upper()

    @staticmethod
    def _walk_json(value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from OverseasFundDataStore._walk_json(child)
        elif isinstance(value, list):
            for child in value:
                yield from OverseasFundDataStore._walk_json(child)

    @classmethod
    def parse_taikang_isins(cls, value: object) -> dict[str, str]:
        text = str(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        entries: dict[str, str] = {}
        for isin, share_class in re.findall(
            r"\b(HK[A-Z0-9]{10})\s*\((Class[^)]+)\)",
            text,
            flags=re.IGNORECASE,
        ):
            entries[isin.upper()] = cls.normalize_share_class(share_class)
        return entries

    @staticmethod
    def currency_from_taikang(value: object) -> str:
        text = str(value or "").strip().upper()
        mappings = {
            "港币": "HKD",
            "港元": "HKD",
            "HKD": "HKD",
            "美元": "USD",
            "USD": "USD",
            "人民币": "CNY",
            "CNY": "CNY",
            "欧元": "EUR",
            "EUR": "EUR",
        }
        return next((currency for name, currency in mappings.items() if name in text), text or "HKD")

    def _request_taikang_json(self, path: str, payload: dict | None = None) -> object:
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"AkSharePPBridge/{BRIDGE_VERSION}",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.TAIKANG_BASE_URL}{path}", data=data, headers=headers)
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def resolve_taikang_isin(self, isin: str) -> dict:
        if isin in self.TAIKANG_KNOWN_ISINS:
            return dict(self.TAIKANG_KNOWN_ISINS[isin])

        tree = self._request_taikang_json("/api/product/fundListTree/en")
        candidates: list[dict] = []
        seen_codes: set[str] = set()
        for item in self._walk_json(tree):
            fund_code = str(item.get("fundCode") or "").strip()
            if not fund_code or fund_code in seen_codes:
                continue
            seen_codes.add(fund_code)
            candidates.append(
                {
                    "fund_code": fund_code,
                    "fund_name": str(item.get("fundName") or item.get("name") or "").strip(),
                }
            )

        for candidate in candidates:
            details = self._request_taikang_json(
                "/api/product/transacTional",
                {"language": "en", "fundCode": candidate["fund_code"]},
            )
            for item in self._walk_json(details):
                entries = self.parse_taikang_isins(item.get("isinCode"))
                if isin in entries:
                    share_class = entries[isin]
                    currency_parts = share_class.split("-")
                    candidate.update(
                        {
                            "share_class": share_class,
                            "currency": next(
                                (part for part in currency_parts if part in {"HKD", "USD", "CNY", "EUR"}),
                                "HKD",
                            ),
                        }
                    )
                    return candidate
        raise ValueError(f"泰康资产官网未找到香港基金 ISIN：{isin}")

    @classmethod
    def normalize_taikang_nav(cls, frame: pd.DataFrame, share_class: str) -> tuple[str, list[dict]]:
        required = {"类别", "货币", "交易日期", "每基金单位资产净值"}
        missing = required.difference(str(column) for column in frame.columns)
        if missing:
            raise ValueError(f"泰康官网净值文件缺少字段：{', '.join(sorted(missing))}")

        selected = frame.copy()
        selected["_share_class"] = selected["类别"].map(cls.normalize_share_class)
        selected = selected[selected["_share_class"] == cls.normalize_share_class(share_class)]
        if selected.empty:
            raise ValueError(f"泰康官网净值文件没有份额类别：{share_class}")
        currency_values = selected["货币"].dropna()
        currency = cls.currency_from_taikang(currency_values.iloc[-1] if not currency_values.empty else "HKD")
        prices = normalize_prices(selected, "交易日期", "每基金单位资产净值")
        if not prices:
            raise ValueError(f"泰康官网没有返回 {share_class} 的有效净值")
        return currency, prices

    def fetch_taikang_isin(self, isin: str) -> dict:
        fund = self.resolve_taikang_isin(isin)
        query = (
            "/api/tblProductQuotation/getQuoExcel"
            f"?language=en&fundCode={quote(fund['fund_code'])}&startTime=&endTime="
        )
        request = Request(
            f"{self.TAIKANG_BASE_URL}{query}",
            headers={"User-Agent": f"AkSharePPBridge/{BRIDGE_VERSION}"},
        )
        with urlopen(request, timeout=60) as response:
            workbook = response.read()
        frame = pd.read_excel(io.BytesIO(workbook))
        currency, prices = self.normalize_taikang_nav(frame, fund["share_class"])
        return {
            "ticker": isin,
            "code": isin,
            "name": f"{fund['fund_name']} - {fund['share_class']}",
            "market": "HK_FUND",
            "currency": currency or fund.get("currency", "HKD"),
            "source": "Taikang Asset Management (Hong Kong) official NAV",
            "fetched_at": utc_now_text(),
            "stale": False,
            "prices": prices,
        }

    def get(self, market: str, code: str, force_refresh: bool = False) -> dict:
        code = code.strip()
        if HK_ISIN_PATTERN.fullmatch(code.upper()):
            code = code.upper()
        cached = self.read_cache(market, code)
        if cached and not force_refresh and StockDataStore.cache_is_fresh(cached, self.cache_ttl_seconds):
            cached["stale"] = False
            return cached
        if market != "hk":
            raise ValueError("当前 AkShare 版本只提供港基历史净值；美基请使用美基代码接口")

        with FETCH_LOCK:
            cached = self.read_cache(market, code)
            if cached and not force_refresh and StockDataStore.cache_is_fresh(cached, self.cache_ttl_seconds):
                cached["stale"] = False
                return cached
            try:
                if HK_ISIN_PATTERN.fullmatch(code):
                    payload = self.fetch_taikang_isin(code)
                else:
                    frame = ak.fund_hk_fund_hist_em(code=code, symbol="历史净值明细")
                    prices = normalize_prices(frame, "净值日期", "单位净值")
                    if not prices:
                        raise ValueError("港基接口没有返回历史净值")
                    payload = {
                        "ticker": f"{code}.HKF",
                        "code": code,
                        "market": "HK_FUND",
                        "currency": self.currency_from_frame(frame),
                        "source": "akshare.fund_hk_fund_hist_em",
                        "fetched_at": utc_now_text(),
                        "stale": False,
                        "prices": prices,
                    }
                self.write_cache(market, code, payload)
                return payload
            except Exception:
                if cached:
                    cached["stale"] = True
                    return cached
                raise


class YahooDataStore:
    """Daily history and current quote for Hong Kong and United States symbols."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_file(self, market: str, symbol: str, category: str) -> Path:
        safe_symbol = re.sub(r"[^A-Za-z0-9_.-]", "_", symbol)
        return self.cache_dir / f"yahoo-{market}-{safe_symbol}-{category}.json"

    def read_cache(self, market: str, symbol: str, category: str) -> dict | None:
        try:
            return json.loads(self.cache_file(market, symbol, category).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_cache(self, market: str, symbol: str, category: str, payload: dict) -> None:
        path = self.cache_file(market, symbol, category)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    @staticmethod
    def normalize_symbol(market: str, symbol: str) -> tuple[str, str]:
        value = symbol.strip().upper()
        if market == "hk":
            match = re.fullmatch(r"0*(\d{1,5})(?:\.HK)?", value)
            if not match:
                raise ValueError("港股代码请输入 0700 或 0700.HK")
            code = match.group(1).zfill(4)
            return f"{code}.HK", f"{code}.HK"
        if market == "us":
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", value):
                raise ValueError("美股或美基请输入代码，例如 MSFT、AAPL 或 SPY")
            return value, value
        raise ValueError("未知海外市场")

    @staticmethod
    def _item_at(values: list, index: int):
        return values[index] if index < len(values) else None

    def fetch(self, market: str, symbol: str, category: str, force_refresh: bool = False) -> dict:
        if category not in ("history", "latest"):
            raise ValueError("行情类别只能是 history 或 latest")
        yahoo_symbol, normalized = self.normalize_symbol(market, symbol)
        cached = self.read_cache(market, yahoo_symbol, category)
        ttl = YAHOO_LATEST_TTL_SECONDS if category == "latest" else YAHOO_HISTORY_TTL_SECONDS
        if cached and not force_refresh and StockDataStore.cache_is_fresh(cached, ttl):
            cached["stale"] = False
            return cached

        if category == "history":
            query = f"period1=0&period2={int(datetime.now(timezone.utc).timestamp())}"
        else:
            query = "range=5d"
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_symbol)}"
            f"?interval=1d&{query}&events=history&includeAdjustedClose=true"
        )
        request = Request(url, headers={"User-Agent": f"AkSharePPBridge/{BRIDGE_VERSION}"})
        try:
            with urlopen(request, timeout=20) as response:
                raw = json.loads(response.read().decode("utf-8"))
            results = (raw.get("chart") or {}).get("result") or []
            if not results:
                error = (raw.get("chart") or {}).get("error") or {}
                raise ValueError(error.get("description") or "Yahoo Finance 没有返回行情")

            result = results[0]
            meta = result.get("meta") or {}
            timestamps = result.get("timestamp") or []
            quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote_data.get("close") or []
            highs = quote_data.get("high") or []
            lows = quote_data.get("low") or []
            volumes = quote_data.get("volume") or []
            timezone_name = meta.get("exchangeTimezoneName") or (
                "Asia/Hong_Kong" if market == "hk" else "America/New_York"
            )
            try:
                local_timezone = ZoneInfo(timezone_name)
            except Exception:
                local_timezone = timezone.utc

            prices: list[dict] = []
            for index, timestamp in enumerate(timestamps):
                close = self._item_at(closes, index)
                if close is None:
                    continue
                item = {
                    "date": datetime.fromtimestamp(timestamp, local_timezone).strftime("%Y-%m-%d"),
                    "close": float(close),
                }
                high = self._item_at(highs, index)
                low = self._item_at(lows, index)
                volume = self._item_at(volumes, index)
                if high is not None:
                    item["high"] = float(high)
                if low is not None:
                    item["low"] = float(low)
                if volume is not None:
                    item["volume"] = int(float(volume))
                prices.append(item)

            if category == "latest" and meta.get("regularMarketPrice") is not None:
                quote_timestamp = meta.get("regularMarketTime") or (timestamps[-1] if timestamps else None)
                if quote_timestamp is not None:
                    current = {
                        "date": datetime.fromtimestamp(quote_timestamp, local_timezone).strftime("%Y-%m-%d"),
                        "close": float(meta["regularMarketPrice"]),
                    }
                    for key, target, converter in (
                        ("regularMarketDayHigh", "high", float),
                        ("regularMarketDayLow", "low", float),
                        ("regularMarketVolume", "volume", lambda value: int(float(value))),
                    ):
                        if meta.get(key) is not None:
                            current[target] = converter(meta[key])
                    prices = [current]
                else:
                    prices = prices[-1:]
            elif category == "latest":
                prices = prices[-1:]
            if not prices:
                raise ValueError("Yahoo Finance 没有有效报价")

            payload = {
                "ticker": normalized,
                "code": normalized.removesuffix(".HK") if market == "hk" else normalized,
                "name": meta.get("longName") or meta.get("shortName") or "",
                "market": "HK" if market == "hk" else "US",
                "currency": meta.get("currency") or ("HKD" if market == "hk" else "USD"),
                "source": "Yahoo Finance chart API",
                "fetched_at": utc_now_text(),
                "stale": False,
                "prices": prices,
            }
            self.write_cache(market, yahoo_symbol, category, payload)
            return payload
        except Exception:
            if cached:
                cached["stale"] = True
                return cached
            raise


class StockDataStore:
    def __init__(
        self,
        cache_dir: Path,
        history_ttl_seconds: int = DEFAULT_STOCK_HISTORY_TTL_SECONDS,
        latest_ttl_seconds: int = DEFAULT_STOCK_LATEST_TTL_SECONDS,
    ) -> None:
        self.cache_dir = cache_dir
        self.history_ttl_seconds = history_ttl_seconds
        self.latest_ttl_seconds = latest_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._spot_frame: pd.DataFrame | None = None
        self._spot_fetched_at: datetime | None = None
        self._trade_dates: set[str] | None = None
        self._trade_dates_fetched_at: datetime | None = None

    @staticmethod
    def normalize_ticker(ticker: str) -> tuple[str, str, str]:
        match = re.fullmatch(r"(?P<code>\d{6})(?:\.(?P<exchange>SZ|SH|SS|BJ))?", ticker.upper())
        if not match:
            raise ValueError("A-share ticker must look like 300308.SZ or 600519.SH")
        code = match.group("code")
        exchange = match.group("exchange")
        if exchange in ("SH", "SS"):
            prefix = "sh"
            normalized = f"{code}.SH"
        elif exchange == "SZ":
            prefix = "sz"
            normalized = f"{code}.SZ"
        elif exchange == "BJ":
            prefix = "bj"
            normalized = f"{code}.BJ"
        elif code.startswith(("6", "5")):
            prefix = "sh"
            normalized = f"{code}.SH"
        elif code.startswith(("4", "8", "92")):
            prefix = "bj"
            normalized = f"{code}.BJ"
        else:
            prefix = "sz"
            normalized = f"{code}.SZ"
        return code, prefix + code, normalized

    def cache_file(self, normalized_ticker: str, category: str) -> Path:
        safe_ticker = normalized_ticker.replace(".", "-")
        return self.cache_dir / f"stock-{safe_ticker}-{category}.json"

    def read_cache(self, normalized_ticker: str, category: str) -> dict | None:
        path = self.cache_file(normalized_ticker, category)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_cache(self, normalized_ticker: str, category: str, payload: dict) -> None:
        path = self.cache_file(normalized_ticker, category)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    @staticmethod
    def cache_is_fresh(payload: dict, ttl_seconds: int) -> bool:
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return False
        try:
            timestamp = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - timestamp).total_seconds() < ttl_seconds

    def fetch_history(self, ticker: str) -> dict:
        code, tx_symbol, normalized_ticker = self.normalize_ticker(ticker)
        errors: list[str] = []

        try:
            frame = ak.stock_zh_a_hist_tx(
                symbol=tx_symbol,
                start_date="19900101",
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="",
                timeout=20,
            )
            prices = normalize_stock_prices(
                frame,
                "date",
                "close",
                high_column="high",
                low_column="low",
                volume_column="volume",
            )
            if prices:
                return {
                    "ticker": normalized_ticker,
                    "code": code,
                    "market": "CN",
                    "currency": "CNY",
                    "source": "akshare.stock_zh_a_hist_tx",
                    "fetched_at": utc_now_text(),
                    "stale": False,
                    "prices": prices,
                }
        except Exception as exc:
            errors.append(f"stock_zh_a_hist_tx: {exc}")

        try:
            frame = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date="19900101",
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="",
                timeout=20,
            )
            prices = normalize_stock_prices(
                frame,
                "\u65e5\u671f",
                "\u6536\u76d8",
                high_column="\u6700\u9ad8",
                low_column="\u6700\u4f4e",
                volume_column="\u6210\u4ea4\u91cf",
            )
            if prices:
                return {
                    "ticker": normalized_ticker,
                    "code": code,
                    "market": "CN",
                    "currency": "CNY",
                    "source": "akshare.stock_zh_a_hist",
                    "fetched_at": utc_now_text(),
                    "stale": False,
                    "prices": prices,
                }
        except Exception as exc:
            errors.append(f"stock_zh_a_hist: {exc}")

        raise RuntimeError("; ".join(errors) or "No stock history returned")

    def get_history(self, ticker: str, force_refresh: bool = False) -> dict:
        _, _, normalized_ticker = self.normalize_ticker(ticker)
        cached = self.read_cache(normalized_ticker, "history")
        if cached and not force_refresh and self.cache_is_fresh(cached, self.history_ttl_seconds):
            cached["stale"] = False
            return cached

        with STOCK_FETCH_LOCK:
            cached = self.read_cache(normalized_ticker, "history")
            if cached and not force_refresh and self.cache_is_fresh(cached, self.history_ttl_seconds):
                cached["stale"] = False
                return cached
            try:
                payload = self.fetch_history(ticker)
                self.write_cache(normalized_ticker, "history", payload)
                return payload
            except Exception:
                if cached:
                    cached["stale"] = True
                    return cached
                raise

    def get_spot_frame(self, force_refresh: bool = False) -> tuple[pd.DataFrame, str]:
        now = datetime.now(timezone.utc)
        if (
            self._spot_frame is not None
            and self._spot_fetched_at is not None
            and not force_refresh
            and (now - self._spot_fetched_at).total_seconds() < self.latest_ttl_seconds
        ):
            return self._spot_frame, self._spot_fetched_at.isoformat(timespec="seconds")

        frame = ak.stock_zh_a_spot_tx()
        if frame is None or frame.empty:
            raise RuntimeError("Tencent A-share snapshot returned no data")
        self._spot_frame = frame
        self._spot_fetched_at = now
        return frame, now.isoformat(timespec="seconds")

    def get_trade_dates(self) -> set[str] | None:
        now = datetime.now(timezone.utc)
        if (
            self._trade_dates is not None
            and self._trade_dates_fetched_at is not None
            and (now - self._trade_dates_fetched_at).total_seconds() < 24 * 60 * 60
        ):
            return self._trade_dates
        try:
            frame = ak.tool_trade_date_hist_sina()
            if frame is None or frame.empty or "trade_date" not in frame.columns:
                return None
            values = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
            self._trade_dates = {value.date().isoformat() for value in values}
            self._trade_dates_fetched_at = now
            return self._trade_dates
        except Exception:
            return None

    def get_latest(self, ticker: str, force_refresh: bool = False) -> dict:
        code, tx_symbol, normalized_ticker = self.normalize_ticker(ticker)
        cached = self.read_cache(normalized_ticker, "latest")
        if cached and not force_refresh and self.cache_is_fresh(cached, self.latest_ttl_seconds):
            cached["stale"] = False
            return cached

        with STOCK_FETCH_LOCK:
            cached = self.read_cache(normalized_ticker, "latest")
            if cached and not force_refresh and self.cache_is_fresh(cached, self.latest_ttl_seconds):
                cached["stale"] = False
                return cached
            try:
                frame, fetched_at = self.get_spot_frame(force_refresh=force_refresh)
                rows = frame[frame["code"].astype(str).str.lower() == tx_symbol]
                if rows.empty:
                    raise ValueError(f"Ticker {normalized_ticker} was not found in the A-share snapshot")
                row = rows.iloc[-1]
                close = float(row["zxj"])
                if close <= 0:
                    raise ValueError(f"Ticker {normalized_ticker} has no valid latest price")

                history = self.get_history(ticker, force_refresh=False)
                latest_history_date = history["prices"][-1]["date"] if history["prices"] else None
                quote_date = stock_quote_date(
                    latest_history_date,
                    trading_dates=self.get_trade_dates(),
                )
                payload = {
                    "ticker": normalized_ticker,
                    "code": code,
                    "name": str(row.get("name", "")),
                    "market": "CN",
                    "currency": "CNY",
                    "source": "akshare.stock_zh_a_spot_tx",
                    "fetched_at": fetched_at,
                    "stale": False,
                    "prices": [{"date": quote_date, "close": close}],
                }
                self.write_cache(normalized_ticker, "latest", payload)
                return payload
            except Exception:
                if cached:
                    cached["stale"] = True
                    return cached
                raise


def stock_quote_date(
    latest_history_date: str | None,
    now: datetime | None = None,
    trading_dates: set[str] | None = None,
) -> str:
    from zoneinfo import ZoneInfo

    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        current = current.astimezone(ZoneInfo("Asia/Shanghai"))

    today = current.date()
    calendar_covers_today = bool(trading_dates) and pd.Timestamp(max(trading_dates)).date() >= today
    is_trading_day = (
        today.isoformat() in trading_dates
        if calendar_covers_today
        else today.weekday() < 5
    )
    if is_trading_day and current.time() >= datetime.strptime("09:15", "%H:%M").time():
        if not latest_history_date or pd.Timestamp(latest_history_date).date() <= today:
            return today.isoformat()
    if latest_history_date:
        return pd.Timestamp(latest_history_date).date().isoformat()
    while today.weekday() >= 5:
        today = today - pd.Timedelta(days=1)
    return today.isoformat()


def filter_prices(payload: dict, start: str | None, end: str | None) -> dict:
    start_date = normalize_date_filter(start)
    end_date = normalize_date_filter(end)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start 不能晚于 end")

    result = dict(payload)
    result["prices"] = [
        item
        for item in payload["prices"]
        if (start_date is None or pd.Timestamp(item["date"]) >= start_date)
        and (end_date is None or pd.Timestamp(item["date"]) <= end_date)
    ]
    return result


def payload_to_csv(payload: dict) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["Date", "Close"])
    for item in payload["prices"]:
        writer.writerow([item["date"], format(item["close"], ".10g")])
    return stream.getvalue().encode("utf-8")


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = f"AkSharePPBridge/{BRIDGE_VERSION}"

    @property
    def fund_store(self) -> FundDataStore:
        return self.server.fund_store  # type: ignore[attr-defined]

    @property
    def stock_store(self) -> StockDataStore:
        return self.server.stock_store  # type: ignore[attr-defined]

    @property
    def overseas_fund_store(self) -> OverseasFundDataStore:
        return self.server.overseas_fund_store  # type: ignore[attr-defined]

    @property
    def yahoo_store(self) -> YahooDataStore:
        return self.server.yahoo_store  # type: ignore[attr-defined]

    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/health"):
            update_status = read_json(update_status_file(), {}) or {}
            runtime = active_runtime()
            self.send_json(
                200,
                {
                    "status": "ok",
                    "service": "AkShare Portfolio Performance Bridge",
                    "bridge_version": BRIDGE_VERSION,
                    "akshare_version": running_akshare_version(),
                    "runtime_version": runtime.get("version")
                    if runtime.get("path")
                    else running_akshare_version(),
                    "started_at": SERVICE_STARTED_AT,
                    "update": {
                        "latest_version": update_status.get("latest_version"),
                        "update_available": update_status.get("update_available", False),
                        "checked_at": update_status.get("checked_at"),
                    },
                    "examples": [
                        "/fund/000305.json",
                        "/fund/000305.csv",
                        "/fund/000305/latest.json",
                        "/stock/300308.SZ.json",
                        "/stock/300308.SZ/latest.json",
                        "/stock/hk/0700.json",
                        "/stock/us/MSFT.json",
                        "/fund/hk/1002200683.json",
                        "/fund/us/SPY.json",
                    ],
                },
            )
            return

        if parsed.path == "/control/shutdown":
            self.send_json(405, {"error": "Use POST"})
            return

        overseas_stock_match = re.fullmatch(
            r"/stock/(?P<market>hk|us)/(?P<ticker>[A-Za-z0-9.-]+)(?P<latest>/latest)?\.(?P<format>json|csv)",
            parsed.path,
            re.IGNORECASE,
        )
        if overseas_stock_match:
            market = overseas_stock_match.group("market").lower()
            ticker = overseas_stock_match.group("ticker").upper()
            try:
                force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                category = "latest" if overseas_stock_match.group("latest") else "history"
                payload = self.yahoo_store.fetch(market, ticker, category, force_refresh=force_refresh)
                if category == "history":
                    payload = filter_prices(
                        payload,
                        query.get("start", [None])[0],
                        query.get("end", [None])[0],
                    )
                if overseas_stock_match.group("format") == "csv":
                    self.send_bytes(200, payload_to_csv(payload), "text/csv; charset=utf-8")
                else:
                    self.send_json(200, payload)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc), "ticker": ticker})
            except Exception as exc:
                self.send_json(502, {"error": str(exc), "ticker": ticker})
            return

        overseas_fund_match = re.fullmatch(
            r"/fund/(?P<market>hk|us)/(?P<code>[A-Za-z0-9.-]+)(?P<latest>/latest)?\.(?P<format>json|csv)",
            parsed.path,
            re.IGNORECASE,
        )
        if overseas_fund_match:
            market = overseas_fund_match.group("market").lower()
            code = overseas_fund_match.group("code").upper()
            try:
                force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                category = "latest" if overseas_fund_match.group("latest") else "history"
                if market == "hk":
                    payload = self.overseas_fund_store.get(market, code, force_refresh=force_refresh)
                else:
                    payload = self.yahoo_store.fetch(market, code, category, force_refresh=force_refresh)
                if category == "history":
                    payload = filter_prices(
                        payload,
                        query.get("start", [None])[0],
                        query.get("end", [None])[0],
                    )
                elif market == "hk" and payload.get("prices"):
                    payload = {**payload, "prices": [payload["prices"][-1]]}
                if overseas_fund_match.group("format") == "csv":
                    self.send_bytes(200, payload_to_csv(payload), "text/csv; charset=utf-8")
                else:
                    self.send_json(200, payload)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc), "code": code})
            except Exception as exc:
                self.send_json(502, {"error": str(exc), "code": code})
            return

        stock_match = re.fullmatch(
            r"/stock/(?P<ticker>\d{6}(?:\.(?:SZ|SH|SS|BJ))?)(?P<latest>/latest)?\.(?P<format>json|csv)",
            parsed.path,
            re.IGNORECASE,
        )
        if stock_match:
            ticker = stock_match.group("ticker").upper()
            try:
                force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                if stock_match.group("latest"):
                    payload = self.stock_store.get_latest(ticker, force_refresh=force_refresh)
                else:
                    payload = self.stock_store.get_history(ticker, force_refresh=force_refresh)
                    payload = filter_prices(
                        payload,
                        query.get("start", [None])[0],
                        query.get("end", [None])[0],
                    )

                if stock_match.group("format") == "csv":
                    self.send_bytes(200, payload_to_csv(payload), "text/csv; charset=utf-8")
                else:
                    self.send_json(200, payload)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc), "ticker": ticker})
            except Exception as exc:
                self.send_json(502, {"error": str(exc), "ticker": ticker})
            return

        match = re.fullmatch(
            r"/fund/(?P<code>\d{6})(?P<latest>/latest)?\.(?P<format>json|csv)",
            parsed.path,
        )
        if not match:
            self.send_json(404, {"error": "地址不存在"})
            return

        code = match.group("code")
        if not CODE_PATTERN.fullmatch(code):
            self.send_json(400, {"error": "基金代码必须是六位数字"})
            return

        try:
            force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
            kind = query.get("kind", ["auto"])[0].lower()
            if kind not in ("auto", "open", "etf"):
                raise ValueError("kind 只能是 auto、open 或 etf")
            payload = self.fund_store.get(code, kind=kind, force_refresh=force_refresh)
            payload = filter_prices(
                payload,
                query.get("start", [None])[0],
                query.get("end", [None])[0],
            )

            if match.group("latest"):
                if not payload["prices"]:
                    raise ValueError("所选日期范围内没有报价")
                payload = {**payload, "prices": [payload["prices"][-1]]}

            if match.group("format") == "csv":
                self.send_bytes(200, payload_to_csv(payload), "text/csv; charset=utf-8")
            else:
                self.send_json(200, payload)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc), "code": code})
        except Exception as exc:
            self.send_json(502, {"error": str(exc), "code": code})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/control/shutdown":
            self.send_json(404, {"error": "Address not found"})
            return
        expected = os.environ.get("AKSHARE_PP_CONTROL_TOKEN", "")
        provided = self.headers.get("X-AkShare-Bridge-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            self.send_json(403, {"error": "Forbidden"})
            return
        self.send_json(200, {"status": "stopping"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {self.client_address[0]} {format % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AkShare Portfolio Performance 报价桥接服务")
    parser.add_argument("--host", default=SERVICE_HOST)
    parser.add_argument("--port", type=int, default=SERVICE_PORT)
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get(
            "AKSHARE_PP_CACHE_DIR",
            str(app_cache_dir()),
        ),
    )
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=int(os.environ.get("AKSHARE_PP_CACHE_TTL", DEFAULT_CACHE_TTL_SECONDS)),
    )
    parser.add_argument("--log-file", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log_stream
        sys.stderr = log_stream
    fund_store = FundDataStore(Path(args.cache_dir), args.cache_ttl)
    overseas_fund_store = OverseasFundDataStore(Path(args.cache_dir), args.cache_ttl)
    yahoo_store = YahooDataStore(Path(args.cache_dir))
    stock_store = StockDataStore(Path(args.cache_dir))
    class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False

    server = ExclusiveThreadingHTTPServer((args.host, args.port), BridgeHandler)
    server.fund_store = fund_store  # type: ignore[attr-defined]
    server.overseas_fund_store = overseas_fund_store  # type: ignore[attr-defined]
    server.yahoo_store = yahoo_store  # type: ignore[attr-defined]
    server.stock_store = stock_store  # type: ignore[attr-defined]
    state = read_json(service_state_file(), {}) or {}
    state.update(
        {
            "pid": os.getpid(),
            "bridge_version": BRIDGE_VERSION,
            "akshare_version": running_akshare_version(),
            "started_at": SERVICE_STARTED_AT,
        }
    )
    write_json(service_state_file(), state)
    print(f"AkShare 报价桥接服务已启动：http://{args.host}:{args.port}/health", flush=True)
    print("按 Ctrl+C 停止服务。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
