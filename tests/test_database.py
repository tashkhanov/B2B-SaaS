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
        booking_time="Завтра в 19:00",
        status="new",
        db_path=TEST_DB
    )

    updated = await update_booking_status(booking_id, "confirmed", db_path=TEST_DB)
    assert updated is True

    bookings = await get_all_bookings(db_path=TEST_DB)
    assert bookings[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_auto_complete_past_bookings():
    from database import auto_complete_past_bookings, get_booking_by_id

    # Запись в прошлом (Сегодня в 10:00)
    past_id = await save_booking(
        user_id=777,
        username="@past",
        full_name="Прошедший Клиент",
        details="Стрижка",
        booking_time="2020-01-01 10:00",
        status="new",
        db_path=TEST_DB
    )

    # Запись в будущем (Завтра в 15:00)
    future_id = await save_booking(
        user_id=888,
        username="@future",
        full_name="Будущий Клиент",
        details="Борода",
        booking_time="Завтра в 15:00",
        status="new",
        db_path=TEST_DB
    )

    completed_count = await auto_complete_past_bookings(db_path=TEST_DB)
    assert completed_count >= 1

    past_b = await get_booking_by_id(past_id, db_path=TEST_DB)
    assert past_b["status"] == "completed"

    future_b = await get_booking_by_id(future_id, db_path=TEST_DB)
    assert future_b["status"] == "new"

@pytest.mark.asyncio
async def test_has_recent_booking():
    from database import has_recent_booking

    assert await has_recent_booking(user_id=333, booking_time="Завтра в 15:00", db_path=TEST_DB) is False

    await save_booking(
        user_id=333,
        username="@abdulaziz",
        full_name="Абдулазиз",
        details="Стрижка",
        booking_time="Завтра в 15:00",
        db_path=TEST_DB
    )

    assert await has_recent_booking(user_id=333, booking_time="Завтра в 15:00", db_path=TEST_DB) is True
    assert await has_recent_booking(user_id=333, booking_time="Послезавтра 18:00", db_path=TEST_DB) is False

