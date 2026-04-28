"""Strategy template library tests.

We exercise the contract — not the strategy logic itself, which is covered by
integration backtests. The contract:
  - registry sees all four built-in templates
  - param_specs() are well-formed
  - validate_params() rejects out-of-range / wrong-shape input
  - instantiate() returns a Strategy that conforms to the existing ABC
  - cross-param invariants are enforced (fast<slow, exit_lookback<entry_lookback, etc.)
  - schema_for_agent() is JSON-serializable (the synthesis agent uses it)
"""
from __future__ import annotations

import json

import pytest

from src.core.strategy import Strategy
from src.templates import (
    ParamSpec,
    StrategyTemplate,
    TemplateRegistryError,
    all_registered,
    get_template,
)


# ---- registry ---------------------------------------------------------------

def test_registry_has_the_four_built_in_templates():
    ids = set(all_registered().keys())
    expected = {"ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"}
    assert expected.issubset(ids), f"missing: {expected - ids}; got: {ids}"


def test_get_template_unknown_id_raises():
    with pytest.raises(TemplateRegistryError):
        get_template("nope_does_not_exist")


@pytest.mark.parametrize("template_id", ["ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"])
def test_template_metadata_well_formed(template_id):
    cls = get_template(template_id)
    assert isinstance(cls.id, str) and cls.id == template_id
    assert isinstance(cls.name, str) and cls.name
    assert isinstance(cls.description, str) and len(cls.description) > 50
    assert cls.category in {"trend", "mean_reversion", "momentum", "breakout", "factor", "other"}
    assert cls.asset_classes  # non-empty


# ---- param specs ------------------------------------------------------------

@pytest.mark.parametrize("template_id", ["ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"])
def test_param_specs_well_formed(template_id):
    cls = get_template(template_id)
    specs = cls.param_specs()
    assert len(specs) >= 3
    seen_names = set()
    for p in specs:
        assert isinstance(p, ParamSpec)
        assert p.name not in seen_names, f"duplicate param: {p.name}"
        seen_names.add(p.name)
        assert p.kind in {"int", "float", "choice", "bool"}
        assert p.description
        # Defaults must validate against their own spec.
        validated = p.validate(p.default)
        assert validated is not None or p.kind == "bool" and validated is False


def test_paramspec_validates_int_bounds():
    p = ParamSpec("x", "int", "test", default=10, low=1, high=100)
    assert p.validate(50) == 50
    assert p.validate("50") == 50  # coerces strings
    with pytest.raises(ValueError):
        p.validate(0)
    with pytest.raises(ValueError):
        p.validate(101)


def test_paramspec_validates_float_bounds():
    p = ParamSpec("x", "float", "test", default=0.5, low=0.0, high=1.0)
    assert p.validate(0.5) == 0.5
    assert p.validate(0) == 0.0
    assert p.validate(1.0) == 1.0
    with pytest.raises(ValueError):
        p.validate(-0.1)
    with pytest.raises(ValueError):
        p.validate(1.1)


def test_paramspec_validates_choice():
    p = ParamSpec("x", "choice", "test", default="ema", choices=["ema", "sma"])
    assert p.validate("ema") == "ema"
    with pytest.raises(ValueError):
        p.validate("hema")


def test_paramspec_bool_coercion():
    p = ParamSpec("x", "bool", "test", default=False)
    assert p.validate(True) is True
    assert p.validate("true") is True
    assert p.validate("YES") is True
    assert p.validate("0") is False
    assert p.validate(0) is False


# ---- validate_params --------------------------------------------------------

def test_validate_params_fills_defaults_and_drops_unknown_keys():
    cls = get_template("ma_cross")
    out = cls.validate_params({"fast": 10, "rogue_key": "ignored"})
    assert out["fast"] == 10
    assert out["slow"] == 50          # default kicked in
    assert "rogue_key" not in out


def test_validate_params_rejects_out_of_range():
    cls = get_template("ma_cross")
    with pytest.raises(ValueError):
        cls.validate_params({"fast": -5})
    with pytest.raises(ValueError):
        cls.validate_params({"adx_threshold": 99.0})


# ---- instantiate ------------------------------------------------------------

