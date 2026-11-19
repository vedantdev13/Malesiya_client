import numpy as np
import pandas as pd
from typing import Any, List, Tuple

# Institutional-grade feature column set for model training & inference
FEATURE_COLUMNS: List[str] = [
    # Trend Strength & Direction (ADX System)
    "ADX_14",
    "Plus_DI_14",
    "Minus_DI_14",
    # Moving Averages & Trend Alignment
    "EMA_9",
    "EMA_21",
    "EMA_50",
    "EMA_9_21_Spread",
    "EMA_21_50_Spread",
    "Trend_Align",
    # Momentum & Oscillators
    "RSI_14",
    "RSI_7",
    "Stoch_K",
    "Stoch_D",
    "MACD",
    "MACD_hist",
    "MACD_Hist_Slope",
    # Volatility & Bollinger Bands
    "ATR_14",
    "BB_upper",
    "BB_lower",
    "BB_pct",
    "BB_Width",
    # Volume & Price Action
    "volume_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "body_ratio",
    # Multi-bar Log Returns
    "Return_1",
    "Return_3",
    "Return_5",
]


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute Relative Strength Index using Wilder's exponential smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Average Directional Index (ADX) along with +DI and -DI (14-period).
    Measures trend strength to filter out sideways/choppy market noise.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # True Range
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    # Wilder's Smoothing
    tr_smoothed = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_dm_smoothed = (
        pd.Series(plus_dm, index=high.index)
        .ewm(alpha=1.0 / period, min_periods=period, adjust=False)
        .mean()
    )
    minus_dm_smoothed = (
        pd.Series(minus_dm, index=high.index)
        .ewm(alpha=1.0 / period, min_periods=period, adjust=False)
        .mean()
    )

    plus_di = 100.0 * (plus_dm_smoothed / (tr_smoothed + 1e-10))
    minus_di = 100.0 * (minus_dm_smoothed / (tr_smoothed + 1e-10))

    di_diff = (plus_di - minus_di).abs()
    di_sum = plus_di + minus_di + 1e-10
    dx = 100.0 * (di_diff / di_sum)

    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx, plus_di, minus_di


def compute_confluence_score(row: Any) -> float:
    """
    Deterministic multi-indicator confluence score between 0.0 and 1.0.

    Requirements:
    - Strong trend: ADX_14 > 22
    - Clean EMA stack: EMA_9 > EMA_21 > EMA_50 (bullish) or EMA_9 < EMA_21 < EMA_50 (bearish)
    - RSI & MACD momentum agreement:
        Bullish: RSI_14 > 50 and MACD_hist > 0
        Bearish: RSI_14 < 50 and MACD_hist < 0
    - If ADX_14 <= 20 (choppy market) or EMAs conflict, CAP confluence score at <= 0.45.
    """
    def _get(key: str, default: float = 0.0) -> float:
        if isinstance(row, dict):
            val = row.get(key, default)
        elif hasattr(row, "get"):
            val = row.get(key, default)
        else:
            try:
                val = row[key]
            except Exception:
                val = default
        try:
            return float(val) if val is not None and not pd.isna(val) else default
        except Exception:
            return default

    adx = _get("ADX_14", 0.0)
    ema_9 = _get("EMA_9", 0.0)
    ema_21 = _get("EMA_21", 0.0)
    ema_50 = _get("EMA_50", 0.0)
    rsi_14 = _get("RSI_14", 50.0)
    macd_hist = _get("MACD_hist", _get("MACD_Hist", 0.0))
    stoch_k = _get("Stoch_K", 0.5)

    is_bullish_stack = (ema_9 > ema_21) and (ema_21 > ema_50)
    is_bearish_stack = (ema_9 < ema_21) and (ema_21 < ema_50)
    emas_conflict = not (is_bullish_stack or is_bearish_stack)

    # STRICT RULE: Choppy market (ADX <= 18) or conflicting EMAs capped at <= 0.45
    if adx <= 18.0 or emas_conflict:
        chop_score = min(0.45, 0.15 + (adx / 100.0))
        return round(float(chop_score), 4)

    # Directional regime with ADX > 18 and clean aligned EMA stack
    score = 0.0

    # 1. Trend strength (ADX) - up to 0.35
    if adx >= 24.0:
        score += 0.35
    elif adx >= 20.0:
        score += 0.28
    else:  # 18 < adx < 20
        score += 0.20

    # 2. Clean EMA stack alignment - 0.30
    if is_bullish_stack or is_bearish_stack:
        score += 0.30

    # 3. RSI / MACD Momentum agreement - up to 0.25
    if is_bullish_stack:
        if rsi_14 > 52.0 and macd_hist > 0.0:
            score += 0.25
        elif rsi_14 > 50.0 or macd_hist > 0.0:
            score += 0.12
    elif is_bearish_stack:
        if rsi_14 < 48.0 and macd_hist < 0.0:
            score += 0.25
        elif rsi_14 < 50.0 or macd_hist < 0.0:
            score += 0.12

    # 4. Stochastic RSI confirmation - up to 0.10
    if is_bullish_stack and (0.20 <= stoch_k <= 0.85):
        score += 0.10
    elif is_bearish_stack and (0.15 <= stoch_k <= 0.80):
        score += 0.10

    return round(float(min(1.0, max(0.0, score))), 4)


