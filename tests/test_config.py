import json
import pytest
from config import Settings

def test_default_settings_brohouse():
    s = Settings()
    assert s.TENANT_SLUG == "brohouse"
    assert s.TENANT_NAME == "Brohouse Barbershop"
    assert s.SALON_SHORT_NAME == "Brohouse"
    assert s.SALON_PHONE == "+998 97 012-55-55"
    assert s.WORK_START_HOUR == 8
    assert s.WORK_END_HOUR == 23
    assert s.MAX_BOOKING_DAYS_AHEAD == 7
    assert s.TIMEZONE == "Asia/Tashkent"
    assert s.OWNER_NAME == "Mr. Akmal"
    assert s.get_admin_cookie_name() == "brohouse_admin_session"

    branches = s.get_initial_branches()
    assert len(branches) >= 2
    assert branches[0]["name"] == "Миллий бог"

    barbers = s.get_initial_barbers()
    assert len(barbers) >= 4
    assert any(b["name"] == "Mr. Akmal" for b in barbers)

    services = s.get_initial_services()
    assert len(services) >= 10
    assert any(srv["name"] == "Стрижка волос" for srv in services)


def test_custom_tenant_override():
    custom_branches = json.dumps([
        {"id": 1, "name": "Центральный филиал", "address": "ул. Навои, 1", "landmark": "ЦУМ", "phone": "+998 90 111-22-33"}
    ])
    s = Settings(
        TENANT_SLUG="topgun",
        TENANT_NAME="TopGun Barbershop",
        SALON_SHORT_NAME="TopGun",
        SALON_PHONE="+998 90 111-22-33",
        ADMIN_PASSWORD="topgun_password",
        INITIAL_BRANCHES_JSON=custom_branches
    )
    assert s.TENANT_SLUG == "topgun"
    assert s.TENANT_NAME == "TopGun Barbershop"
    assert s.get_admin_cookie_name() == "topgun_admin_session"
    branches = s.get_initial_branches()
    assert len(branches) == 1
    assert branches[0]["name"] == "Центральный филиал"
