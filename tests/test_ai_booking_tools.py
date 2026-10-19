import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date

from services.gemini_service import GeminiBookingTools, GeminiService


@pytest.mark.asyncio
async def test_tool_create_booking_reservation_akmal_vip():
    """Тест: бронирование к Mr. Akmal получает статус pending_vip и отправляет карточку согласования"""
    tools = GeminiBookingTools()

    mock_barbers = [
        {"id": 4, "name": "Mr. Akmal", "role_title": "Директор / Шеф-мастер", "is_vip": 1, "is_active": 1, "telegram_id": 555444},
        {"id": 5, "name": "Mr. Dilshod", "role_title": "Стилист", "is_vip": 0, "is_active": 1, "telegram_id": 555555},
    ]
    mock_branches = [
        {"id": 1, "name": "Миллий бог", "address": "ул. Сеульская 8B", "is_active": 1},
        {"id": 2, "name": "Чиланзар", "address": "Чиланзар 2-й кв", "is_active": 1},
    ]
    mock_services = [
        {"id": 1, "name": "Стрижка волос", "price": 100000, "duration_min": 45, "is_active": 1},
    ]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.booking_service.get_user_active_booking", new_callable=AsyncMock, return_value=None), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock, return_value=777) as mock_save, \
         patch("services.booking_service.is_feature_enabled", new_callable=AsyncMock, return_value=True), \
         patch("services.booking_service.dispatch_vip_approval_card", new_callable=AsyncMock) as mock_vip_dispatch:

        result = await tools.create_booking_reservation(
            customer_name="Иброхим",
            booking_time="2026-10-20 14:00 (Вторник)",
            barber_name_or_id="Mr. Akmal",
            branch_name_or_id="Миллий бог",
            service_name_or_id="Стрижка волос",
            phone="+998901234567",
            user_id=1243374131,
            bot=AsyncMock(),
        )

        assert result["success"] is True
        assert result["status"] == "pending_vip"
        assert result["barber_name"] == "Mr. Akmal"
        assert result["requires_manual_approval"] is True
        assert "согласование" in result["message"]
        mock_save.assert_called_once()
        saved_status = mock_save.call_args[1].get("status")
        assert saved_status == "pending_vip"
        mock_vip_dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_tool_create_booking_reservation_regular_barber():
    """Тест: бронирование к обычному барберу подтверждается сразу (статус confirmed/new)"""
    tools = GeminiBookingTools()

    mock_barbers = [
        {"id": 4, "name": "Mr. Akmal", "role_title": "Директор / Шеф-мастер", "is_vip": 1, "is_active": 1, "telegram_id": 555444},
        {"id": 5, "name": "Mr. Dilshod", "role_title": "Стилист", "is_vip": 0, "is_active": 1, "telegram_id": 555555},
    ]
    mock_branches = [
        {"id": 1, "name": "Миллий бог", "address": "ул. Сеульская 8B", "is_active": 1},
    ]
    mock_services = [
        {"id": 1, "name": "Стрижка волос", "price": 100000, "duration_min": 45, "is_active": 1},
    ]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock, return_value=888) as mock_save, \
         patch("services.booking_service.is_feature_enabled", new_callable=AsyncMock, return_value=True):

        result = await tools.create_booking_reservation(
            customer_name="Сардор",
            booking_time="2026-10-20 15:00 (Вторник)",
            barber_name_or_id="Dilshod",
            branch_name_or_id="Миллий бог",
            service_name_or_id="Стрижка волос",
            phone="+998909998877",
            user_id=999888,
            bot=AsyncMock(),
        )

        assert result["success"] is True
        assert result["status"] in ("confirmed", "new")
        assert result["barber_name"] == "Mr. Dilshod"
        assert result["requires_manual_approval"] is False
        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_tool_create_booking_reservation_slot_collision():
    """Тест: попытка записаться на занятый слот отклоняется с предложением альтернатив"""
    tools = GeminiBookingTools()

    mock_barbers = [
        {"id": 5, "name": "Mr. Dilshod", "role_title": "Стилист", "is_vip": 0, "is_active": 1, "telegram_id": 555555},
    ]
    mock_branches = [{"id": 1, "name": "Миллий бог", "address": "ул. Сеульская 8B", "is_active": 1}]
    mock_services = [{"id": 1, "name": "Стрижка волос", "price": 100000, "duration_min": 60, "is_active": 1}]
    existing_bookings = [
        {
            "id": 10,
            "barber_id": 5,
            "booking_time": "2026-10-20 15:00 (Вторник)",
            "service": "Стрижка волос",
            "duration_min": 60,
            "status": "confirmed",
        }
    ]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=existing_bookings), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock) as mock_save:

        result = await tools.create_booking_reservation(
            customer_name="Рустам",
            booking_time="2026-10-20 15:00 (Вторник)",
            barber_name_or_id="Dilshod",
            branch_name_or_id="Миллий бог",
            service_name_or_id="Стрижка волос",
        )

        assert result["success"] is False
        assert result["error"] == "slot_occupied"
        assert "занято" in result["message"].lower() or "недоступно" in result["message"].lower()
        mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_tool_check_available_slots():
    """Тест: проверка свободных слотов возвращает список слотов"""
    tools = GeminiBookingTools()
    mock_barbers = [{"id": 5, "name": "Mr. Dilshod", "role_title": "Стилист", "is_vip": 0, "is_active": 1, "is_working_today": 1, "work_days": "0,1,2,3,4,5,6"}]
    mock_branches = [{"id": 1, "name": "Миллий бог", "address": "ул. Сеульская 8B", "is_active": 1}]
    mock_services = [{"id": 1, "name": "Стрижка волос", "price": 100000, "duration_min": 60, "is_active": 1}]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=[]):

        result = await tools.check_available_slots(
            target_date="2026-10-20",
            branch_name_or_id="Миллий бог",
            barber_name_or_id="Dilshod",
            service_name_or_id="Стрижка волос",
        )

        assert result["available"] is True
        assert len(result["slots"]) > 0
        assert result["branch_name"] == "Миллий бог"
        assert result["barber_name"] == "Mr. Dilshod"


