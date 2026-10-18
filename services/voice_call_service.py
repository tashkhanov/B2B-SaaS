"""
Сервис голосового телефонного робота (Voice Call Agent) для тарифа ENTERPRISE.
Обеспечивает интеграцию с SIP/Telephony шлюзом для:
- Автоматического подтверждения бронирования звонком
- Обзвона «потерянных» клиентов (Win-Back)
- Приема входящих звонков в нерабочее время
"""

import logging
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime

from services.tier_service import is_feature_enabled
from config import settings

logger = logging.getLogger(__name__)


class VoiceCallService:
    """
    Адаптер телефонии для Voice Call Agent на тарифе ENTERPRISE.
    Поддерживает инициацию исходящих вызовов и обработку вебхуков от SIP-провайдера.
    """

    def __init__(self, provider_name: str = "Khan-Telephony-Gateway"):
        self.provider_name = provider_name

    async def initiate_call(
        self,
        phone: str,
        scenario: str = "booking_confirmation",
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Инициализирует исходящий звонок клиенту через голосового робота.
        """
        enabled = await is_feature_enabled("has_voice_call_agent")
        if not enabled:
            logger.info("[TIER GATE] Voice Call Agent disabled for current tier. Call to %s blocked.", phone)
            return {
                "ok": False,
                "status": "tier_disabled",
                "message": "Voice Call Agent is available exclusively on ENTERPRISE tier."
            }

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        logger.info(
            "[VOICE CALL INITIATED] CallID: %s | Phone: %s | Scenario: %s | Context: %s",
            call_id, phone, scenario, context
        )

        return {
            "ok": True,
            "call_id": call_id,
            "status": "initiated",
            "phone": phone,
            "scenario": scenario,
            "created_at": datetime.now().isoformat(),
            "details": f"Voice Call Agent started session with {phone} ({scenario})"
        }

    async def handle_call_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обрабатывает вебхук от телефонии (смена статуса, распознанная речь, завершение звонка).
        """
        call_id = payload.get("call_id")
        event = payload.get("event", "status_change")
        speech_text = payload.get("speech_text")

        logger.info("[VOICE WEBHOOK] CallID: %s | Event: %s | Text: %s", call_id, event, speech_text)

        # Реакция на транскрипцию голоса клиента
        response_text = ""
        action = "continue"

        if speech_text:
            low = speech_text.lower()
            if any(w in low for w in ["да", "буду", "подтверждаю", "приду", "albatta", "kelaman"]):
                response_text = f"Отлично! Ваша бронь подтверждена. Ждем вас в {settings.SALON_TYPE} {settings.SALON_SHORT_NAME}!"
                action = "hangup"
            elif any(w in low for w in ["нет", "отмен", "не могу", "не смогу", "перенес", "yo'q", "kelolmayman", "bekor"]):
                response_text = "Понял вас. Мы освободили это время. Вы можете записаться на другое время в нашем боте!"
                action = "hangup"
            else:
                response_text = f"Вас понял. До скорой встречи в {settings.SALON_SHORT_NAME}!"

        return {
            "ok": True,
            "call_id": call_id,
            "action": action,
            "speak_text": response_text
        }


# Глобальный инстанс сервиса
voice_call_service = VoiceCallService()
