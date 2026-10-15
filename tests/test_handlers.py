import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User
from bot.handlers import handle_start, handle_about, handle_admin, handle_price, handle_reset

@pytest.mark.asyncio
async def test_handle_start():
    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Алишер")
    message.answer = AsyncMock()

    await handle_start(message)
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Салам алейкум, Алишер!" in args[0]
    assert "Black Beard" in args[0]

@pytest.mark.asyncio
async def test_handle_about():
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await handle_about(message)
    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "asatkhanov.uz" in args[0]
    assert "Khan AI Systems" in args[0]
    assert kwargs.get("reply_markup") is not None

@pytest.mark.asyncio
async def test_handle_admin():
    message = MagicMock(spec=Message)
    message.from_user = User(id=999, is_bot=False, first_name="Admin")
    message.answer = AsyncMock()

    with patch("bot.handlers.settings.ADMIN_CHAT_ID", 999):
        await handle_admin(message)
        message.answer.assert_called_once()
        args, kwargs = message.answer.call_args
        assert "Панель управления" in args[0]
        assert kwargs.get("reply_markup") is not None

@pytest.mark.asyncio
async def test_handle_price():
    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Алишер")
    message.answer = AsyncMock()

    await handle_price(message)
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "100 000 сум" in args[0]
    assert "130 000 сум" in args[0]

@pytest.mark.asyncio
async def test_handle_reset():
    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Алишер")
    message.answer = AsyncMock()

    with patch("bot.handlers.clear_history", new_callable=AsyncMock) as mock_clear:
        await handle_reset(message)
        mock_clear.assert_called_once_with(12345)
        message.answer.assert_called_once()
        args, _ = message.answer.call_args
        assert "История диалога очищена" in args[0]

@pytest.mark.asyncio
async def test_process_booking_intent_flow():
    from bot.handlers import process_booking_intent
    from services.gemini_service import ExtractedBooking

    message = MagicMock(spec=Message)
    message.from_user = User(id=777, is_bot=False, first_name="Бабур", username="babur7")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    # Case 1: Booking not confirmed yet (only time asked)
    with patch("bot.handlers.get_recent_messages", new_callable=AsyncMock) as mock_get_recent, \
         patch("bot.handlers.gemini_service.extract_client_action", new_callable=AsyncMock) as mock_client_action, \
         patch("bot.handlers.gemini_service.extract_booking_info", new_callable=AsyncMock) as mock_extract, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save:
        
        mock_client_action.return_value = None
        mock_get_recent.return_value = [{"role": "user", "text": "запиши на завтра в 15:00"}]
        mock_extract.return_value = ExtractedBooking(is_booking_confirmed=False, booking_time="завтра в 15:00")

        await process_booking_intent(
            message=message,
            client_query="запиши на завтра в 15:00",
            ai_reply="Завтра в 15:00 свободно, как вас зовут?"
        )

        mock_get_recent.assert_called_once_with(user_id=777, limit=6)
        mock_save.assert_not_called()

    # Case 2: Booking confirmed (both name and time present and slot is available)
    with patch("bot.handlers.get_recent_messages", new_callable=AsyncMock) as mock_get_recent, \
         patch("bot.handlers.gemini_service.extract_client_action", new_callable=AsyncMock) as mock_client_action, \
         patch("bot.handlers.gemini_service.extract_booking_info", new_callable=AsyncMock) as mock_extract, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_active, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_get_barbers, \
         patch("bot.handlers.has_recent_booking", new_callable=AsyncMock) as mock_has_recent, \
         patch("database.get_user_active_booking", new_callable=AsyncMock) as mock_user_b:
        
        mock_client_action.return_value = None
        mock_user_b.return_value = None
        mock_get_barbers.return_value = []
        mock_active.return_value = []
        mock_get_recent.return_value = [
            {"role": "user", "text": "запиши на завтра в 15:00"},
            {"role": "user", "text": "Бабур"}
        ]
        mock_extract.return_value = ExtractedBooking(
            is_booking_confirmed=True,
            customer_name="Бабур",
            booking_time="2026-10-16 15:00 (Пятница)",
            service="Стрижка"
        )
        mock_has_recent.return_value = False
        mock_save.return_value = 42

        await process_booking_intent(
            message=message,
            client_query="Бабур",
            ai_reply="Записал вас на завтра в 15:00!"
        )

        mock_save.assert_called_once()
        message.answer.assert_called_once()
        assert "Мы ценим ваше доверие" in message.answer.call_args[0][0]

    # Case 3: Slot conflict (trying to book 15:30 when 15:00 is occupied)
    message.answer.reset_mock()
    with patch("bot.handlers.get_recent_messages", new_callable=AsyncMock) as mock_get_recent, \
         patch("bot.handlers.gemini_service.extract_client_action", new_callable=AsyncMock) as mock_client_action, \
         patch("bot.handlers.gemini_service.extract_booking_info", new_callable=AsyncMock) as mock_extract, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_active, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_get_barbers, \
         patch("database.get_user_active_booking", new_callable=AsyncMock) as mock_user_b:
        
        mock_client_action.return_value = None
        mock_user_b.return_value = None
        mock_get_barbers.return_value = []
        mock_active.return_value = [
            {
                "id": 1,
                "full_name": "Абдулазиз",
                "booking_time": "2026-10-16 15:00 (Пятница)",
                "status": "new"
            }
        ]
        mock_extract.return_value = ExtractedBooking(
            is_booking_confirmed=True,
            customer_name="Тимур",
            booking_time="2026-10-16 15:30 (Пятница)",
            service="Стрижка"
        )

        await process_booking_intent(
            message=message,
            client_query="Тимур",
            ai_reply="Записал на 15:30"
        )

        mock_save.assert_not_called()
        message.answer.assert_called_once()
        args, _ = message.answer.call_args
        assert "записать не могу" in args[0]
        assert "15:00" in args[0]


