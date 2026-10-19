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

    # Проверка начального seed (12 дефолтных услуг Brohouse и их распределения по категориям)
    services = await get_services(db_path=TEST_DB)
    assert len(services) == 12
    assert any(s["name"] == "Стрижка волос" for s in services)
    hair_services = [s for s in services if s.get("category") == "hair"]
    beard_services = [s for s in services if s.get("category") == "beard"]
    spa_services = [s for s in services if s.get("category") == "spa"]
    assert len(hair_services) == 5
    assert len(beard_services) == 3
    assert len(spa_services) == 4

    # Создание новой услуги с категорией
    new_id = await create_service(
        name="Камуфляж седины",
        price=70000,
        duration_min=30,
        description="Тонирование бороды и волос",
        badge="NEW",
        category="beard",
        db_path=TEST_DB
    )
    assert new_id > 0

    s = await get_service_by_id(new_id, db_path=TEST_DB)
    assert s["name"] == "Камуфляж седины"
    assert s["price"] == 70000
    assert s["duration_min"] == 30
    assert s["badge"] == "NEW"
    assert s["category"] == "beard"

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

    # Тестирование привязки к филиалу (branch_id)
    # 1. Мастер с конкретным филиалом (Филиал #1: Миллий бог)
    b_branch1_id = await create_barber(
        name="Шерзод",
        role_title="Барбер",
        branch_id=1,
        db_path=TEST_DB
    )
    b_b1 = await get_barber_by_id(b_branch1_id, db_path=TEST_DB)
    assert b_b1["branch_id"] == 1
    assert b_b1["branch_name"] == "Миллий бог"

    # 2. Мастер с конкретным филиалом #2 (Чиланзар)
    b_branch2_id = await create_barber(
        name="Сарвар",
        role_title="Барбер",
        branch_id=2,
        db_path=TEST_DB
    )
    b_b2 = await get_barber_by_id(b_branch2_id, db_path=TEST_DB)
    assert b_b2["branch_id"] == 2
    assert b_b2["branch_name"] == "Чиланзар (2-й кв.)"

    # 3. Фильтрация по филиалу 1: включает Шерзода (branch_id=1) и мастеров с Все филиалы (branch_id=None), но исключает Сарвара (branch_id=2)
    branch1_barbers = await get_barbers(branch_id=1, db_path=TEST_DB)
    assert any(b["id"] == b_branch1_id for b in branch1_barbers)
    assert not any(b["id"] == b_branch2_id for b in branch1_barbers)

    # 4. Обновление филиала на "Все филиалы" (branch_id=None)
    await update_barber(
        barber_id=b_branch1_id,
        name="Шерзод Все Филиалы",
        branch_id=None,
        db_path=TEST_DB
    )
    b_all = await get_barber_by_id(b_branch1_id, db_path=TEST_DB)
    assert b_all["branch_id"] is None
    assert b_all["branch_name"] is None
    # Теперь он виден и в филиале 1, и в филиале 2
    b1_list = await get_barbers(branch_id=1, db_path=TEST_DB)
    b2_list = await get_barbers(branch_id=2, db_path=TEST_DB)
    assert any(b["id"] == b_branch1_id for b in b1_list)
    assert any(b["id"] == b_branch1_id for b in b2_list)


@pytest.mark.asyncio
async def test_tenant_subscription_lifecycle():
    from database import (
        get_tenant_subscription,
        get_all_tenants,
        create_tenant,
        extend_tenant_subscription_30_days,
        toggle_tenant_active,
    )
    # 1. Проверка сида по умолчанию (brohouse)
    tenant = await get_tenant_subscription("brohouse", TEST_DB)
    assert tenant is not None
    assert tenant["tenant_slug"] == "brohouse"
    assert tenant["is_active"] == 1
    assert tenant["grace_period_days"] == 2
    assert "expires_at" in tenant

    # 2. Продление на 30 дней (+30 days)
    old_exp = tenant["expires_at"]
    updated = await extend_tenant_subscription_30_days(tenant["id"], TEST_DB)
    assert updated is not None
    assert updated["expires_at"] > old_exp

    # 3. Переключение Kill Switch (is_active)
    ok = await toggle_tenant_active(tenant["id"], is_active=0, db_path=TEST_DB)
    assert ok is True
    t2 = await get_tenant_subscription("brohouse", TEST_DB)
    assert t2["is_active"] == 0

    # 4. Создание нового арендатора
    new_id = await create_tenant({
        "tenant_slug": "topgun",
        "name": "TopGun Barbershop",
        "admin_chat_id": 999999,
        "contact_phone": "+998 90 123-45-67",
        "days": 14,
    }, TEST_DB)
    assert new_id > 0
    all_tenants = await get_all_tenants(TEST_DB)
    assert len(all_tenants) >= 2
    topgun = await get_tenant_subscription("topgun", TEST_DB)
    assert topgun is not None
    assert topgun["name"] == "TopGun Barbershop"


