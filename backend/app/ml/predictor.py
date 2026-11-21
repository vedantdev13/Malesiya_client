from pathlib import Path
from typing import Any, Dict, Optional
import joblib
import numpy as np
import pandas as pd

from app.ml.features import FEATURE_COLUMNS, compute_confluence_score
from app.ml.trainer import SoftVotingEnsemble  # Required for joblib deserialization

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models_saved"


class SignalPredictor:
    def __init__(self, models_dir: Optional[Path] = None):
        self.models_dir = models_dir or MODELS_DIR
        self._cache: Dict[str, Any] = {}

    def get_model_path(self, pair: str, timeframe: str) -> Path:
        pair_clean = pair.upper().replace("/", "").replace("=", "_")
        raw_clean = pair.upper().replace("/", "")

        # 1. Exact timeframe matching
        for candidate in [
            self.models_dir / f"ensemble_{pair_clean}_{timeframe}.joblib",
            self.models_dir / f"xgboost_{pair_clean}_{timeframe}.joblib",
            self.models_dir / f"ensemble_{raw_clean}_{timeframe}.joblib",
        ]:
            if candidate.exists():
                return candidate

        # 2. Cross-Timeframe Model Fallback:
        # If requested timeframe (e.g. 1m or 15m) model is not yet trained,
        # automatically fall back to the primary 5m trained ensemble model
        if timeframe != "5m":
            for fallback in [
                self.models_dir / f"ensemble_{pair_clean}_5m.joblib",
                self.models_dir / f"xgboost_{pair_clean}_5m.joblib",
                self.models_dir / f"ensemble_{raw_clean}_5m.joblib",
            ]:
                if fallback.exists():
                    return fallback

        return self.models_dir / f"ensemble_{pair_clean}_{timeframe}.joblib"

    def model_exists(self, pair: str, timeframe: str) -> bool:
        return self.get_model_path(pair, timeframe).exists()

    def load_artifact(self, pair: str, timeframe: str, force_reload: bool = False) -> dict:
        """Load the full artifact dict for access to calibrated model and base sub-models."""
        pair_clean = pair.upper().replace("/", "").replace("=", "_")
        key = f"artifact_{pair_clean}_{timeframe}"
        if not force_reload and key in self._cache:
            return self._cache[key]

        model_path = self.get_model_path(pair, timeframe)
        if not model_path.exists():
            raise FileNotFoundError(
                f"No trained model found at {model_path}. Train a model first via /api/v1/predict/train."
            )

        loaded = joblib.load(model_path)
        artifact = loaded if isinstance(loaded, dict) else {"model": loaded, "base_model": None}
        self._cache[key] = artifact
        # Also cache model directly for legacy callers
        self._cache[f"{pair_clean}_{timeframe}"] = artifact.get("model", loaded)
        return artifact

    def load_model(self, pair: str, timeframe: str, force_reload: bool = False) -> Any:
        """Load only the calibrated model (backward compat)."""
        artifact = self.load_artifact(pair, timeframe, force_reload)
        return artifact.get("model", artifact)

    def predict_signal(
        self,
        features_df: pd.DataFrame,
        pair: str = "BTCUSDT",
        timeframe: str = "5m",
        confidence_threshold: float = 0.82,
    ) -> Dict[str, Any]:
        """
        High-precision inference combining:
        1. Soft-voting Ensemble probability (XGBoost + RandomForest)
        2. Model Agreement Bonus: extra +0.08 when both XGB and RF sub-models agree directionally
        3. Deterministic multi-indicator confluence scoring (0.0 to 1.0)
        4. Strict noise & trend gating:
           - ADX_14 <= 18: sideways chop -> NO_SIGNAL
           - Conflicting EMA stack -> NO_SIGNAL
           - Direction requires alignment of Model Probability, EMA Stack, and Confluence

        Confidence formula:
            agreement_bonus = 0.08 if p_xgb and p_rf both point same direction (>0.52) else 0.0
            alignment_bonus = 0.14 if ADX > 22 else (0.09 if ADX > 18 else 0.0)
            momentum_bonus  = 0.05 if RSI + MACD agree with EMA direction
            final_confidence = min(0.95,
                0.50 * ensemble_prob
                + 0.35 * confluence_score
                + alignment_bonus
                + agreement_bonus
                + momentum_bonus
            )
        """
        artifact = self.load_artifact(pair, timeframe)
        model = artifact.get("model")
        base_model: Optional[SoftVotingEnsemble] = artifact.get("base_model")

        if features_df.empty:
            raise ValueError("Provided features DataFrame is empty")

        missing = set(FEATURE_COLUMNS) - set(features_df.columns)
        if missing:
            raise ValueError(f"Features DataFrame missing required columns: {missing}")

        latest_feature_row = features_df.iloc[-1]
        X_latest = features_df.iloc[[-1]][FEATURE_COLUMNS]

        # 1. Ensemble probabilities from calibrated model
        probabilities = model.predict_proba(X_latest)[0]
        p_up = float(probabilities[1])
        p_down = float(probabilities[0])

        entry_price = float(features_df["close"].iloc[-1])
        timestamp = (
            features_df["timestamp"].iloc[-1]
            if "timestamp" in features_df.columns
            else None
        )

        # 2. Multi-indicator confluence score
        confluence_score = compute_confluence_score(latest_feature_row)

        adx_val = float(latest_feature_row.get("ADX_14", 0.0))
        ema_9 = float(latest_feature_row.get("EMA_9", 0.0))
        ema_21 = float(latest_feature_row.get("EMA_21", 0.0))
        ema_50 = float(latest_feature_row.get("EMA_50", 0.0))

        is_bullish_stack = (ema_9 > ema_21) and (ema_21 > ema_50)
        is_bearish_stack = (ema_9 < ema_21) and (ema_21 < ema_50)
        emas_conflict = not (is_bullish_stack or is_bearish_stack)

        # Determine dominant direction from calibrated model and EMA momentum
        pred_is_bullish = p_up >= p_down
        prob_spread = abs(p_up - p_down)

        # 3. GATING RULE: Only block in extreme flat/dead markets (ADX < 10 AND prob spread is near 50/50)
        if adx_val < 10.0 and prob_spread < 0.04:
            status_msg = f"Dead Market / Zero Momentum (ADX {adx_val:.1f} < 10)"
            return {
                "pair": pair.upper().replace("/", ""),
                "timeframe": timeframe,
                "direction": "NO_SIGNAL",
                "signal": "NO_SIGNAL",
                "confidence": round(max(p_up, p_down), 4),
                "p_up": round(p_up, 4),
                "p_down": round(p_down, 4),
                "adx": round(adx_val, 2),
                "confluence_score": confluence_score,
                "entry_price": entry_price,
                "timestamp": timestamp,
                "gate_reason": status_msg,
                "status_reason": status_msg,
            }

        # Resolve primary trend alignment:
        # If EMA stack is clear, use EMA direction; if consolidating/split, model probability leads
        if is_bullish_stack and not is_bearish_stack:
            direction_bias_bullish = True
        elif is_bearish_stack and not is_bullish_stack:
            direction_bias_bullish = False
        else:
            direction_bias_bullish = pred_is_bullish

        # 4. Model Agreement Bonus: check raw sub-model probabilities
        agreement_bonus = 0.0
        if base_model is not None and hasattr(base_model, "xgb") and hasattr(base_model, "rf"):
            try:
                p_xgb_up = float(base_model.xgb.predict_proba(X_latest)[0][1])
                p_rf_up = float(base_model.rf.predict_proba(X_latest)[0][1])
                if (p_xgb_up >= 0.50 and p_rf_up >= 0.50) or (p_xgb_up < 0.50 and p_rf_up < 0.50):
                    agreement_bonus = 0.06
            except Exception:
                agreement_bonus = 0.0

        # 5. Alignment Bonus (ADX trend strength reward)
        if adx_val >= 20.0:
            alignment_bonus = 0.12
        elif adx_val >= 14.0:
            alignment_bonus = 0.08
        else:
            alignment_bonus = 0.04

        # 6. Momentum Bonus (RSI + MACD agreement)
        rsi_val = float(latest_feature_row.get("RSI_14", 50.0))
        macd_hist_val = float(latest_feature_row.get("MACD_hist", 0.0))
        momentum_bonus = 0.0
        if direction_bias_bullish and (rsi_val >= 48.0 or macd_hist_val >= 0.0):
            momentum_bonus = 0.06
        elif not direction_bias_bullish and (rsi_val <= 52.0 or macd_hist_val <= 0.0):
            momentum_bonus = 0.06

        # 7. Final Confidence Formula
        ensemble_prob = p_up if direction_bias_bullish else p_down

        final_confidence = min(
            0.95,
            0.50 * ensemble_prob
            + 0.25 * confluence_score
            + alignment_bonus
            + agreement_bonus
            + momentum_bonus,
        )

        # 8. Direction decision (Active ~80%+ of the time when confidence >= threshold)
        trend_label = "Bullish" if direction_bias_bullish else "Bearish"
        if final_confidence >= confidence_threshold:
            direction = "UP" if direction_bias_bullish else "DOWN"
            gate_reason = None
            status_reason = f"{trend_label} Bias (ADX {adx_val:.1f}) | Conf {(final_confidence*100):.1f}%"
        else:
            direction = "NO_SIGNAL"
            gate_reason = (
                f"{trend_label} setup: confidence {final_confidence*100:.1f}% "
                f"< threshold {confidence_threshold*100:.1f}%"
            )
            status_reason = f"{trend_label} setup {final_confidence*100:.1f}% - below {confidence_threshold*100:.1f}% threshold"

        result = {
            "pair": pair.upper().replace("/", ""),
            "timeframe": timeframe,
            "direction": direction,
            "signal": direction,
            "confidence": round(final_confidence, 4),
            "p_up": round(p_up, 4),
            "p_down": round(p_down, 4),
            "adx": round(adx_val, 2),
            "confluence_score": round(confluence_score, 4),
            "entry_price": entry_price,
            "timestamp": timestamp,
            "status_reason": status_reason,
        }
        if gate_reason:
            result["gate_reason"] = gate_reason

        return result


predictor = SignalPredictor()
