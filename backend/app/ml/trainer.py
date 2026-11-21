import logging
from pathlib import Path
from typing import Any, Dict, Optional
import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    precision_score,
    recall_score,
)
from xgboost import XGBClassifier

try:
    from sklearn.frozen import FrozenEstimator
except ImportError:
    FrozenEstimator = None

# Single source of truth for features
from app.ml.features import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models_saved"


class SoftVotingEnsemble(BaseEstimator, ClassifierMixin):
    """
    Soft-voting ensemble combining pruned XGBoost and Random Forest.
    Compatible with sklearn FrozenEstimator and CalibratedClassifierCV.
    """

    def __init__(self, xgb: Optional[XGBClassifier] = None, rf: Optional[RandomForestClassifier] = None):
        self.xgb = xgb
        self.rf = rf
        self.classes_ = np.array([0, 1])

    def fit(self, X, y=None):
        return self

    def predict_proba(self, X):
        p_xgb = self.xgb.predict_proba(X)
        p_rf = self.rf.predict_proba(X)
        return 0.5 * (p_xgb + p_rf)

    def predict(self, X):
        p = self.predict_proba(X)
        return (p[:, 1] >= 0.5).astype(int)


def train_tuned_model(
    df: pd.DataFrame,
    target_horizon: int = 3,
    pair: str = "BTCUSDT",
    timeframe: str = "5m",
    models_dir: Optional[Path] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Train a heavily regularized & pruned XGBoost + RandomForest Ensemble on real market data:
    1. 3-bar directional horizon: Close(t + 3) > Close(t)
    2. Strict noise filter: drop flat dead-zone candles where |Close(t+3) - Close(t)| < 0.30 * ATR_14
    3. Strict chronological split: 70% Train : 10% Validation : 20% Test
    4. Decision Tree Pruning & Regularization:
       - Pruned XGBoost (depth=3, min_child_weight=8, gamma=0.35, subsample=0.75,
         colsample_bytree=0.75, reg_alpha=1.0, reg_lambda=2.5, lr=0.03, early_stopping=25)
       - Pruned Random Forest (n=250, depth=5, min_samples_split=15, min_samples_leaf=8,
         ccp_alpha=0.005, max_features="sqrt")
    5. Probability calibration on the 10% validation set using CalibratedClassifierCV(method='isotonic')
    6. Out-of-sample evaluation strictly on the untouched 20% test set
    """
    if models_dir is None:
        models_dir = MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    # Verify features exist
    missing_cols = set(FEATURE_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing required feature columns for training: {missing_cols}")

    work_df = df.copy()

    # 1. 3-bar directional target: 1 if Close(t + horizon) > Close(t) else 0
    work_df["close_next"] = work_df["close"].shift(-target_horizon)
    work_df["target"] = (work_df["close_next"] > work_df["close"]).astype(int)

    # 2. Strict noise filter: scale dead-zone to 0.15 * ATR for Forex, 0.25 * ATR for Crypto
    is_forex = pair.upper().endswith("=X") or any(fx in pair.upper() for fx in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"])
    atr_multiplier = 0.15 if is_forex else 0.25
    work_df["move_size"] = (work_df["close_next"] - work_df["close"]).abs()
    if "ATR_14" in work_df.columns:
        dead_zone_threshold = atr_multiplier * work_df["ATR_14"]
        pre_count = len(work_df)
        work_df = work_df[work_df["move_size"] >= dead_zone_threshold]
        post_count = len(work_df)
        logger.info(
            f"Strict noise filter ({atr_multiplier:.2f} * ATR for {'Forex' if is_forex else 'Crypto'}) removed "
            f"{pre_count - post_count} dead-zone candles ({pre_count} -> {post_count} active trending bars)"
        )

    # Drop trailing rows without horizon target and any remaining NaN
    work_df = work_df.iloc[:-target_horizon].dropna().copy()

    total_samples = len(work_df)
    if total_samples < 50:
        raise ValueError(
            f"Insufficient samples ({total_samples}) to train ensemble. At least 50 real bars required."
        )

    X = work_df[FEATURE_COLUMNS]
    y = work_df["target"]

    # 3. Strict chronological split: 70% Train : 10% Validation : 20% Test
    train_end = int(total_samples * 0.70)
    val_end = int(total_samples * 0.80)

    X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
    X_val, y_val = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
    X_test, y_test = X.iloc[val_end:], y.iloc[val_end:]

    logger.info(
        f"Training Pruned Ensemble for {pair} {timeframe} (70:10:20 Split): "
        f"{len(X_train)} train (70%), {len(X_val)} validation (10%), {len(X_test)} test (20%)"
    )

    # 4. Pruned XGBoost with early stopping on validation set
    xgb = XGBClassifier(
        max_depth=4,                  # Shallow pruned trees
        min_child_weight=5,           # Leaf weight threshold
        gamma=0.20,                   # Min loss reduction for split
        subsample=0.80,               # Row subsampling
        colsample_bytree=0.80,        # Column subsampling
        reg_alpha=0.5,                # L1 regularization
        reg_lambda=2.0,               # L2 regularization
        learning_rate=0.04,           # Fast convergence
        n_estimators=250,             # Fast ensemble
        early_stopping_rounds=25,     # Early stopping on (X_val, y_val)
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )
    xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    best_xgb_iter = int(getattr(xgb, "best_iteration", 250) or 250)
    logger.info(f"Pruned XGBoost early stopping best iteration: {best_xgb_iter}")

    # 5. Pruned Random Forest with structural pruning & multi-core n_jobs=-1
    rf = RandomForestClassifier(
        n_estimators=150,             # Optimized tree count for fast multi-core fit (~2s)
        max_depth=6,                  # Tree depth constraint
        min_samples_split=10,         # Split threshold
        min_samples_leaf=5,           # Leaf size constraint
        max_features="sqrt",
        random_state=42,
        n_jobs=-1,
    )
    rf.fit(X_train, y_train)

    # 6. Assemble Soft-Voting Ensemble
    ens = SoftVotingEnsemble(xgb=xgb, rf=rf)

    # 7. Probability Calibration on 10% Validation Set
    # Use 'sigmoid' (Platt scaling) — works reliably on small val sets (80–120 samples).
    # Isotonic regression needs 1000+ samples and collapses probabilities to ~0.5 on small sets.
    try:
        if FrozenEstimator is not None:
            calibrated_model = CalibratedClassifierCV(
                estimator=FrozenEstimator(ens),
                method="sigmoid",
            )
        else:
            calibrated_model = CalibratedClassifierCV(
                estimator=ens,
                method="sigmoid",
                cv="prefit",
            )
        calibrated_model.fit(X_val, y_val)
        logger.info("Probability calibration: sigmoid (Platt scaling) fitted on validation set.")
    except Exception as e:
        logger.warning(f"Probability calibration fallback to raw ensemble: {e}")
        calibrated_model = ens

    # 8. Evaluation on Validation Set
    val_pred = calibrated_model.predict(X_val)
    val_accuracy = float(accuracy_score(y_val, val_pred))

    # 9. Out-of-sample evaluation strictly on untouched 20% Test Set
    test_pred = calibrated_model.predict(X_test)
    test_prob = calibrated_model.predict_proba(X_test)

    test_accuracy = float(accuracy_score(y_test, test_pred))
    precision = float(precision_score(y_test, test_pred, zero_division=0))
    recall = float(recall_score(y_test, test_pred, zero_division=0))
    brier = float(brier_score_loss(y_test, test_prob[:, 1])) if len(test_prob.shape) > 1 else 0.0

    # 10. High-Confidence Precision Scoring on untouched Test Set (>= 78% or top 15%)
    p_up_test = test_prob[:, 1]
    confidence_test = np.maximum(p_up_test, 1.0 - p_up_test)

    high_conf_mask_abs = confidence_test >= 0.78
    top_15pct_cutoff = np.percentile(confidence_test, 85)
    high_conf_mask_pct = confidence_test >= top_15pct_cutoff

    high_conf_mask = (
        high_conf_mask_abs
        if high_conf_mask_abs.sum() >= high_conf_mask_pct.sum()
        else high_conf_mask_pct
    )

    high_conf_sample_count = int(high_conf_mask.sum())
    if high_conf_sample_count >= 5:
        hc_pred = (p_up_test[high_conf_mask] >= 0.5).astype(int)
        hc_true = np.array(y_test)[high_conf_mask]
        high_confidence_accuracy = float(accuracy_score(hc_true, hc_pred))
    else:
        high_confidence_accuracy = test_accuracy

    logger.info(
        f"Pruned Ensemble 70:10:20 Results for {pair}: Val Acc={val_accuracy:.4f} | "
        f"Test Acc={test_accuracy:.4f} | High-Conf Precision={high_confidence_accuracy:.4f} "
        f"({high_conf_sample_count} signals)"
    )

    # 11. Save Model Artifact
    pair_clean = pair.upper().replace("/", "").replace("=", "_")
    model_filename = f"ensemble_{pair_clean}_{timeframe}.joblib"
    model_path = models_dir / model_filename

    artifact = {
        "model": calibrated_model,
        "base_model": ens,
        "feature_columns": FEATURE_COLUMNS,
        "pair": pair_clean,
        "raw_pair": pair,
        "timeframe": timeframe,
        "target_horizon": target_horizon,
        "split_ratio": "70:10:20",
        "best_iteration_xgb": best_xgb_iter,
        "metrics": {
            "val_accuracy": round(val_accuracy, 4),
            "test_accuracy": round(test_accuracy, 4),
            "accuracy": round(test_accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "brier_score": round(brier, 4),
            "high_confidence_accuracy": round(high_confidence_accuracy, 4),
            "high_confidence_sample_count": high_conf_sample_count,
        },
        "sample_counts": {
            "total": total_samples,
            "train": len(X_train),
            "val": len(X_val),
            "test": len(X_test),
        },
    }

    joblib.dump(artifact, model_path)

    # Also save as xgboost_... for backward compatibility
    legacy_path = models_dir / f"xgboost_{pair_clean}_{timeframe}.joblib"
    joblib.dump(artifact, legacy_path)

    return {
        "status": "success",
        "pair": pair_clean,
        "timeframe": timeframe,
        "target_horizon": target_horizon,
        "split_ratio": "70:10:20",
        "model_file": model_filename,
        "model_path": str(model_path),
        "total_bars": total_samples,
        "train_samples": len(X_train),
        "val_samples": len(X_val),
        "test_samples": len(X_test),
        "val_accuracy": round(val_accuracy, 4),
        "test_accuracy": round(test_accuracy, 4),
        "accuracy": round(test_accuracy, 4),  # Standard key
        "cv_accuracy": round(val_accuracy, 4),  # Backwards compatibility for UI
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "brier_score": round(brier, 4),
        "high_confidence_accuracy": round(high_confidence_accuracy, 4),
        "high_confidence_sample_count": high_conf_sample_count,
        "best_iteration_xgb": best_xgb_iter,
    }
