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
from aiogram import Bot
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


@pytest.mark.asyncio
async def test_barber_vip_toggle_and_crud_api():
    from web_app import create_web_app
    from aiohttp.test_utils import TestClient, TestServer
    from config import settings

    mock_bot = AsyncMock()
    app = create_web_app(bot=mock_bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Create barber with is_vip=1
        auth_headers = {"X-Admin-Password": str(settings.ADMIN_CHAT_ID)}
        resp_create = await client.post("/api/barbers", headers=auth_headers, json={
            "name": "Нодир VIP",
            "role": "Шеф",
            "phone": "+998901234567",
            "telegram_id": 777666,
            "work_days": "0,1,2,3,4,5,6",
            "is_vip": 1
        })
        assert resp_create.status == 200
        data_create = await resp_create.json()
        assert data_create["ok"] is True
        b_id = data_create["id"]

        # Check get_barbers returns is_vip == 1
        resp_list = await client.get("/api/barbers")
        assert resp_list.status == 200
        barbers = await resp_list.json()
        created = next((b for b in barbers if b["id"] == b_id), None)
        assert created is not None
        assert created["is_vip"] == 1

        # Toggle VIP off via /api/barbers/{id}/toggle-vip
        resp_toggle = await client.post(f"/api/barbers/{b_id}/toggle-vip", headers=auth_headers, json={
            "is_vip": 0
        })
        assert resp_toggle.status == 200
        data_toggle = await resp_toggle.json()
        assert data_toggle["ok"] is True
        assert data_toggle["is_vip"] == 0

        # Verify toggle in DB
        b_check = await get_barber_by_id(b_id, db_path=TEST_DB)
        assert b_check["is_vip"] == 0

        # Update barber setting is_vip=1 again
        resp_update = await client.post(f"/api/barbers/{b_id}/update", headers=auth_headers, json={
            "name": "Нодир VIP (Updated)",
            "role": "Шеф-мастер",
            "phone": "+998901234567",
            "telegram_id": 777666,
            "work_days": "0,1,2,3,4,5,6",
            "is_vip": 1
        })
        assert resp_update.status == 200
        b_check_2 = await get_barber_by_id(b_id, db_path=TEST_DB)
        assert b_check_2["is_vip"] == 1
        assert b_check_2["name"] == "Нодир VIP (Updated)"

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_vip_notification_delivered_to_both_master_and_admin(monkeypatch):
    from web_app import create_web_app
    from aiohttp.test_utils import TestClient, TestServer
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)

    akmal_id = await create_barber(
        name="Mr. Akmal",
        role_title="Шеф-мастер",
        telegram_id=555444,
        is_vip=1,
        db_path=TEST_DB
    )

    mock_bot = AsyncMock()
    app = create_web_app(bot=mock_bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        resp = await client.post("/api/bookings", json={
            "full_name": "Тест Клиент",
            "username": "@test_user_underscore",
            "phone": "+998901234567",
            "service": "Стрижка от Шефа",
            "booking_time": "2026-10-25 15:00",
            "barber_id": akmal_id,
            "user_id": 111222
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "pending_vip"

        # Check mock_bot received calls for both Akmal (555444) and Admin (999888)
        calls = mock_bot.send_message.call_args_list
        target_ids = [c.kwargs.get("chat_id") for c in calls]
        assert 555444 in target_ids
        assert 999888 in target_ids
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_vip_booking_via_process_client_intent_uzbek(monkeypatch):
    from bot.handlers import process_client_intent
    from services.gemini_service import ClientAction
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    monkeypatch.setattr(settings, "SUPER_ADMIN_CHAT_ID", 1243374131)

    message = MagicMock(spec=Message)
    message.from_user = User(id=888999, is_bot=False, first_name="Ibrohim", username="ibrohim_test")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    barbers_data = [
        {"id": 4, "name": "Mr. Akmal", "role_title": "Шеф-мастер", "phone": "+998970125555", "telegram_id": 555444, "is_vip": 1, "is_working_today": 1, "is_active": 1, "work_days": "0,1,2,3,4,5,6"}
    ]

    with patch("bot.handlers.get_recent_messages", new_callable=AsyncMock) as mock_recent, \
         patch("bot.handlers.gemini_service.extract_client_action", new_callable=AsyncMock) as mock_action, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_bookings, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_barbers, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save, \
         patch("bot.handlers.get_user_active_booking", new_callable=AsyncMock) as mock_user_b:

        mock_recent.return_value = []
        mock_user_b.return_value = None
        mock_barbers.return_value = barbers_data
        mock_bookings.return_value = []
        mock_save.return_value = 101

        mock_action.return_value = ClientAction(
            action="create_booking",
            is_booking_confirmed=True,
            customer_name="Ibrohim",
            barber_name="Mr. Akmal",
            booking_time="2026-10-25 14:00 (Yakshanba)",
            service="Стрижка"
        )

        query = "Ertaga soat 14:00 ga Mr. Akmalning huzuriga soch oldirishga yozib qo'ying"
        ai_reply = "👑 Bosh usta Mr. Akmal huzuriga soat 14:00 ga yozilish so'rovingiz shaxsan Akmal akaga tasdiqlash uchun yuborildi!"

        await process_client_intent(message=message, client_query=query, ai_reply=ai_reply)

        # 1. Booking saved in DB with pending_vip
        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        assert save_kwargs["status"] == "pending_vip"
        assert save_kwargs["barber_id"] == 4
        assert save_kwargs["barber_name"] == "Mr. Akmal"

        # 2. VIP approval notifications dispatched to Akmal (555444) and Admin (999888)
        calls = message.bot.send_message.call_args_list
        target_ids = [c.kwargs.get("chat_id") for c in calls]
        assert 555444 in target_ids
        assert 999888 in target_ids

        # 3. Message answered informing client of pending Chef approval
        message.answer.assert_called_once()
        assert "личное согласование" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_vip_booking_via_process_admin_intent_uzbek(monkeypatch):
    from bot.handlers import process_admin_intent
    from services.gemini_service import AdminAction
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    monkeypatch.setattr(settings, "SUPER_ADMIN_CHAT_ID", 1243374131)

    message = MagicMock(spec=Message)
    message.from_user = User(id=1243374131, is_bot=False, first_name="Иброхим", username="asatkhanov")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    barbers_data = [
        {"id": 4, "name": "Mr. Akmal", "role_title": "Шеф-мастер", "phone": "+998970125555", "telegram_id": 555444, "is_vip": 1, "is_working_today": 1, "is_active": 1, "work_days": "0,1,2,3,4,5,6"}
    ]

    with patch("bot.handlers.gemini_service.extract_admin_action", new_callable=AsyncMock) as mock_action, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_bookings, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_barbers, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save:

        mock_barbers.return_value = barbers_data
        mock_bookings.return_value = []
        mock_save.return_value = 102

        mock_action.return_value = AdminAction(
            action="create_booking",
            client_query="Иброхим",
            barber_name="Mr. Akmal",
            booking_time="2026-10-25 14:00",
            service="Стрижка"
        )

        query = "Ertaga soat 14:00 ga Mr. Akmalning huzuriga soch oldirishga yozib qo'y"
        ai_reply = "👑 От души, Иброхим ака! Запрос на 14:00 к Шеф-мастеру Mr. Akmal отправлен на личное согласование!"

        await process_admin_intent(message=message, admin_query=query, ai_reply=ai_reply)

        # 1. Booking saved in DB with pending_vip
        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args.kwargs
        assert save_kwargs["status"] == "pending_vip"
        assert save_kwargs["barber_id"] == 4
        assert save_kwargs["barber_name"] == "Mr. Akmal"
        assert save_kwargs["full_name"] == "Иброхим"

        # 2. VIP approval notifications dispatched to Akmal (555444), Admin (999888), SuperAdmin (1243374131)
        calls = message.bot.send_message.call_args_list
        target_ids = [c.kwargs.get("chat_id") for c in calls]
        assert 555444 in target_ids
        assert 999888 in target_ids

        # 3. Message answered informing of pending Chef approval
        message.answer.assert_called_once()
        assert "личное согласование" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_revoke_vip_approval_cards_on_cancel():
    """Тест: сохранение ID карточек согласования в базе и их отзыв при отмене брони"""
    from services.booking_service import revoke_vip_approval_cards, dispatch_vip_approval_card, GeminiBookingTools
    from database import save_booking_approval_messages, get_booking_approval_messages

    # 1. Проверка сохранения и получения message ids
    booking_id = 999
    cards_data = [{"chat_id": 555444, "message_id": 1001}, {"chat_id": 999888, "message_id": 1002}]

    with patch("database.execute", new_callable=AsyncMock) as mock_exec, \
         patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:

        await save_booking_approval_messages(booking_id, cards_data)
        mock_exec.assert_called_once()

        mock_fetch.return_value = {
            "id": booking_id,
            "full_name": "Иброхим",
            "barber_name": "Mr. Akmal",
            "booking_time": "2026-10-22 17:00",
            "approval_message_ids": '[{"chat_id": 555444, "message_id": 1001}, {"chat_id": 999888, "message_id": 1002}]'
        }

        retrieved = await get_booking_approval_messages(booking_id)
        assert len(retrieved) == 2
        assert retrieved[0]["chat_id"] == 555444

        # 2. Проверка revoke_vip_approval_cards
        mock_bot = AsyncMock(spec=Bot)
        await revoke_vip_approval_cards(bot=mock_bot, booking_id=booking_id, reason="отменена клиентом")

        assert mock_bot.edit_message_text.call_count == 2
        for call in mock_bot.edit_message_text.call_args_list:
            kwargs = call.kwargs
            assert kwargs["reply_markup"] is None
            assert "ОТОЗВАН" in kwargs["text"] or "ОТМЕНЕН" in kwargs["text"]

    # 3. Интеграция с cancel_booking_reservation
    tools = GeminiBookingTools()
    mock_bot2 = AsyncMock(spec=Bot)
    with patch("services.booking_service.cancel_user_booking", new_callable=AsyncMock, return_value={"id": 999, "status": "cancelled"}), \
         patch("services.booking_service.revoke_vip_approval_cards", new_callable=AsyncMock) as mock_revoke:

        res = await tools.cancel_booking_reservation(booking_id=999, user_id=123, bot=mock_bot2)
        assert res["success"] is True
        mock_revoke.assert_called_once_with(bot=mock_bot2, booking_id=999, reason="отменена клиентом")


@pytest.mark.asyncio
async def test_vip_accept_reject_blocks_cancelled_booking():
    """Тест: подтверждение или отклонение уже отмененной записи блокируется (защита от воскрешения брони)."""
    b_id = await save_booking(
        user_id=777,
        username="@test_cancelled",
        full_name="Клиент Отмененный",
        details="VIP Запись",
        service="Стрижка от Шефа",
        booking_time="2026-10-27 16:00",
        status="cancelled",
        db_path=TEST_DB
    )

    mock_bot = AsyncMock(spec=Bot)
    mock_msg = AsyncMock(spec=Message)
    mock_msg.message_id = 123
    mock_msg.chat = MagicMock(id=999888)
    mock_msg.text = "👑 ЗАПРОС НА ЗАПИСЬ К ШЕФ-МАСТЕРУ!"

    # 1. Попытка принять отмененную запись
    cb_accept = MagicMock(spec=CallbackQuery)
    cb_accept.data = f"vip_accept:{b_id}"
    cb_accept.message = mock_msg
    cb_accept.from_user = User(id=999888, is_bot=False, first_name="Akmal")
    cb_accept.bot = mock_bot
    cb_accept.answer = AsyncMock()

    await handle_vip_accept_callback(cb_accept)

    cb_accept.answer.assert_called_once()
    assert "отменена" in cb_accept.answer.call_args[0][0].lower()
    rec = await get_booking_by_id(b_id, db_path=TEST_DB)
    # Статус НЕ должен стать confirmed
    assert rec["status"] == "cancelled"

    # 2. Попытка отклонить отмененную запись
    cb_reject = MagicMock(spec=CallbackQuery)
    cb_reject.data = f"vip_reject:{b_id}"
    cb_reject.message = mock_msg
    cb_reject.from_user = User(id=999888, is_bot=False, first_name="Akmal")
    cb_reject.bot = mock_bot
    cb_reject.answer = AsyncMock()

    await handle_vip_reject_callback(cb_reject)

    cb_reject.answer.assert_called_once()
    assert "отменена" in cb_reject.answer.call_args[0][0].lower()



