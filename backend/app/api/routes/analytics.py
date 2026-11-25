from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.schema import Signal
from app.services.market_data import market_data_service

router = APIRouter(prefix="/analytics", tags=["Analytics & Performance"])


@router.post("/reset")
async def reset_analytics_history(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Clear historical signal outcomes and test rows from the signals table,
    resetting performance metrics to a fresh zero-baseline state.
    """
    await db.execute(delete(Signal))
    await db.commit()
    return {
        "status": "success",
        "message": "All historical trading signals and performance test stats reset successfully.",
    }


@router.get("/performance")
async def get_performance_metrics(
    pair: Optional[str] = Query(default=None, description="Optional pair filter"),
    timeframe: Optional[str] = Query(
        default=None, description="Optional timeframe filter"
    ),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Calculate and return performance statistics:
    total signals, tradeable signals, wins/losses/draws, win rate, and coverage rate.
    """
    query = select(Signal)

    norm_pair = None
    if pair:
        norm_pair = market_data_service.normalize_pair(pair)
        query = query.where(Signal.pair == norm_pair)
    if timeframe:
        query = query.where(Signal.timeframe == timeframe)

    result = await db.execute(query)
    signals = result.scalars().all()

    total_signals = len(signals)
    tradeable_signals = [s for s in signals if s.direction in ["UP", "DOWN"]]
    total_tradeable = len(tradeable_signals)

    wins = sum(1 for s in signals if s.status == "WIN")
    losses = sum(1 for s in signals if s.status == "LOSS")
    draws = sum(1 for s in signals if s.status == "DRAW")
    pending = sum(1 for s in signals if s.status == "PENDING")

    # Win rate: WIN / (WIN + LOSS)
    resolved_tradeable = wins + losses
    win_rate = (
        round((wins / resolved_tradeable) * 100.0, 2)
        if resolved_tradeable > 0
        else 0.0
    )

    # Coverage rate: tradeable signals / total signals
    coverage_rate = (
        round((total_tradeable / total_signals) * 100.0, 2)
        if total_signals > 0
        else 0.0
    )

    return {
        "status": "success",
        "pair": norm_pair if norm_pair else "ALL",
        "timeframe": timeframe if timeframe else "ALL",
        "total_signals_generated": total_signals,
        "total_tradeable_signals": total_tradeable,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "pending": pending,
        "win_rate": win_rate,
        "coverage_rate": coverage_rate,
    }

