"""
Веб-сервер Telegram Mini App (TMA) для барбершопа Brohouse на базе aiohttp.
Обслуживает три отдельных интерфейса:
1. /book (Клиентская онлайн-запись: услуга, выбор мастера, выбор окон, контакты)
2. /admin (Панель управляющего: 3 вкладки - Записи, Услуги, Барберы)
3. /barber (Кабинет мастера: "Моя смена", очередь клиентов на сегодня, статус смены)
"""

import os
from datetime import datetime, date
import logging
from aiohttp import web
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database import (
    get_all_bookings,
    update_booking_status,
    save_booking,
    get_active_bookings,
    get_services,
    get_service_by_id,
    create_service,
    update_service,
    delete_service,
    get_barbers,
    get_barber_by_id,
    get_barber_by_telegram_id,
    create_barber,
    update_barber,
    toggle_barber_shift,
    delete_barber,
    get_bookings_for_barber,
    get_booking_by_id,
    get_user_active_booking,
    cancel_user_booking,
)
from services.schedule_service import (
    get_now_tashkent,
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
    get_slots_for_date,
    is_barber_working_on_date,
)

logger = logging.getLogger(__name__)

CLIENT_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "client.html")
ADMIN_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "index.html")
BARBER_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "barber.html")
STATIC_DIR_PATH = os.path.join(os.path.dirname(__file__), "static")
BOT_KEY = web.AppKey("bot", object)


def get_booking_action_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Инлайн-кнопки для мгновенного подтверждения или отмены записи мастером/админом"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bstatus:{booking_id}:confirmed"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bstatus:{booking_id}:cancelled"),
            ]
        ]
    )


# =============================================================================
# HTML СТРАНИЦЫ (TMA INTERFACES)
# =============================================================================

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


