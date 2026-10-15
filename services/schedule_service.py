"""
Модуль управления расписанием, часовым поясом Ташкента (UTC+5)
и валидацией пересечения записей в барбершопе Black Beard.
"""

from datetime import datetime, date, time, timedelta, timezone
from typing import List, Dict, Optional, Tuple
import re
import logging

logger = logging.getLogger(__name__)

# Часовой пояс Ташкента (UTC+5)
try:
    from zoneinfo import ZoneInfo
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT_TZ = timezone(timedelta(hours=5))

# Длительность любой процедуры в барбершопе (1 час = 60 минут)
SERVICE_DURATION_MINUTES = 60

# Рабочие часы барбершопа Black Beard
WORK_START_HOUR = 10  # 10:00
WORK_END_HOUR = 22    # 22:00

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def get_now_tashkent() -> datetime:
    """Возвращает текущую дату и время в часовом поясе Ташкента (UTC+5)."""
    return datetime.now(TASHKENT_TZ)


def format_tashkent_now_for_prompt() -> str:
    """
    Форматирует текущую дату и время для передачи в системный промпт нейросети.
    Пример: '15 октября 2026 г., 22:20 (Четверг, Ташкент UTC+5)'
    """
    now = get_now_tashkent()
    day_name = DAYS_RU[now.weekday()]
    month_name = MONTHS_RU[now.month]
    return f"{now.day} {month_name} {now.year} г., {now.strftime('%H:%M')} ({day_name}, Ташкент UTC+5)"


def format_booking_datetime(dt: datetime) -> str:
    """
    Канонический формат хранения даты записи в базе данных:
    'YYYY-MM-DD HH:MM (ДеньНедели)', например: '2026-10-16 15:00 (Пятница)'
    """
    day_name = DAYS_RU[dt.weekday()]
    return f"{dt.strftime('%Y-%m-%d %H:%M')} ({day_name})"


def parse_booking_datetime(val: str, base_dt: Optional[datetime] = None) -> Optional[datetime]:
    """
    Универсальный парсинг даты и времени записи из различных строковых форматов:
    - '2026-10-16 15:00 (Пятница)'
    - '2026-10-16 15:00'
    - '2026-10-16T15:00:00'
    - '16.10.2026 15:00'
    """
    if not val:
        return None

    cleaned = val.strip()
    # 1. Поиск шаблона YYYY-MM-DD HH:MM
    match_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[T\s](\d{1,2}):(\d{2})", cleaned)
    if match_iso:
        y, m, d, h, mn = map(int, match_iso.groups())
        try:
            return datetime(y, m, d, h, mn, tzinfo=TASHKENT_TZ)
        except ValueError:
            pass

    # 2. Поиск шаблона DD.MM.YYYY HH:MM
    match_ru = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})[T\s](\d{1,2}):(\d{2})", cleaned)
    if match_ru:
        d, m, y, h, mn = map(int, match_ru.groups())
        try:
            return datetime(y, m, d, h, mn, tzinfo=TASHKENT_TZ)
        except ValueError:
            pass

    # 3. Fallback: относительные слова "сегодня" / "завтра" + HH:MM
    base = base_dt or get_now_tashkent()
    time_match = re.search(r"(\d{1,2}):(\d{2})", cleaned)
    if time_match:
        h, mn = map(int, time_match.groups())
        lower = cleaned.lower()
        target_date = base.date()
        if "завтра" in lower or "ertaga" in lower:
            target_date += timedelta(days=1)
        elif "послезавтра" in lower:
            target_date += timedelta(days=2)
        try:
            return datetime(target_date.year, target_date.month, target_date.day, h, mn, tzinfo=TASHKENT_TZ)
        except ValueError:
            pass

    return None


def is_slot_overlapping(
    dt1: datetime, dt2: datetime, duration_minutes: int = SERVICE_DURATION_MINUTES
) -> bool:
    """
    Проверяет, пересекаются ли два слота длительностью duration_minutes.
    Слоты пересекаются, если интервал между их началами строго меньше длительности процедуры.
    Например: 15:00 и 15:30 при длительности 60 мин -> разница 30 мин < 60 мин -> ПЕРЕСЕКАЮТСЯ!
    А 15:00 и 16:00 -> разница 60 мин -> НЕ ПЕРЕСЕКАЮТСЯ.
    """
    # Приводим к одному часовому поясу
    t1 = dt1.astimezone(TASHKENT_TZ) if dt1.tzinfo else dt1.replace(tzinfo=TASHKENT_TZ)
    t2 = dt2.astimezone(TASHKENT_TZ) if dt2.tzinfo else dt2.replace(tzinfo=TASHKENT_TZ)
    diff_seconds = abs((t1 - t2).total_seconds())
    return diff_seconds < (duration_minutes * 60)


