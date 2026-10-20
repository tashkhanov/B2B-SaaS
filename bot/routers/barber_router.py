"""
Модульный роутер для мастеров салона (Barber Router).
Обрабатывает:
- /barber (открытие кабинета смены мастера TMA)
- vip_accept:<id> (согласование VIP-записи Шефом / мастером)
- vip_reject:<id> (отклонение VIP-записи Шефом / мастером)
"""

import html
import logging
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.filters import Command

from config import settings
from database import (
    get_barber_by_telegram_id,
    get_booking_by_id,
    update_booking_status,
)
from bot.routers.common_router import get_user_role

logger = logging.getLogger(__name__)

router = Router(name="barber_router")


@router.message(Command("barber"))
async def handle_barber(message: Message):
    """
    Команда /barber для вызова TMA кабинета мастера ('Моя смена').
    Доступна барберам салона и администратору.
    """
    user_id = message.from_user.id if message.from_user else 0
    barber = await get_barber_by_telegram_id(user_id)
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)

    if not barber and not is_admin:
        await message.answer(
            "⚠️ Ваш Telegram ID не привязан ни к одному мастеру салона.\n"
            "Попросите администратора указать ваш Telegram ID в панели управления!"
        )
        return

    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    b_name = barber["name"] if barber else "Администратор"

    if base_url.startswith("https://"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💈 Открыть Мою Смену (TMA)",
                        web_app=WebAppInfo(url=f"{base_url}/barber"),
                    )
                ]
            ]
        )
        await message.answer(
            f"Салам, {b_name}! 💈\n\n"
            "В кабинете мастера вы можете управлять статусом смены ('На смене / Отдыхаю') и видеть всех своих клиентов на сегодня.",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    else:
        barber_url = f"{base_url}/barber" if base_url else "http://localhost:8000/barber"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть в браузере", url=barber_url)]]
        )
        await message.answer(
            f"Салам, {b_name}! 💈\n"
            f"Ссылка на кабинет смены: `{barber_url}`",
            reply_markup=kb,
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("vip_accept:"))
async def handle_vip_accept_callback(callback: CallbackQuery):
    """Шеф-мастер (Mr. Akmal) или админ подтверждает VIP-запись."""
    user_id = callback.from_user.id if callback.from_user else 0
    role, barber = await get_user_role(user_id)
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if role not in ("admin", "barber", "super_admin") and user_id not in (admin_id, super_admin_id):
        await callback.answer("У вас нет прав для подтверждения этой VIP-записи.", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        if booking.get("status") == "cancelled":
            try:
                old_text = callback.message.text or ""
                await callback.message.edit_text(
                    f"{old_text}\n\n⚠️ ЗАПИСЬ УЖЕ БЫЛА ОТМЕНЕНА КЛИЕНТОМ",
                    reply_markup=None,
                    parse_mode=None
                )
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await callback.answer("⚠️ Эта запись уже была отменена клиентом!", show_alert=True)
            return

        await update_booking_status(booking_id, "confirmed")

        actor_name = barber["name"] if barber else (callback.from_user.first_name or "Шеф")
        try:
            old_text = callback.message.text or ""
            await callback.message.edit_text(
                f"{old_text}\n\n✅ ВИЗИТ ПРИНЯТ ШЕФОМ! (Подтвердил: {actor_name})",
                reply_markup=None,
                parse_mode=None
            )
        except Exception:
            pass

        # Уведомляем клиента
        client_tg_id = booking.get("user_id")
        if client_tg_id and client_tg_id != 0:
            b_name = booking.get("barber_name") or settings.OWNER_NAME
            b_time = booking.get("booking_time") or ""
            service = booking.get("service") or "Стрижка"
            branch = booking.get("branch_name") or settings.SALON_SHORT_NAME
            msg_html = (
                "👑 <b>Шеф-мастер ПОДТВЕРДИЛ вашу запись!</b>\n\n"
                f"✂️ <b>Услуга:</b> {html.escape(str(service))}\n"
                f"💈 <b>Мастер:</b> {html.escape(str(b_name))}\n"
                f"⏰ <b>Время:</b> {html.escape(str(b_time))}\n"
                f"📍 <b>Филиал:</b> {html.escape(str(branch))}\n\n"
                f"{html.escape(str(b_name))} ждет вас в назначенное время, ака! 💈"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg_html, parse_mode="HTML")
            except Exception:
                plain_msg = (
                    "👑 Шеф-мастер ПОДТВЕРДИЛ вашу запись!\n\n"
                    f"✂️ Услуга: {service}\n"
                    f"💈 Мастер: {b_name}\n"
                    f"⏰ Время: {b_time}\n"
                    f"📍 Филиал: {branch}\n\n"
                    f"{b_name} ждет вас в назначенное время, ака! 💈"
                )
                try:
                    await callback.bot.send_message(chat_id=client_tg_id, text=plain_msg, parse_mode=None)
                except Exception as e:
                    logger.warning("Failed to notify client on vip_accept: %s", e)

        await callback.answer("VIP-запись подтверждена! Клиент уведомлен.")
    except Exception as e:
        logger.error("Error in handle_vip_accept_callback: %s", e)
        await callback.answer("Произошла ошибка при подтверждении VIP-записи.", show_alert=True)


@router.callback_query(F.data.startswith("vip_reject:"))
async def handle_vip_reject_callback(callback: CallbackQuery):
    """Шеф-мастер (Mr. Akmal) или админ отклоняет VIP-запись."""
    user_id = callback.from_user.id if callback.from_user else 0
    role, barber = await get_user_role(user_id)
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if role not in ("admin", "barber", "super_admin") and user_id not in (admin_id, super_admin_id):
        await callback.answer("У вас нет прав для отклонения этой VIP-записи.", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        if booking.get("status") == "cancelled":
            try:
                old_text = callback.message.text or ""
                await callback.message.edit_text(
                    f"{old_text}\n\n⚠️ ЗАПИСЬ УЖЕ БЫЛА ОТМЕНЕНА КЛИЕНТОМ",
                    reply_markup=None,
                    parse_mode=None
                )
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await callback.answer("⚠️ Эта запись уже была отменена клиентом!", show_alert=True)
            return

        await update_booking_status(booking_id, "cancelled")

        actor_name = barber["name"] if barber else (callback.from_user.first_name or "Шеф")
        try:
            old_text = callback.message.text or ""
            await callback.message.edit_text(
                f"{old_text}\n\n❌ ВИЗИТ ОТКЛОНЕН ШЕФОМ (Кем: {actor_name})",
                reply_markup=None,
                parse_mode=None
            )
        except Exception:
            pass

        # Уведомляем клиента с предложением перезаписи
        client_tg_id = booking.get("user_id")
        if client_tg_id and client_tg_id != 0:
            b_name = booking.get("barber_name") or settings.OWNER_NAME
            b_time = booking.get("booking_time") or ""
            base_url = (getattr(settings, "WEBAPP_URL", "") or "").rstrip("/")
            has_https = base_url.startswith("https://")
            if has_https:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="✂️ Выбрать другое время / мастера", web_app=WebAppInfo(url=f"{base_url}/book"))
                    ]]
                )
            else:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="✂️ Выбрать другое время", callback_data="open_booking_dialog")
                    ]]
                )

            msg_html = (
                f"ℹ️ <b>К сожалению, Шеф-мастер {html.escape(str(b_name))} не сможет принять вас в назначенное время ({html.escape(str(b_time))}).</b>\n\n"
                "График шефа очень плотный. Пожалуйста, выберите другое удобное окно или запишитесь к любому из наших топ-стилистов:"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg_html, reply_markup=kb, parse_mode="HTML")
            except Exception:
                plain_msg = (
                    f"ℹ️ К сожалению, Шеф-мастер {b_name} не сможет принять вас в назначенное время ({b_time}).\n\n"
                    "График шефа очень плотный. Пожалуйста, выберите другое удобное окно или запишитесь к любому из наших топ-стилистов:"
                )
                try:
                    await callback.bot.send_message(chat_id=client_tg_id, text=plain_msg, reply_markup=kb, parse_mode=None)
                except Exception as e:
                    logger.warning("Failed to notify client on vip_reject: %s", e)

        await callback.answer("VIP-запись отклонена. Клиент уведомлен.")
    except Exception as e:
        logger.error("Error in handle_vip_reject_callback: %s", e)
        await callback.answer("Произошла ошибка при отклонении VIP-записи.", show_alert=True)
