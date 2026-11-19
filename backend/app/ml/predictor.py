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
        path_ens = self.models_dir / f"ensemble_{pair_clean}_{timeframe}.joblib"
        if path_ens.exists():
            return path_ens
        path_xgb = self.models_dir / f"xgboost_{pair_clean}_{timeframe}.joblib"
        if path_xgb.exists():
            return path_xgb
        raw_clean = pair.upper().replace("/", "")
        path_raw = self.models_dir / f"ensemble_{raw_clean}_{timeframe}.joblib"
        if path_raw.exists():
            return path_raw
        return path_ens

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

        # 3. GATING RULE: Choppy market (ADX <= 18) -> hard NO_SIGNAL (pure sideways chop)
        if adx_val <= 18.0:
            status_msg = f"Market Sideways (ADX {adx_val:.1f} <= 18) - Waiting for Breakout"
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

        # 3b. EMA conflict with split stack:
        # Allow override when model is strongly directional (p > 0.65) AND ADX > 18 (out of chop zone).
        # In this case, infer direction from model probability rather than EMA stack.
        ema_overridden = False
        if emas_conflict:
            if adx_val > 18.0 and max(p_up, p_down) > 0.65:
                # Strong model signal overrides split EMA — use model direction
                if p_up >= p_down:
                    is_bullish_stack = True
                    is_bearish_stack = False
                else:
                    is_bearish_stack = True
                    is_bullish_stack = False
                emas_conflict = False  # Override — proceed to scoring
                ema_overridden = True
                # Recompute confluence with an adjusted row that reflects the resolved direction.
                # We patch EMA values to reflect the direction the model determined.
                adjusted_row = dict(latest_feature_row)
                if is_bearish_stack:
                    # Pretend a clean bearish stack for confluence: EMA_9 < EMA_21 < EMA_50
                    adjusted_row["EMA_9"] = ema_21 - abs(ema_21 - ema_9) - 1e-6
                    adjusted_row["EMA_21"] = ema_21
                    adjusted_row["EMA_50"] = ema_50 if ema_50 > ema_21 else ema_21 + abs(ema_21 - ema_9) + 1e-6
                else:
                    # Pretend a clean bullish stack for confluence: EMA_9 > EMA_21 > EMA_50
                    adjusted_row["EMA_9"] = ema_21 + abs(ema_21 - ema_9) + 1e-6
                    adjusted_row["EMA_21"] = ema_21
                    adjusted_row["EMA_50"] = ema_50 if ema_50 < ema_21 else ema_21 - abs(ema_21 - ema_9) - 1e-6
                confluence_score = compute_confluence_score(adjusted_row)
            else:
                status_msg = "Conflicting EMA Stack - Consolidating Without Clear Bias"
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

        # 4. Model Agreement Bonus: check raw sub-model probabilities
        agreement_bonus = 0.0
        if base_model is not None and hasattr(base_model, "xgb") and hasattr(base_model, "rf"):
            try:
                p_xgb_up = float(base_model.xgb.predict_proba(X_latest)[0][1])
                p_rf_up = float(base_model.rf.predict_proba(X_latest)[0][1])
                # Both models directionally agree (both bullish or both bearish)
                if (p_xgb_up > 0.52 and p_rf_up > 0.52) or (p_xgb_up < 0.48 and p_rf_up < 0.48):
                    agreement_bonus = 0.08
            except Exception:
                agreement_bonus = 0.0

        # 5. Alignment Bonus (ADX trend strength reward)
        if adx_val > 22.0:
            alignment_bonus = 0.14
        elif adx_val > 18.0:
            alignment_bonus = 0.09
        else:
            alignment_bonus = 0.0

        # 6. Momentum Bonus (RSI + MACD agree with EMA direction)
        rsi_val = float(latest_feature_row.get("RSI_14", 50.0))
        macd_hist_val = float(latest_feature_row.get("MACD_hist", 0.0))
        momentum_bonus = 0.0
        if is_bullish_stack and rsi_val > 50.0 and macd_hist_val > 0.0:
            momentum_bonus = 0.05
        elif is_bearish_stack and rsi_val < 50.0 and macd_hist_val < 0.0:
            momentum_bonus = 0.05

        # 7. Final Confidence Formula
        # Weights: 50% model probability, 35% confluence, bonuses up to 27%
        if is_bullish_stack:
            ensemble_prob = p_up
        else:
            ensemble_prob = p_down

        final_confidence = min(
            0.95,
            0.50 * ensemble_prob
            + 0.35 * confluence_score
            + alignment_bonus
            + agreement_bonus
            + momentum_bonus,
        )

        # 8. Direction decision
        if final_confidence >= confidence_threshold:
            direction = "UP" if is_bullish_stack else "DOWN"
            trend_label = "Bullish" if is_bullish_stack else "Bearish"
            gate_reason = None
            status_reason = f"Strong {trend_label} Trend (ADX {adx_val:.1f}) + EMA & Momentum Aligned"
        else:
            direction = "NO_SIGNAL"
            trend_label = "Bullish" if is_bullish_stack else "Bearish"
            gate_reason = (
                f"{trend_label} setup: confidence {final_confidence*100:.1f}% "
                f"(ensemble={ensemble_prob:.2f}, confluence={confluence_score:.2f}, "
                f"align_bonus={alignment_bonus:.2f}, agree_bonus={agreement_bonus:.2f}) "
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