@pytest.mark.asyncio
async def test_branches_database():
    from database import get_branches, get_branch_by_id, get_booking_by_id

    # 1. Проверяем получение списка филиалов (по умолчанию 2 филиала Brohouse)
    branches = await get_branches(active_only=True, db_path=TEST_DB)
    assert len(branches) == 2
    assert branches[0]["name"] == "Миллий бог"
    assert branches[1]["name"] == "Чиланзар (2-й кв.)"

    # 2. Получение по ID
    b1 = await get_branch_by_id(1, db_path=TEST_DB)
    assert b1 is not None
    assert "метро «Миллий бог»" in b1["address"]

    b2 = await get_branch_by_id(2, db_path=TEST_DB)
    assert b2 is not None
    assert "Чиланзар" in b2["address"]
    assert b2["phone"] == "+998 97 012-55-55"

    # 3. Сохранение записи с привязкой ко второму филиалу
    booking_id = await save_booking(
        user_id=123456,
        username="@chilonzor_client",
        full_name="Шерзод",
        details="Запись на Чиланзар",
        booking_time="2026-10-21 12:00",
        branch_id=2,
        branch_name="Чиланзар (2-й кв.)",
        db_path=TEST_DB
    )
    assert booking_id > 0

    # 4. Проверяем, что филиал корректно сохранен и извлечен
    saved = await get_booking_by_id(booking_id, db_path=TEST_DB)
    assert saved is not None
    assert saved["branch_id"] == 2
    assert saved["branch_name"] == "Чиланзар (2-й кв.)"


@pytest.mark.asyncio
async def test_white_label_seed_and_settings():
    from config import settings
    from database import (
        get_all_salon_settings,
        get_branches,
        get_barbers,
        get_services,
        get_tenant_subscription,
        reseed_tenant_data,
        reseed_brohouse_data
    )

    # 1. Проверяем настройки салона
    salon_settings = await get_all_salon_settings(db_path=TEST_DB)
    assert salon_settings["work_start_hour"] == str(settings.WORK_START_HOUR)
    assert salon_settings["work_end_hour"] == str(settings.WORK_END_HOUR)
    assert salon_settings["salon_phone"] == settings.SALON_PHONE

    # 2. Проверяем филиалы
    branches = await get_branches(active_only=False, db_path=TEST_DB)
    expected_branches = settings.get_initial_branches()
    assert len(branches) == len(expected_branches)
    assert branches[0]["name"] == expected_branches[0]["name"]

    # 3. Проверяем сервисы и барберов
    services = await get_services(active_only=False, db_path=TEST_DB)
    assert len(services) == len(settings.get_initial_services())

    barbers = await get_barbers(active_only=False, db_path=TEST_DB)
    assert len(barbers) == len(settings.get_initial_barbers())

    # 4. Проверяем tenant_subscription
    sub = await get_tenant_subscription(settings.TENANT_SLUG, db_path=TEST_DB)
    assert sub is not None
    assert sub["name"] == settings.TENANT_NAME
    assert sub["is_active"] == 1

    # 5. Проверяем alias reseed_tenant_data
    assert reseed_tenant_data is reseed_brohouse_data
    await reseed_tenant_data(db_path=TEST_DB)
    branches_after = await get_branches(active_only=False, db_path=TEST_DB)
    assert len(branches_after) == len(expected_branches)




