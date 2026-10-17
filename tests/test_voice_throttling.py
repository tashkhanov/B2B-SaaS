import pytest
import time
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, Voice, User, Chat
from bot.handlers import handle_voice_message, _voice_rate_limit


@pytest.mark.asyncio
async def test_voice_duration_limit():
    _voice_rate_limit.clear()
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=111, is_bot=False, first_name="Test")
    msg.chat = Chat(id=111, type="private")
    msg.voice = Voice(file_id="voice_1", file_unique_id="u1", duration=75, file_size=1000)
    msg.bot = AsyncMock()
    msg.answer = AsyncMock()

    await handle_voice_message(msg)
    msg.answer.assert_called_once()
    assert "слишком длинное" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_voice_file_size_limit():
    _voice_rate_limit.clear()
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=112, is_bot=False, first_name="Test")
    msg.chat = Chat(id=112, type="private")
    # 3 MB voice file
    msg.voice = Voice(file_id="voice_2", file_unique_id="u2", duration=30, file_size=3 * 1024 * 1024)
    msg.bot = AsyncMock()
    msg.answer = AsyncMock()

    await handle_voice_message(msg)
    msg.answer.assert_called_once()
    assert "2 МБ" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_voice_rate_limiting_5_seconds():
    _voice_rate_limit.clear()
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=113, is_bot=False, first_name="Test")
    msg.chat = Chat(id=113, type="private")
    msg.voice = Voice(file_id="voice_3", file_unique_id="u3", duration=10, file_size=50000)
    msg.bot = AsyncMock()
    msg.answer = AsyncMock()

    # Имитируем первое сообщение, зафиксировав время
    _voice_rate_limit[113] = time.time()

    await handle_voice_message(msg)
    msg.answer.assert_called_once()
    assert "подождите 5 секунд" in msg.answer.call_args[0][0]
