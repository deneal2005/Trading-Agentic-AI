import pytest
from pydantic import ValidationError

from tradeintel.config import load_settings


def test_defaults_load(tmp_path):
    s = load_settings(env={}, root=tmp_path)
    assert s.market.instrument == "EURUSD"
    assert s.execution.enabled is False
    assert s.paths.db_path.is_relative_to(tmp_path)


def test_env_override(tmp_path):
    s = load_settings(env={"TRADEINTEL_RISK__MAX_DAILY_LOSS": "0.01", "TRADEINTEL_LLM_PROVIDER": "ollama"}, root=tmp_path)
    assert s.risk.max_daily_loss == 0.01
    assert s.llm.provider == "ollama"


def test_live_trading_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(env={}, root=tmp_path, overrides={"execution": {"allow_live": True}})


def test_inconsistent_risk_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(env={}, root=tmp_path, overrides={"risk": {"max_risk_per_trade": 0.02, "max_open_risk": 0.01}})


def test_risk_limits_bounded(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(env={}, root=tmp_path, overrides={"risk": {"max_risk_per_trade": 0.5}})
