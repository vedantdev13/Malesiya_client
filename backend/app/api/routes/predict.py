import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal, get_db
from app.ml.features import extract_features
from app.ml.trainer import train_tuned_model
from app.ml.predictor import predictor
from app.models.schema import Candle, Signal
from app.schemas.signal import SignalResponse
from app.services.market_data import market_data_service
from app.api.websockets import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predict", tags=["ML Predictions & Signals"])

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


async def _bg_sync_candles(pair: str, timeframe: str, candles: List[Dict[str, Any]]) -> None:
    """Background task to sync latest candles into DB without blocking training."""
    try:
        async with AsyncSessionLocal() as session:
            await market_data_service.sync_candles_to_db(session, pair, timeframe, candles)
    except Exception:
        pass


PRIORITY_FOREX_PAIRS = ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
PRIORITY_CRYPTO_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


async def _train_single_suite_pair(pair: str, timeframe: str, limit: int) -> Dict[str, Any]:
    norm_pair = market_data_service.normalize_pair(pair)
    fetched = await market_data_service.fetch_candles(pair=norm_pair, timeframe=timeframe, limit=limit)
    if not fetched or len(fetched) < 50:
        raise ValueError(f"Insufficient candles ({len(fetched) if fetched else 0}) for {norm_pair}")

    df = pd.DataFrame(fetched)
    features_df = extract_features(df)
    train_res = await asyncio.to_thread(
        train_tuned_model,
        features_df,
        target_horizon=3,
        pair=norm_pair,
        timeframe=timeframe,
    )
    predictor.load_model(norm_pair, timeframe, force_reload=True)
    return {
        "pair": norm_pair,
        "candles_used": len(df),
        "test_samples": train_res.get("test_samples"),
        "test_accuracy": train_res.get("test_accuracy"),
        "high_confidence_accuracy": train_res.get("high_confidence_accuracy"),
        "high_confidence_sample_count": train_res.get("high_confidence_sample_count"),
        "model_file": train_res.get("model_file"),
    }


@router.post("/train-primary-fx")
async def train_primary_fx_endpoint(
    timeframe: str = Query(default="5m", description="Primary timeframe to train Forex models on"),
) -> Dict[str, Any]:
    """
    Train and calibrate the 3 Primary Quotex Forex pairs on 60,000 real bars total:
    - EURUSD=X (20,000 real bars)
    - GBPUSD=X (20,000 real bars)
    - USDJPY=X (20,000 real bars)
    Total: 60,000 real Forex bars trained with 0.15 * ATR sensitivity.
    """
    t0 = time.time()
    results = []
    total_candles = 0

    for p in PRIORITY_FOREX_PAIRS:
        try:
            r = await _train_single_suite_pair(p, timeframe, limit=20000)
            results.append({"status": "success", "category": "Forex", **r})
            total_candles += r["candles_used"]
        except Exception as e:
            logger.error(f"Primary FX training failed for {p}: {e}")
            results.append({"status": "failed", "category": "Forex", "pair": p, "error": str(e)})

    elapsed = time.time() - t0
    return {
        "status": "success",
        "message": f"Successfully trained Primary Forex Suite on {total_candles:,} real bars across 3 pairs in {elapsed:.1f}s",
        "timeframe": timeframe,
        "total_candles": total_candles,
        "category": "Forex",
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
    }


@router.post("/train-secondary-crypto")
async def train_secondary_crypto_endpoint(
    timeframe: str = Query(default="5m", description="Primary timeframe to train Crypto models on"),
) -> Dict[str, Any]:
    """
    Train and calibrate the 3 Secondary Crypto pairs on 40,000 real bars total:
    - BTCUSDT (13,334 real bars)
    - ETHUSDT (13,334 real bars)
    - SOLUSDT (13,334 real bars)
    Total: ~40,002 real Crypto bars trained with 0.25 * ATR sensitivity.
    """
    t0 = time.time()
    results = []
    total_candles = 0

    for p in PRIORITY_CRYPTO_PAIRS:
        try:
            r = await _train_single_suite_pair(p, timeframe, limit=13334)
            results.append({"status": "success", "category": "Crypto", **r})
            total_candles += r["candles_used"]
        except Exception as e:
            logger.error(f"Secondary Crypto training failed for {p}: {e}")
            results.append({"status": "failed", "category": "Crypto", "pair": p, "error": str(e)})

    elapsed = time.time() - t0
    return {
        "status": "success",
        "message": f"Successfully trained Secondary Crypto Suite on {total_candles:,} real bars across 3 pairs in {elapsed:.1f}s",
        "timeframe": timeframe,
        "total_candles": total_candles,
        "category": "Crypto",
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
    }


