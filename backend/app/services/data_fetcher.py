import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd
import yfinance as yf
from app.core.database import AsyncSessionLocal
from app.models.schema import Candle
from sqlalchemy import desc, select

logger = logging.getLogger(__name__)


class DataFetcher:
    """
    Fast Paginated Historical Data Fetcher:
    - Fetches up to 25,000 real 5m bars for Crypto (Binance Public API, no key required).
    - Fetches up to ~17,000 real 5m bars for Forex (Yahoo Finance 60d, no key required).
    - Fast backward pagination (< 3s for 25,000 bars) for limit > 1000.
    - Single instant request (< 0.5s) for live inference when limit <= 1000.
    - ZERO synthetic or random-walk data.
    """

    BINANCE_URL = "https://api.binance.com/api/v3/klines"

    TIMEFRAME_MAP_BINANCE = {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "2h": "2h",
        "4h": "4h",
        "1d": "1d",
    }

    TIMEFRAME_SECONDS = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "1d": 86400,
    }

    FOREX_PAIRS = {
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
        "EURJPY=X", "GBPJPY=X", "NZDUSD=X", "USDCAD=X", "USDCHF=X",
    }

    def normalize_pair(self, pair: str) -> str:
        cleaned = pair.strip().upper().replace("/", "").replace("_OTC", "").replace("OTC", "")
        if cleaned.endswith("=X"):
            return cleaned
        if cleaned in {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "GBPJPY", "NZDUSD", "USDCAD", "USDCHF"}:
            return f"{cleaned}=X"
        return cleaned

    def is_forex(self, pair: str) -> bool:
        norm = self.normalize_pair(pair)
        return norm.endswith("=X")

    async def fetch_ohlcv(
        self, pair: str, timeframe: str = "5m", limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Fast fetch OHLCV candles:
        - limit <= 1000: single instant API request (< 0.5s) for live inference
        - limit > 1000: fast backward pagination up to 25,000 bars (~2.5-3.5s)
        """
        norm_pair = self.normalize_pair(pair)
        if self.is_forex(norm_pair):
            return await self._fetch_forex_ohlcv(norm_pair, timeframe, limit)
        else:
            return await self._fetch_binance_ohlcv(norm_pair, timeframe, limit)

    async def _fetch_binance_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Dict[str, Any]]:
        binance_interval = self.TIMEFRAME_MAP_BINANCE.get(timeframe.lower(), "5m")

        # 1. Single instant request for live inference if limit <= 1000
        if limit <= 1000:
            fetch_limit = min(max(limit, 10), 1000)
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    self.BINANCE_URL,
                    params={"symbol": symbol, "interval": binance_interval, "limit": fetch_limit},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"Binance API error {r.status_code}: {r.text}")
                return self._parse_binance_klines(r.json())

        # 2. Fast backward pagination loop when limit > 1000 (up to 25,000 bars in ~2-3s)
        target_limit = min(limit, 25000)
        t0 = time.time()
        bar_secs = self.TIMEFRAME_SECONDS.get(timeframe.lower(), 300)
        chunk_span_ms = 1000 * bar_secs * 1000
        now_ms = int(time.time() * 1000)
        num_chunks = max(1, (target_limit + 999) // 1000)

        async def _fetch_chunk(client: httpx.AsyncClient, end_ts: Optional[int], count: int):
            params: Dict[str, Any] = {
                "symbol": symbol,
                "interval": binance_interval,
                "limit": count,
            }
            if end_ts:
                params["endTime"] = end_ts
            try:
                resp = await client.get(self.BINANCE_URL, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else []
            except Exception as e:
                logger.warning(f"Binance chunk fetch error: {e}")
            return []

        async with httpx.AsyncClient(timeout=20.0) as client:
            tasks = []
            for i in range(num_chunks):
                chunk_end = now_ms - (i * chunk_span_ms)
                chunk_count = min(1000, target_limit - (i * 1000))
                if chunk_count > 0:
                    tasks.append(_fetch_chunk(client, chunk_end, chunk_count))

            chunk_results = await asyncio.gather(*tasks)

        # Merge, deduplicate by open_time, sort chronologically
        raw_dict: Dict[int, Any] = {}
        for chunk in chunk_results:
            for item in chunk:
                raw_dict[item[0]] = item

        sorted_raw = [raw_dict[k] for k in sorted(raw_dict.keys())]
        if len(sorted_raw) > target_limit:
            sorted_raw = sorted_raw[-target_limit:]

        elapsed = time.time() - t0
        parsed = self._parse_binance_klines(sorted_raw)
        logger.info(f"Fetched {len(parsed)} real Binance bars for {symbol} in {elapsed:.2f}s")
        return parsed

    def _parse_binance_klines(self, raw_data: List[Any]) -> List[Dict[str, Any]]:
        candles: List[Dict[str, Any]] = []
        for item in raw_data:
            open_time_ms = item[0]
            dt = datetime.fromtimestamp(open_time_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            candles.append({
                "timestamp": dt,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })
        return candles

    def _sync_fetch_yfinance(
        self, symbol: str, interval: str, period: str, limit: int
    ) -> List[Dict[str, Any]]:
        try:
            ticker = yf.Ticker(symbol)
            df: pd.DataFrame = ticker.history(period=period, interval=interval)
            if df.empty and period != "5d":
                df = ticker.history(period="5d", interval=interval)
        except Exception as e:
            logger.warning(f"yfinance fetch error for {symbol}: {e}")
            return []

        if df.empty:
            return []

        df = df.sort_index()
        if len(df) > limit:
            df = df.iloc[-limit:]

        candles: List[Dict[str, Any]] = []
        for idx, row in df.iterrows():
            if hasattr(idx, "to_pydatetime"):
                dt = idx.to_pydatetime()
            else:
                dt = pd.to_datetime(idx).to_pydatetime()
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            candles.append({
                "timestamp": dt,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0.0) or 0.0),
            })
        return candles

    async def _fetch_forex_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Dict[str, Any]]:
        # For training limit > 1000 on 5m, use period="60d" (~17,000 real bars)
        if limit > 1000 and timeframe.lower() in ["5m", "15m", "30m", "1h"]:
            interval = "5m" if timeframe.lower() == "5m" else timeframe.lower()
            period = "60d"
        elif limit > 1000:
            interval = "1m"
            period = "7d"
        else:
            interval = "5m" if timeframe.lower() == "5m" else "1m"
            period = "5d"

        t0 = time.time()
        try:
            candles = await asyncio.to_thread(
                self._sync_fetch_yfinance, symbol, interval, period, limit
            )
            elapsed = time.time() - t0
            if candles:
                logger.info(
                    f"Fetched {len(candles)} real Forex bars for {symbol} via yfinance ({period}) in {elapsed:.2f}s"
                )
                return candles
        except Exception as e:
            logger.warning(f"Failed to fetch Forex candles for {symbol} via yfinance: {e}")

        # Fallback to cached candles in DB if yfinance throttled
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(Candle)
                    .where(Candle.pair == symbol, Candle.timeframe == timeframe)
                    .order_by(desc(Candle.timestamp))
                    .limit(limit)
                )
                res = await session.execute(stmt)
                db_candles = list(reversed(res.scalars().all()))
                if db_candles:
                    return [
                        {
                            "timestamp": c.timestamp,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                            "volume": c.volume,
                        }
                        for c in db_candles
                    ]
        except Exception as dbe:
            logger.debug(f"DB fallback error: {dbe}")

        return []


data_fetcher = DataFetcher()
fetch_ohlcv = data_fetcher.fetch_ohlcv
