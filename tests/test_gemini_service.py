import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.gemini_service import GeminiService, ExtractedBooking

def test_format_history_to_contents():
    service = GeminiService(api_key="test_key")
    raw_history = [
        {"role": "user", "text": "Салам"},
        {"role": "model", "text": "Салам алейкум, ака!"},
    ]
    contents = service.format_history_to_contents(raw_history, "Сколько стоит стрижка?")
    assert len(contents) == 3
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[2].role == "user"

@pytest.mark.asyncio
async def test_generate_response_from_voice():
    service = GeminiService(api_key="test_key")
    mock_client = MagicMock()
    mock_models = MagicMock()
    
    mock_response = MagicMock()
    mock_response.text = "Салам алейкум, ака! Без проблем, стрижка 100к сум."
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_client.aio.models = mock_models
    service.client = mock_client

    reply, transcript = await service.generate_response_from_voice(user_id=888, voice_bytes=b"fake_audio_bytes")
    assert "Салам алейкум" in reply
    assert mock_models.generate_content.called

@pytest.mark.asyncio
async def test_extract_booking_info():
    service = GeminiService(api_key="test_key")
    mock_client = MagicMock()
    mock_models = MagicMock()

    mock_response = MagicMock()
    mock_response.text = '{"is_booking_confirmed": true, "customer_name": "Тимур", "phone": "+998901112233", "service": "Стрижка + Борода", "booking_time": "Завтра в 15:00"}'
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_client.aio.models = mock_models
    service.client = mock_client

    extracted = await service.extract_booking_info("Клиент: Запишите меня на завтра в 15:00, Тимур")
    assert extracted is not None
    assert extracted.is_booking_confirmed is True
    assert extracted.customer_name == "Тимур"
    assert extracted.booking_time == "Завтра в 15:00"


def test_is_super_admin_user():
    from services.gemini_service import is_super_admin_user
    assert is_super_admin_user(1243374131) is True
    assert is_super_admin_user(99999999) is False
    assert is_super_admin_user(None) is False


