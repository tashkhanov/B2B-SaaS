import os
import pytest
from aiohttp.test_utils import TestClient, TestServer
from config import settings
from database import init_db
from web_app import create_web_app

TEST_DB = "test_auth.db"


@pytest.mark.asyncio
async def test_desktop_login_flow(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "super_secret_123")
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Попытка без авторизации на /api/bookings -> 401
        resp_unauth = await client.get("/api/bookings")
        assert resp_unauth.status == 401

        # 2. Неверный пароль -> 401
        resp_wrong = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert resp_wrong.status == 401
        wrong_data = await resp_wrong.json()
        assert "Неверный пароль" in wrong_data["error"]

        # 3. Пустой пароль -> 400
        resp_empty = await client.post("/api/auth/login", json={"password": ""})
        assert resp_empty.status == 400

        # 4. Успешный вход суперадмина
        resp_login = await client.post("/api/auth/login", json={"password": "super_secret_123"})
        assert resp_login.status == 200
        login_data = await resp_login.json()
        assert login_data["ok"] is True
        assert login_data["role"] == "super_admin"

        # 5. Проверка доступа к защищенному эндпоинту с полученными cookie -> 200 OK
        resp_auth = await client.get("/api/bookings")
        assert resp_auth.status == 200
        bookings_data = await resp_auth.json()
        assert isinstance(bookings_data, list)

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
