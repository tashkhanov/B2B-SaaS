import os
import pytest
import pytest_asyncio
from services.tier_service import (
    SubscriptionTier,
    TIER_CONFIG,
    normalize_tier,
    get_tier_limits,
    get_tenant_tier,
    is_feature_enabled,
    check_can_add_master,
    check_can_add_branch,
)
from database import init_db, set_tenant_tier

TEST_DB = "test_tier_db.sqlite3"


@pytest_asyncio.fixture(autouse=True)
async def cleanup():
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
async def test_tier_config_specs():
    """Проверка точных финансовых параметров и лимитов тарифов"""
    # START
    start = TIER_CONFIG[SubscriptionTier.START]
    assert start["max_branches"] == 1
    assert start["max_masters"] == 5
    assert start["setup_fee_uzs"] == 2400000
    assert start["monthly_fee_uzs"] == 690000
    assert start["has_voice_ai"] is False
    assert start["has_smart_reminders"] is False

    # PRO
    pro = TIER_CONFIG[SubscriptionTier.PRO]
    assert pro["max_branches"] == 3
    assert pro["max_masters"] == 15
    assert pro["setup_fee_uzs"] == 3500000
    assert pro["monthly_fee_uzs"] == 1190000
    assert pro["has_voice_ai"] is False
    assert pro["has_smart_reminders"] is True

    # ENTERPRISE (исправленная цена: 7.5 млн сум)
    enterprise = TIER_CONFIG[SubscriptionTier.ENTERPRISE]
    assert enterprise["setup_fee_uzs"] == 7500000
    assert enterprise["monthly_fee_uzs"] == 3500000
    assert enterprise["has_voice_ai"] is True
    assert enterprise["has_voice_call_agent"] is True


@pytest.mark.asyncio
async def test_normalize_tier():
    assert normalize_tier("start") == SubscriptionTier.START
    assert normalize_tier("PRO") == SubscriptionTier.PRO
    assert normalize_tier("ENTERPRISE") == SubscriptionTier.ENTERPRISE
    assert normalize_tier("vip") == SubscriptionTier.ENTERPRISE
    assert normalize_tier("unknown") == SubscriptionTier.PRO


@pytest.mark.asyncio
async def test_tenant_tier_persistence():
    # По умолчанию Brohouse имеет тариф PRO
    tier = await get_tenant_tier("brohouse", db_path=TEST_DB)
    assert tier == SubscriptionTier.PRO
    assert await is_feature_enabled("has_voice_ai", "brohouse", db_path=TEST_DB) is False
    assert await is_feature_enabled("has_vip_protection", "brohouse", db_path=TEST_DB) is True

    # Переключаем на ULTRA / ENTERPRISE
    ok = await set_tenant_tier("brohouse", "ENTERPRISE", db_path=TEST_DB)
    assert ok is True
    assert await is_feature_enabled("has_voice_ai", "brohouse", db_path=TEST_DB) is True

    # Переключаем на START
    ok = await set_tenant_tier("brohouse", "START", db_path=TEST_DB)
    assert ok is True

    tier_after = await get_tenant_tier("brohouse", db_path=TEST_DB)
    assert tier_after == SubscriptionTier.START
    assert await is_feature_enabled("has_voice_ai", "brohouse", db_path=TEST_DB) is False


@pytest.mark.asyncio
async def test_capacity_checks():
    # На тарифе START (лимит 5 мастеров, 1 филиал)
    await set_tenant_tier("brohouse", "START", db_path=TEST_DB)

    can_add_m, err_m = await check_can_add_master(4, "brohouse", db_path=TEST_DB)
    assert can_add_m is True
    assert err_m is None

    can_add_m5, err_m5 = await check_can_add_master(5, "brohouse", db_path=TEST_DB)
    assert can_add_m5 is False
    assert "PRO" in err_m5

    can_add_b1, err_b1 = await check_can_add_branch(1, "brohouse", db_path=TEST_DB)
    assert can_add_b1 is False
    assert "PRO" in err_b1

    # На тарифе PRO (лимит 15 мастеров, 3 филиала)
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)
    can_add_m6, _ = await check_can_add_master(5, "brohouse", db_path=TEST_DB)
    assert can_add_m6 is True

    can_add_b2, _ = await check_can_add_branch(2, "brohouse", db_path=TEST_DB)
    assert can_add_b2 is True

    can_add_b3, err_b3 = await check_can_add_branch(3, "brohouse", db_path=TEST_DB)
    assert can_add_b3 is False
    assert "ULTRA" in err_b3
