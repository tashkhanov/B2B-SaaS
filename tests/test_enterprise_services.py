import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from database import init_db, set_tenant_tier
from config import settings
from services.tier_service import SubscriptionTier
from services.eskiz_sms_service import EskizSMSService
from services.voice_call_service import VoiceCallService

TEST_DB = "test_enterprise.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_eskiz_phone_formatting():
    service = EskizSMSService()
    assert service.clean_phone("+998 90 123-45-67") == "998901234567"
    assert service.clean_phone("901234567") == "998901234567"
    assert service.clean_phone("89012345670") == "9989012345670"


@pytest.mark.asyncio
async def test_eskiz_tier_gating_and_mock_mode():
    service = EskizSMSService()

    # 1. On PRO tier -> SMS gateway should be blocked by tier
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)
    resp_pro = await service.send_sms("+998901234567", "Тестовое SMS")
    assert resp_pro["ok"] is False
    assert resp_pro["status"] == "tier_disabled"

    # 2. On ENTERPRISE tier -> SMS gateway is enabled
    await set_tenant_tier("brohouse", "ENTERPRISE", db_path=TEST_DB)

    # In default dev/test environment without .env keys, it must send safely in mock mode
    resp_ent = await service.send_sms("+998901234567", "Тестовое SMS для Enterprise")
    assert resp_ent["ok"] is True
    assert resp_ent["status"] == "mock_sent"
    assert resp_ent["phone"] == "998901234567"


@pytest.mark.asyncio
async def test_voice_call_service_tier_gating_and_initiation():
    vcs = VoiceCallService()

    # 1. Blocked on PRO
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)
    res_pro = await vcs.initiate_call("+998901234567", scenario="confirmation")
    assert res_pro["ok"] is False
    assert res_pro["status"] == "tier_disabled"

    # 2. Enabled on ENTERPRISE
    await set_tenant_tier("brohouse", "ENTERPRISE", db_path=TEST_DB)
    res_ent = await vcs.initiate_call(
        phone="+998901234567",
        scenario="booking_confirmation",
        context={"client": "Азиз", "time": "15:00", "barber": "Mr. Akmal"}
    )
    assert res_ent["ok"] is True
    assert res_ent["status"] == "initiated"
    assert res_ent["call_id"].startswith("call_")


@pytest.mark.asyncio
async def test_voice_call_webhook_dialogue_handling():
    vcs = VoiceCallService()

    # Positive confirmation
    res_yes = await vcs.handle_call_webhook({
        "call_id": "call_12345",
        "speech_text": "Да, конечно буду вовремя"
    })
    assert res_yes["ok"] is True
    assert res_yes["action"] == "hangup"
    assert "подтверждена" in res_yes["speak_text"]

    # Cancellation
    res_no = await vcs.handle_call_webhook({
        "call_id": "call_12345",
        "speech_text": "К сожалению не смогу прийти, отмените"
    })
    assert res_no["ok"] is True
    assert res_no["action"] == "hangup"
    assert "освободили" in res_no["speak_text"]
