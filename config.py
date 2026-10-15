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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Глобальный экземпляр настроек
settings = Settings()
