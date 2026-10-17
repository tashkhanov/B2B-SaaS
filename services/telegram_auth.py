import hmac
import hashlib
import json
import time
import urllib.parse
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def generate_telegram_init_data(user_dict: Dict[str, Any], bot_token: str, auth_date: Optional[int] = None) -> str:
    """
    Генерирует криптографически валидную строку Telegram initData для тестов и интеграций.
    """
    if auth_date is None:
        auth_date = int(time.time())
    
    user_json = json.dumps(user_dict, separators=(',', ':'))
    data = {
        "auth_date": str(auth_date),
        "user": user_json,
    }
    
    # Сортировка ключей
    sorted_items = sorted(data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)
    
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    
    # Формирование URL-кодированной строки
    data["hash"] = calculated_hash
    return urllib.parse.urlencode(data)


def verify_telegram_webapp_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400
) -> Optional[Dict[str, Any]]:
    """
    Криптографическая верификация строки Telegram initData с использованием HMAC-SHA256.
    
    :param init_data: Сырая строка window.Telegram.WebApp.initData
    :param bot_token: Токен бота из настроек
    :param max_age_seconds: Максимальное время жизни подписи (по умолчанию 24 часа)
    :return: Словарь с распарсенными данными и объектом user, либо None, если подпись невалидна.
    """
    if not init_data or not bot_token:
        return None

    try:
        # Парсинг параметров query-string
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_to_check = parsed.pop("hash", None)
        if not hash_to_check:
            logger.warning("Telegram initData verification failed: missing hash")
            return None

        # Сортировка ключей по алфавиту и сборка data_check_string
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        # Вычисление секретного ключа: HMAC_SHA256(b"WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()

        # Вычисление проверочного HMAC от data_check_string
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        # Криптографическое сравнение
        if not hmac.compare_digest(calculated_hash, hash_to_check):
            logger.warning("Telegram initData verification failed: invalid signature hash")
            return None

        # Проверка срока действия auth_date
        auth_date_str = parsed.get("auth_date")
        if auth_date_str and auth_date_str.isdigit():
            auth_date = int(auth_date_str)
            current_time = int(time.time())
            if current_time - auth_date > max_age_seconds:
                logger.warning(
                    "Telegram initData verification failed: auth_date expired (%d seconds old)",
                    current_time - auth_date
                )
                return None
        else:
            logger.warning("Telegram initData verification failed: missing or invalid auth_date")
            return None

        # Распарсивание объекта user
        user_str = parsed.get("user")
        if user_str:
            parsed["user_data"] = json.loads(user_str)
        else:
            parsed["user_data"] = None

        return parsed
    except Exception as e:
        logger.error("Error during Telegram initData verification: %s", e)
        return None
