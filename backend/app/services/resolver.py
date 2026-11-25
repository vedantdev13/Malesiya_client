import asyncio
from datetime import datetime, timezone
import logging
from typing import List
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.websockets import ws_manager
from app.core.database import AsyncSessionLocal
from app.models.schema import Candle, Signal
from app.services.market_data import market_data_service

logger = logging.getLogger(__name__)


class SignalResolver:
    async def get_latest_price(
        self, db: AsyncSession, pair: str, timeframe: str = "1m"
    ) -> float:
        """Fetch the most recent close price for a pair from live streamer, feed or database."""
        norm_pair = market_data_service.normalize_pair(pair)
        try:
            from app.services.live_streamer import live_streamer
            state = live_streamer.states.get(norm_pair)
            if state and state.price > 0:
                return float(state.price)
        except Exception:
            pass

        try:
            live_candles = await market_data_service.fetch_candles(
                pair=norm_pair, timeframe=timeframe, limit=5
            )
            if live_candles:
                return float(live_candles[-1]["close"])
        except Exception as e:
            logger.warning(f"Could not fetch live price for resolver: {e}")

        # Fallback to local DB candle
        stmt = (
            select(Candle.close)
            .where(Candle.pair == norm_pair)
            .order_by(desc(Candle.timestamp))
            .limit(1)
        )
        res = await db.execute(stmt)
        price = res.scalar_one_or_none()
        return float(price) if price is not None else 0.0

    async def resolve_pending_signals(self, db: AsyncSession) -> List[Signal]:
        """
        Query all expired PENDING signals, evaluate against exit prices,
        update statuses to WIN/LOSS/DRAW, commit changes, and broadcast updates.
        """
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        stmt = select(Signal).where(
            Signal.status == "PENDING", Signal.expiry_timestamp <= now_utc
        )
        result = await db.execute(stmt)
        expired_signals = result.scalars().all()

        if not expired_signals:
            return []

        resolved: List[Signal] = []

        # Cache latest price per pair within the resolution cycle
        prices_cache = {}

        for sig in expired_signals:
            cache_key = (sig.pair, sig.timeframe)
            if cache_key not in prices_cache:
                prices_cache[cache_key] = await self.get_latest_price(
                    db, sig.pair, sig.timeframe
                )

            exit_price = prices_cache[cache_key]
            if exit_price <= 0.0:
                exit_price = sig.entry_price

            sig.exit_price = exit_price

            # Evaluation logic
            if sig.direction == "UP":
                if exit_price > sig.entry_price:
                    sig.status = "WIN"
                elif exit_price < sig.entry_price:
                    sig.status = "LOSS"
                else:
                    sig.status = "DRAW"
            elif sig.direction == "DOWN":
                if exit_price < sig.entry_price:
                    sig.status = "WIN"
                elif exit_price > sig.entry_price:
                    sig.status = "LOSS"
                else:
                    sig.status = "DRAW"
            else:  # NO_SIGNAL
                sig.status = "DRAW"

            resolved.append(sig)

        await db.commit()

        # Broadcast each resolved signal over WebSocket
        for sig in resolved:
            await ws_manager.broadcast(
                {
                    "event": "SIGNAL_RESOLVED",
                    "data": {
                        "id": sig.id,
                        "pair": sig.pair,
                        "timeframe": sig.timeframe,
                        "direction": sig.direction,
                        "confidence": sig.confidence,
                        "entry_price": sig.entry_price,
                        "exit_price": sig.exit_price,
                        "status": sig.status,
                        "timestamp": (
                            sig.timestamp.isoformat() if sig.timestamp else None
                        ),
                        "expiry_timestamp": (
                            sig.expiry_timestamp.isoformat()
                            if sig.expiry_timestamp
                            else None
                        ),
                    },
                }
            )

        logger.info(f"Resolved {len(resolved)} expired trading signals.")
        return resolved


resolver = SignalResolver()


async def start_resolver_loop():
    """
    Background loop running every 10 seconds to resolve expired signals.
    """
    logger.info("Signal outcome resolver background loop started.")
    try:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    await resolver.resolve_pending_signals(session)
            except Exception as e:
                logger.error(f"Error in resolver background loop: {e}", exc_info=True)

            await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Signal outcome resolver background loop stopped cleanly.")

