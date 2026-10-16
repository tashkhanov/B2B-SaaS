import pytest
from datetime import datetime, timedelta
from services.schedule_service import (
    TASHKENT_TZ,
    get_now_tashkent,
    parse_booking_datetime,
    format_booking_datetime,
    is_slot_overlapping,
    check_slot_availability,
    build_schedule_context_for_prompt,
)

def test_parse_booking_datetime():
    # ISO string
    dt = parse_booking_datetime("2026-10-16 15:00 (Пятница)")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 10
    assert dt.day == 16
    assert dt.hour == 15
    assert dt.minute == 0

    # DD.MM.YYYY string
    dt2 = parse_booking_datetime("16.10.2026 15:00")
    assert dt2 is not None
    assert dt2.day == 16
    assert dt2.month == 10
    assert dt2.hour == 15

    # Relative string with base_dt
    base = datetime(2026, 10, 15, 12, 0, tzinfo=TASHKENT_TZ)
    dt3 = parse_booking_datetime("завтра в 17:30", base_dt=base)
    assert dt3 is not None
    assert dt3.day == 16
    assert dt3.hour == 17
    assert dt3.minute == 30

def test_is_slot_overlapping():
    dt1 = datetime(2026, 10, 16, 15, 0, tzinfo=TASHKENT_TZ)

    # 15:30 overlaps with 15:00 (diff 30 min < 60 min)
    dt_overlap_1 = datetime(2026, 10, 16, 15, 30, tzinfo=TASHKENT_TZ)
    assert is_slot_overlapping(dt1, dt_overlap_1, duration_minutes=60) is True

    # 14:30 overlaps with 15:00 (diff 30 min < 60 min)
    dt_overlap_2 = datetime(2026, 10, 16, 14, 30, tzinfo=TASHKENT_TZ)
    assert is_slot_overlapping(dt1, dt_overlap_2, duration_minutes=60) is True

    # 16:00 does NOT overlap with 15:00 (diff 60 min == 60 min)
    dt_free_1 = datetime(2026, 10, 16, 16, 0, tzinfo=TASHKENT_TZ)
    assert is_slot_overlapping(dt1, dt_free_1, duration_minutes=60) is False

    # 14:00 does NOT overlap with 15:00
    dt_free_2 = datetime(2026, 10, 16, 14, 0, tzinfo=TASHKENT_TZ)
    assert is_slot_overlapping(dt1, dt_free_2, duration_minutes=60) is False

def test_check_slot_availability():
    now = get_now_tashkent()
    tomorrow = now.date() + timedelta(days=1)
    b_time_str = f"{tomorrow.isoformat()} 15:00"

    existing = [
        {
            "id": 1,
            "full_name": "Абдулазиз",
            "booking_time": b_time_str,
            "status": "new",
        }
    ]

    # Target 15:30 -> should CONFLICT
    t_1530 = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 15, 30, tzinfo=TASHKENT_TZ)
    is_avail, err, conflict = check_slot_availability(t_1530, existing)
    assert is_avail is False
    assert "занят" in err
    assert "15:00" in err
    assert "16:00" in err
    assert conflict["id"] == 1

    # Target 16:00 -> should be FREE
    t_1600 = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 16, 0, tzinfo=TASHKENT_TZ)
    is_avail2, err2, conflict2 = check_slot_availability(t_1600, existing)
    assert is_avail2 is True
    assert err2 is None

    # Off-hours: 07:00 (до открытия в 08:00) -> should be REJECTED
    t_0700 = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 7, 0, tzinfo=TASHKENT_TZ)
    is_avail3, err3, _ = check_slot_availability(t_0700, existing)
    assert is_avail3 is False
    assert "08:00" in err3

