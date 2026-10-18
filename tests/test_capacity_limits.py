import os
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from database import init_db, set_tenant_tier, create_barber, create_branch
from web_app import create_web_app
from config import settings
from services.tier_service import SubscriptionTier, check_can_add_master, check_can_add_branch

TEST_DB = "test_capacity.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "supersecret123")
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_start_tier_master_capacity_limit():
    # 1. Set tier to START
    await set_tenant_tier("brohouse", "START", db_path=TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    # Pass admin authorization via header
    headers = {"X-Admin-Password": "999888"}

    try:
        # Initial seed has 4 barbers. Adding 5th barber is allowed on START.
        resp_5 = await client.post("/api/barbers", headers=headers, json={
            "name": "Барбер 5",
            "role": "Барбер",
            "phone": "+998900000005"
        })
        assert resp_5.status == 200, "5th barber should be allowed on START (limit is 5)"

        # 6th barber must be blocked with HTTP 403 tier_limit_reached
        resp_6 = await client.post("/api/barbers", headers=headers, json={
            "name": "Барбер 6",
            "role": "Барбер",
            "phone": "+998900000006"
        })
        assert resp_6.status == 403
        data_6 = await resp_6.json()
        assert data_6.get("code") == "tier_limit_reached"
        assert "PRO" in data_6.get("error", "")

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_start_tier_branch_capacity_limit():
    # Set tier to START
    await set_tenant_tier("brohouse", "START", db_path=TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    headers = {"X-Admin-Password": "999888"}

    try:
        # Initial seed already has 2 branches (or at least 1).
        # Adding a new branch must be blocked on START with 403
        resp_new = await client.post("/api/branches", headers=headers, json={
            "name": "Новый филиал",
            "address": "ул. Навои, 10",
            "landmark": "ЦУМ",
            "phone": "+998 97 000-00-00"
        })
        assert resp_new.status == 403
        data_new = await resp_new.json()
        assert data_new.get("code") == "tier_limit_reached"
        assert "PRO" in data_new.get("error", "")

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pro_tier_extended_capacity():
    # Set tier to PRO
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    headers = {"X-Admin-Password": "999888"}

    try:
        # Initial seed has 4 barbers. Adding 6 barbers on PRO (total 10 <= 15) -> should succeed!
        for i in range(5, 11):
            resp = await client.post("/api/barbers", headers=headers, json={
                "name": f"Барбер {i}",
                "role": "Барбер"
            })
            assert resp.status == 200

        # Initial seed has 2 branches. Adding 3rd branch on PRO (total 3 <= 3) -> should succeed!
        resp_b3 = await client.post("/api/branches", headers=headers, json={
            "name": "Филиал 3",
            "address": "Юнусабад"
        })
        assert resp_b3.status == 200

        # 4th branch on PRO should be blocked (limit is 3)
        resp_b4 = await client.post("/api/branches", headers=headers, json={
            "name": "Филиал 4",
            "address": "Сергели"
        })
        assert resp_b4.status == 403
        data_b4 = await resp_b4.json()
        assert data_b4.get("code") == "tier_limit_reached"
        assert "ULTRA" in data_b4.get("error", "")

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_config_endpoint_reports_capacity():
    await set_tenant_tier("brohouse", "PRO", db_path=TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        resp = await client.get("/api/config")
        assert resp.status == 200
        cfg = await resp.json()

        assert cfg["tier"] == "PRO"
        assert cfg["max_masters"] == 15
        assert cfg["max_branches"] == 3
        assert cfg["has_smart_reminders"] is True
        assert cfg["has_winback_pushes"] is True
        assert cfg["has_vip_protection"] is True
        assert cfg["has_promo_discount"] is True

    finally:
        await client.close()
