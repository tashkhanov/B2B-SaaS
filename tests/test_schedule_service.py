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
    existing = [
        {
            "id": 1,
            "full_name": "Абдулазиз",
            "booking_time": "2026-10-16 15:00 (Пятница)",
            "status": "new",
        }
    ]

    # Target 15:30 -> should CONFLICT
    t_1530 = datetime(2026, 10, 16, 15, 30, tzinfo=TASHKENT_TZ)
    is_avail, err, conflict = check_slot_availability(t_1530, existing)
    assert is_avail is False
    assert "занят" in err
    assert "15:00" in err
    assert "16:00" in err
    assert conflict["id"] == 1

    # Target 16:00 -> should be FREE
    t_1600 = datetime(2026, 10, 16, 16, 0, tzinfo=TASHKENT_TZ)
    is_avail2, err2, conflict2 = check_slot_availability(t_1600, existing)
    assert is_avail2 is True
    assert err2 is None

    # Off-hours: 23:00 -> should be REJECTED
    t_2300 = datetime(2026, 10, 16, 23, 0, tzinfo=TASHKENT_TZ)
    is_avail3, err3, _ = check_slot_availability(t_2300, existing)
    assert is_avail3 is False
    assert "22:00" in err3

def test_build_schedule_context_for_prompt():
    existing = [
        {
            "id": 1,
            "full_name": "Абдулазиз",
            "service": "Комплекс",
            "booking_time": "2026-10-16 15:00 (Пятница)",
            "status": "new",
        }
    ]
    ctx = build_schedule_context_for_prompt(existing)
    assert "ТАШКЕНТ" in ctx
    assert "15:00 — 16:00" in ctx
    assert "Абдулазиз" in ctx
    assert "15:30" in ctx


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

