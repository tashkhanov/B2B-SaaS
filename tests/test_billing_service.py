import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from services.billing_service import (
    SubscriptionStatus,
    calculate_subscription_status,
    is_subscription_locked,
    check_tenant_access,
)
from database import init_db, toggle_tenant_active, extend_tenant_subscription_30_days, update_tenant_subscription

TEST_DB = "test_billing.db"

@pytest_asyncio.fixture(autouse=True)
async def setup_billing_db():
    import os
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_subscription_status_active():
    now = datetime(2026, 10, 17, 12, 0)
    sub = {
        "is_active": 1,
        "expires_at": (now + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
        "grace_period_days": 2,
    }
    status, info = calculate_subscription_status(sub, now=now)
    assert status == SubscriptionStatus.ACTIVE
    assert is_subscription_locked(sub, now=now) is False
    assert info["days_left"] == 10
    assert info["is_locked"] is False

def test_subscription_status_warning():
    now = datetime(2026, 10, 17, 12, 0)
    sub = {
        "is_active": 1,
        "expires_at": (now + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "grace_period_days": 2,
    }
    status, info = calculate_subscription_status(sub, now=now)
    assert status == SubscriptionStatus.WARNING
    assert is_subscription_locked(sub, now=now) is False
    assert info["days_left"] == 2
    assert info["is_locked"] is False

def test_subscription_status_grace_period():
    now = datetime(2026, 10, 17, 12, 0)
    sub = {
        "is_active": 1,
        "expires_at": (now - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S"),
        "grace_period_days": 2,
    }
    status, info = calculate_subscription_status(sub, now=now)
    assert status == SubscriptionStatus.GRACE_PERIOD
    assert is_subscription_locked(sub, now=now) is False
    assert info["is_locked"] is False

def test_subscription_status_locked_after_grace():
    now = datetime(2026, 10, 17, 12, 0)
    sub = {
        "is_active": 1,
        "expires_at": (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
        "grace_period_days": 2,
    }
    status, info = calculate_subscription_status(sub, now=now)
    assert status == SubscriptionStatus.LOCKED
    assert is_subscription_locked(sub, now=now) is True
    assert info["is_locked"] is True

def test_subscription_status_manual_kill_switch():
    now = datetime(2026, 10, 17, 12, 0)
    sub = {
        "is_active": 0,  # manually switched off
        "expires_at": (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "grace_period_days": 2,
    }
    status, info = calculate_subscription_status(sub, now=now)
    assert status == SubscriptionStatus.LOCKED
    assert is_subscription_locked(sub, now=now) is True
    assert info["is_locked"] is True

@pytest.mark.asyncio
async def test_check_tenant_access_integration():
    is_locked, status, info = await check_tenant_access("brohouse", db_path=TEST_DB)
    assert is_locked is False
    assert status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.WARNING)
    assert info["name"] == "Brohouse Barbershop"

    # Toggle Kill Switch
    await toggle_tenant_active(info["id"], is_active=0, db_path=TEST_DB)
    is_locked2, status2, info2 = await check_tenant_access("brohouse", db_path=TEST_DB)
    assert is_locked2 is True
    assert status2 == SubscriptionStatus.LOCKED
