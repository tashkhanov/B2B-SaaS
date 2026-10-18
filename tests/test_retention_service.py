import os
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from database import (
    init_db,
    save_booking,
    get_bookings_due_for_2h_reminder,
    mark_booking_reminder_sent,
    confirm_booking_by_client,
    cancel_booking_by_client,
    get_clients_due_for_winback,
    mark_client_winback_sent,
    get_all_bookings,
    set_tenant_tier,
)
from services.retention_service import (
    check_and_send_2h_reminders,
    check_and_send_winback_pushes,
    build_reminder_keyboard,
    build_winback_keyboard,
)
from services.schedule_service import get_now_tashkent
from services.tier_service import SubscriptionTier, get_tenant_tier

TEST_DB = "test_retention.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass
    await init_db(TEST_DB)
    # Default to PRO for retention tests
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_2h_reminder_query_and_marking():
    now = get_now_tashkent()
    
    # 1. Booking due in 120 minutes (within 105-135 range)
    due_time = (now + timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M")
    b1_id = await save_booking(
        user_id=111,
        username="@client1",
        full_name="Клиент 1",
        details="",
        service="Стрижка",
        booking_time=due_time,
        status="new",
        db_path=TEST_DB
    )

    # 2. Booking due in 300 minutes (outside range)
    far_time = (now + timedelta(minutes=300)).strftime("%Y-%m-%d %H:%M")
    b2_id = await save_booking(
        user_id=222,
        username="@client2",
        full_name="Клиент 2",
        details="",
        service="Борода",
        booking_time=far_time,
        status="new",
        db_path=TEST_DB
    )

    # Query due reminders
    due_bookings = await get_bookings_due_for_2h_reminder(db_path=TEST_DB)
    ids = [b["id"] for b in due_bookings]
    assert b1_id in ids
    assert b2_id not in ids

    # Mark as sent
    await mark_booking_reminder_sent(b1_id, db_path=TEST_DB)

    # Should no longer be returned
    due_after = await get_bookings_due_for_2h_reminder(db_path=TEST_DB)
    ids_after = [b["id"] for b in due_after]
    assert b1_id not in ids_after


@pytest.mark.asyncio
async def test_confirm_and_cancel_by_client():
    now = get_now_tashkent()
    booking_time = (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    b_id = await save_booking(
        user_id=333,
        username="@client3",
        full_name="Клиент 3",
        details="",
        service="Стрижка",
        booking_time=booking_time,
        status="new",
        db_path=TEST_DB
    )

    # Confirm
    ok_conf = await confirm_booking_by_client(b_id, db_path=TEST_DB)
    assert ok_conf is True

    all_b = await get_all_bookings(db_path=TEST_DB)
    b = next(x for x in all_b if x["id"] == b_id)
    assert b["client_confirmed"] == 1

    # Cancel
    ok_cancel = await cancel_booking_by_client(b_id, db_path=TEST_DB)
    assert ok_cancel is True

    all_b2 = await get_all_bookings(db_path=TEST_DB)
    b2 = next(x for x in all_b2 if x["id"] == b_id)
    assert b2["status"] == "cancelled"


@pytest.mark.asyncio
async def test_winback_query_and_marking():
    now = get_now_tashkent()
    past_25d = (now - timedelta(days=25)).strftime("%Y-%m-%d 14:00")
    b_id = await save_booking(
        user_id=777,
        username="@loyal_client",
        full_name="Постоянный Клиент",
        details="",
        service="Стрижка",
        booking_time=past_25d,
        status="completed",
        db_path=TEST_DB
    )

    due_winback = await get_clients_due_for_winback(days_ago=25, db_path=TEST_DB)
    user_ids = [c["user_id"] for c in due_winback]
    assert 777 in user_ids

    # Mark as sent
    await mark_client_winback_sent(777, db_path=TEST_DB)

    due_after = await get_clients_due_for_winback(days_ago=25, db_path=TEST_DB)
    user_ids_after = [c["user_id"] for c in due_after]
    assert 777 not in user_ids_after


@pytest.mark.asyncio
async def test_retention_feature_gating_on_start_tier():
    # Downgrade to START
    await set_tenant_tier("brohouse", "START", db_path=TEST_DB)
    tier = await get_tenant_tier("brohouse", db_path=TEST_DB)
    assert tier == SubscriptionTier.START

    mock_bot = AsyncMock()
    
    # Reminders should not be sent on START
    sent_count = await check_and_send_2h_reminders(mock_bot, db_path=TEST_DB)
    assert sent_count == 0
    mock_bot.send_message.assert_not_called()

    # Win-back pushes should not be sent on START
    winback_count = await check_and_send_winback_pushes(mock_bot, db_path=TEST_DB)
    assert winback_count == 0
    mock_bot.send_message.assert_not_called()


def test_keyboards_builder():
    kb = build_reminder_keyboard(123)
    buttons = kb.inline_keyboard[0]
    assert len(buttons) == 2
    assert "confirm_visit:123" in buttons[0].callback_data
    assert "cancel_visit:123" in buttons[1].callback_data

    wkb = build_winback_keyboard("https://example.com")
    assert len(wkb.inline_keyboard) == 1
    assert wkb.inline_keyboard[0][0].web_app.url == "https://example.com/book"
