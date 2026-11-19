import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
import yfinance as yf
from app.core.database import AsyncSessionLocal
from app.models.schema import Candle

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    100% Real Live Market Data Service.
    - 24/7 Crypto (BTCUSDT, ETHUSDT, SOLUSDT, etc.) via Binance Public REST API (no API key required)
    - Live Forex (EURUSD=X, GBPUSD=X, USDJPY=X, AUDUSD=X, etc.) via Yahoo Finance (no API key required)
    - ZERO synthetic or random-walk data generators.
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

    TIMEFRAME_MAP_YFINANCE = {
        "1m": ("1m", "5d"),
        "3m": ("5m", "5d"),   # YFinance does not support 3m, 5m is closest
        "5m": ("5m", "5d"),
        "15m": ("15m", "1mo"),
        "30m": ("30m", "1mo"),
        "1h": ("60m", "2mo"),
        "2h": ("60m", "2mo"),
        "4h": ("60m", "2mo"),
        "1d": ("1d", "1y"),
    }

    FOREX_PAIRS = {
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
        "EURJPY=X", "GBPJPY=X", "NZDUSD=X", "USDCAD=X", "USDCHF=X",
    }

    def normalize_pair(self, pair: str) -> str:
        """
        Normalize pair string to standard canonical representation:
        - Forex: EURUSD=X, GBPUSD=X, USDJPY=X, AUDUSD=X
        - Crypto: BTCUSDT, ETHUSDT, SOLUSDT
        """
        cleaned = pair.strip().upper().replace("/", "").replace("_OTC", "").replace("OTC", "")
        if cleaned.endswith("=X"):
            return cleaned
        if cleaned in {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "GBPJPY", "NZDUSD", "USDCAD", "USDCHF"}:
            return f"{cleaned}=X"
        return cleaned

    def is_forex(self, pair: str) -> bool:
        norm = self.normalize_pair(pair)
        return norm.endswith("=X")

    async def fetch_candles(
        self, pair: str, timeframe: str = "1m", limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Fetch real-time historical OHLCV candles from real live market feeds.
        Supports fast pagination up to 25,000 bars.
        Zero synthetic or simulated data.
        """
        from app.services.data_fetcher import data_fetcher
        return await data_fetcher.fetch_ohlcv(pair=pair, timeframe=timeframe, limit=limit)

    async def fetch_ohlcv(
        self, pair: str, timeframe: str = "1m", limit: int = 1000
    ) -> List[Dict[str, Any]]:
        from app.services.data_fetcher import data_fetcher
        return await data_fetcher.fetch_ohlcv(pair=pair, timeframe=timeframe, limit=limit)

    async def _fetch_binance_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch 100% real crypto candles from Binance public REST API."""
        binance_interval = self.TIMEFRAME_MAP_BINANCE.get(timeframe.lower(), "1m")
        fetch_limit = min(max(limit, 10), 1000)

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                self.BINANCE_URL,
                params={
                    "symbol": symbol,
                    "interval": binance_interval,
                    "limit": fetch_limit,
                },
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Binance API returned status {response.status_code} for {symbol}: {response.text}"
                )

            raw_data = response.json()
            candles: List[Dict[str, Any]] = []
            for item in raw_data:
                # Binance structure: [open_time, open, high, low, close, volume, ...]
                open_time_ms = item[0]
                dt = datetime.fromtimestamp(
                    open_time_ms / 1000.0, tz=timezone.utc
                ).replace(tzinfo=None)
                candles.append(
                    {
                        "timestamp": dt,
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                )
            logger.info(f"Fetched {len(candles)} real crypto candles for {symbol} from Binance.")
            return candles

    def _sync_fetch_yfinance(
        self, ticker_symbol: str, interval: str, period: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Synchronous worker to fetch Forex candles via yfinance."""
        try:
            ticker = yf.Ticker(ticker_symbol)
            df: pd.DataFrame = ticker.history(period=period, interval=interval)
            if df.empty and period != "2d":
                df = ticker.history(period="2d", interval=interval)
        except Exception as e:
            logger.warning(f"yfinance fetch error for {ticker_symbol}: {e}")
            return []

        if df.empty:
            logger.warning(f"No yfinance data returned for {ticker_symbol} with interval={interval}, period={period}")
            return []

        # Ensure index is DatetimeIndex and sort chronologically
        df = df.sort_index()
        if len(df) > limit:
            df = df.iloc[-limit:]

        candles: List[Dict[str, Any]] = []
        for idx, row in df.iterrows():
            # Convert timestamp to UTC naive datetime
            if hasattr(idx, "to_pydatetime"):
                dt = idx.to_pydatetime()
            else:
                dt = pd.to_datetime(idx).to_pydatetime()

            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

            candles.append(
                {
                    "timestamp": dt,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume", 0.0) or 0.0),
                }
            )
        return candles

    async def _fetch_forex_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch 100% real Forex candles via yfinance in async thread pool with DB cache fallback."""
        interval, period = self.TIMEFRAME_MAP_YFINANCE.get(timeframe.lower(), ("1m", "5d"))

        try:
            candles = await asyncio.to_thread(
                self._sync_fetch_yfinance, symbol, interval, period, limit
            )
            if candles:
                logger.info(f"Fetched {len(candles)} real Forex candles for {symbol} via yfinance.")
                return candles
        except Exception as e:
            logger.warning(f"Failed to fetch Forex candles for {symbol} from yfinance: {e}")

        # If yfinance is temporarily throttled, fallback to cached real candles in DB
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
                    logger.info(f"Using {len(db_candles)} cached real Forex candles for {symbol}")
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
            logger.debug(f"DB fallback fetch error: {dbe}")

        return []

    async def sync_candles_to_db(
        self,
        db: AsyncSession,
        pair: str,
        timeframe: str,
        candles: List[Dict[str, Any]],
    ) -> int:
        """
        Persist fetched real market candles to the database avoiding duplicate timestamps.
        Returns the number of new candles inserted.
        """
        if not candles:
            return 0

        norm_pair = self.normalize_pair(pair)
        timestamps = [c["timestamp"] for c in candles]
        min_ts, max_ts = min(timestamps), max(timestamps)

        # Query existing timestamps in this range
        stmt = select(Candle.timestamp).where(
            Candle.pair == norm_pair,
            Candle.timeframe == timeframe,
            Candle.timestamp >= min_ts,
            Candle.timestamp <= max_ts,
        )
        result = await db.execute(stmt)
        existing_timestamps = set(result.scalars().all())

        new_candles = []
        for c in candles:
            if c["timestamp"] not in existing_timestamps:
                new_candles.append(
                    Candle(
                        pair=norm_pair,
                        timeframe=timeframe,
                        timestamp=c["timestamp"],
                        open=c["open"],
                        high=c["high"],
                        low=c["low"],
                        close=c["close"],
                        volume=c["volume"],
                    )
                )

        if new_candles:
            db.add_all(new_candles)
            await db.commit()

        return len(new_candles)


market_data_service = MarketDataService()
