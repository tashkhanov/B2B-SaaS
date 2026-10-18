"""
Модуль удержания клиентов (LTV / Retention Service) платформы KHAN AI SYSTEMS.
Реализует функции тарифа PRO BUSINESS и ENTERPRISE:
1. Автопуш клиенту за 2 часа до времени записи с инлайн-кнопками:
   - [Буду вовремя ✅] -> фиксирует подтверждение явки
   - [Не смогу прийти ❌] -> освобождает слот, уведомляет салон и дает кнопку перезаписи в TMA
2. Автопуш возврата через 25 дней (Win-Back) клиентам без будущих записей.
"""

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import settings
from database import (
    get_bookings_due_for_2h_reminder,
    mark_booking_reminder_sent,
    get_clients_due_for_winback,
    mark_client_winback_sent,
)
from services.tier_service import is_feature_enabled

logger = logging.getLogger(__name__)


def build_reminder_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для подтверждения визита за 2 часа."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Буду вовремя ✅", callback_data=f"confirm_visit:{booking_id}"),
                InlineKeyboardButton(text="Не смогу прийти ❌", callback_data=f"cancel_visit:{booking_id}"),
            ]
        ]
    )


def build_winback_keyboard(base_url_override: Optional[str] = None) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для пуша возврата (Win-Back) через 25 дней."""
    base_url = (base_url_override or getattr(settings, "WEBAPP_URL", "") or "").rstrip("/")
    if base_url.startswith("https://"):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💈 Записаться в {settings.SALON_SHORT_NAME} (TMA)",
                        web_app=WebAppInfo(url=f"{base_url}/book"),
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💈 Записаться на стрижку", callback_data="open_booking_dialog")
            ]
        ]
    )


async def check_and_send_2h_reminders(bot: Bot, db_path: Optional[str] = None) -> int:
    """
    Проверка и отправка напоминаний за 2 часа до начала визита.
    Функция заблокирована на тарифе START и активна на PRO / ENTERPRISE.
    """
    try:
        enabled = await is_feature_enabled("has_smart_reminders", db_path=db_path)
        if not enabled:
            logger.debug("Smart 2h reminders disabled by tenant subscription tier.")
            return 0

        due_bookings = await get_bookings_due_for_2h_reminder(
            window_start_minutes=105,
            window_end_minutes=135,
            db_path=db_path
        )

        sent_count = 0
        for booking in due_bookings:
            booking_id = booking["id"]
            user_id = booking.get("user_id")
            if not user_id:
                continue

            # Помечаем отправку для предотвращения дублирования
            await mark_booking_reminder_sent(booking_id, db_path=db_path)

            client_name = booking.get("full_name") or "Дорогой клиент"
            booking_time = booking.get("booking_time") or "сегодня"
            service = booking.get("service") or "Стрижка"
            barber = booking.get("barber_name") or f"Мастер {settings.SALON_SHORT_NAME}"
            branch = booking.get("branch_name") or settings.SALON_SHORT_NAME

            text = (
                f"💈 **Напоминаем о вашем визите в {settings.SALON_SHORT_NAME}!**\n\n"
                f"👤 Клиент: **{client_name}**\n"
                f"⏰ Время визита: **{booking_time}** (через ~2 часа)\n"
                f"✂️ Услуга: {service}\n"
                f"💈 Барбер: {barber}\n"
                f"📍 Филиал: {branch}\n\n"
                f"Пожалуйста, подтвердите ваш визит, чтобы мастер был готов вас принять:"
            )

            try:
                kb = build_reminder_keyboard(booking_id)
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
                sent_count += 1
                logger.info("2h reminder sent for booking #%s to user %s", booking_id, user_id)
            except Exception as e:
                logger.warning("Failed to send 2h reminder for booking #%s to user %s: %s", booking_id, user_id, e)

        return sent_count

    except Exception as e:
        logger.error("Error in check_and_send_2h_reminders: %s", e)
        return 0


async def check_and_send_winback_pushes(bot: Bot, db_path: Optional[str] = None) -> int:
    """
    Проверка и отправка пушей возврата клиентов (через 25 дней после стрижки).
    Функция заблокирована на тарифе START и активна на PRO / ENTERPRISE.
    """
    try:
        enabled = await is_feature_enabled("has_winback_pushes", db_path=db_path)
        if not enabled:
            logger.debug("Winback pushes disabled by tenant subscription tier.")
            return 0

        due_clients = await get_clients_due_for_winback(days_threshold=25, db_path=db_path)

        sent_count = 0
        for client in due_clients:
            booking_id = client["id"]
            user_id = client.get("user_id")
            if not user_id:
                continue

            await mark_client_winback_sent(booking_id, db_path=db_path)

            client_name = client.get("full_name") or "Ака"
            first_name = client_name.split()[0] if client_name else "Ака"

            text = (
                f"💈 **{first_name}, прошло 25 дней со стрижки в {settings.SALON_SHORT_NAME}!**\n\n"
                f"Самое время освежить образ, подравнять контуры бороды и отдохнуть в мужской атмосфере. ✂️\n\n"
                f"Ваш барбер готов вас принять. Забронируйте удобное время за 10 секунд:"
            )

            try:
                kb = build_winback_keyboard()
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
                sent_count += 1
                logger.info("Winback push sent for booking #%s to user %s", booking_id, user_id)
            except Exception as e:
                logger.warning("Failed to send winback push for booking #%s to user %s: %s", booking_id, user_id, e)

        return sent_count

    except Exception as e:
        logger.error("Error in check_and_send_winback_pushes: %s", e)
        return 0


async def run_retention_notifier(
    bot: Bot,
    check_interval_seconds: int = 300,
    db_path: Optional[str] = None
) -> None:
    """
    Фоновый рабочий цикл модуля удержания клиентов.
    Периодически запускает проверку 2-часовых напоминаний и 25-дневного Win-back.
    """
    logger.info("Retention notifier worker started (check every %s sec).", check_interval_seconds)
    while True:
        try:
            await check_and_send_2h_reminders(bot, db_path=db_path)
            await check_and_send_winback_pushes(bot, db_path=db_path)
        except asyncio.CancelledError:
            logger.info("Retention notifier worker cancelled.")
            break
        except Exception as e:
            logger.error("Unexpected error in retention notifier loop: %s", e)

        try:
            await asyncio.sleep(check_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Retention notifier worker sleep cancelled.")
            break
