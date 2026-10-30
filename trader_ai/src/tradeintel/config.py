"""Configuration loading and validation.

Precedence (lowest to highest): config/default.yaml, config/local.yaml,
TRADEINTEL_* environment variables. Invalid configuration fails fast.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Paths(BaseModel):
    data_dir: Path
    db_path: Path
    reports_dir: Path
    models_dir: Path
    kill_switch_file: Path

    def resolve(self, root: Path) -> "Paths":
        return Paths(**{k: (v if v.is_absolute() else root / v) for k, v in self.model_dump().items()})


class Market(BaseModel):
    asset_class: Literal["forex", "equity", "crypto", "futures"]
    instrument: str
    timeframe: Literal["1h", "4h", "1d"]
    account_currency: str = "USD"
    pip_size: float = Field(gt=0)

    @property
    def base_ccy(self) -> str:
        return self.instrument[:3] if self.asset_class == "forex" else self.instrument

    @property
    def quote_ccy(self) -> str:
        return self.instrument[3:6] if self.asset_class == "forex" else self.account_currency


class DataCfg(BaseModel):
    provider: Literal["yahoo", "stooq", "alpaca", "csv", "synthetic"]
    start: str
    csv_path: str | None = None
    max_staleness_bars: int = Field(default=3, ge=1)


class Costs(BaseModel):
    spread_pips: float = Field(ge=0)
    slippage_pips: float = Field(ge=0)
    commission_per_million: float = Field(ge=0)
    financing_rate_annual: float = Field(ge=0)

    def scaled(self, k: float) -> "Costs":
        return Costs(**{f: v * k for f, v in self.model_dump().items()})


class WalkForward(BaseModel):
    train_bars: int = Field(gt=10)
    test_bars: int = Field(gt=5)


class BacktestCfg(BaseModel):
    initial_equity: float = Field(gt=0)
    holdout_fraction: float = Field(ge=0, lt=0.9)
    walkforward: WalkForward


class RiskCfg(BaseModel):
    max_risk_per_trade: float = Field(gt=0, le=0.05)
    max_open_risk: float = Field(gt=0, le=0.2)
    max_daily_loss: float = Field(gt=0, le=0.2)
    max_drawdown: float = Field(gt=0, le=0.5)
    max_leverage: float = Field(gt=0, le=50)
    max_position_units: float = Field(gt=0)
    max_positions_per_instrument: int = Field(ge=1)
    max_correlated_exposure: int = Field(ge=1)
    max_spread_pips: float = Field(gt=0)
    max_consecutive_losses: int = Field(ge=1)
    event_blackout_minutes: int = Field(ge=0)
    require_stop_loss: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> "RiskCfg":
        if self.max_risk_per_trade > self.max_open_risk:
            raise ValueError("max_risk_per_trade cannot exceed max_open_risk")
        return self


class Feed(BaseModel):
    name: str
    url: str
    reliability: Literal["official", "reputable", "aggregator", "unverified"]


class NewsCfg(BaseModel):
    rss_feeds: list[Feed]
    forexfactory_calendar_url: str
    poll_seconds: int = Field(ge=60)
    request_timeout_seconds: int = Field(ge=1, le=120)


class LLMCfg(BaseModel):
    provider: Literal["none", "ollama", "anthropic"]
    ollama_model: str
    anthropic_model: str
    timeout_seconds: int = Field(ge=1)
    max_input_chars: int = Field(ge=500)


class PromotionCfg(BaseModel):
    min_trades: int
    min_oos_sharpe: float
    max_oos_drawdown: float
    min_profit_factor: float
    must_beat_baselines: list[str]
    max_cost_stress_multiplier_loss: float
    min_deflated_sharpe: float = 0.95  # chance the true Sharpe > 0 after correcting for every variation tried


class ExecutionCfg(BaseModel):
    enabled: bool = False
    broker: Literal["paper", "oanda", "alpaca"] = "paper"
    allow_live: bool = False

    @field_validator("allow_live")
    @classmethod
    def _no_live(cls, v: bool) -> bool:
        if v:
            raise ValueError("live trading is not supported by this build; allow_live must be false")
        return v


class LiveCfg(BaseModel):
    strategy: str = "sma_cross"
    params: dict = Field(default_factory=dict)
    require_promotion: bool = True


class Settings(BaseModel):
    paths: Paths
    market: Market
    data: DataCfg
    costs: Costs
    backtest: BacktestCfg
    risk: RiskCfg
    news: NewsCfg
    llm: LLMCfg
    promotion: PromotionCfg
    execution: ExecutionCfg
    live: LiveCfg = Field(default_factory=LiveCfg)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides(env: dict[str, str]) -> dict[str, Any]:
    """TRADEINTEL_<SECTION>__<KEY>=value -> {section: {key: value}}."""
    out: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith("TRADEINTEL_") or "__" not in key:
            continue
        parts = key[len("TRADEINTEL_"):].lower().split("__")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(raw)
    if "TRADEINTEL_LLM_PROVIDER" in env:
        out.setdefault("llm", {})["provider"] = env["TRADEINTEL_LLM_PROVIDER"]
    return out


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE reader for trader_ai/.env. Variables already set in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad may add a BOM
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def load_settings(
    config_dir: Path | None = None,
    overrides: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    root: Path | None = None,
) -> Settings:
    config_dir = config_dir or PROJECT_ROOT / "config"
    root = root or PROJECT_ROOT
    raw = yaml.safe_load((config_dir / "default.yaml").read_text(encoding="utf-8"))
    local = config_dir / "local.yaml"
    if local.exists():
        raw = _deep_merge(raw, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    raw = _deep_merge(raw, _env_overrides(dict(os.environ if env is None else env)))
    if overrides:
        raw = _deep_merge(raw, overrides)
    s = Settings.model_validate(raw)
    s.paths = s.paths.resolve(root)
    return s