async def handle_barber_page(request: web.Request) -> web.Response:
    """Отдача страницы кабинета мастера (/barber)"""
    if os.path.exists(BARBER_HTML_PATH):
        with open(BARBER_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    return web.Response(text="<h1>Barber workspace not found</h1>", content_type="text/html", status=404)


# =============================================================================
# API КОНФИГУРАЦИИ И СЛОТОВ
# =============================================================================

async def handle_get_config(request: web.Request) -> web.Response:
    """API: Конфигурационные данные для фронтенда (ID администратора)"""
    return web.json_response({
        "admin_chat_id": settings.ADMIN_CHAT_ID,
    })


async def handle_get_slots(request: web.Request) -> web.Response:
    """
    API: Получение списка слотов на дату с мульти-мастер валидацией.
    Параметры:
    - date (YYYY-MM-DD)
    - barber_id (int, optional)
    - duration (int, optional, default 60)
    """
    try:
        date_str = request.query.get("date")
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = get_now_tashkent().date()

        barber_id_raw = request.query.get("barber_id")
        barber_id = int(barber_id_raw) if barber_id_raw and barber_id_raw.isdigit() and int(barber_id_raw) > 0 else None

        duration_raw = request.query.get("duration")
        duration = int(duration_raw) if duration_raw and duration_raw.isdigit() else 60

        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True)

        slots = get_slots_for_date(
            target_date=target_date,
            existing_bookings=active_bookings,
            barber_id=barber_id,
            available_barbers=barbers,
            duration_minutes=duration
        )
        return web.json_response({"date": target_date.strftime("%Y-%m-%d"), "slots": slots})
    except Exception as e:
        logger.error(f"Ошибка при получении слотов: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API УСЛУГ (SERVICES CRUD)
# =============================================================================

async def handle_get_services(request: web.Request) -> web.Response:
    """API: Получение списка услуг (по умолчанию только активные, ?all=1 для всех)"""
    try:
        active_only = request.query.get("all") != "1"
        services = await get_services(active_only=active_only)
        for s in services:
            s["duration"] = s.get("duration_min", 60)
        return web.json_response(services)
    except Exception as e:
        logger.error(f"Ошибка при получении услуг: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_service(request: web.Request) -> web.Response:
    """API: Создание новой услуги администратором"""
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        price = int(data.get("price") or 0)
        duration_min = int(data.get("duration_min") or data.get("duration") or 60)
        description = (data.get("description") or "").strip()
        badge = (data.get("badge") or "").strip()
        is_active = int(data.get("is_active", 1))

        if not name:
            return web.json_response({"error": "Название услуги обязательно"}, status=400)
        if price <= 0:
            return web.json_response({"error": "Цена услуги должна быть больше нуля"}, status=400)

        s_id = await create_service(
            name=name,
            price=price,
            duration_min=duration_min,
            description=description,
            badge=badge,
            is_active=is_active
        )
        return web.json_response({"ok": True, "id": s_id})
    except Exception as e:
        logger.error(f"Ошибка при создании услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_service(request: web.Request) -> web.Response:
    """API: Редактирование услуги администратором"""
    try:
        s_id = int(request.match_info["id"])
        data = await request.json()
        name = (data.get("name") or "").strip()
        price = int(data.get("price") or 0)
        duration_min = int(data.get("duration_min") or data.get("duration") or 60)
        description = (data.get("description") or "").strip()
        badge = (data.get("badge") or "").strip()
        is_active = int(data.get("is_active", 1))

        if not name or price <= 0:
            return web.json_response({"error": "Некорректное имя или цена"}, status=400)

        updated = await update_service(
            service_id=s_id,
            name=name,
            price=price,
            duration_min=duration_min,
            description=description,
            badge=badge,
            is_active=is_active
        )
        return web.json_response({"ok": updated})
    except Exception as e:
        logger.error(f"Ошибка при обновлении услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_delete_service(request: web.Request) -> web.Response:
    """API: Удаление услуги (soft delete)"""
    try:
        s_id = int(request.match_info["id"])
        deleted = await delete_service(service_id=s_id, hard=False)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error(f"Ошибка при удалении услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API БАРБЕРОВ (BARBERS CRUD)
# =============================================================================

async def handle_get_barbers(request: web.Request) -> web.Response:
    """API: Получение списка барберов (с опциональной проверкой даты ?date=YYYY-MM-DD)"""
    try:
        active_only = request.query.get("all") != "1"
        date_str = request.query.get("date")
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

        barbers = await get_barbers(active_only=active_only)
        for b in barbers:
            b["role"] = b.get("role_title", "Барбер")
            b["is_on_shift"] = bool(b.get("is_working_today", 1))
            if target_date:
                b["is_working_on_date"] = is_barber_working_on_date(b, target_date)
            else:
                b["is_working_on_date"] = bool(b.get("is_working_today", 1))

        return web.json_response(barbers)
    except Exception as e:
        logger.error(f"Ошибка при получении барберов: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_barber(request: web.Request) -> web.Response:
    """API: Добавление нового мастера администратором"""
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        role_title = (data.get("role_title") or data.get("role") or "Барбер").strip()
        phone = (data.get("phone") or "").strip() or None
        telegram_id_raw = data.get("telegram_id")
        telegram_id = int(telegram_id_raw) if telegram_id_raw else None
        work_days = (data.get("work_days") or "0,1,2,3,4,5,6").strip()
        is_working_today = int(data.get("is_working_today", data.get("is_on_shift", 1)))

        if not name:
            return web.json_response({"error": "Имя барбера обязательно"}, status=400)

        b_id = await create_barber(
            name=name,
            role_title=role_title,
            phone=phone,
            telegram_id=telegram_id,
            work_days=work_days,
            is_working_today=is_working_today
        )
        return web.json_response({"ok": True, "id": b_id})
    except Exception as e:
        logger.error(f"Ошибка при создании барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_barber(request: web.Request) -> web.Response:
    """API: Редактирование карточки мастера"""
    try:
        b_id = int(request.match_info["id"])
        data = await request.json()
        name = (data.get("name") or "").strip()
        role_title = (data.get("role_title") or data.get("role") or "Барбер").strip()
        phone = (data.get("phone") or "").strip() or None
        telegram_id_raw = data.get("telegram_id")
        telegram_id = int(telegram_id_raw) if telegram_id_raw else None
        work_days = (data.get("work_days") or "0,1,2,3,4,5,6").strip()
        is_working_today = int(data.get("is_working_today", data.get("is_on_shift", 1)))
        is_active = int(data.get("is_active", 1))

        if not name:
            return web.json_response({"error": "Имя барбера обязательно"}, status=400)

        updated = await update_barber(
            barber_id=b_id,
            name=name,
            role_title=role_title,
            phone=phone,
            telegram_id=telegram_id,
            work_days=work_days,
            is_working_today=is_working_today,
            is_active=is_active
        )
        return web.json_response({"ok": updated})
    except Exception as e:
        logger.error(f"Ошибка при обновлении барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_user_role(request: web.Request) -> web.Response:
    """API: Определение роли пользователя (admin, barber, client) по user_id"""
    try:
        user_id_raw = request.query.get("user_id")
        if not user_id_raw:
            return web.json_response({"role": "client"})

        user_id = int(user_id_raw)
        if settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID:
            return web.json_response({"role": "admin"})

        barber = await get_barber_by_telegram_id(user_id)
        if barber:
            return web.json_response({
                "role": "barber",
                "barber": {
                    "id": barber["id"],
                    "name": barber["name"],
                    "role": barber.get("role_title", "Барбер")
                }
            })
        return web.json_response({"role": "client"})
    except Exception as e:
        logger.error(f"Ошибка при определении роли: {e}")
        return web.json_response({"role": "client"})


async def handle_toggle_barber_shift(request: web.Request) -> web.Response:
    """API: Мгновенный тумблер смены мастера (1=на смене, 0=выходной)"""
    try:
        b_id = int(request.match_info["id"])
        data = await request.json() if request.can_read_body else {}
        val = data.get("is_working_today", data.get("is_on_shift"))
        is_working_today = int(val) if val is not None else None

        await toggle_barber_shift(barber_id=b_id, is_working_today=is_working_today)
        barber = await get_barber_by_id(b_id)
        is_on_shift = bool(barber.get("is_working_today")) if barber else True
        return web.json_response({"ok": True, "barber": barber, "is_on_shift": is_on_shift})
    except Exception as e:
        logger.error(f"Ошибка при переключении смены: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_delete_barber(request: web.Request) -> web.Response:
    """API: Удаление/архивация мастера"""
    try:
        b_id = int(request.match_info["id"])
        deleted = await delete_barber(barber_id=b_id, hard=False)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error(f"Ошибка при удалении барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API КАБИНЕТА МАСТЕРА (/barber)
# =============================================================================

async def handle_get_barber_my_schedule(request: web.Request) -> web.Response:
    """API: Получение расписания мастера по его telegram_id или barber_id"""
    try:
        tg_id_raw = request.query.get("telegram_id")
        b_id_raw = request.query.get("barber_id")
        target_date = request.query.get("date")

        barber = None
        if tg_id_raw and tg_id_raw.isdigit():
            barber = await get_barber_by_telegram_id(int(tg_id_raw))

        if not barber and b_id_raw and b_id_raw.isdigit():
            barber = await get_barber_by_id(int(b_id_raw))

        # Если мастер не найден, но запрос от администратора салона (для тестирования/просмотра)
        if not barber and tg_id_raw and tg_id_raw.isdigit():
            if settings.ADMIN_CHAT_ID and int(tg_id_raw) == settings.ADMIN_CHAT_ID:
                barbers = await get_barbers()
                if barbers:
                    barber = barbers[0]

        # Если всё еще не найден и нет ID (dev-режим в браузере)
        if not barber and not tg_id_raw and not b_id_raw:
            barbers = await get_barbers()
            if barbers:
                barber = barbers[0]

        if not barber:
            return web.json_response({"error": "Мастер не найден"}, status=404)

        barber["is_on_shift"] = bool(barber.get("is_working_today", 1))
        bookings = await get_bookings_for_barber(barber["id"], target_date=target_date)
        return web.json_response({
            "ok": True,
            "barber": barber,
            "bookings": bookings,
            "schedule": bookings
        })
    except Exception as e:
        logger.error(f"Ошибка при получении расписания мастера: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API БРОНИРОВАНИЙ (BOOKINGS)
# =============================================================================

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
    Проверяет доступность времени с учетом мастера и длительности.
    Автоматически подбирает свободного мастера при выборе «Любой мастер».
    Отправляет Telegram-уведомления клиенту, администратору и назначенному мастеру!
    """
    try:
        data = await request.json()
        full_name = (data.get("full_name") or "").strip()
        raw_booking_time = (data.get("booking_time") or "").strip()
        service = data.get("service") or "Стрижка / Комплекс"
        service_id = data.get("service_id")
        phone = data.get("phone")
        user_id = data.get("user_id") or 0
        username = data.get("username")
        details = data.get("details") or "Онлайн-запись через Web App"

        barber_id_raw = data.get("barber_id")
        barber_id = int(barber_id_raw) if barber_id_raw and int(barber_id_raw) > 0 else None
        barber_name = data.get("barber_name")
        duration_minutes = int(data.get("duration_minutes") or 60)

        if not full_name:
            return web.json_response({"error": "Имя клиента обязательно для заполнения"}, status=400)
        if not raw_booking_time:
            return web.json_response({"error": "Дата и время обязательны для заполнения"}, status=400)

        # Валидация времени и проверка слотов
        target_dt = parse_booking_datetime(raw_booking_time)
        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True)

        assigned_barber = None
        if target_dt:
            res = check_slot_availability(
                target_dt=target_dt,
                existing_bookings=active_bookings,
                barber_id=barber_id,
                available_barbers=barbers,
                duration_minutes=duration_minutes
            )
            if not res.is_available:
                return web.json_response({"error": res.error_message}, status=400)

            normalized_time = format_booking_datetime(target_dt)
            # Назначенный мастер (если был выбран конкретный или авто-подбор)
            final_barber_id = res.assigned_barber_id or barber_id
            final_barber_name = res.assigned_barber_name or barber_name
            if final_barber_id and barbers:
                assigned_barber = next((b for b in barbers if b["id"] == final_barber_id), None)
                if assigned_barber and not final_barber_name:
                    final_barber_name = assigned_barber["name"]
        else:
            normalized_time = raw_booking_time
            final_barber_id = barber_id
            final_barber_name = barber_name

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
            barber_id=final_barber_id,
            barber_name=final_barber_name,
            service_id=service_id,
        )

        bot = request.app.get(BOT_KEY)

        # 1. Мгновенное подтверждение клиенту в личный чат Telegram
        if bot and user_id and user_id != 0:
            master_line = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            client_msg = (
                "💈 **Вы успешно записаны в барбершоп Brohouse!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"{master_line}"
                f"⏰ **Время:** {normalized_time}\n"
                "📍 **Адрес:** метро «Миллий бог», 8B\n"
                "📞 **Телефон:** +998 97 012-55-55\n\n"
                "Ждем вас! Если потребуется отменить запись — вы можете сделать это прямо через онлайн-меню или написав мне в чат."
            )
            try:
                await bot.send_message(chat_id=user_id, text=client_msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Не удалось отправить подтверждение клиенту {user_id}: {e}")

        # 2. Уведомление администратору салона
        if bot and settings.ADMIN_CHAT_ID:
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_msg = (
                "💈 **НОВАЯ ОНЛАЙН-ЗАПИСЬ (из Web App)!**\n\n"
                f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
                f"{master_str}"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                "📱 _Подтвердите или отклоните кнопками ниже:_"
            )
            try:
                await bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode="Markdown",
                    reply_markup=get_booking_action_keyboard(booking_id)
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление админу: {e}")

        # 3. Мгновенное push-уведомление назначенному мастеру в Telegram
        if bot and assigned_barber and assigned_barber.get("telegram_id"):
            master_alert = (
                "💈 **У ВАС НОВАЯ ЗАПИСЬ НА СМЕНУ!**\n\n"
                f"👤 **Клиент:** {full_name}\n"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                "📱 _Подтвердите или отклоните прямо сейчас:_"
            )
            try:
                await bot.send_message(
                    chat_id=assigned_barber["telegram_id"],
                    text=master_alert,
                    parse_mode="Markdown",
                    reply_markup=get_booking_action_keyboard(booking_id)
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление мастеру: {e}")

        return web.json_response({
            "ok": True,
            "id": booking_id,
            "booking_time": normalized_time,
            "barber_id": final_barber_id,
            "barber_name": final_barber_name,
        })

    except Exception as e:
        logger.error(f"Ошибка при создании записи через API: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_status(request: web.Request) -> web.Response:
    """API: Смена статуса записи (new, confirmed, cancelled) с авто-уведомлением клиенту"""
    try:
        booking_id = int(request.match_info["id"])
        data = await request.json()
        new_status = data.get("status")

        if not new_status:
            return web.json_response({"error": "Статус не передан"}, status=400)

        # Получаем данные брони перед обновлением
        booking = await get_booking_by_id(booking_id)
        if not booking:
            return web.json_response({"error": "Запись не найдена"}, status=404)

        success = await update_booking_status(booking_id, new_status)
        if not success:
            return web.json_response({"error": "Не удалось обновить статус в БД"}, status=500)

        # Двустороннее оповещение клиента в Telegram при смене статуса администратором
        bot = request.app.get(BOT_KEY)
        client_tg_id = booking.get("user_id")

        if bot and client_tg_id and client_tg_id != 0:
            b_time = booking.get("booking_time") or ""
            service = booking.get("service") or "Стрижка"
            barber = booking.get("barber_name") or "Мастер"

            if new_status == "confirmed":
                msg = (
                    "✅ **Ваша запись в Brohouse ПОДТВЕРЖДЕНА!**\n\n"
                    f"✂️ **Услуга:** {service}\n"
                    f"💈 **Мастер:** {barber}\n"
                    f"⏰ **Время:** {b_time}\n"
                    "📍 **Адрес:** метро «Миллий бог», 8B\n"
                    "📞 **Телефон:** +998 97 012-55-55\n\n"
                    "Мы уже ждем вас к назначенному времени, ака! 💈"
                )
                try:
                    await bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление о подтверждении клиенту {client_tg_id}: {e}")

            elif new_status == "cancelled":
                msg = (
                    "ℹ️ **Ваша запись в Brohouse была отменена.**\n\n"
                    f"⏰ Время: {b_time}\n"
                    f"✂️ Услуга: {service}\n\n"
                    "Если вы хотите выбрать другое время или мастера — просто откройте онлайн-меню или напишите мне в чат."
                )
                try:
                    await bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление об отмене клиенту {client_tg_id}: {e}")

        return web.json_response({"ok": True, "status": new_status})
    except Exception as e:
        logger.error(f"Ошибка при смене статуса записи: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_my_booking(request: web.Request) -> web.Response:
    """API: Получение активной записи конкретного пользователя для клиентского TMA"""
    try:
        user_id_raw = request.query.get("user_id")
        if not user_id_raw or not user_id_raw.isdigit():
            return web.json_response({"booking": None})

        user_id = int(user_id_raw)
        booking = await get_user_active_booking(user_id)
        return web.json_response({"booking": booking})
    except Exception as e:
        logger.error(f"Ошибка при получении записи пользователя: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_cancel_my_booking(request: web.Request) -> web.Response:
    """API: Отмена текущей активной брони клиентом через TMA кнопку"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        booking_id = data.get("booking_id")

        if not user_id:
            return web.json_response({"error": "user_id обязателен"}, status=400)

        cancelled = await cancel_user_booking(user_id=int(user_id), booking_id=int(booking_id) if booking_id else None)
        if not cancelled:
            return web.json_response({"error": "Активная бронь не найдена"}, status=404)

        bot = request.app.get(BOT_KEY)
        if bot:
            if settings.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=(
                            f"🔔 **ОТМЕНА ЗАПИСИ (через Client TMA):**\n\n"
                            f"👤 **Клиент:** {cancelled['full_name']} (@{cancelled.get('username') or 'нет'})\n"
                            f"💈 **Мастер:** {cancelled.get('barber_name') or 'Любой'}\n"
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


# =============================================================================
# ПРИЛОЖЕНИЕ AIOHTTP И РОУТЕР
# =============================================================================

def create_web_app(bot: Bot = None) -> web.Application:
    """Фабрика создания приложения aiohttp для TMA"""
    app = web.Application()
    app[BOT_KEY] = bot

    # Статические файлы (аватарки барберов, логотипы, иконки)
    if os.path.exists(STATIC_DIR_PATH):
        app.router.add_static("/static", STATIC_DIR_PATH, name="static")

    # TMA страницы
    app.router.add_get("/", handle_client)
    app.router.add_get("/book", handle_client)
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/webapp", handle_admin)
    app.router.add_get("/barber", handle_barber_page)

    # API маршруты
    app.router.add_get("/api/config", handle_get_config)
    app.router.add_get("/api/user-role", handle_get_user_role)
    app.router.add_get("/api/slots", handle_get_slots)

    # Услуги
    app.router.add_get("/api/services", handle_get_services)
    app.router.add_post("/api/services", handle_create_service)
    app.router.add_post("/api/services/{id}/update", handle_update_service)
    app.router.add_post("/api/services/{id}/delete", handle_delete_service)

    # Барберы
    app.router.add_get("/api/barbers", handle_get_barbers)
    app.router.add_post("/api/barbers", handle_create_barber)
    app.router.add_post("/api/barbers/{id}/update", handle_update_barber)
    app.router.add_post("/api/barbers/{id}/toggle-shift", handle_toggle_barber_shift)
    app.router.add_post("/api/barbers/{id}/delete", handle_delete_barber)

    # Кабинет мастера
    app.router.add_get("/api/barber/my-schedule", handle_get_barber_my_schedule)

    # Бронирования
    app.router.add_get("/api/bookings", handle_get_bookings)
    app.router.add_post("/api/bookings", handle_create_booking)
    app.router.add_post("/api/bookings/{id}/status", handle_update_status)
    app.router.add_get("/api/my-booking", handle_get_my_booking)
    app.router.add_post("/api/my-booking/cancel", handle_cancel_my_booking)

    return app


async def start_web_server(host: str = None, port: int = None, bot: Bot = None) -> web.AppRunner:
    """Запуск асинхронного веб-сервера aiohttp на общем event loop"""
    h = host or settings.WEBAPP_HOST or "0.0.0.0"
    p = port or settings.WEBAPP_PORT or 8000

    app = create_web_app(bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, h, p)
    await site.start()
    logger.info("🌐 Сервер Telegram Mini App успешно запущен: http://%s:%s", h, p)
    return runner


if __name__ == "__main__":
    from database import init_db
    import asyncio
    asyncio.run(init_db())
    web.run_app(create_web_app(), host="127.0.0.1", port=8000)
