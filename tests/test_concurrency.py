import os
import asyncio
import pytest
from datetime import timedelta
from aiohttp.test_utils import TestClient, TestServer
from config import settings
from database import init_db, get_bookings_for_barber
from services.schedule_service import get_now_tashkent
from web_app import create_web_app

TEST_DB = "test_concurrency.db"


@pytest.mark.asyncio
async def test_concurrent_double_booking_prevention(monkeypatch):
    """
    Тест на устойчивость к состоянию гонки (Race Condition / Double Booking):
    10 одновременных запросов на один и тот же слот времени одного мастера.
    Результат: ровно 1 успешная запись (200 OK), остальные 9 получают отказ (409 Conflict).
    В базе данных создается ровно 1 запись.
    """
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        tomorrow = (get_now_tashkent().date() + timedelta(days=2)).isoformat()
        slot_time = f"{tomorrow} 14:00"

        async def attempt_booking(idx: int):
            payload = {
                "full_name": f"Клиент #{idx}",
                "phone": f"+99890123000{idx}",
                "service": "Мужская стрижка",
                "booking_time": slot_time,
                "barber_id": 1,
            }
            return await client.post("/api/bookings", json=payload)

        # Запускаем 10 параллельных запросов одновременно
        tasks = [attempt_booking(i) for i in range(10)]
        responses = await asyncio.gather(*tasks)

        statuses = [r.status for r in responses]
        success_count = statuses.count(200)
        conflict_count = statuses.count(409)

        # Ровно один запрос успешен
        assert success_count == 1, f"Ожидался ровно 1 успешный запрос, но успешно: {success_count}. Статусы: {statuses}"
        # Остальные получили 409 Conflict
        assert conflict_count == 9, f"Ожидалось 9 конфликтов 409, но получено: {conflict_count}"

        # Проверяем базу данных: создана ровно 1 запись, никаких дубликатов
        from database import get_all_bookings
        bookings = await get_all_bookings(db_path=TEST_DB)
        assert len(bookings) == 1, f"В базе обнаружен дубликат слота: {len(bookings)} записей!"
        assert bookings[0]["barber_id"] == 1


    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