@pytest.mark.asyncio
async def test_creator_prompt_injection():
    from services.gemini_service import GeminiService
    from unittest.mock import patch

    service = GeminiService(api_key="test_key")
    mock_client = MagicMock()
    mock_models = MagicMock()

    mock_response = MagicMock()
    mock_response.text = "От души, Иброхим ака! Рад вас приветствовать."
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_client.aio.models = mock_models
    service.client = mock_client

    with patch("services.gemini_service.get_recent_messages", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_barbers", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_services", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.add_message", new_callable=AsyncMock):

        reply = await service.generate_response(user_id=1243374131, user_message="Салам, как дела?")
        assert "Иброхим ака" in reply

        # Проверяем, что в config.system_instruction был передан промпт создателя
        call_kwargs = mock_models.generate_content.call_args[1]
        system_instruction = call_kwargs["config"].system_instruction
        assert "ИБРОХИМ" in system_instruction
        assert "СОЗДАТЕЛЬ" in system_instruction
        assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО СПРАШИВАТЬ ЕГО ИМЯ" in system_instruction


@pytest.mark.asyncio
async def test_regular_user_prompt_does_not_contain_creator_identity():
    service = GeminiService()
    mock_client = MagicMock()
    mock_models = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Здравствуйте! Как к вам обращаться?"
    mock_response.function_calls = []
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_client.aio.models = mock_models
    service.client = mock_client

    with patch("services.gemini_service.get_recent_messages", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_barbers", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_services", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.add_message", new_callable=AsyncMock):

        await service.generate_response(user_id=905730373, user_message="Запиши меня к Акмалю")
        call_kwargs = mock_models.generate_content.call_args[1]
        system_instruction = call_kwargs["config"].system_instruction
        assert "ОСОБЫЙ СТАТУС ПОЛЬЗОВАТЕЛЯ — СОЗДАТЕЛЬ И СУПЕР-АДМИН (ИБРОХИМ)" not in system_instruction
        assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО СПРАШИВАТЬ ЕГО ИМЯ" not in system_instruction


def test_dynamic_white_label_prompts():
    from prompts import (
        build_client_system_prompt,
        build_admin_system_prompt,
        build_creator_identity_prompt,
    )
    from config import settings

    client_p = build_client_system_prompt()
    assert settings.SALON_SHORT_NAME in client_p
    assert settings.SALON_NAME in client_p
    assert settings.SALON_CITY in client_p
    assert str(settings.PROMO_DISCOUNT_PERCENT) in client_p

    admin_p = build_admin_system_prompt()
    assert settings.SALON_NAME in admin_p
    assert settings.SALON_CITY in admin_p

    creator_p = build_creator_identity_prompt()
    assert settings.SALON_SHORT_NAME in creator_p

    # Проверка голосового режима по тарифу
    assert "СТРОГО ТЕКСТОМ" in client_p
    client_p_voice = build_client_system_prompt(has_voice=True)
    assert "отлично воспринимаешь голосовые" in client_p_voice

    # Проверка отсутствия навязчивых шаблонных фраз и слов-паразитов
    for bad_phrase in ["от души", "ээ ака", "братан", "туда-сюда", "гап йўқ"]:
        assert bad_phrase not in client_p.lower()

    # Проверка правил языковой чистоты
    assert "СТРОГО ЗЕРКАЛЬ ЯЗЫК КЛИЕНТА" in client_p
    assert "ЖЕСТКИЙ ЗАПРЕТ НА СМЕШИВАНИЕ ЯЗЫКОВ" in client_p


@pytest.mark.asyncio
async def test_generate_response_executes_tool_call():
    from google.genai import types
    from unittest.mock import patch

    service = GeminiService(api_key="test_key")
    mock_client = MagicMock()
    mock_models = MagicMock()

    # Turn 1: Model calls create_booking_reservation tool
    fc = types.FunctionCall(
        name="create_booking_reservation",
        args={
            "customer_name": "Иброхим",
            "booking_time": "2026-10-20 14:00 (Вторник)",
            "barber_name_or_id": "Mr. Akmal",
            "branch_name_or_id": "Миллий бог",
            "service_name_or_id": "Стрижка волос",
        }
    )
    first_resp = MagicMock()
    first_resp.function_calls = [fc]
    first_resp.candidates = [MagicMock(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))]
    first_resp.text = ""

    # Turn 2: After receiving tool response, model outputs final confirmation
    second_resp = MagicMock()
    second_resp.function_calls = None
    second_resp.text = "👑 От души, Иброхим ака! Запрос на 14:00 к Шеф-мастеру Mr. Akmal отправлен на личное согласование!"

    mock_models.generate_content = AsyncMock(side_effect=[first_resp, second_resp])
    mock_client.aio.models = mock_models
    service.client = mock_client

    mock_tool_result = {
        "success": True,
        "status": "pending_vip",
        "booking_id": 999,
        "barber_name": "Mr. Akmal",
        "branch_name": "Миллий бог",
        "requires_manual_approval": True,
        "message": "Запрос отправлен на согласование",
    }

    with patch.object(service.booking_tools, "create_booking_reservation", new_callable=AsyncMock, return_value=mock_tool_result) as mock_book_tool, \
         patch("services.gemini_service.get_recent_messages", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_barbers", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_services", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_branches", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.add_message", new_callable=AsyncMock):

        reply = await service.generate_response(
            user_id=1243374131,
            user_message="Ertaga 14:00 ga Mr. Akmalga yozib qo'y",
        )

        assert "Mr. Akmal" in reply
        assert "согласование" in reply
        mock_book_tool.assert_called_once()
        call_kwargs = mock_book_tool.call_args[1]
        assert call_kwargs["barber_name_or_id"] == "Mr. Akmal"
        assert call_kwargs["customer_name"] == "Иброхим"


@pytest.mark.asyncio
async def test_multi_turn_compound_cancel_and_create():
    """Тест: составной запрос 'отмени и запиши' выполняет оба инструмента в цикле"""
    service = GeminiService()
    mock_client = MagicMock()
    mock_models = AsyncMock()

    # Turn 1: Model calls cancel_booking_reservation
    fc_cancel = MagicMock()
    fc_cancel.name = "cancel_booking_reservation"
    fc_cancel.args = {}
    resp_turn1 = MagicMock()
    resp_turn1.function_calls = [fc_cancel]
    resp_turn1.candidates = []

    # Turn 2: Model calls create_booking_reservation with glitched name 'Каким'
    fc_create = MagicMock()
    fc_create.name = "create_booking_reservation"
    fc_create.args = {
        "customer_name": "Каким",
        "booking_time": "2026-10-20 18:00 (Вторник)",
        "barber_name_or_id": "Mr. Akmal",
    }
    resp_turn2 = MagicMock()
    resp_turn2.function_calls = [fc_create]
    resp_turn2.candidates = []

    # Turn 3: Model returns final text summary
    resp_turn3 = MagicMock()
    resp_turn3.function_calls = []
    resp_turn3.text = "Старая бронь отменена, а новая запись к Mr. Akmal на 18:00 передана Шефу на согласование!"

    mock_models.generate_content = AsyncMock(side_effect=[resp_turn1, resp_turn2, resp_turn3])
    mock_client.aio.models = mock_models
    service.client = mock_client

    mock_cancel_res = {"success": True, "message": "Запись успешно отменена"}
    mock_create_res = {
        "success": True,
        "status": "pending_vip",
        "booking_id": 1001,
        "barber_name": "Mr. Akmal",
        "message": "Запрос отправлен на согласование",
    }

    with patch.object(service.booking_tools, "cancel_booking_reservation", new_callable=AsyncMock, return_value=mock_cancel_res) as mock_cancel, \
         patch.object(service.booking_tools, "create_booking_reservation", new_callable=AsyncMock, return_value=mock_create_res) as mock_create, \
         patch("services.gemini_service.get_recent_messages", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_active_bookings", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_barbers", new_callable=AsyncMock, return_value=[{"id": 4, "name": "Mr. Akmal"}]), \
         patch("services.gemini_service.get_services", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.get_branches", new_callable=AsyncMock, return_value=[]), \
         patch("services.gemini_service.add_message", new_callable=AsyncMock):

        reply = await service.generate_response(
            user_id=1243374131,
            user_message="Отмени текущую запись и запиши меня к Акмалю на 18:00",
        )

        assert mock_cancel.called
        assert mock_create.called
        create_kwargs = mock_create.call_args[1]
        # "Каким" was sanitized to "Иброхим"
        assert create_kwargs["customer_name"] == "Иброхим"
        assert service.had_tool_calls(1243374131) is True
        assert "Старая бронь отменена" in reply




