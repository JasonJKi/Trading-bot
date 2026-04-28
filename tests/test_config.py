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


# ---- validate_for_runtime() ------------------------------------------------

def _live_settings(**overrides) -> Settings:
    """Live-mode settings with the live token but nothing else by default."""
    base = dict(
        alpaca_paper=False,
        alpaca_live_confirm=LIVE_CONFIRM_TOKEN,
        alpaca_api_key="",
        alpaca_api_secret="",
        slack_webhook_url="",
        discord_webhook_url="",
        alert_email_to="",
        smtp_host="",
        smtp_user="",
        smtp_password="",
    )
    base.update(overrides)
    return Settings(**base)


def test_validate_paper_mode_accepts_minimal_config():
    Settings(alpaca_paper=True).validate_for_runtime()


def test_validate_empty_database_url_rejected():
    s = Settings(alpaca_paper=True, database_url="")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        s.validate_for_runtime()


def test_validate_live_without_confirm_rejected():
    s = _live_settings(alpaca_live_confirm="")
    with pytest.raises(RuntimeError, match="ALPACA_LIVE_CONFIRM"):
        s.validate_for_runtime()


def test_validate_live_without_alpaca_creds_rejected():
    s = _live_settings(slack_webhook_url="https://hooks.slack.com/x")
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
        s.validate_for_runtime()


def test_validate_live_without_alert_channel_rejected():
    s = _live_settings(alpaca_api_key="k", alpaca_api_secret="s")
    with pytest.raises(RuntimeError, match="alert channel"):
        s.validate_for_runtime()


def test_validate_live_with_slack_ok():
    s = _live_settings(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        slack_webhook_url="https://hooks.slack.com/x",
    )
    s.validate_for_runtime()


def test_validate_live_with_discord_ok():
    s = _live_settings(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        discord_webhook_url="https://discord.com/api/webhooks/x",
    )
    s.validate_for_runtime()


def test_validate_live_with_complete_email_ok():
    s = _live_settings(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        alert_email_to="ops@example.com",
        smtp_host="smtp.example.com",
        smtp_user="u",
        smtp_password="p",
    )
    s.validate_for_runtime()


def test_validate_live_with_partial_email_rejected():
    # alert_email_to set but no SMTP creds — counts as "no channel."
    s = _live_settings(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        alert_email_to="ops@example.com",
    )
    with pytest.raises(RuntimeError, match="alert channel"):
        s.validate_for_runtime()
