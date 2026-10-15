import os
import pytest
from config import Settings

def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "fake_bot_token:123456")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key_xyz")
    monkeypatch.setenv("ADMIN_CHAT_ID", "987654321")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("DB_NAME", "test_barbershop.db")

    settings = Settings()

    assert settings.BOT_TOKEN == "fake_bot_token:123456"
    assert settings.GEMINI_API_KEY == "fake_gemini_key_xyz"
    assert settings.ADMIN_CHAT_ID == 987654321
    assert settings.GEMINI_MODEL == "gemini-2.5-flash"
    assert settings.DB_NAME == "test_barbershop.db"

def test_settings_optional_admin(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "fake_bot_token:123456")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key_xyz")
    monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)

    settings = Settings()

    assert settings.ADMIN_CHAT_ID is None
    assert settings.GEMINI_MODEL == "gemini-2.5-flash"
