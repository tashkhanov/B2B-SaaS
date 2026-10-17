import time
import pytest
from services.telegram_auth import generate_telegram_init_data, verify_telegram_webapp_data

TEST_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"


def test_valid_init_data_verification():
    user = {"id": 1243374131, "first_name": "Khan", "username": "khan710"}
    raw_init_data = generate_telegram_init_data(user, TEST_BOT_TOKEN)
    
    verified = verify_telegram_webapp_data(raw_init_data, TEST_BOT_TOKEN)
    assert verified is not None
    assert verified["user_data"]["id"] == 1243374131
    assert verified["user_data"]["username"] == "khan710"


def test_tampered_hash_rejected():
    user = {"id": 1243374131, "first_name": "Khan"}
    raw_init_data = generate_telegram_init_data(user, TEST_BOT_TOKEN)
    # Подменяем хэш
    tampered = raw_init_data.replace("hash=", "hash=deadbeef")
    
    assert verify_telegram_webapp_data(tampered, TEST_BOT_TOKEN) is None


def test_tampered_user_id_rejected():
    user = {"id": 1243374131, "first_name": "Khan"}
    raw_init_data = generate_telegram_init_data(user, TEST_BOT_TOKEN)
    # Злоумышленник пытается подменить user id без пересчета хэша
    tampered = raw_init_data.replace("1243374131", "999999999")
    
    assert verify_telegram_webapp_data(tampered, TEST_BOT_TOKEN) is None


def test_wrong_bot_token_rejected():
    user = {"id": 1243374131, "first_name": "Khan"}
    raw_init_data = generate_telegram_init_data(user, TEST_BOT_TOKEN)
    
    # Проверка с другим токеном
    assert verify_telegram_webapp_data(raw_init_data, "WRONG_BOT_TOKEN:123") is None


def test_expired_auth_date_rejected():
    user = {"id": 1243374131, "first_name": "Khan"}
    # Дата 25 часов назад
    old_time = int(time.time()) - 90000
    raw_init_data = generate_telegram_init_data(user, TEST_BOT_TOKEN, auth_date=old_time)
    
    # Должен быть отклонен по тайм-ауту
    assert verify_telegram_webapp_data(raw_init_data, TEST_BOT_TOKEN, max_age_seconds=86400) is None
