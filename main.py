"""
=============================================================================
Проект: AI-Администратор для Барбершопа "Black Beard" (Ташкент)
Стек: Python 3.10+, aiogram 3.x, google-genai (Gemini 2.5 Flash), aiosqlite
=============================================================================
Файл main.py — главная точка входа приложения.
Здесь происходит:
1. Загрузка конфигурации из .env (токены Telegram и Google Gemini).
2. Инициализация асинхронной базы данных SQLite (хранение контекста и лидов).
3. Создание экземпляров Bot и Dispatcher (aiogram 3.x).
4. Регистрация маршрутизаторов (роутеров) с хэндлерами сообщений.
5. Запуск цикла long-polling для получения обновлений из Telegram.
=============================================================================
"""

import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database import init_db
from bot.handlers import router

# Настройка подробного логирования в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BarberAdminBot")


async def main() -> None:
    """
    Основная асинхронная функция запуска бота.
    """
    logger.info("Запуск инициализации бота AI-Администратора 'Black Beard'...")

    # 1. Проверка наличия обязательных ключей конфигурации
    if not settings.BOT_TOKEN or "your_telegram_bot_token" in settings.BOT_TOKEN:
        logger.critical(
            "ОШИБКА: Токен Telegram бота не задан! "
            "Создайте файл .env и укажите в нем переменную BOT_TOKEN=..."
        )
        sys.exit(1)

    if not settings.GEMINI_API_KEY or "your_gemini_api_key" in settings.GEMINI_API_KEY:
        logger.critical(
            "ОШИБКА: API ключ Google Gemini не задан! "
            "Получите бесплатный ключ на https://aistudio.google.com/ "
            "и укажите в .env переменную GEMINI_API_KEY=..."
        )
        sys.exit(1)

    # 2. Инициализация структуры базы данных SQLite
    # Создает таблицы messages (для истории диалога) и bookings (для записи клиентов)
    logger.info("Инициализация базы данных SQLite (%s)...", settings.DB_NAME)
    await init_db(settings.DB_NAME)
    logger.info("База данных успешно инициализирована.")

    # 3. Инициализация объекта бота Telegram
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    # 4. Инициализация диспетчера aiogram 3.x
    dp = Dispatcher()

    # 5. Подключение маршрутизатора с обработчиками команд и сообщений
    dp.include_router(router)

    # 6. Удаление накопившихся вебхуков и запуск постоянного опроса (polling)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(
        "🚀 Бот 'Иброхим (Black Beard)' успешно запущен! Ожидание сообщений клиентов..."
    )

    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота и закрытие сессии...")
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот выключен пользователем.")
