import pytest

from src.config import LIVE_CONFIRM_TOKEN, Settings


def test_paper_default_safe():
    s = Settings(alpaca_paper=True)
    s.assert_safe_to_trade()  # should not raise


def test_live_without_confirm_raises():
    s = Settings(alpaca_paper=False, alpaca_live_confirm="")
    with pytest.raises(RuntimeError):
        s.assert_safe_to_trade()


def test_live_with_wrong_confirm_raises():
    s = Settings(alpaca_paper=False, alpaca_live_confirm="sure")
    with pytest.raises(RuntimeError):
        s.assert_safe_to_trade()


def test_live_with_correct_confirm_ok():
    s = Settings(alpaca_paper=False, alpaca_live_confirm=LIVE_CONFIRM_TOKEN)
    s.assert_safe_to_trade()


def test_enabled_bot_list_parses():
    s = Settings(enabled_bots=" momentum , mean_reversion ,  ")
    assert s.enabled_bot_list == ["momentum", "mean_reversion"]
