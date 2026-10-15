"""
=============================================================================
Проект: AI-Администратор для Барбершопа "Black Beard" (Ташкент)
Стек: Python 3.10+, aiogram 3.x, google-genai (Gemini 2.5 Flash), aiosqlite, aiohttp
=============================================================================
Файл main.py — главная точка входа приложения.
Здесь происходит:
1. Загрузка конфигурации из .env (токены Telegram, Google Gemini, TMA URL).
2. Инициализация асинхронной базы данных SQLite (хранение контекста и лидов).
3. Запуск веб-сервера Telegram Mini App (TMA) для панели администратора.
4. Создание экземпляров Bot и Dispatcher (aiogram 3.x).
5. Регистрация маршрутизаторов (роутеров) с хэндлерами сообщений и голоса.
6. Параллельный запуск long-polling и HTTP-сервера.
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
from web_app import start_web_server
from aiogram.types import BotCommand, MenuButtonWebApp, MenuButtonCommands, WebAppInfo

# Настройка подробного логирования в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BarberAdminBot")


async def setup_bot_commands(bot: Bot) -> None:
    """
    Очищает старые сторонние команды в Telegram и устанавливает аккуратное меню барбершопа.
    Также настраивает официальную кнопку меню (Menu Button) в поле ввода Telegram.
    """
    commands = [
        BotCommand(command="start", description="💈 Главное меню / Запуск"),
        BotCommand(command="price", description="✂️ Прайс-лист услуг"),
        BotCommand(command="admin", description="📊 Панель записей (TMA)"),
        BotCommand(command="about", description="⚡ О разработчике (Khan AI)"),
        BotCommand(command="reset", description="🔄 Сбросить диалог"),
    ]
    await bot.set_my_commands(commands)
    logger.info("Список команд бота в Telegram успешно обновлен.")

    # Если задан HTTPS URL — устанавливаем постоянную кнопку Mini App в поле ввода Telegram
    if settings.WEBAPP_URL and settings.WEBAPP_URL.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="💈 Записи",
                web_app=WebAppInfo(url=settings.WEBAPP_URL),
            )
        )
        logger.info("Кнопка Menu Button (Mini App '💈 Записи') установлена в поле ввода Telegram.")
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    """
    Основная асинхронная функция запуска бота и веб-сервера TMA.
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
    logger.info("Инициализация базы данных SQLite (%s)...", settings.DB_NAME)
    await init_db(settings.DB_NAME)
    logger.info("База данных успешно инициализирована.")

    # 3. Запуск встроенного веб-сервера для Telegram Mini App (TMA)
    web_runner = await start_web_server(
        host=settings.WEBAPP_HOST,
        port=settings.WEBAPP_PORT,
    )

    # 4. Инициализация объекта бота Telegram
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    # 5. Установка актуального меню команд и кнопки Mini App в Telegram
    await setup_bot_commands(bot)

    # 6. Инициализация диспетчера aiogram 3.x
    dp = Dispatcher()

    # 7. Подключение маршрутизатора с обработчиками текста, голоса и команд
    dp.include_router(router)

    # 8. Сброс старых накопившихся обновлений и запуск опроса (polling)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(
        "🚀 Бот 'Иброхим (Black Beard)' запущен! Mini App: %s",
        settings.WEBAPP_URL,
    )

    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота и веб-сервера...")
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Приложение выключено пользователем.")
