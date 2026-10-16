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


@pytest.mark.asyncio
async def test_services_crud():
    from database import get_services, get_service_by_id, create_service, update_service, delete_service

    # Проверка начального seed (12 дефолтных услуг Brohouse)
    services = await get_services(db_path=TEST_DB)
    assert len(services) == 12
    assert any(s["name"] == "Стрижка волос" for s in services)

    # Создание новой услуги
    new_id = await create_service(
        name="Камуфляж седины",
        price=70000,
        duration_min=30,
        description="Тонирование бороды и волос",
        badge="NEW",
        db_path=TEST_DB
    )
    assert new_id > 0

    s = await get_service_by_id(new_id, db_path=TEST_DB)
    assert s["name"] == "Камуфляж седины"
    assert s["price"] == 70000
    assert s["duration_min"] == 30
    assert s["badge"] == "NEW"

    # Обновление услуги
    updated = await update_service(
        service_id=new_id,
        name="Камуфляж седины PRO",
        price=75000,
        duration_min=35,
        description="Премиум тонирование",
        badge="ХИТ",
        is_active=1,
        db_path=TEST_DB
    )
    assert updated is True
    s_updated = await get_service_by_id(new_id, db_path=TEST_DB)
    assert s_updated["name"] == "Камуфляж седины PRO"
    assert s_updated["price"] == 75000

    # Soft-delete услуги
    deleted = await delete_service(new_id, hard=False, db_path=TEST_DB)
    assert deleted is True

    # active_only=True не должен ее возвращать
    active_services = await get_services(active_only=True, db_path=TEST_DB)
    assert not any(x["id"] == new_id for x in active_services)

    # active_only=False возвращает
    all_services = await get_services(active_only=False, db_path=TEST_DB)
    assert any(x["id"] == new_id for x in all_services)


@pytest.mark.asyncio
async def test_barbers_crud_and_bookings():
    from database import (
        get_barbers, get_barber_by_id, get_barber_by_telegram_id,
        create_barber, update_barber, toggle_barber_shift,
        get_bookings_for_barber
    )

    # Проверка начального seed (4 дефолтных мастера Brohouse)
    barbers = await get_barbers(db_path=TEST_DB)
    assert len(barbers) == 4
    assert any(b["name"] == "Mr. Akmal" for b in barbers)
    assert any(b["name"] == "Mr. Dilshod" for b in barbers)

    # Создание мастера
    new_barber_id = await create_barber(
        name="Тимур",
        role_title="Топ-барбер",
        phone="+998909998877",
        telegram_id=999888,
        work_days="0,1,2,3,4",
        is_working_today=1,
        db_path=TEST_DB
    )
    assert new_barber_id > 0

    # Поиск по Telegram ID
    b_tg = await get_barber_by_telegram_id(999888, db_path=TEST_DB)
    assert b_tg is not None
    assert b_tg["name"] == "Тимур"
    assert b_tg["role_title"] == "Топ-барбер"

    # Тумблер смены (на смене -> выходной)
    await toggle_barber_shift(new_barber_id, is_working_today=0, db_path=TEST_DB)
    b_off = await get_barber_by_id(new_barber_id, db_path=TEST_DB)
    assert b_off["is_working_today"] == 0

    # Запись к этому мастеру
    b_id = await save_booking(
        user_id=555,
        username="@client1",
        full_name="Клиент Мастера",
        details="Стрижка у Тимура",
        booking_time="2026-10-18 14:00 (Воскресенье)",
        barber_id=new_barber_id,
        barber_name="Тимур",
        db_path=TEST_DB
    )
    assert b_id > 0

    # Проверка получения записей мастера
    master_bookings = await get_bookings_for_barber(new_barber_id, db_path=TEST_DB)
    assert len(master_bookings) == 1
    assert master_bookings[0]["barber_name"] == "Тимур"
    assert master_bookings[0]["full_name"] == "Клиент Мастера"

