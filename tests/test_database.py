import os
import pytest
import pytest_asyncio
import aiosqlite
from database import init_db, add_message, get_recent_messages, clear_history, save_booking

TEST_DB = "test_run.db"

@pytest_asyncio.fixture(autouse=True)
async def cleanup():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

@pytest.mark.asyncio
async def test_add_and_get_messages():
    await add_message(101, "user", "Привет, сколько стоит стрижка?", db_path=TEST_DB)
    await add_message(101, "model", "Салам алейкум, ака! Стрижка 100к сум.", db_path=TEST_DB)

    messages = await get_recent_messages(101, limit=10, db_path=TEST_DB)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "Привет, сколько стоит стрижка?"
    assert messages[1]["role"] == "model"
    assert "Салам алейкум" in messages[1]["text"]

@pytest.mark.asyncio
async def test_sliding_window_limit():
    for i in range(15):
        await add_message(202, "user" if i % 2 == 0 else "model", f"Message {i}", db_path=TEST_DB)

    # Get last 6 messages
    messages = await get_recent_messages(202, limit=6, db_path=TEST_DB)
    assert len(messages) == 6
    assert messages[0]["text"] == "Message 9"
    assert messages[-1]["text"] == "Message 14"

@pytest.mark.asyncio
async def test_clear_history():
    await add_message(303, "user", "Привет", db_path=TEST_DB)
    await clear_history(303, db_path=TEST_DB)
    messages = await get_recent_messages(303, limit=10, db_path=TEST_DB)
    assert len(messages) == 0

@pytest.mark.asyncio
async def test_save_booking():
    booking_id = await save_booking(404, "testuser", "Тимур", "Стрижка + борода, завтра в 15:00", db_path=TEST_DB)
    assert booking_id is not None
    assert booking_id > 0
