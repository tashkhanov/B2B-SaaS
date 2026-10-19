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
    assert "Assalomu alaykum, Алишер! Xush kelibsiz! / Здравствуйте!" in args[0]
    assert "Muloqot tili / Язык общения" in args[0]
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
async def test_handle_clear_secret_command():
    from bot.handlers import handle_clear, get_main_keyboard
    message = MagicMock(spec=Message)
    message.from_user = User(id=905730373, is_bot=False, first_name="TestUser")
    message.answer = AsyncMock()

    with patch("bot.handlers.clear_history", new_callable=AsyncMock) as mock_clear:
        await handle_clear(message)
        mock_clear.assert_called_once_with(905730373)
        message.answer.assert_called_once()


def test_keyboards_no_reset_button():
    from bot.handlers import get_main_keyboard
    for role in ["client", "barber", "admin", "super_admin"]:
        kb = get_main_keyboard(role=role)
        all_btn_texts = [btn.text for row in kb.keyboard for btn in row]
        assert "🔄 Сбросить диалог" not in all_btn_texts

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


@pytest.mark.asyncio
async def test_subscription_middleware_blocks_client_when_locked():
    from bot.middlewares import SubscriptionCheckMiddleware
    from services.billing_service import SubscriptionStatus
    from aiogram.types import Message, User, Chat

    middleware = SubscriptionCheckMiddleware()
    message = MagicMock(spec=Message)
    message.from_user = User(id=111222, is_bot=False, first_name="Client")
    message.text = "Хочу записаться"
    message.answer = AsyncMock()

    next_handler = AsyncMock()

    with patch("bot.middlewares.check_tenant_access", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = (True, SubscriptionStatus.LOCKED, {"name": "Brohouse", "is_locked": True})

        await middleware(next_handler, message, {})

        next_handler.assert_not_called()
        message.answer.assert_called_once()
        assert "временно недоступна" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_subscription_middleware_allows_super_admin_bypass():
    from bot.middlewares import SubscriptionCheckMiddleware
    from services.billing_service import SubscriptionStatus
    from aiogram.types import Message, User
    from config import settings

    middleware = SubscriptionCheckMiddleware()
    message = MagicMock(spec=Message)
    message.from_user = User(id=999999, is_bot=False, first_name="SuperAdmin")
    message.text = "Любая команда"
    message.answer = AsyncMock()

    next_handler = AsyncMock(return_value="OK")

    with patch.object(settings, "SUPER_ADMIN_CHAT_ID", 999999):
        res = await middleware(next_handler, message, {})
        next_handler.assert_called_once_with(message, {})
        assert res == "OK"


@pytest.mark.asyncio
async def test_handle_super_command():
    from bot.handlers import handle_super
    from aiogram.types import Message, User
    from config import settings

    message = MagicMock(spec=Message)
    message.from_user = User(id=777, is_bot=False, first_name="Khan")
    message.answer = AsyncMock()

    with patch.object(settings, "SUPER_ADMIN_CHAT_ID", 777), \
         patch.object(settings, "WEBAPP_URL", "https://test.brohouse.uz"):
        await handle_super(message)
        message.answer.assert_called_once()
        assert "Super-Admin" in message.answer.call_args[0][0]
        reply_markup = message.answer.call_args[1]["reply_markup"]
        btn = reply_markup.inline_keyboard[0][0]
        assert ("hub.asatkhanov.uz" in btn.web_app.url) or ("super-admin" in btn.web_app.url)


@pytest.mark.asyncio
async def test_super_admin_role_and_greeting():
    """Verify that user_id=1243374131 is identified as Super-Admin / Creator and greeted by name Иброхим"""
    from bot.handlers import get_user_role, handle_start
    from aiogram.types import Message, User

    role, barber = await get_user_role(1243374131)
    assert role == "super_admin"
    assert barber is None

    message = MagicMock(spec=Message)
    message.from_user = User(id=1243374131, is_bot=False, first_name="Ibrohim")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    await handle_start(message)
    message.answer.assert_called_once()
    greeting_text = message.answer.call_args[0][0]
    assert "Иброхим ака" in greeting_text
    assert "Создатель платформы" in greeting_text


@pytest.mark.asyncio
async def test_creator_personal_booking():
    """Verify that when Ibrohim requests a booking for himself, it is recorded under his name and ID without asking name"""
    from bot.handlers import process_admin_intent
    from aiogram.types import Message, User
    from services.gemini_service import AdminAction

    message = MagicMock(spec=Message)
    message.from_user = User(id=1243374131, is_bot=False, first_name="Ibrohim", username="asatkhanov")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    mock_admin_action = AdminAction(
        action="create_booking",
        client_query="Иброхим",
        booking_time="2026-10-20 16:00",
        service="Стрижка волос",
        barber_name="Tursunov Nodir",
    )

    with patch("bot.handlers.gemini_service.extract_admin_action", new_callable=AsyncMock) as mock_extract, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_active, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_barbers:

        mock_extract.return_value = mock_admin_action
        mock_active.return_value = []
        mock_barbers.return_value = [
            {"id": 4, "name": "Tursunov Nodir", "role": "Стилист", "is_active": 1, "telegram_id": None}
        ]

        await process_admin_intent(
            message=message,
            admin_query="Запиши меня на 16:00 к Нодиру",
            ai_reply="От души, Иброхим ака! Записал вас на 16:00 к Нодиру.",
        )

        mock_save.assert_called_once()
        save_kwargs = mock_save.call_args[1]
        assert save_kwargs["user_id"] == 1243374131
        assert save_kwargs["full_name"] == "Иброхим"
        assert save_kwargs["status"] == "confirmed"
        assert "Личная запись Создателя" in save_kwargs["details"]

        message.answer.assert_called_once()
        reply_text = message.answer.call_args[0][0]
        assert "Иброхим ака, вы успешно записаны" in reply_text


@pytest.mark.asyncio
async def test_handle_location_branches():
    from bot.handlers import handle_location

    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Клиент")
    message.answer = AsyncMock()

    await handle_location(message)
    message.answer.assert_called_once()
    text = message.answer.call_args[0][0]
    assert "Миллий бог" in text
    assert "Чиланзар (2-й кв.)" in text
    assert "Чиланзар, 2-й квартал, дом 58/5" in text
    assert "+998 97 012-55-55" in text


@pytest.mark.asyncio
async def test_process_client_intent_branch_selection():
    from bot.handlers import process_client_intent
    from services.gemini_service import ClientAction

    message = MagicMock(spec=Message)
    message.from_user = User(id=999111, is_bot=False, first_name="Сардор", username="sardor_uz")
    message.answer = AsyncMock()
    message.bot = AsyncMock()

    mock_client_action = ClientAction(
        action="create_booking",
        is_booking_confirmed=True,
        customer_name="Сардор",
        booking_time="2026-10-22 15:00",
        service="Стрижка волос",
        branch_name="Чиланзар (2-й кв.)",
        branch_id=2,
    )

    with patch("bot.handlers.get_recent_messages", new_callable=AsyncMock) as mock_recent, \
         patch("bot.handlers.gemini_service.extract_client_action", new_callable=AsyncMock) as mock_extract, \
         patch("bot.handlers.get_user_active_booking", new_callable=AsyncMock) as mock_active_b, \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock) as mock_bookings, \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock) as mock_barbers, \
         patch("bot.handlers.save_booking", new_callable=AsyncMock) as mock_save:

        mock_recent.return_value = [{"role": "user", "text": "Запишите меня на Чиланзар в четверг в 15:00"}]
        mock_extract.return_value = mock_client_action
        mock_active_b.return_value = None
        mock_bookings.return_value = []
        mock_barbers.return_value = [
            {"id": 1, "name": "Mr. Akmal", "role": "Барбер", "is_active": 1, "telegram_id": None}
        ]
        mock_save.return_value = 77

        await process_client_intent(
            message=message,
            client_query="Запишите меня на Чиланзар в четверг в 15:00",
            ai_reply="Договорились, записал вас на Чиланзаре на 15:00!",
        )

        mock_save.assert_called_once()
        kwargs = mock_save.call_args[1]
        assert kwargs["branch_id"] == 2
        assert kwargs["branch_name"] == "Чиланзар (2-й кв.)"


