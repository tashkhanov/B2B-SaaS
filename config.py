import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Класс настроек приложения с автоматической загрузкой из .env
    """
    BOT_TOKEN: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    ADMIN_CHAT_ID: Optional[int] = None
    DB_NAME: str = "barbershop.db"

    # Telegram Mini App (TMA) настройки
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = 8000
    WEBAPP_URL: str = "http://localhost:8000"

    # Мультитенантность и биллинг (SaaS Super-Admin)
    TENANT_SLUG: str = "brohouse"
    SAAS_HUB_URL: str = "https://hub.asatkhanov.uz"
    SUPER_ADMIN_CHAT_ID: Optional[int] = None
    SUPER_ADMIN_PASSWORD: str = ""

    # Eskiz SMS (Тариф ENTERPRISE)
    ESKIZ_EMAIL: str = ""
    ESKIZ_PASSWORD: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Глобальный экземпляр настроек
settings = Settings()
