import pytest
from unittest.mock import AsyncMock, MagicMock
from google.genai import types
from services.gemini_service import GeminiService
from prompts import FALLBACK_RESPONSE

def test_format_history_to_contents():
    service = GeminiService(api_key="test_key")
    raw_history = [
        {"role": "user", "text": "Салам"},
        {"role": "model", "text": "Салам алейкум, ака!"},
    ]
    contents = service.format_history_to_contents(raw_history, "Сколько стоит стрижка?")
    
    assert len(contents) == 3
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Салам"
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "Салам алейкум, ака!"
    assert contents[2].role == "user"
    assert contents[2].parts[0].text == "Сколько стоит стрижка?"

@pytest.mark.asyncio
async def test_generate_response_fallback_on_exception(monkeypatch):
    service = GeminiService(api_key="test_key")
    mock_client = MagicMock()
    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(side_effect=RuntimeError("API quota exceeded"))
    mock_client.aio.models = mock_models
    service.client = mock_client

    result = await service.generate_response(user_id=999, user_message="Тест сбоя")
    assert result == FALLBACK_RESPONSE
