"""
Сервис управления тарифами, лимитами и шлюзами функциональности (Feature-Gating).
Реализует трехуровневую тарифную систему платформы KHAN AI SYSTEMS:
- START (Solo): 1 филиал, до 5 мастеров, текстовый ИИ, без пушей удержания
- PRO BUSINESS (Хит / Brohouse): до 3 филиалов, до 15 мастеров, голосовой ИИ, пуши за 2ч и возврат 25 дней
- ENTERPRISE (VIP / Сеть): безлимит филиалов и мастеров, телефонный робот Voice Call Agent
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple, Union

from database import get_tenant_by_id_or_slug, get_tenant_subscription

logger = logging.getLogger(__name__)


class SubscriptionTier(str, Enum):
    START = "START"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


TIER_CONFIG: Dict[SubscriptionTier, Dict[str, Any]] = {
    SubscriptionTier.START: {
        "tier": SubscriptionTier.START,
        "label": "START (Solo)",
        "max_branches": 1,
        "max_masters": 5,
        "setup_fee_uzs": 2400000,
        "monthly_fee_uzs": 690000,
        "has_voice_ai": False,            # Только текстовый ввод для клиентов
        "has_smart_reminders": False,     # Без пушей за 2 часа
        "has_winback_pushes": False,      # Без возврата через 25 дней
        "has_custom_tma_brand": False,    # Базовый шаблон без кастомного бренда
        "has_promo_discount": False,      # Без акции -30%
        "has_vip_protection": False,      # Без ручного согласования VIP-записей
        "has_sms_eskiz": False,           # Без SMS-шлюза
        "has_voice_call_agent": False,    # Без телефонного робота
    },
    SubscriptionTier.PRO: {
        "tier": SubscriptionTier.PRO,
        "label": "PRO BUSINESS (Хит)",
        "max_branches": 3,
        "max_masters": 15,
        "setup_fee_uzs": 3500000,         # Спеццена: ~3 000 000 сум
        "monthly_fee_uzs": 1190000,       # Спеццена: ~950 000 сум
        "has_voice_ai": True,             # Текст + Голос (Узб/Рус)
        "has_smart_reminders": True,      # Пуши за 2ч до визита
        "has_winback_pushes": True,       # Автовозврат через 25 дней
        "has_custom_tma_brand": True,     # Dark Gold + логотипы Brohouse
        "has_promo_discount": True,       # Фирменная акция -30%
        "has_vip_protection": True,       # Защита графика Mr. Akmal (ручное подтверждение)
        "has_sms_eskiz": False,
        "has_voice_call_agent": False,
    },
    SubscriptionTier.ENTERPRISE: {
        "tier": SubscriptionTier.ENTERPRISE,
        "label": "ENTERPRISE (VIP / Сеть)",
        "max_branches": 9999,
        "max_masters": 9999,
        "setup_fee_uzs": 7500000,         # 7.5 млн сум
        "monthly_fee_uzs": 3500000,
        "has_voice_ai": True,
        "has_smart_reminders": True,
        "has_winback_pushes": True,
        "has_custom_tma_brand": True,
        "has_promo_discount": True,
        "has_vip_protection": True,
        "has_sms_eskiz": True,            # SMS через Eskiz.uz
        "has_voice_call_agent": True,     # Голосовой агент по номеру телефона
    },
}


def normalize_tier(tier_val: Union[SubscriptionTier, str, None]) -> SubscriptionTier:
    """Приведение строки к SubscriptionTier с безопасным fallback на PRO."""
    if not tier_val:
        return SubscriptionTier.PRO
    if isinstance(tier_val, SubscriptionTier):
        return tier_val
    clean_val = str(tier_val).strip().upper()
    if clean_val == "START":
        return SubscriptionTier.START
    elif clean_val in ("PRO", "PRO_BUSINESS", "BUSINESS"):
        return SubscriptionTier.PRO
    elif clean_val in ("ENTERPRISE", "VIP"):
        return SubscriptionTier.ENTERPRISE
    return SubscriptionTier.PRO


def get_tier_limits(tier: Union[SubscriptionTier, str, None]) -> Dict[str, Any]:
    """Получение словаря лимитов и флагов возможностей для тарифа."""
    norm = normalize_tier(tier)
    return dict(TIER_CONFIG.get(norm, TIER_CONFIG[SubscriptionTier.PRO]))


async def get_tenant_tier(
    tenant_identifier: Any = "brohouse",
    db_path: Optional[str] = None
) -> SubscriptionTier:
    """Получение текущего тарифа заведения из базы данных."""
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path=db_path)
    if not tenant:
        return SubscriptionTier.PRO
    return normalize_tier(tenant.get("tier", "PRO"))


async def is_feature_enabled(
    feature_key: str,
    tenant_identifier: Any = "brohouse",
    db_path: Optional[str] = None
) -> bool:
    """Проверка, разрешена ли конкретная функция на текущем тарифе заведения."""
    tier = await get_tenant_tier(tenant_identifier, db_path=db_path)
    limits = get_tier_limits(tier)
    return bool(limits.get(feature_key, False))


async def check_can_add_master(
    current_count: int,
    tenant_identifier: Any = "brohouse",
    db_path: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Проверка лимита количества мастеров.
    Возвращает (can_add: bool, error_message: Optional[str]).
    """
    tier = await get_tenant_tier(tenant_identifier, db_path=db_path)
    limits = get_tier_limits(tier)
    max_masters = limits.get("max_masters", 15)

    if current_count >= max_masters:
        if tier == SubscriptionTier.START:
            msg = (
                f"Достигнут лимит мастеров тарифа START (максимум {max_masters}). "
                "Для подключения дополнительных мастеров и кресел перейдите на тариф PRO BUSINESS."
            )
        elif tier == SubscriptionTier.PRO:
            msg = (
                f"Достигнут лимит мастеров тарифа PRO BUSINESS (максимум {max_masters}). "
                "Для масштабирования сети перейдите на тариф ENTERPRISE."
            )
        else:
            msg = f"Достигнут лимит мастеров ({max_masters})."
        return False, msg

    return True, None


async def check_can_add_branch(
    current_count: int,
    tenant_identifier: Any = "brohouse",
    db_path: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Проверка лимита количества филиалов.
    Возвращает (can_add: bool, error_message: Optional[str]).
    """
    tier = await get_tenant_tier(tenant_identifier, db_path=db_path)
    limits = get_tier_limits(tier)
    max_branches = limits.get("max_branches", 3)

    if current_count >= max_branches:
        if tier == SubscriptionTier.START:
            msg = (
                f"Тариф START поддерживает только {max_branches} филиал. "
                "Для добавления новых точек и филиалов перейдите на тариф PRO BUSINESS."
            )
        elif tier == SubscriptionTier.PRO:
            msg = (
                f"Тариф PRO BUSINESS поддерживает до {max_branches} филиалов. "
                "Для безлимитного подключения точек сети перейдите на тариф ENTERPRISE."
            )
        else:
            msg = f"Достигнут лимит филиалов ({max_branches})."
        return False, msg

    return True, None
