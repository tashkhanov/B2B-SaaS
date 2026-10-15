import os
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from web_app import create_web_app
from database import init_db, save_booking
from config import settings

TEST_DB = "test_web.db"

@pytest.mark.asyncio
async def test_web_app_endpoints(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    booking_id = await save_booking(
        user_id=123,
        username="@bek",
        full_name="Бекзод",
        details="Комплекс",
        service="Комплекс",
        booking_time="2026-10-16 15:00 (Пятница)",
        db_path=TEST_DB
    )

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Test Client HTML (/ and /book)
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "Black Beard" in text
        assert "Онлайн-запись" in text
        assert "Powered by" in text
        assert "Khan" in text

        resp_book = await client.get("/book")
        assert resp_book.status == 200

        # 2. Test Admin HTML (/admin and /webapp)
        resp_admin = await client.get("/admin")
        assert resp_admin.status == 200
        admin_text = await resp_admin.text()
        assert "Панель администратора" in admin_text

        # 3. Test API config
        resp_cfg = await client.get("/api/config")
        assert resp_cfg.status == 200
        cfg_data = await resp_cfg.json()
        assert cfg_data["admin_chat_id"] == 999888

        # 4. Test API slots
        resp_slots = await client.get("/api/slots?date=2026-10-16")
        assert resp_slots.status == 200
        slots_data = await resp_slots.json()
        assert "slots" in slots_data
        # 15:00 should be busy because of booking_id
        slot_15 = next((s for s in slots_data["slots"] if s["time"] == "15:00"), None)
        assert slot_15 is not None
        assert slot_15["is_available"] is False

        # 5. Test API create booking (Success)
        resp_create = await client.post("/api/bookings", json={
            "full_name": "Жасур",
            "phone": "+998901112233",
            "service": "Мужская стрижка",
            "booking_time": "2026-10-16 17:00",
        })
        assert resp_create.status == 200
        res_create = await resp_create.json()
        assert res_create["ok"] is True
        assert res_create["id"] > 0

        # 6. Test API create booking (Conflict - trying 15:30 when 15:00 is taken)
        resp_conflict = await client.post("/api/bookings", json={
            "full_name": "Камиль",
            "service": "Стрижка",
            "booking_time": "2026-10-16 15:30",
        })
        assert resp_conflict.status == 400
        err_data = await resp_conflict.json()
        assert "занят" in err_data["error"]

        # 7. Test API get bookings
        resp_list = await client.get("/api/bookings")
        assert resp_list.status == 200
        bookings_list = await resp_list.json()
        assert len(bookings_list) >= 2

        # 8. Test status update
        resp_status = await client.post(f"/api/bookings/{booking_id}/status", json={"status": "confirmed"})
        assert resp_status.status == 200
        res_status = await resp_status.json()
        assert res_status.get("ok") is True

        # 9. Test /api/my-booking
        resp_my = await client.get("/api/my-booking?user_id=123")
        assert resp_my.status == 200
        my_data = await resp_my.json()
        assert my_data["booking"] is not None
        assert my_data["booking"]["id"] == booking_id

        # 10. Test /api/my-booking/cancel
        resp_cancel = await client.post("/api/my-booking/cancel", json={"user_id": 123, "booking_id": booking_id})
        assert resp_cancel.status == 200
        cancel_data = await resp_cancel.json()
        assert cancel_data["ok"] is True

        # Verify it's no longer active
        resp_my_after = await client.get("/api/my-booking?user_id=123")
        my_after_data = await resp_my_after.json()
        assert my_after_data["booking"] is None

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