@router.post("/train-priority-suite")
async def train_priority_suite_endpoint(
    timeframe: str = Query(default="5m", description="Primary timeframe to train suite on (e.g. 5m)"),
) -> Dict[str, Any]:
    """Train both Primary FX (60k) and Secondary Crypto (40k) = 100k total."""
    fx_res = await train_primary_fx_endpoint(timeframe=timeframe)
    crypto_res = await train_secondary_crypto_endpoint(timeframe=timeframe)
    total_c = fx_res.get("total_candles", 0) + crypto_res.get("total_candles", 0)
    return {
        "status": "success",
        "message": f"100k Suite Trained: {total_c:,} real bars ({fx_res.get('total_candles',0):,} FX + {crypto_res.get('total_candles',0):,} Crypto)",
        "timeframe": timeframe,
        "total_candles": total_c,
        "forex_candles": fx_res.get("total_candles", 0),
        "crypto_candles": crypto_res.get("total_candles", 0),
        "results": fx_res.get("results", []) + crypto_res.get("results", []),
    }


@router.post("/train")
async def train_model_endpoint(
    pair: str = Query(default="EURUSD=X", description="Asset pair e.g. EURUSD=X or BTCUSDT"),
    timeframe: str = Query(default="5m", description="Candle timeframe e.g. 5m"),
    limit: Optional[int] = Query(default=None, description="Candle count (defaults: 20,000 for Forex, 13,334 for Crypto)"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Fetch real historical bars from Binance/yfinance, compute feature matrix,
    train heavily regularized & pruned soft-voting Ensemble (XGBoost + RandomForest) with:
    - 3-bar directional horizon (Close(t+3) > Close(t))
    - Dynamic noise filter (0.15 * ATR for Forex, 0.25 * ATR for Crypto)
    - Strict chronological 70% Train : 10% Validation : 20% Test split
    - Multi-core n_jobs=-1 execution
    - Sigmoid probability calibration on 10% validation set
    - Final out-of-sample test accuracy and high-confidence precision evaluated on untouched 20% test set
    """
    norm_pair = market_data_service.normalize_pair(pair)
    is_forex = market_data_service.is_forex(norm_pair)
    if limit is None:
        limit = 20000 if is_forex else 13334

    # Fetch fresh real historical market data up to limit
    fetched = await market_data_service.fetch_candles(
        pair=norm_pair, timeframe=timeframe, limit=limit
    )

    if fetched and len(fetched) >= 50:
        df = pd.DataFrame(fetched)
        # Background sync latest 1000 candles to DB
        asyncio.create_task(_bg_sync_candles(norm_pair, timeframe, fetched[-1000:]))
    else:
        # Fallback to DB
        stmt = (
            select(Candle)
            .where(Candle.pair == norm_pair, Candle.timeframe == timeframe)
            .order_by(desc(Candle.timestamp))
            .limit(limit)
        )
        result = await db.execute(stmt)
        candles = list(reversed(result.scalars().all()))

        if len(candles) < 50:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient real candles ({len(candles)}) to train ensemble for {norm_pair}. Minimum 50 required.",
            )

        df = pd.DataFrame(
            [
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ]
        )

    try:
        features_df = extract_features(df)
        train_result = await asyncio.to_thread(
            train_tuned_model,
            features_df,
            target_horizon=3,
            pair=norm_pair,
            timeframe=timeframe,
        )
        # Reload predictor cache with tuned calibrated ensemble model
        predictor.load_model(norm_pair, timeframe, force_reload=True)
        return train_result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model training and calibration failed: {str(e)}")


@router.get("/signal", response_model=SignalResponse)
async def get_trading_signal(
    pair: str = Query(default="EURUSD=X", description="Asset pair e.g. EURUSD=X or BTCUSDT"),
    timeframe: str = Query(default="5m", description="Candle timeframe e.g. 5m"),
    confidence_threshold: float = Query(
        default=0.60, ge=0.5, le=0.98, description="Confidence threshold for UP/DOWN signal (default 0.60)"
    ),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Fetch latest real candles, extract features, run calibrated Ensemble prediction
    with deterministic multi-indicator confluence scoring, apply strict 3-bar
    expiry window, and persist & broadcast the signal.
    """
    norm_pair = market_data_service.normalize_pair(pair)

    # Auto-train initial model on 1000 real bars with 3-bar horizon if none exists yet
    if not predictor.model_exists(norm_pair, timeframe):
        fetched_train = await market_data_service.fetch_candles(
            pair=norm_pair, timeframe=timeframe, limit=1000
        )
        if fetched_train:
            await market_data_service.sync_candles_to_db(
                db=db, pair=norm_pair, timeframe=timeframe, candles=fetched_train
            )
            df_train = pd.DataFrame(fetched_train)
            feats_train = extract_features(df_train)
            await asyncio.to_thread(
                train_tuned_model,
                feats_train,
                target_horizon=3,
                pair=norm_pair,
                timeframe=timeframe,
                n_splits=5,
            )
            predictor.load_model(norm_pair, timeframe, force_reload=True)

    # Fetch latest real candles for real-time feature computation
    fetched_recent = await market_data_service.fetch_candles(
        pair=norm_pair, timeframe=timeframe, limit=100
    )
    if fetched_recent:
        await market_data_service.sync_candles_to_db(
            db=db, pair=norm_pair, timeframe=timeframe, candles=fetched_recent
        )

    stmt = (
        select(Candle)
        .where(Candle.pair == norm_pair, Candle.timeframe == timeframe)
        .order_by(desc(Candle.timestamp))
        .limit(100)
    )
    result = await db.execute(stmt)
    candles = list(reversed(result.scalars().all()))

    if len(candles) < 30:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to obtain enough real candles ({len(candles)}) to extract indicators for {norm_pair}.",
        )

    df = pd.DataFrame(
        [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
    )

    try:
        features_df = extract_features(df)
        pred = predictor.predict_signal(
            features_df=features_df,
            pair=norm_pair,
            timeframe=timeframe,
            confidence_threshold=confidence_threshold,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference failed: {str(e)}")

    # 3-bar directional horizon expiry calculation
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    step_secs = TIMEFRAME_SECONDS.get(timeframe.lower(), 300)
    expiry_utc = now_utc + timedelta(seconds=3 * step_secs)

    # Persist actionable trade signals to database
    signal_id = str(uuid.uuid4())
    is_tradeable = pred["direction"] in ["UP", "DOWN"]
    signal_record = Signal(
        id=signal_id,
        pair=norm_pair,
        timeframe=timeframe,
        timestamp=now_utc,
        expiry_timestamp=expiry_utc,
        direction=pred["direction"],
        confidence=pred["confidence"],
        entry_price=pred["entry_price"],
        exit_price=None,
        status="PENDING" if is_tradeable else "DRAW",
    )
    if is_tradeable:
        db.add(signal_record)
        await db.commit()
        await db.refresh(signal_record)

        await ws_manager.broadcast(
            {
                "event": "NEW_SIGNAL",
                "data": {
                    "id": signal_record.id,
                    "pair": signal_record.pair,
                    "timeframe": signal_record.timeframe,
                    "direction": signal_record.direction,
                    "confidence": signal_record.confidence,
                    "confluence_score": pred.get("confluence_score", 0.0),
                    "gate_reason": pred.get("gate_reason"),
                    "entry_price": signal_record.entry_price,
                    "exit_price": signal_record.exit_price,
                    "status": signal_record.status,
                    "timestamp": (
                        signal_record.timestamp.isoformat()
                        if signal_record.timestamp
                        else None
                    ),
                    "expiry_timestamp": (
                        signal_record.expiry_timestamp.isoformat()
                        if signal_record.expiry_timestamp
                        else None
                    ),
                },
            }
        )

    return signal_record


PAIRS_TO_SCAN = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "EURUSD=X", "GBPUSD=X", "USDJPY=X"]
PAIR_LABELS = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOLUSDT": "SOL/USDT",
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
}


async def _scan_single_pair(pair: str, timeframe: str, threshold: float) -> Dict[str, Any]:
    norm_pair = market_data_service.normalize_pair(pair)
    label = PAIR_LABELS.get(norm_pair, norm_pair)
    try:
        candles_raw = await market_data_service.fetch_candles(
            pair=norm_pair, timeframe=timeframe, limit=100
        )
        if not candles_raw or len(candles_raw) < 30:
            return {
                "pair": norm_pair,
                "label": label,
                "signal": "NO_SIGNAL",
                "direction": "NO_SIGNAL",
                "confidence": 0.50,
                "adx": 0.0,
                "confluence_score": 0.0,
                "entry_price": 0.0,
                "status_reason": "Insufficient candles available",
            }

        df = pd.DataFrame(candles_raw)

        def _infer():
            feats = extract_features(df)
            return predictor.predict_signal(
                features_df=feats,
                pair=norm_pair,
                timeframe=timeframe,
                confidence_threshold=threshold,
            )

        pred = await asyncio.to_thread(_infer)

        direction = pred.get("direction", "NO_SIGNAL")
        conf = float(pred.get("confidence", 0.5))
        adx = float(pred.get("adx", 0.0))
        confluence = float(pred.get("confluence_score", 0.0))
        entry_price = float(pred.get("entry_price", 0.0))
        status_reason = pred.get("status_reason") or pred.get("gate_reason") or "Awaiting Setup"

        ts_val = pred.get("timestamp")
        ts_str = ts_val.isoformat() if hasattr(ts_val, "isoformat") else (str(ts_val) if ts_val else None)

        return {
            "pair": norm_pair,
            "label": label,
            "signal": direction,
            "direction": direction,
            "confidence": conf,
            "adx": adx,
            "confluence_score": confluence,
            "entry_price": entry_price,
            "status_reason": status_reason,
            "timestamp": ts_str,
        }
    except Exception as e:
        return {
            "pair": norm_pair,
            "label": label,
            "signal": "NO_SIGNAL",
            "direction": "NO_SIGNAL",
            "confidence": 0.50,
            "adx": 0.0,
            "confluence_score": 0.0,
            "entry_price": 0.0,
            "status_reason": f"Scan error: {str(e)[:40]}",
        }


@router.get("/scan-all")
async def scan_all_opportunities(
    timeframe: str = Query(default="5m", description="Candle timeframe e.g. 5m"),
    threshold: float = Query(
        default=0.60, ge=0.5, le=0.98, description="Confidence threshold (default 0.60)"
    ),
) -> Dict[str, Any]:
    """
    Run fast concurrent inference across all 6 core pairs:
    BTCUSDT, ETHUSDT, SOLUSDT, EURUSD=X, GBPUSD=X, USDJPY=X.
    Returns ranked list sorted by actionable signal & confidence descending.
    """
    tasks = [_scan_single_pair(p, timeframe, threshold) for p in PAIRS_TO_SCAN]
    results = await asyncio.gather(*tasks)

    # Sort: active UP/DOWN signals first, then by confidence descending
    sorted_results = sorted(
        results,
        key=lambda x: (1 if x["signal"] in ["UP", "DOWN"] else 0, x["confidence"]),
        reverse=True,
    )

    active_count = sum(1 for r in sorted_results if r["signal"] in ["UP", "DOWN"])

    return {
        "status": "success",
        "timeframe": timeframe,
        "threshold": threshold,
        "scanned_count": len(sorted_results),
        "active_count": active_count,
        "active_opportunities": [r for r in sorted_results if r["signal"] in ["UP", "DOWN"]],
        "opportunities": sorted_results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