@pytest.mark.asyncio
async def test_tool_cancel_booking_reservation():
    """Тест: отмена бронирования освобождает слот"""
    tools = GeminiBookingTools()

    with patch("services.booking_service.cancel_user_booking", new_callable=AsyncMock, return_value={"id": 42, "status": "cancelled"}), \
         patch("services.booking_service.update_booking_status", new_callable=AsyncMock):

        result = await tools.cancel_booking_reservation(user_id=12345)
        assert result["success"] is True
        assert result["booking_id"] == 42


@pytest.mark.asyncio
async def test_tool_get_salon_info():
    """Тест: получение справочника филиалов, услуг и мастеров"""
    tools = GeminiBookingTools()

    mock_barbers = [{"id": 4, "name": "Mr. Akmal", "role_title": "Директор", "is_vip": 1}]
    mock_branches = [{"id": 1, "name": "Миллий бог", "address": "ул. Сеульская 8B", "landmark": "Метро"}]
    mock_services = [{"id": 1, "name": "Стрижка волос", "price": 100000, "duration_min": 45, "badge": "ТОП"}]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services):

        info = await tools.get_salon_info()
        assert len(info["branches"]) == 1
        assert len(info["services"]) == 1
        assert len(info["barbers"]) == 1
        assert info["barbers"][0]["is_vip"] is True


