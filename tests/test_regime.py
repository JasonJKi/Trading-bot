"""Regime classifier tests — exercise the classification logic on canned inputs."""
from __future__ import annotations

import pytest

from src.core import regime


def test_classify_crisis_on_vix_spike():
    label = regime._classify(spy_trend=-0.05, vix=42, term_ratio=1.1, breadth=0.3, corr=0.7)
    assert label == "crisis"


def test_classify_crisis_on_term_inversion_plus_corr():
    label = regime._classify(spy_trend=0.0, vix=18, term_ratio=1.08, breadth=0.5, corr=0.7)
    assert label == "crisis"


def test_classify_bull_on_uptrend_and_breadth():
    label = regime._classify(spy_trend=0.05, vix=15, term_ratio=0.95, breadth=0.7, corr=0.4)
    assert label == "bull"


def test_classify_bear_on_downtrend_and_low_breadth():
    label = regime._classify(spy_trend=-0.04, vix=22, term_ratio=0.98, breadth=0.3, corr=0.5)
    assert label == "bear"


def test_classify_chop_default():
    label = regime._classify(spy_trend=0.005, vix=20, term_ratio=0.95, breadth=0.5, corr=0.5)
    assert label == "chop"


def test_regime_is_one_of_known_labels():
    assert set(regime.REGIMES) == {"bull", "bear", "chop", "crisis"}
