"""
Model definitions and exports for ML pipeline.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from app.ml.trainer import (
    FEATURE_COLUMNS,
    MODELS_DIR,
    train_tuned_model,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def train_binary_model(
    df: pd.DataFrame,
    target_horizon: int = 1,
    pair: str = "BTCUSDT",
    timeframe: str = "1m",
    models_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible training function wrapping the advanced hyperparameter-tuned
    and calibrated trainer.
    """
    return train_tuned_model(
        df=df,
        target_horizon=target_horizon,
        pair=pair,
        timeframe=timeframe,
        n_iter_search=10,
        models_dir=models_dir,
    )