@pytest.mark.asyncio
async def test_ai_uzbek_dialog_flow_akmal_vip():
    """Тест сквозного сценария на узбекском: запись к Mr. Akmal отправляется на личное согласование"""
    tools = GeminiBookingTools()
    bot_mock = AsyncMock()

    mock_barbers = [
        {"id": 4, "name": "Mr. Akmal", "role_title": "Bosh usta / Direktor", "is_vip": 1, "is_active": 1, "telegram_id": 555444},
    ]
    mock_branches = [
        {"id": 1, "name": "Milliy bog'", "address": "Seul ko'chasi 8B", "is_active": 1},
    ]
    mock_services = [
        {"id": 1, "name": "Soch oldirish", "price": 100000, "duration_min": 45, "is_active": 1},
    ]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.booking_service.get_user_active_booking", new_callable=AsyncMock, return_value=None), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock, return_value=999) as mock_save, \
         patch("services.booking_service.is_feature_enabled", new_callable=AsyncMock, return_value=True), \
         patch("services.booking_service.dispatch_vip_approval_card", new_callable=AsyncMock) as mock_dispatch:

        res = await tools.create_booking_reservation(
            customer_name="Ibrohim",
            booking_time="2026-10-25 14:00 (Yakshanba)",
            barber_name_or_id="Mr. Akmal",
            branch_name_or_id="Milliy bog'",
            service_name_or_id="Soch oldirish",
            phone="+998901234567",
            user_id=1243374131,
            bot=bot_mock,
        )

        assert res["success"] is True
        assert res["status"] == "pending_vip"
        assert res["barber_name"] == "Mr. Akmal"
        assert res["requires_manual_approval"] is True
        mock_save.assert_called_once()
        save_kw = mock_save.call_args[1]
        assert save_kw["status"] == "pending_vip"
        assert save_kw["barber_id"] == 4
        mock_dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_ai_uzbek_dialog_flow_regular_barber():
    """Тест сквозного сценария на узбекском: запись к обычному мастеру подтверждается сразу"""
    tools = GeminiBookingTools()
    bot_mock = AsyncMock()

    mock_barbers = [
        {"id": 5, "name": "Dilshod", "role_title": "Stilist", "is_vip": 0, "is_active": 1, "telegram_id": 555555},
    ]
    mock_branches = [
        {"id": 2, "name": "Chilonzor", "address": "Chilonzor 2-mavze", "is_active": 1},
    ]
    mock_services = [
        {"id": 1, "name": "Soch oldirish", "price": 80000, "duration_min": 45, "is_active": 1},
    ]

    with patch("services.booking_service.get_barbers", new_callable=AsyncMock, return_value=mock_barbers), \
         patch("services.booking_service.get_branches", new_callable=AsyncMock, return_value=mock_branches), \
         patch("services.booking_service.get_services", new_callable=AsyncMock, return_value=mock_services), \
         patch("services.booking_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock, return_value=1000) as mock_save, \
         patch("services.booking_service.is_feature_enabled", new_callable=AsyncMock, return_value=True):

        res = await tools.create_booking_reservation(
            customer_name="Sardor",
            booking_time="2026-10-25 16:00 (Yakshanba)",
            barber_name_or_id="Dilshod",
            branch_name_or_id="Chilonzor",
            service_name_or_id="Soch oldirish",
            phone="+998909876543",
            user_id=888777,
            bot=bot_mock,
        )

        assert res["success"] is True
        assert res["status"] in ("confirmed", "new")
        assert res["barber_name"] == "Dilshod"
        assert res["requires_manual_approval"] is False
        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_tool_create_booking_reservation_rejects_duplicate_active_booking():
    """Тест: строгий лимит 1 активной брони на клиента - отклонение повторной брони"""
    tools = GeminiBookingTools()
    existing_active = {
        "id": 42,
        "booking_time": "2026-10-21 16:00",
        "service": "Стрижка волос",
        "barber_name": "Mr. Dilshod"
    }

    with patch("services.booking_service.get_user_active_booking", new_callable=AsyncMock, return_value=existing_active), \
         patch("services.booking_service.save_booking", new_callable=AsyncMock) as mock_save:

        res = await tools.create_booking_reservation(
            customer_name="Иброхим",
            booking_time="2026-10-22 17:00",
            barber_name_or_id="Mr. Akmal",
            user_id=1243374131,
        )

        assert res["success"] is False
        assert res["error"] == "active_booking_exists"
        assert "уже есть активная запись" in res["message"].lower()
        mock_save.assert_not_called()



