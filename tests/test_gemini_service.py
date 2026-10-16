import pytest
from unittest.mock import AsyncMock, MagicMock
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

