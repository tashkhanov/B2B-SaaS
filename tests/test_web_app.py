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

        # 2.2 Test Static Files (.css & .js for client, admin, barber)
        for asset in [
            "/static/css/client.css", "/static/js/client.js",
            "/static/css/admin.css", "/static/js/admin.js",
            "/static/css/barber.css", "/static/js/barber.js"
        ]:
            resp_asset = await client.get(asset)
            assert resp_asset.status == 200, f"Static asset {asset} failed to load: {resp_asset.status}"
            content = await resp_asset.text()
            assert len(content) > 50, f"Static asset {asset} content too short"

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


@pytest.mark.asyncio
async def test_web_app_subscription_lockout(monkeypatch):
    from database import toggle_tenant_active, get_tenant_subscription
    lock_db = "test_lock.db"
    monkeypatch.setattr(settings, "DB_NAME", lock_db)
    monkeypatch.setattr(settings, "TENANT_SLUG", "brohouse")
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "secret123")

    if os.path.exists(lock_db):
        os.remove(lock_db)
    await init_db(lock_db)

    # Lock the tenant
    tenant = await get_tenant_subscription("brohouse", lock_db)
    await toggle_tenant_active(tenant["id"], is_active=0, db_path=lock_db)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. API endpoint should return 403 Forbidden
        resp_api = await client.get("/api/services")
        assert resp_api.status == 403
        api_data = await resp_api.json()
        assert api_data["error"] == "subscription_expired"
        assert "Доступ приостановлен" in api_data["message"]

        # 2. HTML page (/book) should return 403 with lockout page
        resp_page = await client.get("/book")
        assert resp_page.status == 403
        page_html = await resp_page.text()
        assert "Доступ приостановлен" in page_html
        assert "Оплатите подписку" in page_html
        assert "Plus Jakarta Sans" in page_html
        assert "Позвонить в салон" not in page_html
        assert "tel:" not in page_html
        assert "https://t.me/asatkhanov" in page_html

        # 3. Super-Admin bypass with header should return 200
        resp_bypass = await client.get("/api/services", headers={"X-Super-Admin-Token": "secret123"})
        assert resp_bypass.status == 200

        # 4. Super-Admin bypass with query param
        resp_bypass_q = await client.get("/book?token=secret123")
        assert resp_bypass_q.status == 200

    finally:
        await client.close()
        if os.path.exists(lock_db):
            os.remove(lock_db)


@pytest.mark.asyncio
async def test_super_admin_api_endpoints(monkeypatch):
    sa_db = "test_sa.db"
    monkeypatch.setattr(settings, "DB_NAME", sa_db)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "superpass")

    if os.path.exists(sa_db):
        os.remove(sa_db)
    await init_db(sa_db)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Auth failed with wrong password
        resp_bad_auth = await client.post("/api/super/auth", json={"password": "wrong"})
        assert resp_bad_auth.status == 401

        # 2. Auth success
        resp_auth = await client.post("/api/super/auth", json={"password": "superpass"})
        assert resp_auth.status == 200
        auth_data = await resp_auth.json()
        assert auth_data["ok"] is True
        token = auth_data["token"]
        assert token == "superpass"

        headers = {"X-Super-Admin-Token": token}

        # 3. Get tenants list
        resp_list = await client.get("/api/super/tenants", headers=headers)
        assert resp_list.status == 200
        tenants = await resp_list.json()
        assert len(tenants) >= 1
        brohouse = tenants[0]
        assert brohouse["tenant_slug"] == "brohouse"
        assert "days_left" in brohouse
        assert "is_locked" in brohouse

        # 4. Create new tenant via API
        resp_create = await client.post("/api/super/tenants", headers=headers, json={
            "tenant_slug": "chicago",
            "name": "Chicago Barbershop",
            "admin_chat_id": 123456,
            "contact_phone": "+998909998877",
            "days": 15,
        })
        assert resp_create.status == 200
        new_t_data = await resp_create.json()
        assert new_t_data["ok"] is True
        new_id = new_t_data["id"]

        # 5. Extend 30 days
        resp_ext = await client.post(f"/api/super/tenants/{new_id}/extend-30", headers=headers)
        assert resp_ext.status == 200
        ext_data = await resp_ext.json()
        assert ext_data["ok"] is True
        assert ext_data["tenant"]["days_left"] >= 40

        # 6. Toggle active (Kill Switch)
        resp_toggle = await client.post(f"/api/super/tenants/{new_id}/toggle-active", headers=headers)
        assert resp_toggle.status == 200
        toggle_data = await resp_toggle.json()
        assert toggle_data["ok"] is True
        assert toggle_data["tenant"]["is_active"] == 0
        assert toggle_data["tenant"]["is_locked"] is True

        # 7. Check /super-admin redirect or serve
        resp_page = await client.get("/super-admin?token=superpass", allow_redirects=False)
        assert resp_page.status in (200, 302)

    finally:
        await client.close()
        if os.path.exists(sa_db):
            os.remove(sa_db)


@pytest.mark.asyncio
async def test_web_app_branches(monkeypatch):
    b_db = "test_branches_web.db"
    monkeypatch.setattr(settings, "DB_NAME", b_db)
    if os.path.exists(b_db):
        os.remove(b_db)
    await init_db(b_db)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. GET /api/branches
        resp = await client.get("/api/branches")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Миллий бог"
        assert data[1]["name"] == "Чиланзар (2-й кв.)"

        # 2. POST /api/bookings with branch_id=2
        from services.schedule_service import get_now_tashkent
        from datetime import timedelta
        day_str = (get_now_tashkent().date() + timedelta(days=2)).isoformat()
        resp_post = await client.post("/api/bookings", json={
            "full_name": "Фаррух",
            "phone": "+998901234567",
            "service": "Стрижка волос",
            "booking_time": f"{day_str} 14:00",
            "branch_id": 2,
            "branch_name": "Чиланзар (2-й кв.)",
        })
        assert resp_post.status == 200
        post_data = await resp_post.json()
        assert post_data["ok"] is True
        booking_id = post_data["id"]

        from database import get_booking_by_id
        saved = await get_booking_by_id(booking_id, db_path=b_db)
        assert saved["branch_id"] == 2
        assert saved["branch_name"] == "Чиланзар (2-й кв.)"
    finally:
        await client.close()
        if os.path.exists(b_db):
            os.remove(b_db)