# Максимальный горизонт бронирования вперед (7 дней)
MAX_BOOKING_DAYS_AHEAD = 7


def is_booking_past(raw_booking_time: str, grace_minutes: int = SERVICE_DURATION_MINUTES) -> bool:
    """
    Проверяет, завершилась ли процедура записи (время начала + длительность 60 мин < текущее время Ташкента).
    """
    dt = parse_booking_datetime(raw_booking_time)
    if not dt:
        return False
    now = get_now_tashkent()
    end_time = dt + timedelta(minutes=grace_minutes)
    return end_time < now


def check_slot_availability(
    target_dt: datetime,
    existing_bookings: List[Dict],
    ignore_booking_id: Optional[int] = None
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Проверяет, свободно ли желаемое время target_dt:
    1. Попадает ли в рабочие часы (10:00 - 22:00, последняя запись в 21:00).
    2. Не превышает ли горизонт планирования (максимум 7 дней вперед).
    3. Не находится ли в прошлом.
    4. Не пересекается ли с существующими активными бронями (длительность 1 час).
    
    Возвращает (is_available, error_message, conflicting_booking).
    """
    target = target_dt.astimezone(TASHKENT_TZ) if target_dt.tzinfo else target_dt.replace(tzinfo=TASHKENT_TZ)
    now = get_now_tashkent()

    # 1. Проверка на прошедшее время
    if target < now - timedelta(minutes=15):
        return False, "Выбранное время уже прошло.", None

    # 2. Проверка горизонта планирования (максимум 7 дней вперед)
    diff_days = (target.date() - now.date()).days
    if diff_days > MAX_BOOKING_DAYS_AHEAD:
        max_allowed_date = now.date() + timedelta(days=MAX_BOOKING_DAYS_AHEAD)
        max_month = MONTHS_RU[max_allowed_date.month] if max_allowed_date.month < len(MONTHS_RU) else ""
        max_str = f"{max_allowed_date.day} {max_month}"
        return (
            False,
            f"Запись открыта максимум на 7 дней вперед (до {max_str}). "
            f"Пожалуйста, выберите удобный день на ближайшей неделе, ака!",
            None,
        )

    # 3. Проверка рабочих часов (10:00 - 22:00)
    # Так как процедура длится 1 час, последняя возможная запись - в 21:00
    if target.hour < WORK_START_HOUR or (target.hour == WORK_END_HOUR and target.minute > 0) or target.hour > WORK_END_HOUR:
        return False, f"Барбершоп работает с {WORK_START_HOUR}:00 до {WORK_END_HOUR}:00. Выберите время в этом интервале.", None

    if target.hour == WORK_END_HOUR - 1 and target.minute > 0:
        return False, f"Барбершоп закрывается в {WORK_END_HOUR}:00, а стрижка длится ровно 1 час. Последняя запись возможна в 21:00.", None

    # Проверка конфликтов с существующими бронированиями
    for b in existing_bookings:
        if ignore_booking_id and b.get("id") == ignore_booking_id:
            continue
        if b.get("status") == "cancelled":
            continue

        raw_time = b.get("booking_time")
        b_dt = parse_booking_datetime(raw_time)
        if not b_dt:
            continue

        if is_slot_overlapping(target, b_dt):
            b_end = (b_dt + timedelta(minutes=SERVICE_DURATION_MINUTES)).strftime("%H:%M")
            b_start = b_dt.strftime("%H:%M")
            client_name = b.get("full_name") or "клиент"
            reason = (
                f"В это время мастер занят: с {b_start} до {b_end} оформлена запись "
                f"({client_name}). Процедура длится ровно 1 час."
            )
            return False, reason, b

    return True, None, None


def build_schedule_context_for_prompt(bookings: List[Dict]) -> str:
    """
    Генерирует актуальный текстовый блок с графиком и занятыми окнами
    для системного промпта нейросети на основе реальной базы SQLite.
    """
    now = get_now_tashkent()
    now_formatted = format_tashkent_now_for_prompt()

    lines = [
        f"ТЕКУЩЕЕ ВРЕМЯ И ДАТА В ТАШКЕНТЕ: {now_formatted}",
        f"РЕЖИМ РАБОТЫ БАРБЕРШОПА: ежедневно с {WORK_START_HOUR}:00 до {WORK_END_HOUR}:00 (последняя запись в 21:00).",
        f"ДЛИТЕЛЬНОСТЬ ЛЮБОЙ УСЛУГИ: РОВНО {SERVICE_DURATION_MINUTES} МИНУТ (1 ЧАС).",
        "",
        "АКТУАЛЬНЫЕ ЗАПИСИ ИЗ БАЗЫ ДАННЫХ (ЗАНЯТЫЕ ОКНА):"
    ]

    # Собираем записи на ближайшие 7 дней
    active_slots_by_date: Dict[date, List[Tuple[datetime, datetime, str]]] = {}

    for b in bookings:
        if b.get("status") == "cancelled":
            continue
        raw_time = b.get("booking_time")
        b_dt = parse_booking_datetime(raw_time)
        if not b_dt:
            continue
        # Фильтруем записи старше сегодняшнего дня
        if b_dt.date() < now.date():
            continue

        end_dt = b_dt + timedelta(minutes=SERVICE_DURATION_MINUTES)
        client = b.get("full_name") or "Клиент"
        service = b.get("service") or "Стрижка"
        desc = f"{client} ({service})"

        active_slots_by_date.setdefault(b_dt.date(), []).append((b_dt, end_dt, desc))

    if not active_slots_by_date:
        lines.append("- На ближайшие дни все окна с 10:00 до 21:00 полностью свободны!")
    else:
        for d in sorted(active_slots_by_date.keys())[:7]:
            day_name = DAYS_RU[d.weekday()]
            diff_days = (d - now.date()).days
            rel_label = ""
            if diff_days == 0:
                rel_label = " (Сегодня)"
            elif diff_days == 1:
                rel_label = " (Завтра)"
            elif diff_days == 2:
                rel_label = " (Послезавтра)"

            date_header = f"• {d.strftime('%Y-%m-%d')} ({day_name}){rel_label}:"
            lines.append(date_header)

            slots = sorted(active_slots_by_date[d], key=lambda x: x[0])
            for start, end, info in slots:
                lines.append(
                    f"  - ЗАНЯТО: {start.strftime('%H:%M')} — {end.strftime('%H:%M')} [{info}]. "
                    f"Запись в промежуток между {start.strftime('%H:%M')} и {end.strftime('%H:%M')} (включая 15:30 и т.д.) НЕВОЗМОЖНА!"
                )

    lines.extend([
        "",
        "СТРОЖАЙШИЕ ПРАВИЛА ПРОВЕРКИ СВОБОДНЫХ ОКОН:",
        "1. ПЕРЕД ТЕМ как сказать клиенту, свободно ли время, СТРОГО сверься со списком ЗАНЯТЫХ ОКОН выше!",
        "2. Учитывай, что стрижка длится РОВНО 1 ЧАС: если человек записан на 15:00, то окно 15:00-16:00 ЗАНЯТО! Запись на 15:15, 15:30, 15:45 или 14:30 ЗАПРЕЩЕНА.",
        "3. Если клиент спрашивает про занятое время (например: 'Свободно ли в 15:30?'):",
        "   ОТВЕТЬ ЧЕТКО И ДРУЖЕЛЮБНО:",
        "   'Ака, на 15:30 не получится — у мастера в это время идет стрижка (с 15:00 до 16:00 занято, стрижка длится ровно час). Могу предложить на 14:00 или на 16:00 — какое время удобнее?'",
        "4. Если время СВОБОДНО — скажи, что время свободно, и (если имя еще не названо) спроси имя клиента!",
        "5. Если клиент спрашивает: 'Какие есть окна на завтра?' — назови свободные круглые часы с 10:00 до 21:00, исключая занятые слоты."
    ])

    return "\n".join(lines)


def get_slots_for_date(
    target_date: date, existing_bookings: List[Dict], step_minutes: int = 30
) -> List[Dict]:
    """
    Возвращает список слотов с шагом step_minutes (по умолчанию 30 мин)
    с 10:00 до 21:00 включительно.
    Для каждого слота указывается статус доступности (с учетом правила 1 часа и занятости).
    """
    now = get_now_tashkent()
    slots = []

    # Генерируем слоты с 10:00 до 21:00 с шагом 30 минут
    start_min = WORK_START_HOUR * 60
    end_min = (WORK_END_HOUR - 1) * 60  # 21:00 - последний слот на 1 час работы

    for total_m in range(start_min, end_min + 1, step_minutes):
        hour = total_m // 60
        minute = total_m % 60
        slot_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=TASHKENT_TZ)
        time_str = f"{hour:02d}:{minute:02d}"

        # Если время уже прошло (для сегодняшнего дня)
        if slot_dt < now:
            slots.append({
                "time": time_str,
                "is_available": False,
                "reason": "Время уже прошло"
            })
            continue

        is_avail, reason, _ = check_slot_availability(slot_dt, existing_bookings)
        slots.append({
            "time": time_str,
            "is_available": is_avail,
            "reason": reason if not is_avail else None
        })

    return slots


