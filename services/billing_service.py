"""
Сервис биллинга, подписок и контроля доступа (Kill Switch) для SaaS-платформы.
Управляет жизненным циклом подписок заведений:
- ACTIVE: обычный доступ, все функции работают
- WARNING: за 3 дня до окончания, отправляется предупреждение
- GRACE_PERIOD: 2 дня после окончания срока, доступ не блокируется, но в админке горит плашка
- LOCKED: ручная блокировка или превышение льготного периода, доступ закрывается
"""

import asyncio
import logging
from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List

from config import settings
from database import (
    get_tenant_subscription,
    get_all_tenants,
    update_tenant_subscription,
)
from services.schedule_service import get_now_tashkent

logger = logging.getLogger(__name__)


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    WARNING = "warning"
    GRACE_PERIOD = "grace_period"
    LOCKED = "locked"


def parse_datetime(dt_val) -> Optional[datetime]:
    """Безопасный парсер даты/времени из строки или datetime."""
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(dt_val).strip(), fmt)
        except ValueError:
            continue
    return None


def calculate_subscription_status(
    subscription: Dict,
    now: Optional[datetime] = None,
) -> Tuple[SubscriptionStatus, Dict]:
    """
    Расчет текущего статуса подписки и метаданных.
    Возвращает (SubscriptionStatus, info_dict).
    """
    now_dt = now or get_now_tashkent().replace(tzinfo=None)

    is_active_flag = subscription.get("is_active", 1)
    # 1. Принудительный ручной Kill Switch
    if not is_active_flag or is_active_flag == 0:
        return SubscriptionStatus.LOCKED, {
            "status": SubscriptionStatus.LOCKED,
            "is_locked": True,
            "days_left": -999,
            "status_label": "Заблокировано (Kill Switch)",
            "badge_color": "red",
            "message": "Доступ отключен вручную главным администратором.",
            "raw": subscription,
        }

    expires_at_dt = parse_datetime(subscription.get("expires_at"))
    if not expires_at_dt:
        return SubscriptionStatus.LOCKED, {
            "status": SubscriptionStatus.LOCKED,
            "is_locked": True,
            "days_left": 0,
            "status_label": "Некорректная дата",
            "badge_color": "red",
            "message": "Дата окончания подписки не установлена.",
            "raw": subscription,
        }

    grace_days = int(subscription.get("grace_period_days", 2))
    grace_expiry_dt = expires_at_dt + timedelta(days=grace_days)

    # Разница во времени
    delta_to_expire = expires_at_dt - now_dt
    delta_to_grace = grace_expiry_dt - now_dt

    days_left = delta_to_expire.days
    seconds_left = int(delta_to_expire.total_seconds())

    # 2. Срок еще не наступил
    if now_dt < expires_at_dt:
        if days_left <= 3:
            return SubscriptionStatus.WARNING, {
                "status": SubscriptionStatus.WARNING,
                "is_locked": False,
                "days_left": max(0, days_left),
                "seconds_left": max(0, seconds_left),
                "status_label": f"Истекает через {max(1, days_left)} дн.",
                "badge_color": "yellow",
                "message": f"Подписка истекает через {max(1, days_left)} дн.",
                "raw": subscription,
            }
        else:
            return SubscriptionStatus.ACTIVE, {
                "status": SubscriptionStatus.ACTIVE,
                "is_locked": False,
                "days_left": days_left,
                "seconds_left": seconds_left,
                "status_label": f"Активна ({days_left} дн.)",
                "badge_color": "green",
                "message": "Подписка активна.",
                "raw": subscription,
            }

    # 3. expires_at истек, но действует льготный период grace period
    if now_dt < grace_expiry_dt:
        grace_hours_left = max(1, int(delta_to_grace.total_seconds() // 3600))
        return SubscriptionStatus.GRACE_PERIOD, {
            "status": SubscriptionStatus.GRACE_PERIOD,
            "is_locked": False,
            "days_left": 0,
            "grace_hours_left": grace_hours_left,
            "status_label": f"Grace Period ({grace_hours_left} ч.)",
            "badge_color": "amber",
            "message": f"Срок подписки истек. Действует льготный период ({grace_hours_left} ч.).",
            "raw": subscription,
        }

    # 4. Льготный период полностью исчерпан -> Полная блокировка
    return SubscriptionStatus.LOCKED, {
        "status": SubscriptionStatus.LOCKED,
        "is_locked": True,
        "days_left": days_left,
        "status_label": "Заблокировано (Срок истек)",
        "badge_color": "red",
        "message": "Срок действия подписки и льготного периода истек. Доступ заблокирован.",
        "raw": subscription,
    }


def is_subscription_locked(subscription: Dict, now: Optional[datetime] = None) -> bool:
    """Удобный хелпер для проверки заблокирована ли подписка."""
    status, info = calculate_subscription_status(subscription, now=now)
    return info.get("is_locked", False)


async def check_tenant_access(
    tenant_slug: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[bool, SubscriptionStatus, Dict]:
    """
    Проверка доступа для арендатора.
    Возвращает: (is_locked: bool, status: SubscriptionStatus, info: Dict)
    """
    slug = tenant_slug or getattr(settings, "TENANT_SLUG", "brohouse")
    subscription = await get_tenant_subscription(slug, db_path=db_path)

    if not subscription:
        # Если салон не найден в базе, по умолчанию создаем безопасную заглушку
        return True, SubscriptionStatus.LOCKED, {
            "status": SubscriptionStatus.LOCKED,
            "is_locked": True,
            "name": slug,
            "message": f"Салон '{slug}' не зарегистрирован в системе биллинга.",
        }

    status, info = calculate_subscription_status(subscription)
    info["name"] = subscription.get("name", slug)
    info["tenant_slug"] = slug
    info["id"] = subscription.get("id")
    info["admin_chat_id"] = subscription.get("admin_chat_id")
    info["contact_phone"] = subscription.get("contact_phone", "")
    info["contact_name"] = subscription.get("contact_name", "")
    info["expires_at"] = subscription.get("expires_at", "")
    info["grace_period_days"] = subscription.get("grace_period_days", 2)
    return info.get("is_locked", False), status, info


# =============================================================================
# ФОНОВЫЙ ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ (CRON LOOP)
# =============================================================================

async def check_and_send_billing_notifications(bot) -> None:
    """
    Однократная итерация проверки всех заведений в базе и рассылка уведомлений:
    - За 3 дня: предупреждение о скором завершении
    - В день окончания: предупреждение о 2 днях льготного периода
    - После окончания льготного периода: уведомление о полной блокировке
    """
    try:
        tenants = await get_all_tenants()
        now_dt = get_now_tashkent().replace(tzinfo=None)
        super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)

        for t in tenants:
            tenant_id = t["id"]
            slug = t["tenant_slug"]
            name = t["name"]
            admin_chat_id = t.get("admin_chat_id")
            last_stage = t.get("last_notification_stage", "none")

            status, info = calculate_subscription_status(t, now=now_dt)

            # 1. За 3 дня до expires_at
            if status == SubscriptionStatus.WARNING and last_stage != "warning_3d":
                text = (
                    f"⚠️ **ВНИМАНИЕ: Подписка истекает через 3 дня!**\n\n"
                    f"🏢 Салон: **{name}**\n"
                    f"📅 Срок действия до: `{t['expires_at']}`\n\n"
                    "Пожалуйста, свяжитесь с суперадминистратором и произведите оплату, "
                    "чтобы система онлайн-записи и кабинеты работали без перебоев."
                )
                if admin_chat_id and bot:
                    try:
                        await bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="Markdown")
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление админу {admin_chat_id}: {e}")

                if super_admin_id and bot and super_admin_id != admin_chat_id:
                    try:
                        await bot.send_message(
                            chat_id=super_admin_id,
                            text=f"🔔 [Super-Admin Alert]\nУ клиента **{name}** ({slug}) подписка истекает через 3 дня (`{t['expires_at']}`).",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление суперадмину: {e}")

                await update_tenant_subscription(tenant_id, {"last_notification_stage": "warning_3d"})

            # 2. В день окончания (Grace Period 2 дня)
            elif status == SubscriptionStatus.GRACE_PERIOD and last_stage != "expired_0d":
                text = (
                    f"❌ **СРОК ПОДПИСКИ ИСТЁК!**\n\n"
                    f"🏢 Салон: **{name}**\n"
                    f"⏳ У вас есть **2 дня льготного периода** для оплаты до полной блокировки системы.\n\n"
                    "По истечении 2 дней запись клиентов и личные кабинеты мастеров будут отключены."
                )
                if admin_chat_id and bot:
                    try:
                        await bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="Markdown")
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление админу {admin_chat_id}: {e}")

                if super_admin_id and bot and super_admin_id != admin_chat_id:
                    try:
                        await bot.send_message(
                            chat_id=super_admin_id,
                            text=f"⚠️ [Super-Admin Alert]\nПодписка клиента **{name}** ({slug}) истекла! Активирован льготный период (2 дня).",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление суперадмину: {e}")

                await update_tenant_subscription(tenant_id, {"last_notification_stage": "expired_0d"})

            # 3. Полная блокировка (срок истек + льготный период прошел)
            elif status == SubscriptionStatus.LOCKED and last_stage != "locked" and t.get("is_active", 1) == 1:
                text = (
                    f"🚫 **СИСТЕМА ЗАБЛОКИРОВАНА!**\n\n"
                    f"🏢 Салон: **{name}**\n"
                    "Срок действия подписки и льготного периода завершён.\n"
                    "Все модули онлайн-записи и кабинеты мастеров переведены в режим блокировки.\n\n"
                    "Для разблокировки произведите оплату подписки."
                )
                if admin_chat_id and bot:
                    try:
                        await bot.send_message(chat_id=admin_chat_id, text=text, parse_mode="Markdown")
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление админу {admin_chat_id}: {e}")

                if super_admin_id and bot and super_admin_id != admin_chat_id:
                    try:
                        await bot.send_message(
                            chat_id=super_admin_id,
                            text=f"🚨 [Super-Admin Alert]\nКлиент **{name}** ({slug}) полностью заблокирован по Kill Switch (неуплата).",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление суперадмину: {e}")

                await update_tenant_subscription(tenant_id, {"last_notification_stage": "locked"})

            # 4. Если подписку продлили (> 3 дней), сбрасываем stage
            elif status == SubscriptionStatus.ACTIVE and last_stage != "none":
                await update_tenant_subscription(tenant_id, {"last_notification_stage": "none"})

    except Exception as e:
        logger.error(f"Ошибка в цикле проверки биллинга: {e}")


async def run_billing_notifier(bot, check_interval_seconds: int = 86400) -> None:
    """
    Фоновый долгоживущий цикл (Cron task) для периодической проверки биллинга.
    Запускается в asyncio event loop при старте бота.
    """
    logger.info("Запущен фоновый планировщик проверки подписок (интервал: %s сек).", check_interval_seconds)
    while True:
        try:
            await check_and_send_billing_notifications(bot)
        except asyncio.CancelledError:
            logger.info("Фоновый планировщик биллинга остановлен.")
            break
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в run_billing_notifier: {e}")

        try:
            await asyncio.sleep(check_interval_seconds)
        except asyncio.CancelledError:
            break