@pytest.mark.asyncio
async def test_white_label_handlers_output():
    from bot.handlers import handle_start, handle_about, handle_price
    from config import settings

    # 1. Test /start for client
    client_msg = MagicMock(spec=Message)
    client_msg.from_user = MagicMock()
    client_msg.from_user.id = 987654321
    client_msg.from_user.first_name = "Алишер"
    client_msg.answer = AsyncMock()

    with patch("bot.handlers.get_user_role", new_callable=AsyncMock, return_value=("client", None)), \
         patch("bot.handlers.update_chat_menu_button", new_callable=AsyncMock):
        await handle_start(client_msg)
        args, kwargs = client_msg.answer.call_args
        text = args[0]
        assert settings.SALON_SHORT_NAME in text
        assert settings.SALON_CITY in text

    # 2. Test /about
    about_msg = MagicMock(spec=Message)
    about_msg.answer = AsyncMock()
    with patch("bot.handlers.get_branches", new_callable=AsyncMock, return_value=[]), \
         patch("bot.handlers.get_salon_setting", new_callable=AsyncMock, side_effect=lambda k, d: d):
        await handle_about(about_msg)
        args, kwargs = about_msg.answer.call_args
        text = args[0]
        assert settings.SALON_NAME in text
        assert settings.DEVELOPER_NAME in text
        assert settings.PLATFORM_NAME in text

    # 3. Test /price
    price_msg = MagicMock(spec=Message)
    price_msg.from_user = MagicMock()
    price_msg.from_user.id = 987654321
    price_msg.answer = AsyncMock()
    with patch("bot.handlers.get_user_role", new_callable=AsyncMock, return_value=("client", None)), \
         patch("bot.handlers.get_services", new_callable=AsyncMock, return_value=[{"name": "Стрижка", "price": 100000, "duration_min": 45, "badge": "", "description": ""}]), \
         patch("bot.handlers.get_salon_setting", new_callable=AsyncMock, side_effect=lambda k, d: d):
        await handle_price(price_msg)
        args, kwargs = price_msg.answer.call_args
        text = args[0]
        assert settings.SALON_NAME in text
        assert settings.CURRENCY_SYMBOL in text


