import os
import pytest
import pytest_asyncio
from database import init_db, save_booking, get_all_bookings, update_booking_status

TEST_DB = "test_tma.db"

@pytest_asyncio.fixture(autouse=True)
async def cleanup():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

@pytest.mark.asyncio
async def test_save_and_get_structured_booking():
    booking_id = await save_booking(
        user_id=111,
        username="@testuser",
        full_name="Сардор",
        details="Хочу стрижку",
        phone="+998901234567",
        service="Стрижка",
        booking_time="Завтра в 17:00",
        status="new",
        db_path=TEST_DB
    )
    assert booking_id > 0

    bookings = await get_all_bookings(db_path=TEST_DB)
    assert len(bookings) == 1
    b = bookings[0]
    assert b["full_name"] == "Сардор"
    assert b["phone"] == "+998901234567"
    assert b["service"] == "Стрижка"
    assert b["booking_time"] == "Завтра в 17:00"
    assert b["status"] == "new"

@pytest.mark.asyncio
async def test_update_booking_status():
    booking_id = await save_booking(
        user_id=222,
        username="@rustam",
        full_name="Рустам",
        details="Комплекс",
        service="Комплекс",
        booking_time="Сегодня 19:00",
        status="new",
        db_path=TEST_DB
    )

    updated = await update_booking_status(booking_id, "confirmed", db_path=TEST_DB)
    assert updated is True

    bookings = await get_all_bookings(db_path=TEST_DB)
    assert bookings[0]["status"] == "confirmed"
