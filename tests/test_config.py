"""Tests for configuration management"""

import pytest
from pydantic import ValidationError

from rehoboam.config import Settings


def test_settings_with_valid_env(monkeypatch):
    """Test settings loads correctly with valid environment variables"""
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")

    settings = Settings()

    assert settings.kickbase_email == "test@example.com"
    assert settings.kickbase_password == "testpassword"
    assert settings.dry_run is True  # Default value


def test_settings_with_custom_values(monkeypatch):
    """Test settings with custom trading parameters"""
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")
    monkeypatch.setenv("MIN_SELL_PROFIT_PCT", "10.0")
    monkeypatch.setenv("MAX_LOSS_PCT", "-5.0")
    monkeypatch.setenv("DRY_RUN", "false")

    settings = Settings()

    assert settings.min_sell_profit_pct == 10.0
    assert settings.max_loss_pct == -5.0
    assert settings.dry_run is False


def test_settings_defaults(monkeypatch):
    """Test that default values are set correctly"""
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")

    settings = Settings()

    assert settings.min_sell_profit_pct == 5.0
    assert settings.max_loss_pct == -3.0
    assert settings.min_buy_value_increase_pct == 10.0
    assert settings.max_player_cost == 5_000_000
    assert settings.reserve_budget == 1_000_000
    assert settings.dry_run is True


@pytest.mark.skip(reason="Test conflicts with .env file in repo - skip for CI")
def test_settings_missing_required_fields(monkeypatch, tmp_path):
    """Test that missing required fields raise validation error"""
    # Change to temp directory so .env file isn't found
    monkeypatch.chdir(tmp_path)

    # Clear any environment variables
    monkeypatch.delenv("KICKBASE_EMAIL", raising=False)
    monkeypatch.delenv("KICKBASE_PASSWORD", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    # Check that both required fields are in the error
    errors = exc_info.value.errors()
    field_names = [error["loc"][0] for error in errors]
    assert "kickbase_email" in field_names
    assert "kickbase_password" in field_names