@pytest.mark.asyncio
async def test_process_admin_intent_booking_conflict():
    from bot.handlers import process_admin_intent
    from database import BookingConflictError
    from services.gemini_service import AdminAction

    message = MagicMock(spec=Message)
    message.from_user = User(id=1243374131, is_bot=False, first_name="Ibrohim", username="asatkhanov")
    message.answer = AsyncMock()

    mock_action = AdminAction(
        action="create_booking",
        is_booking_confirmed=True,
        booking_time="2026-10-20 14:00 (Вторник)",
        barber_name="Mr. Akmal",
        service="Стрижка волос",
        branch_name="Миллий бог",
    )

    with patch("bot.handlers.GeminiService.extract_admin_action", new_callable=AsyncMock, return_value=mock_action), \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock, return_value=[{"id": 4, "name": "Mr. Akmal", "is_vip": 1}]), \
         patch("bot.handlers.resolve_branch", new_callable=AsyncMock, return_value=(1, "Миллий бог", "ул. Чиланзар")), \
         patch("bot.handlers.resolve_service_info", new_callable=AsyncMock, return_value=({"id": 1, "name": "Стрижка волос"}, 45)), \
         patch("bot.handlers.save_booking", new_callable=AsyncMock, side_effect=BookingConflictError("Слот занят")):
        await process_admin_intent(message, admin_query="Запиши меня на 14:00 к Акмалю", ai_reply="Хорошо")
        message.answer.assert_called_once()
        args, _ = message.answer.call_args
        assert "⚠️ **Шеф, этот слот уже занят!**" in args[0]


@pytest.mark.asyncio
async def test_process_client_intent_booking_conflict():
    from bot.handlers import process_client_intent
    from database import BookingConflictError
    from services.gemini_service import ClientAction

    message = MagicMock(spec=Message)
    message.from_user = User(id=99999, is_bot=False, first_name="Сардор", username="sardor")
    message.answer = AsyncMock()

    mock_action = ClientAction(
        action="create_booking",
        is_booking_confirmed=True,
        booking_time="2026-10-20 14:00 (Вторник)",
        barber_name="Dilshod",
        service="Стрижка волос",
    )

    with patch("bot.handlers.GeminiService.extract_client_action", new_callable=AsyncMock, return_value=mock_action), \
         patch("bot.handlers.get_user_active_booking", new_callable=AsyncMock, return_value=None), \
         patch("bot.handlers.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("bot.handlers.get_barbers", new_callable=AsyncMock, return_value=[{"id": 2, "name": "Dilshod", "is_vip": 0}]), \
         patch("bot.handlers.resolve_branch", new_callable=AsyncMock, return_value=(1, "Миллий бог", "ул. Чиланзар")), \
         patch("bot.handlers.resolve_service_info", new_callable=AsyncMock, return_value=({"id": 1, "name": "Стрижка волос"}, 45)), \
         patch("bot.handlers.save_booking", new_callable=AsyncMock, side_effect=BookingConflictError("Слот занят")):
        await process_client_intent(message, client_query="Запиши на 14:00 к Дильшоду", ai_reply="Ок")
        message.answer.assert_called_once()
        args, _ = message.answer.call_args
        assert "⚠️ **Ака, на это время" in args[0]









