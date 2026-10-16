"""
Middleware для aiogram 3: Жесткий контроль доступа и проверка статуса подписки (Kill Switch).
"""

import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import settings
from services.billing_service import check_tenant_access

logger = logging.getLogger(__name__)


class SubscriptionCheckMiddleware(BaseMiddleware):
    """
    Middleware проверки подписки заведения (Kill Switch).
    Проверяет статус подписки при ЛЮБОМ входящем обращении (сообщение или инлайн-кнопка).
    - Если отправитель SUPER_ADMIN_CHAT_ID: пропускает без ограничений (Bypass).
    - Если система LOCKED:
      * Клиент: блокирует попытки записи и отвечает:
        "Система онлайн-бронирования временно недоступна. Пожалуйста, свяжитесь с салоном напрямую."
      * Мастер (/barber или кнопки): блокирует доступ к смене/кабинету.
      * Админ салона (ADMIN_CHAT_ID): сообщает о блокировке и необходимости оплаты.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = user.id if user else None

        # 1. Super-Admin bypass
        super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
        if super_admin_id and user_id == super_admin_id:
            return await handler(event, data)

        # 2. Проверка статуса подписки текущего арендатора
        is_locked, status, info = await check_tenant_access(settings.TENANT_SLUG)

        if not is_locked:
            # Если подписка активна или в grace period — штатная работа
            return await handler(event, data)

        # 3. Система заблокирована (LOCKED)
        if isinstance(event, Message):
            # Если это локальный админ салона
            if settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID:
                await event.answer(
                    "❌ **Доступ к управлению заблокирован!**\n\n"
                    "Срок действия лицензии салона и льготного периода завершён.\n"
                    "Пожалуйста, свяжитесь с суперадминистратором платформы для продления подписки.",
                    parse_mode="Markdown"
                )
                return

            text = (event.text or "").strip()
            # Если обращение в кабинет мастера
            if text.startswith("/barber"):
                await event.answer(
                    "🔒 **Кабинет мастера временно заблокирован.**\n"
                    "Срок действия подписки салона истёк. Пожалуйста, обратитесь к руководству.",
                    parse_mode="Markdown"
                )
                return

            # Для всех обычных клиентов
            await event.answer(
                "Система онлайн-бронирования временно недоступна. Пожалуйста, свяжитесь с салоном напрямую."
            )
            return

        elif isinstance(event, CallbackQuery):
            await event.answer(
                "Система онлайн-бронирования временно недоступна. Пожалуйста, свяжитесь с салоном напрямую.",
                show_alert=True,
            )
            return

        return None
