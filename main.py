"""
=============================================================================
Проект: AI-Администратор для Мужского Салона "Brohouse" (Ташкент)
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database import init_db
from bot.handlers import router
from web_app import start_web_server
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    MenuButtonWebApp,
    MenuButtonCommands,
    WebAppInfo,
)

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
    Настраивает дифференцированные кнопки Menu Button:
    - Для клиентов: кнопка '💈 Записаться' (Client TMA: /book)
    - Для администратора: кнопка '📊 Админка' (Admin TMA: /admin)
    """
    # 1. Общедоступные команды для клиентов и мастеров
    client_commands = [
        BotCommand(command="start", description="💈 Главное меню / Запись"),
        BotCommand(command="price", description="✂️ Прайс-лист услуг"),
        BotCommand(command="barber", description="💈 Кабинет мастера (TMA)"),
        BotCommand(command="about", description="⚡ О разработчике (Khan)"),
        BotCommand(command="reset", description="🔄 Сбросить диалог"),
    ]
    await bot.set_my_commands(client_commands)

    # 2. Персональные команды для управляющего
    if settings.ADMIN_CHAT_ID:
        admin_commands = [
            BotCommand(command="start", description="💈 Главное меню / Запуск"),
            BotCommand(command="admin", description="📊 Панель управления (TMA)"),
            BotCommand(command="barber", description="💈 Кабинет мастера (TMA)"),
            BotCommand(command="price", description="✂️ Прайс-лист услуг"),
            BotCommand(command="about", description="⚡ О разработчике (Khan)"),
            BotCommand(command="reset", description="🔄 Сбросить диалог"),
        ]
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=settings.ADMIN_CHAT_ID))
        except Exception as e:
            logger.warning(f"Не удалось установить персональные команды для админа: {e}")

    logger.info("Список команд бота в Telegram успешно обновлен.")

    # 3. Настройка кнопок Menu Button в поле ввода Telegram
    if settings.WEBAPP_URL and settings.WEBAPP_URL.startswith("https://"):
        base_url = settings.WEBAPP_URL.rstrip("/")

        # Глобальная кнопка для клиентов -> онлайн-запись (/book)
        client_menu_btn = MenuButtonWebApp(
            text="💈 Записаться",
            web_app=WebAppInfo(url=f"{base_url}/book"),
        )
        await bot.set_chat_menu_button(menu_button=client_menu_btn)
        logger.info("Кнопка Menu Button ('💈 Записаться' -> /book) установлена для всех клиентов.")

        # Персональная кнопка для администратора -> дашборд (/admin)
        if settings.ADMIN_CHAT_ID:
            admin_menu_btn = MenuButtonWebApp(
                text="📊 Админка",
                web_app=WebAppInfo(url=f"{base_url}/admin"),
            )
            try:
                await bot.set_chat_menu_button(
                    chat_id=settings.ADMIN_CHAT_ID,
                    menu_button=admin_menu_btn,
                )
                logger.info(
                    "Персональная кнопка Menu Button ('📊 Админка' -> /admin) установлена для админа (%s).",
                    settings.ADMIN_CHAT_ID,
                )
            except Exception as e:
                logger.warning(f"Не удалось установить персональную кнопку меню для админа: {e}")
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    """
    Основная асинхронная функция запуска бота и веб-сервера TMA.
    """
    logger.info("Запуск инициализации бота AI-Администратора 'Brohouse'...")

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

    # 3. Инициализация объекта бота Telegram
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    # 4. Запуск встроенного веб-сервера TMA с передачей бота для уведомлений
    web_runner = await start_web_server(
        host=settings.WEBAPP_HOST,
        port=settings.WEBAPP_PORT,
        bot=bot,
    )

    # 5. Установка актуального меню команд и кнопок Mini App в Telegram
    await setup_bot_commands(bot)

    # 6. Инициализация диспетчера aiogram 3.x
    dp = Dispatcher()

    # 7. Подключение middleware проверки подписки и Kill Switch
    from bot.middlewares import SubscriptionCheckMiddleware
    router.message.middleware(SubscriptionCheckMiddleware())
    router.callback_query.middleware(SubscriptionCheckMiddleware())

    # 8. Подключение маршрутизатора с обработчиками текста, голоса и команд
    dp.include_router(router)

    # 8. Сброс старых накопившихся обновлений и запуск опроса (polling)
    await bot.delete_webhook(drop_pending_updates=True)
    base_url = (settings.WEBAPP_URL or "http://localhost:8000").rstrip("/")
    logger.info(
        "🚀 Бот 'Brohouse Ассистент' запущен!\n"
        "   👉 Онлайн-запись для клиентов: %s/book\n"
        "   👉 Панель управляющего:         %s/admin",
        base_url,
        base_url,
    )

    # 9. Запуск фоновой задачи проверки подписок (Cron task раз в 24 часа)
    from services.billing_service import run_billing_notifier
    billing_task = asyncio.create_task(run_billing_notifier(bot))
    logger.info("Фоновая служба проверки подписок и Kill Switch успешно запущена.")

    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота и веб-сервера...")
        billing_task.cancel()
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Приложение выключено пользователем.")
