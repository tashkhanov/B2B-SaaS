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

    from services.schedule_service import get_now_tashkent
    from datetime import timedelta
    tomorrow = (get_now_tashkent().date() + timedelta(days=1)).isoformat()

    booking_id = await save_booking(
        user_id=123,
        username="@bek",
        full_name="Бекзод",
        details="Комплекс",
        service="Стрижка волос",
        booking_time=f"{tomorrow} 15:00",
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
        assert "Brohouse" in text
        assert "Онлайн-запись" in text
        assert "Powered by" in text
        assert "Khan" in text

        resp_book = await client.get("/book")
        assert resp_book.status == 200

        # 2. Test Admin HTML (/admin and /webapp)
        resp_admin = await client.get("/admin")
        assert resp_admin.status == 200
        admin_text = await resp_admin.text()
        assert "Панель" in admin_text

        # 2.1 Test Barber HTML (/barber)
        resp_barber = await client.get("/barber")
        assert resp_barber.status == 200
        barber_text = await resp_barber.text()
        assert "Рабочее место мастера" in barber_text

        # 3. Test API config
        resp_cfg = await client.get("/api/config")
        assert resp_cfg.status == 200
        cfg_data = await resp_cfg.json()
        assert cfg_data["admin_chat_id"] == 999888

        # 4. Test API slots
        resp_slots = await client.get(f"/api/slots?date={tomorrow}")
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
            "service": "Стрижка волос",
            "booking_time": f"{tomorrow} 17:00",
        })
        assert resp_create.status == 200
        res_create = await resp_create.json()
        assert res_create["ok"] is True
        assert res_create["id"] > 0

        # 6. Test API create booking (Conflict - trying 15:30 when 15:00 is taken)
        resp_conflict = await client.post("/api/bookings", json={
            "full_name": "Камиль",
            "service": "Стрижка волос",
            "booking_time": f"{tomorrow} 15:30",
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

        # 11. Test Services API (/api/services)
        resp_srv = await client.get("/api/services")
        assert resp_srv.status == 200
        srv_list = await resp_srv.json()
        assert len(srv_list) >= 4

        # Create new service
        resp_create_srv = await client.post("/api/services", json={
            "name": "Мытье и премиум-массаж",
            "price": 40000,
            "duration_min": 20,
            "description": "СПА процедура",
            "badge": "СПА"
        })
        assert resp_create_srv.status == 200
        new_srv_data = await resp_create_srv.json()
        assert new_srv_data["ok"] is True
        new_srv_id = new_srv_data["id"]

        # Update service
        resp_upd_srv = await client.post(f"/api/services/{new_srv_id}/update", json={
            "name": "Мытье и премиум-массаж LUX",
            "price": 45000,
            "duration_min": 25,
            "description": "СПА процедура LUX",
            "badge": "СПА+",
            "is_active": 1
        })
        assert resp_upd_srv.status == 200

        # Delete service
        resp_del_srv = await client.post(f"/api/services/{new_srv_id}/delete")
        assert resp_del_srv.status == 200

        # 12. Test Barbers API (/api/barbers)
        resp_barbers = await client.get("/api/barbers")
        assert resp_barbers.status == 200
        barbers_data = await resp_barbers.json()
        assert len(barbers_data) >= 3

        # Toggle barber shift
        first_barber = barbers_data[0]
        resp_toggle = await client.post(f"/api/barbers/{first_barber['id']}/toggle-shift", json={"is_working_today": 0})
        assert resp_toggle.status == 200
        toggle_data = await resp_toggle.json()
        assert toggle_data["barber"]["is_working_today"] == 0

        # Toggle back
        await client.post(f"/api/barbers/{first_barber['id']}/toggle-shift", json={"is_working_today": 1})

        # 13. Test Barber Workspace schedule (/api/barber/my-schedule)
        resp_barber_sched = await client.get(f"/api/barber/my-schedule?barber_id={first_barber['id']}")
        assert resp_barber_sched.status == 200
        sched_data = await resp_barber_sched.json()
        assert sched_data["ok"] is True
        assert sched_data["barber"]["id"] == first_barber["id"]
        assert "is_on_shift" in sched_data["barber"]
        assert sched_data["barber"]["is_on_shift"] is True

        # 14. Test User Role endpoint (/api/user-role)
        # 14.1 Admin role
        resp_admin_role = await client.get("/api/user-role?user_id=999888")
        assert resp_admin_role.status == 200
        role_data = await resp_admin_role.json()
        assert role_data["role"] == "admin"

        # 14.2 Barber role
        barber_with_tg = next((b for b in barbers_data if b.get("telegram_id")), None)
        if barber_with_tg:
            resp_b_role = await client.get(f"/api/user-role?user_id={barber_with_tg['telegram_id']}")
            assert resp_b_role.status == 200
            b_role_data = await resp_b_role.json()
            assert b_role_data["role"] == "barber"
            assert b_role_data["barber"]["name"] == barber_with_tg["name"]

        # 14.3 Client role
        resp_client_role = await client.get("/api/user-role?user_id=555444333")
        assert resp_client_role.status == 200
        c_role_data = await resp_client_role.json()
        assert c_role_data["role"] == "client"

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