@pytest.mark.asyncio
async def test_handle_barber():
    from bot.handlers import handle_barber

    message = MagicMock(spec=Message)
    message.from_user = User(id=333444, is_bot=False, first_name="Сардор")
    message.answer = AsyncMock()

    with patch("bot.handlers.get_barber_by_telegram_id", new_callable=AsyncMock) as mock_get_b:
        mock_get_b.return_value = {"id": 2, "name": "Сардор", "telegram_id": 333444}
        await handle_barber(message)
        message.answer.assert_called_once()
        args, kwargs = message.answer.call_args
        assert "Сардор" in args[0]
        assert "Мою Смену" in str(kwargs.get("reply_markup"))


@pytest.mark.asyncio
async def test_get_user_role():
    from bot.handlers import get_user_role

    with patch("bot.handlers.settings.ADMIN_CHAT_ID", 111222), \
         patch("bot.handlers.get_barber_by_telegram_id", new_callable=AsyncMock) as mock_get_b:

        mock_get_b.return_value = None

        # Admin
        role, barber = await get_user_role(111222)
        assert role == "admin"
        assert barber is None

        # Barber
        mock_get_b.return_value = {"id": 1, "name": "Иброхим", "role": "Шеф-мастер"}
        role, barber = await get_user_role(333444)
        assert role == "barber"
        assert barber["name"] == "Иброхим"

        # Client
        mock_get_b.return_value = None
        role, barber = await get_user_role(999000)
        assert role == "client"
        assert barber is None


@pytest.mark.asyncio
async def test_barber_auto_detected_on_start():
    """Verify that a barber typing /start is automatically recognized without needing /barber"""
    from bot.handlers import handle_start

    message = MagicMock(spec=Message)
    message.from_user = User(id=333444, is_bot=False, first_name="Иброхим")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    with patch("bot.handlers.settings.ADMIN_CHAT_ID", 111222), \
         patch("bot.handlers.get_barber_by_telegram_id", new_callable=AsyncMock) as mock_get_b:

        mock_get_b.return_value = {"id": 1, "name": "Иброхим", "role": "Шеф-мастер", "telegram_id": 333444}

        await handle_start(message)

        message.answer.assert_called_once()
        args, kwargs = message.answer.call_args
        # Greeted as master
        assert "Иброхим" in args[0]
        assert "Шеф-мастер" in args[0]
        # Keyboard has barber button
        markup_str = str(kwargs.get("reply_markup"))
        assert "Моя смена" in markup_str
        assert "/barber" in markup_str



