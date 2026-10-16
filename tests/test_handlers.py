import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User
from bot.handlers import handle_start, handle_about, handle_admin, handle_price, handle_reset

@pytest.mark.asyncio
async def test_handle_start():
    from aiogram.types import InlineKeyboardMarkup

    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Алишер")
    message.answer = AsyncMock()

    await handle_start(message)
    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Салам алейкум, Алишер!" in args[0]
    assert "Brohouse" in args[0]
    markup = kwargs.get("reply_markup")
    assert isinstance(markup, InlineKeyboardMarkup)
    assert "Открыть меню записи" in str(markup)

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
    assert "30% OFF" in args[0]

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
    from services.schedule_service import get_now_tashkent
    from datetime import timedelta
    tomorrow = (get_now_tashkent() + timedelta(days=1)).strftime("%Y-%m-%d")

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
            booking_time=f"{tomorrow} 15:00 (Завтра)",
            service="Стрижка волос"
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
                "booking_time": f"{tomorrow} 15:00 (Завтра)",
                "status": "new"
            }
        ]
        mock_extract.return_value = ExtractedBooking(
            is_booking_confirmed=True,
            customer_name="Тимур",
            booking_time=f"{tomorrow} 15:30 (Завтра)",
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


@pytest.mark.asyncio
async def test_barber_status_callback_confirmed():
    from bot.handlers import handle_booking_status_callback
    from aiogram.types import CallbackQuery

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=333444, is_bot=False, first_name="Дильшод")
    callback.data = "bstatus:42:confirmed"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "💈 Новая запись"
    callback.message.caption = None
    callback.message.edit_text = AsyncMock()
    callback.bot = AsyncMock()

    barber_data = {"id": 2, "name": "Mr. Dilshod", "role": "Стилист", "telegram_id": 333444}
    booking_data = {
        "id": 42,
        "full_name": "Тест Клиент",
        "service": "Мужская стрижка",
        "booking_time": "2026-10-18 14:00",
        "user_id": 777888,
        "barber_id": 2,
        "barber_name": "Mr. Dilshod",
    }

    with patch("bot.handlers.get_user_role", new_callable=AsyncMock) as mock_role, \
         patch("bot.handlers.get_booking_by_id", new_callable=AsyncMock) as mock_get_booking, \
         patch("bot.handlers.update_booking_status", new_callable=AsyncMock) as mock_update:

        mock_role.return_value = ("barber", barber_data)
        mock_get_booking.return_value = booking_data
        mock_update.return_value = True

        await handle_booking_status_callback(callback)

        mock_update.assert_called_once_with(42, "confirmed")
        callback.bot.send_message.assert_called_once()
        sent_args, sent_kwargs = callback.bot.send_message.call_args
        assert sent_kwargs["chat_id"] == 777888
        assert "ПОДТВЕРЖДЕНА" in sent_kwargs["text"]
        callback.message.edit_text.assert_called_once()
        callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_barber_status_callback_cancelled():
    from bot.handlers import handle_booking_status_callback
    from aiogram.types import CallbackQuery

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=111222, is_bot=False, first_name="Admin")
    callback.data = "bstatus:42:cancelled"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "💈 Новая запись"
    callback.message.caption = None
    callback.message.edit_text = AsyncMock()
    callback.bot = AsyncMock()

    booking_data = {
        "id": 42,
        "full_name": "Тест Клиент",
        "service": "Мужская стрижка",
        "booking_time": "2026-10-18 14:00",
        "user_id": 777888,
        "barber_id": 2,
        "barber_name": "Mr. Dilshod",
    }

    with patch("bot.handlers.get_user_role", new_callable=AsyncMock) as mock_role, \
         patch("bot.handlers.get_booking_by_id", new_callable=AsyncMock) as mock_get_booking, \
         patch("bot.handlers.update_booking_status", new_callable=AsyncMock) as mock_update:

        mock_role.return_value = ("admin", None)
        mock_get_booking.return_value = booking_data
        mock_update.return_value = True

        await handle_booking_status_callback(callback)

        mock_update.assert_called_once_with(42, "cancelled")
        callback.bot.send_message.assert_called_once()
        sent_args, sent_kwargs = callback.bot.send_message.call_args
        assert sent_kwargs["chat_id"] == 777888
        assert "отменена" in sent_kwargs["text"]
        callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_barber_status_callback_unauthorized():
    from bot.handlers import handle_booking_status_callback
    from aiogram.types import CallbackQuery

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=999888, is_bot=False, first_name="Stranger")
    callback.data = "bstatus:42:confirmed"
    callback.answer = AsyncMock()

    with patch("bot.handlers.get_user_role", new_callable=AsyncMock) as mock_role, \
         patch("bot.handlers.update_booking_status", new_callable=AsyncMock) as mock_update:

        mock_role.return_value = ("client", None)

        await handle_booking_status_callback(callback)

        mock_update.assert_not_called()
        callback.answer.assert_called_once()
        assert "прав" in callback.answer.call_args[0][0]




