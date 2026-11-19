"""
PaperTradingEngine — Automated continuous paper-trading loop on 100% Real Market Data.

Runs in background on FastAPI startup. For enabled real live pairs:
  1. Fetches real OHLCV candle data from Binance/yfinance on candle-close interval.
  2. Auto-trains and calibrates high-precision Ensemble if none exists yet on 1,000 real bars.
  3. Runs signal inference with 3-bar horizon & 82% confidence threshold.
  4. Persists the Signal record to the database with 3-bar expiration window.
  5. Broadcasts the new signal over WebSocket to all connected clients.

Zero synthetic/dummy data.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websockets import ws_manager
from app.core.database import AsyncSessionLocal
from app.ml.features import extract_features
from app.ml.trainer import train_tuned_model
from app.ml.predictor import predictor
from app.models.schema import Candle, Signal
from app.services.market_data import market_data_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration: Real live pairs (5m default timeframe)
# ---------------------------------------------------------------------------

ENABLED_PAIRS: List[Dict[str, str]] = [
    {"pair": "BTCUSDT",  "timeframe": "5m"},
    {"pair": "ETHUSDT",  "timeframe": "5m"},
    {"pair": "EURUSD=X", "timeframe": "5m"},
    {"pair": "GBPUSD=X", "timeframe": "5m"},
]

POLL_INTERVAL_SECONDS: int = 60          # Check for new candles every 60s
CONFIDENCE_THRESHOLD: float = 0.82       # Strict 82% confidence filter
MIN_CANDLES_FOR_INFERENCE: int = 30
TRAINING_CANDLE_LIMIT: int = 1000

TIMEFRAME_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


class PaperTradingEngine:
    """
    Autonomous paper-trading background engine powered by 100% real live market feeds.
    """

    def __init__(
        self,
        pairs: Optional[List[Dict[str, str]]] = None,
        poll_interval: int = POLL_INTERVAL_SECONDS,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ):
        self.pairs = pairs or ENABLED_PAIRS
        self.poll_interval = poll_interval
        self.confidence_threshold = confidence_threshold

        self._last_signal_time: Dict[str, datetime] = {}
        self._error_counts: Dict[str, int] = {}
        self._total_generated: int = 0
        self._running: bool = False
        self._started_at: datetime = datetime.now(timezone.utc)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_generated(self) -> int:
        return self._total_generated

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self._started_at).total_seconds()

    async def run(self) -> None:
        """Main async loop."""
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        logger.info(
            f"[PaperTrader] Started on 100% real market feeds. Monitoring {len(self.pairs)} pairs "
            f"every {self.poll_interval}s | threshold={self.confidence_threshold} (3-bar horizon)"
        )

        try:
            while True:
                await self._tick()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("[PaperTrader] Background loop cancelled — shutting down cleanly.")
        finally:
            self._running = False

    async def _tick(self) -> None:
        """Process all real pairs in one polling tick."""
        for cfg in self.pairs:
            pair = cfg["pair"]
            timeframe = cfg.get("timeframe", "5m")
            norm_pair = market_data_service.normalize_pair(pair)
            key = f"{norm_pair}_{timeframe}"

            # Exponential back-off for failing pairs
            err_count = self._error_counts.get(key, 0)
            if err_count > 0:
                skip_ticks = min(2 ** err_count, 8)
                last_time = self._last_signal_time.get(key)
                if last_time:
                    elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
                    if elapsed < (skip_ticks * self.poll_interval):
                        continue

            try:
                await self._process_pair(norm_pair, timeframe, key)
                self._error_counts[key] = 0
            except Exception as exc:
                self._error_counts[key] = err_count + 1
                logger.error(
                    f"[PaperTrader] Error processing {key} (attempt {err_count + 1}): {exc}",
                    exc_info=False,
                )

    async def _process_pair(self, norm_pair: str, timeframe: str, key: str) -> None:
        """Fetch real candles -> auto-tune if needed -> infer -> persist & broadcast."""
        async with AsyncSessionLocal() as db:
            candles_df = await self._get_candles_df(db, norm_pair, timeframe)
            if candles_df is None or len(candles_df) < MIN_CANDLES_FOR_INFERENCE:
                logger.warning(f"[PaperTrader] Insufficient real candles for {key}")
                return

            # Auto-train tuned ensemble on real bars if missing
            if not predictor.model_exists(norm_pair, timeframe):
                logger.info(f"[PaperTrader] No model for {key} — tuning ensemble on 1,000 real bars...")
                await self._auto_train(db, norm_pair, timeframe, candles_df)

            features_df = extract_features(candles_df)
            if features_df.empty:
                return

            pred: Dict[str, Any] = predictor.predict_signal(
                features_df=features_df,
                pair=norm_pair,
                timeframe=timeframe,
                confidence_threshold=self.confidence_threshold,
            )

            # Only persist and broadcast actionable signals (UP or DOWN)
            if pred["direction"] in ["UP", "DOWN"]:
                signal = await self._persist_signal(db, pred, norm_pair, timeframe)
                self._total_generated += 1
                self._last_signal_time[key] = datetime.now(timezone.utc)

                logger.info(
                    f"[PaperTrader] {norm_pair} ({timeframe}) -> {pred['direction']} "
                    f"@ {pred['confidence']*100:.1f}% (confluence={pred.get('confluence_score', 0):.2f}) | "
                    f"entry={pred['entry_price']:.5g}"
                )

                await ws_manager.broadcast(
                    {
                        "event": "NEW_SIGNAL",
                        "source": "paper_trader",
                        "data": {
                            "id": signal.id,
                            "pair": signal.pair,
                            "timeframe": signal.timeframe,
                            "direction": signal.direction,
                            "confidence": signal.confidence,
                            "confluence_score": pred.get("confluence_score", 0.0),
                            "gate_reason": pred.get("gate_reason"),
                            "entry_price": signal.entry_price,
                            "exit_price": signal.exit_price,
                            "status": signal.status,
                            "timestamp": (
                                signal.timestamp.isoformat() if signal.timestamp else None
                            ),
                            "expiry_timestamp": (
                                signal.expiry_timestamp.isoformat()
                                if signal.expiry_timestamp
                                else None
                            ),
                        },
                    }
                )
            else:
                logger.debug(
                    f"[PaperTrader] {norm_pair} ({timeframe}) -> NO_SIGNAL "
                    f"({pred.get('gate_reason', 'waiting for confluence')})"
                )

    async def _get_candles_df(
        self, db: AsyncSession, norm_pair: str, timeframe: str
    ) -> Optional[pd.DataFrame]:
        """Fetch fresh real candles from provider and persist."""
        try:
            fetched = await market_data_service.fetch_candles(
                pair=norm_pair, timeframe=timeframe, limit=100
            )
            if fetched:
                await market_data_service.sync_candles_to_db(
                    db=db, pair=norm_pair, timeframe=timeframe, candles=fetched
                )
        except Exception as e:
            logger.warning(f"[PaperTrader] Candle fetch warning for {norm_pair}: {e}")

        # Load from DB
        stmt = (
            select(Candle)
            .where(Candle.pair == norm_pair, Candle.timeframe == timeframe)
            .order_by(desc(Candle.timestamp))
            .limit(100)
        )
        res = await db.execute(stmt)
        db_candles = list(reversed(res.scalars().all()))

        if not db_candles:
            return None

        return pd.DataFrame(
            [
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
        )

    async def _auto_train(
        self,
        db: AsyncSession,
        norm_pair: str,
        timeframe: str,
        candles_df: pd.DataFrame,
    ) -> None:
        """Fetch 1000 real bars and train ensemble with 3-bar horizon & calibration."""
        try:
            fetched = await market_data_service.fetch_candles(
                pair=norm_pair, timeframe=timeframe, limit=TRAINING_CANDLE_LIMIT
            )
            if fetched:
                await market_data_service.sync_candles_to_db(
                    db=db, pair=norm_pair, timeframe=timeframe, candles=fetched
                )
                df_ext = pd.DataFrame(fetched)
                features_df = extract_features(df_ext)
            else:
                features_df = extract_features(candles_df)

            result = await asyncio.to_thread(
                train_tuned_model,
                features_df,
                target_horizon=3,
                pair=norm_pair,
                timeframe=timeframe,
                n_splits=5,
            )
            predictor.load_model(norm_pair, timeframe, force_reload=True)
            logger.info(
                f"[PaperTrader] Tuned ensemble saved for {norm_pair}_{timeframe} | "
                f"CV Acc={result.get('cv_accuracy', 0):.4f} | Calibrated Acc={result.get('accuracy', 0):.4f} | "
                f"High-Conf Acc={result.get('high_confidence_accuracy', 0):.4f}"
            )
        except Exception as e:
            logger.error(f"[PaperTrader] Auto-tuning failed for {norm_pair}_{timeframe}: {e}")
            raise

    async def _persist_signal(
        self, db: AsyncSession, pred: Dict[str, Any], norm_pair: str, timeframe: str
    ) -> Signal:
        # Align with 3-bar directional horizon
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        step_secs = TIMEFRAME_SECONDS.get(timeframe.lower(), 300)
        expiry_utc = now_utc + timedelta(seconds=3 * step_secs)

        signal = Signal(
            id=str(uuid.uuid4()),
            pair=norm_pair,
            timeframe=timeframe,
            timestamp=now_utc,
            expiry_timestamp=expiry_utc,
            direction=pred["direction"],
            confidence=pred["confidence"],
            entry_price=pred["entry_price"],
            exit_price=None,
            status="PENDING",
        )
        db.add(signal)
        await db.commit()
        await db.refresh(signal)
        return signal


paper_trader = PaperTradingEngine()


async def start_paper_trading_loop() -> None:
    await paper_trader.run()
