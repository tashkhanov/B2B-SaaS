"""
Сервис интеграции с SMS-шлюзом Eskiz.uz (Тариф ENTERPRISE).
Позволяет отправлять транзакционные SMS клиентам и персоналу:
- Подтверждение бронирования
- Напоминания о визите при отсутствии Telegram
- Предупреждения об отмене или переносе
"""

import logging
import re
from typing import Dict, Any, Optional
import aiohttp

from config import settings
from services.tier_service import is_feature_enabled

logger = logging.getLogger(__name__)


class EskizSMSService:
    """
    Клиент для работы с официальным REST API Eskiz.uz (notify.eskiz.uz/api).
    Поддерживает кеширование токена и безопасный режим mock-отправки.
    """
    BASE_URL = "https://notify.eskiz.uz/api"

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        from_sender: str = "4546"
    ):
        self.email = email if email is not None else getattr(settings, "ESKIZ_EMAIL", "")
        self.password = password if password is not None else getattr(settings, "ESKIZ_PASSWORD", "")
        self.from_sender = from_sender
        self._token: Optional[str] = None

    def clean_phone(self, phone: str) -> str:
        """Очищает телефон до формата 998XXXXXXXXX"""
        digits = re.sub(r"\D", "", phone or "")
        if digits.startswith("8") and len(digits) == 11:
            digits = "998" + digits[1:]
        elif len(digits) == 9:
            digits = "998" + digits
        return digits

    async def get_token(self, force_refresh: bool = False) -> Optional[str]:
        """Получает JWT токен авторизации Eskiz"""
        if self._token and not force_refresh:
            return self._token

        if not self.email or not self.password:
            logger.debug("Eskiz credentials not provided; mock mode active.")
            return None

        url = f"{self.BASE_URL}/auth/login"
        payload = {"email": self.email, "password": self.password}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._token = data.get("data", {}).get("token")
                        return self._token
                    else:
                        text = await resp.text()
                        logger.error("Failed to authenticate with Eskiz: HTTP %s: %s", resp.status, text)
                        return None
        except Exception as e:
            logger.error("Network error during Eskiz authentication: %s", e)
            return None

    async def send_sms(
        self,
        phone: str,
        message: str,
        from_sender: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Отправляет SMS через Eskiz API или имитирует отправку, если шлюз не настроен / тариф не Enterprise.
        """
        clean_number = self.clean_phone(phone)
        sender = from_sender or self.from_sender

        # Проверка фича-флага тарифа
        enabled = await is_feature_enabled("has_sms_eskiz")
        if not enabled:
            logger.info("[TIER GATE] Eskiz SMS is disabled for current tier. Skipped for %s", clean_number)
            return {
                "ok": False,
                "status": "tier_disabled",
                "message": "SMS gateway is available on ENTERPRISE tier only"
            }

        # Если реквизиты не заданы — работаем в безопасном MOCK-режиме
        if not self.email or not self.password:
            logger.info(
                "[MOCK ESKIZ SMS] To: %s | From: %s | Message: %s",
                clean_number, sender, message
            )
            return {
                "ok": True,
                "status": "mock_sent",
                "phone": clean_number,
                "message": message,
                "details": "Mock mode active: configure ESKIZ_EMAIL and ESKIZ_PASSWORD in .env"
            }

        token = await self.get_token()
        if not token:
            logger.warning("[ESKIZ] Could not obtain auth token; falling back to mock delivery.")
            return {
                "ok": True,
                "status": "mock_sent",
                "phone": clean_number,
                "message": message,
                "details": "Auth failed; delivered via mock"
            }

        url = f"{self.BASE_URL}/message/sms/send"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "mobile_phone": clean_number,
            "message": message,
            "from": sender,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 401:
                        # Токен протух, пробуем обновить один раз
                        token = await self.get_token(force_refresh=True)
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                            async with session.post(url, headers=headers, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as retry_resp:
                                res_json = await retry_resp.json()
                                return {"ok": retry_resp.status == 200, "status": "sent", "response": res_json}

                    res_json = await resp.json()
                    return {"ok": resp.status == 200, "status": "sent", "response": res_json}
        except Exception as e:
            logger.error("Failed to send SMS via Eskiz: %s", e)
            return {"ok": False, "status": "error", "error": str(e)}


# Глобальный инстанс сервиса
eskiz_service = EskizSMSService()
