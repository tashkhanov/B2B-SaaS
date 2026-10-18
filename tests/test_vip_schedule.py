import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from database import (
    init_db,
    create_barber,
    get_barber_by_id,
    set_barber_vip_status,
    save_booking,
    get_booking_by_id,
    update_booking_status,
    set_tenant_tier,
)
from services.tier_service import SubscriptionTier, is_feature_enabled
from bot.handlers import handle_vip_accept_callback, handle_vip_reject_callback
from aiogram.types import CallbackQuery, User, Message

TEST_DB = "test_vip.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass
    await init_db(TEST_DB)
    # Default to PRO for VIP tests
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_vip_barber_flag_and_helper():
    b_id = await create_barber(
        name="Дильшод",
        role_title="Топ-барбер",
        phone="+998901234567",
        is_vip=0,
        db_path=TEST_DB
    )
    b = await get_barber_by_id(b_id, db_path=TEST_DB)
    assert b is not None
    assert b["is_vip"] == 0

    # Toggle to VIP
    ok = await set_barber_vip_status(b_id, 1, db_path=TEST_DB)
    assert ok is True
    b_updated = await get_barber_by_id(b_id, db_path=TEST_DB)
    assert b_updated["is_vip"] == 1


@pytest.mark.asyncio
async def test_vip_booking_workflow_via_web_api(monkeypatch):
    from web_app import create_web_app
    from aiohttp.test_utils import TestClient, TestServer
    from config import settings

    # Create Mr. Akmal barber
    akmal_id = await create_barber(
        name="Mr. Akmal",
        role_title="Шеф-мастер / Основатель",
        phone="+998970125555",
        telegram_id=888777,
        is_vip=1,
        db_path=TEST_DB
    )

    # Normal barber
    sardor_id = await create_barber(
        name="Сардор",
        role_title="Барбер",
        phone="+998901112233",
        is_vip=0,
        db_path=TEST_DB
    )

    mock_bot = AsyncMock()
    app = create_web_app(bot=mock_bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Booking for normal barber Sardor -> status 'new'
        resp_sardor = await client.post("/api/bookings", json={
            "full_name": "Тест Клиент",
            "phone": "+998909998877",
            "service": "Стрижка",
            "booking_time": "2026-10-25 14:00",
            "barber_id": sardor_id,
            "barber_name": "Сардор",
            "user_id": 123456
        })
        assert resp_sardor.status == 200
        res_data_sardor = await resp_sardor.json()
        assert res_data_sardor["status"] == "new"

        # 2. Booking for VIP barber Mr. Akmal -> status 'pending_vip'
        resp_akmal = await client.post("/api/bookings", json={
            "full_name": "VIP Клиент",
            "phone": "+998909990011",
            "service": "Премиум комплекс",
            "booking_time": "2026-10-25 16:00",
            "barber_id": akmal_id,
            "barber_name": "Mr. Akmal",
            "user_id": 654321
        })
        assert resp_akmal.status == 200
        res_data_akmal = await resp_akmal.json()
        assert res_data_akmal["status"] == "pending_vip"
        assert "согласование" in res_data_akmal["message"]

        # 3. Check database record
        b_record = await get_booking_by_id(res_data_akmal["id"], db_path=TEST_DB)
        assert b_record["status"] == "pending_vip"

        # 4. Check that notification with inline keyboard was dispatched
        assert mock_bot.send_message.called
        # Check target chat included Akmal's telegram_id (888777)
        calls = mock_bot.send_message.call_args_list
        chef_call = next((c for c in calls if c.kwargs.get("chat_id") == 888777), None)
        assert chef_call is not None
        assert "ЗАПРОС НА ЗАПИСЬ К ШЕФ-МАСТЕРУ" in chef_call.kwargs.get("text", "")
        assert chef_call.kwargs.get("reply_markup") is not None

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_vip_accept_and_reject_callbacks():
    b_id = await save_booking(
        user_id=555,
        username="@vip_guest",
        full_name="VIP Гость",
        details="Премиум",
        service="Комплекс от Шефа",
        booking_time="2026-10-26 12:00",
        status="pending_vip",
        db_path=TEST_DB
    )

    mock_bot = AsyncMock()
    mock_msg = AsyncMock(spec=Message)
    mock_msg.message_id = 99
    mock_msg.chat = MagicMock(id=999888)

    # 1. Accept callback
    cb_accept = MagicMock(spec=CallbackQuery)
    cb_accept.data = f"vip_accept:{b_id}"
    cb_accept.message = mock_msg
    cb_accept.from_user = User(id=999888, is_bot=False, first_name="Akmal")
    cb_accept.bot = mock_bot
    cb_accept.answer = AsyncMock()

    await handle_vip_accept_callback(cb_accept)

    # DB status should become confirmed
    rec_after_accept = await get_booking_by_id(b_id, db_path=TEST_DB)
    assert rec_after_accept["status"] == "confirmed"
    assert mock_bot.send_message.called

    # 2. Reject callback
    b_id_2 = await save_booking(
        user_id=666,
        username="@vip_guest_2",
        full_name="Второй Гость",
        details="Премиум",
        service="Стрижка",
        booking_time="2026-10-26 15:00",
        status="pending_vip",
        db_path=TEST_DB
    )

    cb_reject = MagicMock(spec=CallbackQuery)
    cb_reject.data = f"vip_reject:{b_id_2}"
    cb_reject.message = mock_msg
    cb_reject.from_user = User(id=999888, is_bot=False, first_name="Akmal")
    cb_reject.bot = mock_bot
    cb_reject.answer = AsyncMock()

    await handle_vip_reject_callback(cb_reject)

    rec_after_reject = await get_booking_by_id(b_id_2, db_path=TEST_DB)
    assert rec_after_reject["status"] == "cancelled"


@pytest.mark.asyncio
async def test_vip_protection_disabled_on_start_tier():
    # Set to START
    await set_tenant_tier("brohouse", "START", db_path=TEST_DB)
    enabled = await is_feature_enabled("has_vip_protection", db_path=TEST_DB)
    assert enabled is False
