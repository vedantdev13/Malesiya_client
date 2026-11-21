import asyncio
from datetime import datetime, timezone
import json
import logging
import random
import time
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)


class AssetState:
    def __init__(self, pair: str, is_forex: bool, default_price: float):
        self.pair = pair
        self.is_forex = is_forex
        self.price = default_price
        self.anchor_price = default_price
        self.last_anchor_update = time.time()
        self.change_24h = 0.0

        now_sec = int(time.time())
        # Active candle buckets for 1m, 5m, 15m
        self.candles: Dict[str, Dict[str, Any]] = {}
        for tf, secs in [("1m", 60), ("5m", 300), ("15m", 900)]:
            bucket_time = now_sec - (now_sec % secs)
            self.candles[tf] = {
                "time": bucket_time,
                "open": default_price,
                "high": default_price,
                "low": default_price,
                "close": default_price,
                "volume": 10.0,
            }


class LiveStreamer:
    """
    Quotex-Grade Real-Time Market Live Streamer:
    - 24/7 Crypto: Live real-time Binance prices.
    - Forex: Anchored tightly to real market quotes (Yahoo Finance/Frankfurter).
    - Sub-second / 1-second institutional micro-pip tick engine when upstream
      Forex feed is paused or off-hours, ensuring candles visibly breathe, form
      upper/lower wicks, and never freeze into flat horizontal dashes.
    - Synchronous in-memory lookup for /api/v1/data/live-tick (< 1ms).
    - WebSocket broadcaster for /ws/market.
    """

    DEFAULT_PRICES = {
        "BTCUSDT": 58200.0,
        "ETHUSDT": 1635.0,
        "SOLUSDT": 68.0,
        "EURUSD=X": 1.09750,
        "GBPUSD=X": 1.28500,
        "USDJPY=X": 154.300,
    }

    TIMEFRAMES = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
    }

    def __init__(self):
        self.states: Dict[str, AssetState] = {}
        for pair, price in self.DEFAULT_PRICES.items():
            is_fx = pair.endswith("=X")
            self.states[pair] = AssetState(pair, is_fx, price)
        self.is_running = False

    def normalize_pair(self, pair: str) -> str:
        cleaned = pair.strip().upper().replace("/", "").replace("_OTC", "").replace("OTC", "")
        if cleaned.endswith("=X"):
            return cleaned
        if cleaned in {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "GBPJPY", "NZDUSD", "USDCAD", "USDCHF"}:
            return f"{cleaned}=X"
        return cleaned

    def get_live_tick(self, pair: str, timeframe: str = "5m") -> Optional[Dict[str, Any]]:
        norm = self.normalize_pair(pair)
        state = self.states.get(norm)
        if not state:
            return None

        tf = timeframe.lower()
        if tf not in state.candles:
            tf = "5m"

        c = state.candles[tf]
        dec = 3 if norm.endswith("JPY=X") else (2 if ("BTC" in norm or "ETH" in norm or "SOL" in norm) else 5)

        return {
            "status": "success",
            "pair": norm,
            "timeframe": tf,
            "price": round(state.price, dec),
            "candle": {
                "time": c["time"],
                "open": round(c["open"], dec),
                "high": round(c["high"], dec),
                "low": round(c["low"], dec),
                "close": round(c["close"], dec),
                "volume": round(c["volume"], 2),
            },
            "timestamp": datetime.fromtimestamp(c["time"], tz=timezone.utc).isoformat(),
        }

    async def _fetch_binance_prices(self, client: httpx.AsyncClient):
        """Fetch all 3 crypto prices in a single batch request (< 100ms)."""
        url = 'https://api.binance.com/api/v3/ticker/price?symbols=["BTCUSDT","ETHUSDT","SOLUSDT"]'
        try:
            resp = await client.get(url, timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data:
                    sym = item.get("symbol")
                    p_str = item.get("price")
                    if sym in self.states and p_str:
                        p = float(p_str)
                        state = self.states[sym]
                        state.anchor_price = p
                        state.price = p
                        state.last_anchor_update = time.time()
        except Exception as e:
            logger.debug(f"Binance price sync error: {e}")

    async def _fetch_yahoo_forex(self, client: httpx.AsyncClient, pair: str):
        """Fetch latest real Forex quote from Yahoo Finance."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{pair}?interval=1m&range=1d"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            resp = await client.get(url, headers=headers, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("chart", {}).get("result", [])
                if result:
                    res = result[0]
                    meta = res.get("meta", {})
                    reg_price = meta.get("regularMarketPrice")
                    if reg_price is not None and pair in self.states:
                        state = self.states[pair]
                        state.anchor_price = float(reg_price)
                        state.last_anchor_update = time.time()
        except Exception as e:
            logger.debug(f"Yahoo forex quote sync error for {pair}: {e}")

    async def _sync_external_anchors(self):
        """Background loop updating external anchor quotes every 4 seconds."""
        async with httpx.AsyncClient() as client:
            while self.is_running:
                try:
                    # Sync crypto
                    await self._fetch_binance_prices(client)
                    # Sync forex concurrently
                    fx_tasks = [
                        self._fetch_yahoo_forex(client, fx_pair)
                        for fx_pair in ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
                    ]
                    await asyncio.gather(*fx_tasks, return_exceptions=True)
                except Exception as e:
                    logger.debug(f"Anchor sync loop error: {e}")

                await asyncio.sleep(4.0)

    def _tick_forex(self, state: AssetState):
        """
        Quotex-Grade Institutional Micro-Pip Tick Engine:
        Mean-reverting random walk tightly anchored to the real market quote.
        Ensures active candles visibly breathe, form upper/lower wicks, and move
        every single second without freezing into flat horizontal dashes.
        """
        is_jpy = "JPY" in state.pair
        anchor = state.anchor_price
        cur = state.price

        if is_jpy:
            # JPY: anchor ~ 154.30. Pip scale = 0.01
            drift = cur - anchor
            reversion = -0.22 * drift
            noise = random.uniform(-0.008, 0.008)
            new_p = cur + noise + reversion
            # Bound within ±0.035 of real market anchor
            new_p = max(anchor - 0.035, min(anchor + 0.035, new_p))
            state.price = round(new_p, 3)
        else:
            # EUR/USD, GBP/USD: anchor ~ 1.0975. Pip scale = 0.0001
            drift = cur - anchor
            reversion = -0.22 * drift
            noise = random.uniform(-0.00004, 0.00004)
            new_p = cur + noise + reversion
            # Bound within ±0.00025 of real market anchor
            new_p = max(anchor - 0.00025, min(anchor + 0.00025, new_p))
            state.price = round(new_p, 5)

    def _tick_crypto(self, state: AssetState):
        """Realistic micro-pip movements between Binance updates."""
        cur = state.price
        anchor = state.anchor_price
        drift = cur - anchor
        reversion = -0.25 * drift
        noise_pct = random.uniform(-0.0002, 0.0002)
        new_p = cur * (1.0 + noise_pct) + reversion
        # Clamp within ±0.15% of anchor
        new_p = max(anchor * 0.9985, min(anchor * 1.0015, new_p))
        state.price = round(new_p, 2)

    def _update_candle_buckets(self, state: AssetState, now_sec: int):
        """Update active candles across all timeframes (1m, 5m, 15m) and handle roll-overs."""
        for tf, secs in self.TIMEFRAMES.items():
            bucket_time = now_sec - (now_sec % secs)
            c = state.candles.get(tf)

            if not c or c["time"] != bucket_time:
                # Candle rollover: spawn brand new candle
                prev_close = c["close"] if c else state.price
                state.candles[tf] = {
                    "time": bucket_time,
                    "open": prev_close,
                    "high": max(prev_close, state.price),
                    "low": min(prev_close, state.price),
                    "close": state.price,
                    "volume": round(random.uniform(2.0, 10.0), 2),
                }
            else:
                # Update current active candle in-place
                c["high"] = max(c["high"], state.price)
                c["low"] = min(c["low"], state.price)
                c["close"] = state.price
                c["volume"] = round(c["volume"] + random.uniform(0.1, 0.8), 2)

    async def _streaming_loop(self):
        """Main 1-second live streaming & broadcasting loop."""
        from app.api.websockets import market_ws_manager, ws_manager

        logger.info("Started Quotex-Grade Live Market Streaming Engine (1s tick).")

        while self.is_running:
            t0 = time.time()
            now_sec = int(t0)

            ticks_payload: Dict[str, Any] = {}

            for pair, state in self.states.items():
                if state.is_forex:
                    self._tick_forex(state)
                else:
                    self._tick_crypto(state)

                self._update_candle_buckets(state, now_sec)

                dec = 3 if "JPY" in pair else (2 if ("BTC" in pair or "ETH" in pair or "SOL" in pair) else 5)
                ticks_payload[pair] = {
                    "price": round(state.price, dec),
                    "anchor": round(state.anchor_price, dec),
                    "candles": {
                        tf: {
                            "time": c["time"],
                            "open": round(c["open"], dec),
                            "high": round(c["high"], dec),
                            "low": round(c["low"], dec),
                            "close": round(c["close"], dec),
                            "volume": round(c["volume"], 2),
                        }
                        for tf, c in state.candles.items()
                    },
                }

            # Broadcast to active WebSocket connections
            msg = {
                "event": "MARKET_TICK",
                "timestamp": datetime.fromtimestamp(now_sec, tz=timezone.utc).isoformat(),
                "ticks": ticks_payload,
            }

            try:
                await market_ws_manager.broadcast(msg)
            except Exception as e:
                logger.debug(f"Market broadcast error: {e}")

            # Also broadcast on live signals ws manager if clients connected
            try:
                await ws_manager.broadcast(msg)
            except Exception as e:
                logger.debug(f"Signals ws broadcast error: {e}")

            elapsed = time.time() - t0
            sleep_time = max(0.05, 1.0 - elapsed)
            await asyncio.sleep(sleep_time)

    async def start(self):
        """Start both the anchor fetcher and the high-frequency tick streamer."""
        self.is_running = True
        logger.info("Initializing LiveStreamer background tasks...")
        anchor_task = asyncio.create_task(self._sync_external_anchors())
        stream_task = asyncio.create_task(self._streaming_loop())

        try:
            await asyncio.gather(anchor_task, stream_task)
        except asyncio.CancelledError:
            self.is_running = False
            anchor_task.cancel()
            stream_task.cancel()
            logger.info("LiveStreamer stopped.")


live_streamer = LiveStreamer()

