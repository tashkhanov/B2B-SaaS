"""
Веб-сервер Telegram Mini App (TMA) для барбершопа Black Beard на базе aiohttp.
Обслуживает два отдельных интерфейса:
1. /book (Клиентская онлайн-запись: выбор услуги, свободных окон, имя, телефон)
2. /admin (Панель управляющего: список броней, смена статусов, ручное создание записей)
"""

import os
from datetime import datetime, date
import logging
from aiohttp import web
from aiogram import Bot

from config import settings
from database import get_all_bookings, update_booking_status, save_booking, get_active_bookings
from services.schedule_service import (
    get_now_tashkent,
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
    get_slots_for_date,
)

logger = logging.getLogger(__name__)

CLIENT_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "client.html")
ADMIN_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "index.html")
BOT_KEY = web.AppKey("bot", object)


async def handle_client(request: web.Request) -> web.Response:
    """Отдача клиентской страницы онлайн-записи (/book, /)"""
    if os.path.exists(CLIENT_HTML_PATH):
        with open(CLIENT_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    return web.Response(text="<h1>Client webapp not found</h1>", content_type="text/html", status=404)


async def handle_admin(request: web.Request) -> web.Response:
    """Отдача страницы панели управляющего (/admin, /webapp)"""
    if os.path.exists(ADMIN_HTML_PATH):
        with open(ADMIN_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    return web.Response(text="<h1>Admin webapp not found</h1>", content_type="text/html", status=404)


async def handle_get_config(request: web.Request) -> web.Response:
    """API: Конфигурационные данные для фронтенда (ID администратора)"""
    return web.json_response({
        "admin_chat_id": settings.ADMIN_CHAT_ID,
    })


async def handle_get_slots(request: web.Request) -> web.Response:
    """API: Получение списка почасовых слотов на дату с флагом доступности"""
    try:
        date_str = request.query.get("date")
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = get_now_tashkent().date()

        active_bookings = await get_active_bookings()
        slots = get_slots_for_date(target_date, active_bookings)
        return web.json_response({"date": target_date.strftime("%Y-%m-%d"), "slots": slots})
    except Exception as e:
        logger.error(f"Ошибка при получении слотов: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_bookings(request: web.Request) -> web.Response:
    """API: Получение списка всех записей клиентов для админки"""
    try:
        bookings = await get_all_bookings()
        return web.json_response(bookings)
    except Exception as e:
        logger.error(f"Ошибка при получении записей из БД: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_booking(request: web.Request) -> web.Response:
    """
    API: Создание новой записи (из клиентского TMA или вручную администратором).
    Проверяет доступность времени с учетом длительности 1 час.
    """
    try:
        data = await request.json()
        full_name = (data.get("full_name") or "").strip()
        raw_booking_time = (data.get("booking_time") or "").strip()
        service = data.get("service") or "Стрижка / Комплекс"
        phone = data.get("phone")
        user_id = data.get("user_id") or 0
        username = data.get("username")
        details = data.get("details") or "Онлайн-запись через Web App"

        if not full_name:
            return web.json_response({"error": "Имя клиента обязательно для заполнения"}, status=400)
        if not raw_booking_time:
            return web.json_response({"error": "Дата и время обязательны для заполнения"}, status=400)

        # Валидация времени и проверка на пересечение слотов (1 час)
        target_dt = parse_booking_datetime(raw_booking_time)
        if target_dt:
            active_bookings = await get_active_bookings()
            is_avail, error_msg, conflict = check_slot_availability(target_dt, active_bookings)
            if not is_avail:
                return web.json_response({"error": error_msg}, status=400)
            normalized_time = format_booking_datetime(target_dt)
        else:
            normalized_time = raw_booking_time

        # Сохранение в базу
        booking_id = await save_booking(
            user_id=user_id,
            username=username,
            full_name=full_name,
            details=details,
            phone=phone,
            service=service,
            booking_time=normalized_time,
            status="new",
        )

        bot = request.app.get(BOT_KEY)

        # 1. Мгновенное подтверждение клиенту в личный чат Telegram
        if bot and user_id and user_id != 0:
            client_msg = (
                "💈 **Вы успешно записаны в барбершоп Black Beard!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                "📍 **Адрес:** ул. Амира Темура (ориентир — гостиница 'Узбекистан')\n\n"
                "Ждем вас! Если потребуется отменить или перенести запись — вы можете сделать это прямо через онлайн-меню или написав мне сюда в чат."
            )
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=client_msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить подтверждение клиенту {user_id}: {e}")

        # 2. Уведомление в Telegram администратору
        if bot and settings.ADMIN_CHAT_ID:
            admin_msg = (
                "💈 **НОВАЯ ОНЛАЙН-ЗАПИСЬ (из Web App)!**\n\n"
                f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                "📱 _Запись доступна в панели управления!_"
            )
            try:
                await bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление админу: {e}")

        return web.json_response({
            "ok": True,
            "id": booking_id,
            "booking_time": normalized_time,
        })

    except Exception as e:
        logger.error(f"Ошибка при создании записи через API: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_status(request: web.Request) -> web.Response:
    """API: Смена статуса записи (new, confirmed, cancelled) с авто-уведомлением клиенту"""
    from database import get_booking_by_id

    try:
        booking_id = int(request.match_info.get("id", 0))
        data = await request.json()
        new_status = data.get("status", "confirmed")

        booking = await get_booking_by_id(booking_id)
        success = await update_booking_status(booking_id, new_status)

        bot = request.app.get(BOT_KEY)
        if bot and booking and booking.get("user_id"):
            client_chat_id = booking["user_id"]
            if new_status == "confirmed":
                try:
                    await bot.send_message(
                        chat_id=client_chat_id,
                        text=(
                            f"✅ **Ваша запись в Black Beard подтверждена администратором!**\n\n"
                            f"✂️ **Услуга:** {booking['service']}\n"
                            f"⏰ **Время:** {booking['booking_time']}\n"
                            f"📍 **Адрес:** ул. Амира Темура\n\n"
                            "Ждем вас в назначенное время, ака! 💈"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить клиента о подтверждении: {e}")
            elif new_status == "cancelled":
                try:
                    await bot.send_message(
                        chat_id=client_chat_id,
                        text=(
                            f"❌ **Ваша запись на {booking['booking_time']} была отменена.**\n\n"
                            "Если у вас изменились планы, вы всегда можете выбрать другое время через онлайн-меню! 💈"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить клиента об отмене: {e}")

        return web.json_response({"ok": success, "id": booking_id, "status": new_status})
    except Exception as e:
        logger.error(f"Ошибка при смене статуса записи: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_my_booking(request: web.Request) -> web.Response:
    """API: Получение активной предстоящей брони клиента"""
    from database import get_user_active_booking

    try:
        user_id_str = request.query.get("user_id")
        if not user_id_str:
            return web.json_response({"booking": None})
        user_id = int(user_id_str)
        booking = await get_user_active_booking(user_id)
        return web.json_response({"booking": booking})
    except Exception as e:
        logger.error(f"Ошибка при получении брони пользователя: {e}")
        return web.json_response({"booking": None, "error": str(e)}, status=500)


async def handle_cancel_my_booking(request: web.Request) -> web.Response:
    """API: Отмена своей брони клиентом через Client TMA"""
    from database import cancel_user_booking

    try:
        data = await request.json()
        user_id = int(data.get("user_id") or 0)
        booking_id = data.get("booking_id")
        if not user_id:
            return web.json_response({"error": "user_id обязателен"}, status=400)

        cancelled = await cancel_user_booking(user_id=user_id, booking_id=booking_id)
        if not cancelled:
            return web.json_response({"error": "Активная бронь не найдена"}, status=404)

        bot = request.app.get(BOT_KEY)
        if bot:
            # Уведомляем клиента в личный чат
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"❌ **Ваша запись на {cancelled['booking_time']} ({cancelled['service']}) успешно отменена.**\n\n"
                        "Будем рады видеть вас в другой удобный день! 💈"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить подтверждение клиенту об отмене: {e}")

            # Уведомляем администратора
            if settings.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=(
                            f"🔔 **ОТМЕНА ЗАПИСИ (через Client TMA):**\n\n"
                            f"👤 **Клиент:** {cancelled['full_name']} (@{cancelled.get('username') or 'нет'})\n"
                            f"✂️ **Услуга:** {cancelled['service']}\n"
                            f"⏰ **Было забронировано:** {cancelled['booking_time']}\n\n"
                            "🟢 _Окно снова открыто для записи!_"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление админу: {e}")

        return web.json_response({"ok": True, "cancelled": cancelled})
    except Exception as e:
        logger.error(f"Ошибка при отмене брони через API: {e}")
        return web.json_response({"error": str(e)}, status=500)


def create_web_app(bot: Bot = None) -> web.Application:
    """Фабрика создания приложения aiohttp для TMA"""
    app = web.Application()
    app[BOT_KEY] = bot

    # Клиентские маршруты
    app.router.add_get("/", handle_client)
    app.router.add_get("/book", handle_client)

    # Административные маршруты
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/webapp", handle_admin)

    # API маршруты
    app.router.add_get("/api/config", handle_get_config)
    app.router.add_get("/api/slots", handle_get_slots)
    app.router.add_get("/api/bookings", handle_get_bookings)
    app.router.add_post("/api/bookings", handle_create_booking)
    app.router.add_post("/api/bookings/{id}/status", handle_update_status)
    app.router.add_get("/api/my-booking", handle_get_my_booking)
    app.router.add_post("/api/my-booking/cancel", handle_cancel_my_booking)

    return app


async def start_web_server(host: str = None, port: int = None, bot: Bot = None) -> web.AppRunner:
    """
    Запуск асинхронного веб-сервера aiohttp на том же event loop, что и бот
    """
    h = host or settings.WEBAPP_HOST or "0.0.0.0"
    p = port or settings.WEBAPP_PORT or 8000

    app = create_web_app(bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, h, p)
    await site.start()
    logger.info("🌐 Сервер Telegram Mini App успешно запущен: http://%s:%s", h, p)
    return runner
