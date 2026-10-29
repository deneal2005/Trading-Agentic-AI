"""Direction-probability model with walk-forward evaluation.

Purpose: test whether a simple, regularised classifier predicts next-bar
direction better than naive baselines out of sample. Models that do not
beat the baselines are rejected and never used for decisions.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..strategies.indicators import atr, rsi, sma

BASE_FEATURES = ["r1", "r5", "r20", "vol20", "rsi14", "dist_sma50", "atr_pct"]
# from the research strategies (knowledge/strategies): 6/12-month momentum, 200-day trend,
# volatility regime (Moreira & Muir), turn-of-month window
RESEARCH_FEATURES = ["mom_126", "mom_252_21", "dist_sma200", "vol_ratio", "tom"]
FEATURES = BASE_FEATURES + RESEARCH_FEATURES


def make_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Features at t use bars <= t. Label y_t = 1 if close[t+1] > close[t]."""
    from ..strategies.library import TurnOfMonth

    c = bars["close"]
    lr = np.log(c).diff()
    X = pd.DataFrame(index=bars.index)
    X["r1"] = lr
    X["r5"] = np.log(c).diff(5)
    X["r20"] = np.log(c).diff(20)
    X["vol20"] = lr.rolling(20).std()
    X["rsi14"] = rsi(c, 14) / 100
    X["dist_sma50"] = c / sma(c, 50) - 1
    X["atr_pct"] = atr(bars, 14) / c
    X["mom_126"] = np.log(c).diff(126)
    X["mom_252_21"] = np.log(c.shift(21) / c.shift(252))
    X["dist_sma200"] = c / sma(c, 200) - 1
    X["vol_ratio"] = X["vol20"] / lr.rolling(120).std()
    X["tom"] = TurnOfMonth().signals(bars)["target"].astype(float)  # 1 = next bar is in the window
    X["y"] = (c.shift(-1) > c).astype(float).where(c.shift(-1).notna())
    return X


@dataclass
class ModelEval:
    n_folds: int
    n_predictions: int
    model: dict
    baselines: dict
    calibration: list[dict]
    verdict: str
    reasons: list[str]


def _scores(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"accuracy": float(((p > 0.5) == y).mean()), "log_loss": float(log_loss(y, p, labels=[0, 1])),
            "brier": float(brier_score_loss(y, p))}


MLP_LAYERS = (16, 8)


def make_model(kind: str = "logit", C: float = 0.1):
    if kind == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=500))
    if kind == "mlp":  # small tanh network, L2-regularised, early stopping on a split of the training window only
        return make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=MLP_LAYERS, activation="tanh", alpha=1e-2,
                                                             max_iter=500, early_stopping=True, random_state=0))
    raise ValueError(f"unknown model kind {kind}")


def walk_forward_eval(bars: pd.DataFrame, train_bars: int = 750, test_bars: int = 250, C: float = 0.1,
                      kind: str = "logit", features: list[str] | None = None) -> tuple[ModelEval, object]:
    features = features or FEATURES
    X = make_features(bars).dropna()
    preds, ys, prior_p, persist_p = [], [], [], []
    start, folds, model = 0, 0, None
    while start + train_bars + test_bars <= len(X):
        tr = X.iloc[start:start + train_bars]
        te = X.iloc[start + train_bars:start + train_bars + test_bars]
        model = make_model(kind, C)
        model.fit(tr[features], tr["y"])
        preds.append(model.predict_proba(te[features])[:, 1])
        ys.append(te["y"].to_numpy())
        prior_p.append(np.full(len(te), tr["y"].mean()))                 # base rate from train
        persist_p.append(np.where(te["r1"] > 0, 0.55, 0.45))             # naive momentum
        start += test_bars
        folds += 1
    if not folds:
        raise ValueError("not enough data for model walk-forward")
    y, p = np.concatenate(ys), np.concatenate(preds)
    m = _scores(y, p)
    bl = {"base_rate": _scores(y, np.concatenate(prior_p)), "persistence": _scores(y, np.concatenate(persist_p))}
    bins = np.linspace(0, 1, 6)
    cal = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (p >= lo) & (p < hi)
        if mask.sum():
            cal.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(mask.sum()), "mean_pred": float(p[mask].mean()),
                        "observed": float(y[mask].mean())})
    reasons = []
    best_ll = min(b["log_loss"] for b in bl.values())
    if m["log_loss"] >= best_ll:
        reasons.append(f"log loss {m['log_loss']:.4f} does not beat best baseline {best_ll:.4f}")
    # Accuracy edge must exceed ~2 standard errors of a coin flip.
    se = 0.5 / np.sqrt(len(y))
    if m["accuracy"] - 0.5 < 2 * se:
        reasons.append(f"accuracy {m['accuracy']:.3f} within 2 s.e. ({2 * se:.3f}) of 0.5")
    verdict = "accepted" if not reasons else "rejected"
    return ModelEval(folds, len(y), m, bl, cal, verdict, reasons), model


class ModelRegistry:
    """Versioned model artifacts with metadata; older versions are kept."""

    def __init__(self, models_dir: Path):
        self.dir = models_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, model, meta: dict) -> str:
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        d = self.dir / name / version
        d.mkdir(parents=True)
        with open(d / "model.pkl", "wb") as f:
            pickle.dump(model, f)
        (d / "meta.json").write_text(json.dumps({"features": FEATURES, **meta, "version": version}, indent=2, default=str), encoding="utf-8")
        return version

    def versions(self, name: str) -> list[dict]:
        base = self.dir / name
        if not base.exists():
            return []
        return [json.loads((v / "meta.json").read_text(encoding="utf-8")) for v in sorted(base.iterdir()) if (v / "meta.json").exists()]

    def load_latest_accepted(self, name: str):
        for meta in reversed(self.versions(name)):
            if meta.get("verdict") == "accepted":
                with open(self.dir / name / meta["version"] / "model.pkl", "rb") as f:
                    return pickle.load(f), meta
        return None, None