def extract_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute institutional-grade technical indicator feature matrix from OHLCV DataFrame.

    Indicators computed:
    1. EMA_9, EMA_21, EMA_50 & Spreads
    2. ADX_14, Plus_DI_14, Minus_DI_14 (Trend strength filter)
    3. RSI_14, RSI_7, Stoch_K, Stoch_D
    4. MACD, MACD_Signal, MACD_hist, MACD_Hist_Slope
    5. ATR_14, Bollinger Bands (BB_upper, BB_lower, BB_pct, BB_Width)
    6. volume_ratio (rel volume vs 20-period mean)
    7. Price action: upper_wick_ratio, lower_wick_ratio, body_ratio
    8. Confluence score (deterministic 0.0 - 1.0)
    9. Log Returns (Return_1, Return_3, Return_5)
    """
    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(set(df.columns)):
        missing = required_cols - set(df.columns)
        raise ValueError(f"DataFrame is missing required columns: {missing}")

    feat = df.copy()

    # Ensure numeric float types
    for col in ["open", "high", "low", "close", "volume"]:
        feat[col] = pd.to_numeric(feat[col], errors="coerce")

    close = feat["close"]
    open_p = feat["open"]
    high = feat["high"]
    low = feat["low"]
    volume = feat["volume"]

    # 1. Moving Averages: EMA_9, EMA_21, EMA_50
    ema_9 = close.ewm(span=9, adjust=False).mean()
    ema_21 = close.ewm(span=21, adjust=False).mean()
    ema_50 = close.ewm(span=50, adjust=False).mean()

    feat["EMA_9"] = ema_9
    feat["EMA_21"] = ema_21
    feat["EMA_50"] = ema_50
    feat["EMA_9_21_Spread"] = (ema_9 - ema_21) / (close + 1e-10)
    feat["EMA_21_50_Spread"] = (ema_21 - ema_50) / (close + 1e-10)

    # Trend alignment: 1.0 Bullish (9 > 21 > 50), -1.0 Bearish (9 < 21 < 50), 0.0 Mixed
    bullish_stack = (ema_9 > ema_21) & (ema_21 > ema_50)
    bearish_stack = (ema_9 < ema_21) & (ema_21 < ema_50)
    feat["Trend_Align"] = np.where(bullish_stack, 1.0, np.where(bearish_stack, -1.0, 0.0))

    # 2. Trend Strength (ADX 14)
    adx, plus_di, minus_di = compute_adx(high, low, close, period=14)
    feat["ADX_14"] = adx
    feat["Plus_DI_14"] = plus_di
    feat["Minus_DI_14"] = minus_di

    # 3. Momentum & Oscillators (RSI 14, RSI 7, Stochastic RSI)
    rsi_14 = compute_rsi(close, period=14)
    feat["RSI_14"] = rsi_14
    feat["RSI_7"] = compute_rsi(close, period=7)

    min_rsi = rsi_14.rolling(window=14).min()
    max_rsi = rsi_14.rolling(window=14).max()
    stoch_rsi = (rsi_14 - min_rsi) / (max_rsi - min_rsi + 1e-10)
    feat["Stoch_K"] = stoch_rsi.rolling(window=3).mean()
    feat["Stoch_D"] = feat["Stoch_K"].rolling(window=3).mean()

    # 4. MACD & Momentum Acceleration
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    feat["MACD"] = ema_12 - ema_26
    feat["MACD_Signal"] = feat["MACD"].ewm(span=9, adjust=False).mean()
    macd_hist = feat["MACD"] - feat["MACD_Signal"]
    feat["MACD_hist"] = macd_hist
    feat["MACD_Hist"] = macd_hist  # Alias for backward compatibility
    feat["MACD_Hist_Slope"] = macd_hist - macd_hist.shift(1)

    # 5. Volatility (ATR 14, Bollinger Bands)
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    feat["ATR_14"] = true_range.ewm(alpha=1.0 / 14, min_periods=14, adjust=False).mean()

    sma_20 = close.rolling(window=20).mean()
    std_20 = close.rolling(window=20).std()
    bb_upper = sma_20 + (2.0 * std_20)
    bb_lower = sma_20 - (2.0 * std_20)
    feat["BB_upper"] = bb_upper
    feat["BB_lower"] = bb_lower
    feat["BB_pct"] = (close - bb_lower) / (bb_upper - bb_lower + 1e-10)
    feat["BB_Width"] = (bb_upper - bb_lower) / (sma_20 + 1e-10)

    # 6. Volume Ratio
    vol_mean_20 = volume.rolling(window=20).mean()
    volume_ratio = volume / (vol_mean_20 + 1e-8)
    feat["volume_ratio"] = volume_ratio
    feat["Rel_Volume"] = volume_ratio  # Alias

    # 7. Price Action / Candle Rejection Ratios
    total_range = high - low
    safe_range = total_range + 1e-8
    upper_bound = np.maximum(open_p, close)
    lower_bound = np.minimum(open_p, close)

    upper_wick = (high - upper_bound) / safe_range
    lower_wick = (lower_bound - low) / safe_range
    body_ratio = (close - open_p).abs() / safe_range

    feat["upper_wick_ratio"] = upper_wick
    feat["lower_wick_ratio"] = lower_wick
    feat["body_ratio"] = body_ratio
    # Aliases
    feat["Upper_Wick_Ratio"] = upper_wick
    feat["Lower_Wick_Ratio"] = lower_wick
    feat["Body_Range_Ratio"] = body_ratio

    # 8. Multi-bar Log Returns
    feat["Return_1"] = np.log(close / (close.shift(1) + 1e-10))
    feat["Return_3"] = np.log(close / (close.shift(3) + 1e-10))
    feat["Return_5"] = np.log(close / (close.shift(5) + 1e-10))

    # 9. Confluence Score per row (vectorized for instant execution on 25,000+ bars)
    adx_arr = feat["ADX_14"].values
    ema_9_arr = feat["EMA_9"].values
    ema_21_arr = feat["EMA_21"].values
    ema_50_arr = feat["EMA_50"].values
    rsi_14_arr = feat["RSI_14"].values
    macd_hist_arr = feat["MACD_hist"].values
    stoch_k_arr = feat["Stoch_K"].values

    is_bullish = (ema_9_arr > ema_21_arr) & (ema_21_arr > ema_50_arr)
    is_bearish = (ema_9_arr < ema_21_arr) & (ema_21_arr < ema_50_arr)
    emas_conflict = ~(is_bullish | is_bearish)
    is_chop = (adx_arr <= 18.0) | emas_conflict

    c_score = np.where(adx_arr >= 24.0, 0.35, np.where(adx_arr >= 20.0, 0.28, 0.20))
    c_score += np.where(is_bullish | is_bearish, 0.30, 0.0)

    bull_mom = np.where(
        is_bullish & (rsi_14_arr > 52.0) & (macd_hist_arr > 0.0),
        0.25,
        np.where(is_bullish & ((rsi_14_arr > 50.0) | (macd_hist_arr > 0.0)), 0.12, 0.0),
    )
    bear_mom = np.where(
        is_bearish & (rsi_14_arr < 48.0) & (macd_hist_arr < 0.0),
        0.25,
        np.where(is_bearish & ((rsi_14_arr < 50.0) | (macd_hist_arr < 0.0)), 0.12, 0.0),
    )
    c_score += bull_mom + bear_mom

    stoch_score = np.where(
        (is_bullish & (stoch_k_arr >= 0.20) & (stoch_k_arr <= 0.85))
        | (is_bearish & (stoch_k_arr >= 0.15) & (stoch_k_arr <= 0.80)),
        0.10,
        0.0,
    )
    c_score += stoch_score
    c_score = np.clip(c_score, 0.0, 1.0)

    chop_score = np.minimum(0.45, 0.15 + (adx_arr / 100.0))
    feat["confluence_score"] = np.round(np.where(is_chop, chop_score, c_score), 4)

    # Cleanly drop lookback NaNs
    cleaned_feat = feat.dropna().copy()
    return cleaned_feat
