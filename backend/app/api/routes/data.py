
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.ml.features import extract_features
from app.models.schema import Candle
from app.services.market_data import market_data_service

router = APIRouter(prefix="/data", tags=["Market Data & Features"])


@router.get("/fetch")
async def fetch_market_data(
    pair: str = Query(default="BTCUSDT", description="Trading asset pair"),
    timeframe: str = Query(default="1m", description="Candle timeframe e.g. 1m, 5m"),
    limit: int = Query(default=50, ge=1, le=1000, description="Number of candles to fetch"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Fetch OHLCV market candles, store them in the database avoiding duplicates,
    and return the result.
    """
    norm_pair = market_data_service.normalize_pair(pair)
    candles = await market_data_service.fetch_candles(
        pair=norm_pair, timeframe=timeframe, limit=limit
    )
    if not candles:
        raise HTTPException(status_code=502, detail="Failed to retrieve market candles")

    new_saved = await market_data_service.sync_candles_to_db(
        db=db, pair=norm_pair, timeframe=timeframe, candles=candles
    )

    return {
        "status": "success",
        "pair": norm_pair,
        "timeframe": timeframe,
        "fetched_count": len(candles),
        "newly_stored_count": new_saved,
        "candles": [
            {
                "timestamp": c["timestamp"].isoformat(),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in candles
        ],
    }


@router.get("/features")
async def get_engineered_features(
    pair: str = Query(default="BTCUSDT", description="Trading asset pair"),
    timeframe: str = Query(default="1m", description="Candle timeframe"),
    limit: int = Query(default=100, ge=30, le=1000, description="Candles lookback"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Retrieve latest candles, calculate technical indicators (RSI, MACD, EMA, BB, ATR),
    and return the latest engineered feature vector.
    """
    norm_pair = market_data_service.normalize_pair(pair)

    # Query existing candles from DB
    stmt = (
        select(Candle)
        .where(Candle.pair == norm_pair, Candle.timeframe == timeframe)
        .order_by(desc(Candle.timestamp))
        .limit(max(limit, 100))
    )
    result = await db.execute(stmt)
    candles_db = list(reversed(result.scalars().all()))

    # If insufficient candles in DB to compute lookbacks, fetch live and sync
    if len(candles_db) < 30:
        fetched = await market_data_service.fetch_candles(
            pair=norm_pair, timeframe=timeframe, limit=max(limit, 100)
        )
        if fetched:
            await market_data_service.sync_candles_to_db(
                db=db, pair=norm_pair, timeframe=timeframe, candles=fetched
            )
            # Re-query
            res = await db.execute(stmt)
            candles_db = list(reversed(res.scalars().all()))

    if len(candles_db) < 25:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient candles available ({len(candles_db)}) to calculate technical indicators. Minimum 25 required.",
        )

    # Convert to pandas DataFrame
    data_records = [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles_db
    ]
    df = pd.DataFrame(data_records)

    try:
        features_df = extract_features(df)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Feature extraction failed: {str(e)}"
        )

    if features_df.empty:
        raise HTTPException(
            status_code=400,
            detail="Feature matrix is empty after dropping initial NaN lookback windows. Provide a larger limit.",
        )

    # Extract latest row as JSON-serializable dictionary
    latest_row = features_df.iloc[-1].to_dict()

    # Convert any non-serializable objects (timestamps/numpy types)
    sanitized_latest: Dict[str, Any] = {}
    for k, v in latest_row.items():
        if isinstance(v, pd.Timestamp):
            sanitized_latest[k] = v.isoformat()
        elif hasattr(v, "item"):  # numpy float/int
            sanitized_latest[k] = v.item()
        else:
            sanitized_latest[k] = v

    return {
        "status": "success",
        "pair": norm_pair,
        "timeframe": timeframe,
        "total_candles": len(df),
        "features_rows_count": len(features_df),
        "latest_features": sanitized_latest,
    }
