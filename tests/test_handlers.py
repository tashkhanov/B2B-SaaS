import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User
from bot.handlers import handle_start, handle_price, handle_reset

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
async def test_handle_price():
    message = MagicMock(spec=Message)
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