def test_ma_cross_instantiate_returns_strategy():
    cls = get_template("ma_cross")
    s = cls.instantiate(
        bot_id="ma_test_bot",
        params={"fast": 10, "slow": 30, "ma_type": "ema"},
        universe=["SPY", "QQQ"],
    )
    assert isinstance(s, Strategy)
    assert s.id == "ma_test_bot"
    assert s.version == cls.version
    assert s.universe() == ["SPY", "QQQ"]
    assert callable(s.target_positions)
    # The validated param dict should be on the instance.
    assert s.params["fast"] == 10
    assert s.params["slow"] == 30


def test_ma_cross_invariant_fast_lt_slow():
    cls = get_template("ma_cross")
    with pytest.raises(ValueError, match="fast"):
        cls.instantiate(
            bot_id="bad",
            params={"fast": 50, "slow": 20},
            universe=["SPY"],
        )


def test_rsi_invariant_exit_gt_buy():
    cls = get_template("mean_reversion_rsi")
    with pytest.raises(ValueError, match="rsi_exit"):
        cls.instantiate(
            bot_id="bad",
            params={"rsi_buy": 30.0, "rsi_exit": 25.0},
            universe=["SPY"],
        )


def test_vol_breakout_invariant_exit_lt_entry():
    cls = get_template("vol_breakout")
    with pytest.raises(ValueError, match="exit_lookback"):
        cls.instantiate(
            bot_id="bad",
            params={"entry_lookback": 20, "exit_lookback": 25},
            universe=["SPY"],
        )


def test_momentum_zscore_invariant_fetch_lookback():
    cls = get_template("momentum_zscore")
    with pytest.raises(ValueError, match="fetch_lookback_days"):
        cls.instantiate(
            bot_id="bad",
            params={"lookback_days": 252, "skip_recent_days": 21, "fetch_lookback_days": 100},
            universe=["SPY", "QQQ", "IWM", "DIA"],
        )


@pytest.mark.parametrize("template_id", ["ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"])
def test_instantiate_with_only_defaults(template_id):
    """A spec with empty params should still produce a usable Strategy."""
    cls = get_template(template_id)
    s = cls.instantiate(
        bot_id=f"{template_id}_default",
        params={},
        universe=["SPY", "QQQ", "IWM", "DIA"],
    )
    assert isinstance(s, Strategy)
    assert s.universe() == ["SPY", "QQQ", "IWM", "DIA"]


# ---- agent-facing schema ----------------------------------------------------

@pytest.mark.parametrize("template_id", ["ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"])
def test_schema_for_agent_is_json_serializable(template_id):
    """The synthesis agent reads this schema as part of its prompt — it must
    survive a JSON round-trip without losing any field."""
    cls = get_template(template_id)
    schema = cls.schema_for_agent()
    j = json.dumps(schema)
    again = json.loads(j)
    assert again["id"] == cls.id
    assert again["name"] == cls.name
    assert again["category"] == cls.category
    assert len(again["params"]) == len(cls.param_specs())
    # Every param round-trips.
    for src, dst in zip(cls.param_specs(), again["params"]):
        assert dst["name"] == src.name
        assert dst["kind"] == src.kind


# ---- behavior smoke (no broker, no LLM) -------------------------------------

@pytest.mark.parametrize("template_id", ["ma_cross", "mean_reversion_rsi", "momentum_zscore", "vol_breakout"])
def test_instantiated_strategy_degrades_to_empty_when_no_bars(template_id, monkeypatch):
    """target_positions must degrade to [] when bar data is unavailable, never raise.

    We monkeypatch `fetch_daily_bars` to return empty so we don't hit the
    network and so the test is deterministic.
    """
    from datetime import datetime, timezone

    from src.core.strategy import StrategyContext

    # Patch every call site that imported fetch_daily_bars.
    def _empty_bars(symbols, lookback_days=180):
        return {sym: __import__("pandas").DataFrame() for sym in symbols}

    for mod_name in (
        "src.templates.ma_cross",
        "src.templates.mean_reversion_rsi",
        "src.templates.momentum_zscore",
        "src.templates.vol_breakout",
    ):
        monkeypatch.setattr(f"{mod_name}.fetch_daily_bars", _empty_bars)

    cls = get_template(template_id)
    s = cls.instantiate(
        bot_id=f"{template_id}_nodata",
        params={},
        universe=["SPY", "QQQ", "IWM", "DIA"],
    )
    ctx = StrategyContext(
        now=datetime.now(timezone.utc),
        cash=10_000.0,
        positions={},
        bot_equity=10_000.0,
    )
    assert s.target_positions(ctx) == []
