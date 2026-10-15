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
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    booking_id = await save_booking(
        user_id=123,
        username="@bek",
        full_name="Бекзод",
        details="Комплекс",
        service="Комплекс",
        booking_time="Пятница 16:00",
        db_path=TEST_DB
    )

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Test index HTML
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "Black Beard" in text
        assert "Powered by" in text
        assert "Khan AI Systems" in text
        assert "https://asatkhanov.uz" in text

        # 2. Test API bookings
        resp = await client.get("/api/bookings")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)

        # 3. Test status update
        resp = await client.post(f"/api/bookings/{booking_id}/status", json={"status": "confirmed"})
        assert resp.status == 200
        res_data = await resp.json()
        assert res_data.get("ok") is True

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