def test_build_schedule_context_for_prompt():
    now = get_now_tashkent()
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    existing = [
        {
            "id": 1,
            "full_name": "Абдулазиз",
            "service": "Комплекс",
            "booking_time": f"{tomorrow} 15:00 (Пятница)",
            "status": "new",
        }
    ]
    ctx = build_schedule_context_for_prompt(existing)
    assert "ТАШКЕНТ" in ctx
    assert "15:00 — 16:00" in ctx
    assert "Абдулазиз" in ctx


def test_7_days_horizon_limit():
    now = get_now_tashkent()
    existing = []

    # 3 days ahead -> should be allowed
    t_3days = (now + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    is_avail, err, _ = check_slot_availability(t_3days, existing)
    assert is_avail is True

    # 8 days ahead -> should be rejected
    t_8days = (now + timedelta(days=8)).replace(hour=14, minute=0, second=0, microsecond=0)
    is_avail2, err2, _ = check_slot_availability(t_8days, existing)
    assert is_avail2 is False
    assert "максимум на 7 дней" in err2


def test_is_booking_past():
    from services.schedule_service import is_booking_past

    assert is_booking_past("2020-01-01 12:00") is True
    # Future year 2099
    assert is_booking_past("2099-01-01 12:00") is False


def test_multi_master_slot_checking():
    barbers = [
        {"id": 1, "name": "Иброхим", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 2, "name": "Сардор", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
    ]
    # Запись только к мастеру Иброхим (ID=1) на 14:00
    now = get_now_tashkent()
    target_dt = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    time_str = target_dt.strftime("%Y-%m-%d 14:00")

    existing = [
        {
            "id": 10,
            "full_name": "Клиент Иброхима",
            "booking_time": time_str,
            "barber_id": 1,
            "barber_name": "Иброхим",
            "status": "confirmed"
        }
    ]

    # 1. Запрос к Иброхиму на 14:00 -> Должен быть ЗАНЯТ
    res_b1 = check_slot_availability(target_dt, existing, barber_id=1, available_barbers=barbers)
    assert res_b1.is_available is False
    assert "Иброхим" in res_b1.error_message

    # 2. Запрос к Сардору на 14:00 -> Должен быть СВОБОДЕН!
    res_b2 = check_slot_availability(target_dt, existing, barber_id=2, available_barbers=barbers)
    assert res_b2.is_available is True
    assert res_b2.assigned_barber_id == 2
    assert res_b2.assigned_barber_name == "Сардор"

    # 3. Запрос «Любой свободный мастер» на 14:00 -> Должен автоматически назначить Сардора!
    res_any = check_slot_availability(target_dt, existing, barber_id=None, available_barbers=barbers)
    assert res_any.is_available is True
    assert res_any.assigned_barber_id == 2
    assert res_any.assigned_barber_name == "Сардор"


def test_barber_off_day():
    # Мастер работает только по будням (Пн-Пт: 0,1,2,3,4)
    barber = {"id": 3, "name": "Жасур", "work_days": "0,1,2,3,4", "is_working_today": 1, "is_active": 1}
    now = get_now_tashkent()

    # Найдем ближайшее воскресенье (weekday = 6)
    days_to_sunday = (6 - now.weekday()) % 7
    if days_to_sunday == 0:
        days_to_sunday = 7
    sunday_dt = (now + timedelta(days=days_to_sunday)).replace(hour=12, minute=0, second=0, microsecond=0)

    # Жасур в воскресенье отдыхает
    res = check_slot_availability(sunday_dt, [], barber_id=3, available_barbers=[barber])
    assert res.is_available is False
    assert "отдыхает" in res.error_message or "выходной" in res.error_message


def test_dynamic_duration():
    now = get_now_tashkent()
    # 22:30 при закрытии в 23:00
    t_2230 = (now + timedelta(days=1)).replace(hour=22, minute=30, second=0, microsecond=0)

    # 30-минутная услуга помещается (22:30 -> 23:00)
    res_30 = check_slot_availability(t_2230, [], duration_minutes=30)
    assert res_30.is_available is True

    # 60-минутная услуга НЕ помещается (22:30 -> 23:30 > 23:00)
    res_60 = check_slot_availability(t_2230, [], duration_minutes=60)
    assert res_60.is_available is False
    assert "закрывается в 23:00" in res_60.error_message


def test_auto_assign_excludes_chef_master():
    """Проверка: Шеф-мастер/Директор (Mr. Akmal) исключается из общего автоподбора, если есть доступные стилисты."""
    now = get_now_tashkent()
    target_dt = (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

    barbers = [
        {"id": 1, "name": "Mr. Akmal", "role_title": "Директор / Шеф-мастер", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 2, "name": "Mr. Dilshod", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 3, "name": "Ismailov Abbos", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
    ]

    # Ни у кого нет записей
    res = check_slot_availability(target_dt, [], barber_id=None, available_barbers=barbers)
    assert res.is_available is True
    # Назначен стилист (не Mr. Akmal)
    assert res.assigned_barber_id in (2, 3)
    assert res.assigned_barber_name in ("Mr. Dilshod", "Ismailov Abbos")


def test_auto_assign_least_busy_balancing():
    """Проверка: Автоподбор выбирает наименее загруженного мастера на целевой день (Least Busy)."""
    now = get_now_tashkent()
    target_day = (now + timedelta(days=1))
    target_dt = target_day.replace(hour=16, minute=0, second=0, microsecond=0)

    barbers = [
        {"id": 2, "name": "Mr. Dilshod", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 3, "name": "Ismailov Abbos", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
    ]

    # У Mr. Dilshod уже есть 2 записи в этот день (в другое время: 12:00 и 14:00)
    existing_bookings = [
        {
            "id": 101,
            "barber_id": 2,
            "booking_time": target_day.replace(hour=12, minute=0).strftime("%Y-%m-%d %H:%M"),
            "status": "confirmed"
        },
        {
            "id": 102,
            "barber_id": 2,
            "booking_time": target_day.replace(hour=14, minute=0).strftime("%Y-%m-%d %H:%M"),
            "status": "confirmed"
        }
    ]

    # На 16:00 оба свободны, но у Abbos 0 записей, а у Dilshod 2 -> Должен быть назначен Abbos!
    res = check_slot_availability(target_dt, existing_bookings, barber_id=None, available_barbers=barbers)
    assert res.is_available is True
    assert res.assigned_barber_id == 3
    assert res.assigned_barber_name == "Ismailov Abbos"


def test_auto_assign_random_distribution():
    """Проверка: При равной загрузке автоподбор не привязан к первому мастеру намертво."""
    now = get_now_tashkent()
    target_dt = (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)

    barbers = [
        {"id": 2, "name": "Mr. Dilshod", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 3, "name": "Ismailov Abbos", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 4, "name": "Tursunov Nodir", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
    ]

    chosen_ids = set()
    for _ in range(30):
        res = check_slot_availability(target_dt, [], barber_id=None, available_barbers=barbers)
        assert res.is_available is True
        chosen_ids.add(res.assigned_barber_id)

    # За 30 итераций должны быть выбраны как минимум 2 разных мастера (вероятность выбора только 1 равна (1/3)^29 ~= 0)
    assert len(chosen_ids) >= 2


def test_direct_chef_master_booking_allowed():
    """Проверка: Прямая запись к Mr. Akmal (Шеф-мастер) при явном выборе разрешена и работает штатно."""
    now = get_now_tashkent()
    target_dt = (now + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)

    barbers = [
        {"id": 1, "name": "Mr. Akmal", "role_title": "Директор / Шеф-мастер", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
        {"id": 2, "name": "Mr. Dilshod", "role_title": "Стилист", "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1},
    ]

    res = check_slot_availability(target_dt, [], barber_id=1, available_barbers=barbers)
    assert res.is_available is True
    assert res.assigned_barber_id == 1
    assert res.assigned_barber_name == "Mr. Akmal"



