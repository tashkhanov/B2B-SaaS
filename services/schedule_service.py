"""
Модуль управления расписанием, часовым поясом Ташкента (UTC+5),
мульти-мастер валидацией и умным распределением слотов в мужском салоне Brohouse.
"""

from datetime import datetime, date, time, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any
import re
import logging
import random
from config import settings

logger = logging.getLogger(__name__)

# Часовой пояс Ташкента (UTC+5)
try:
    from zoneinfo import ZoneInfo
    TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT_TZ = timezone(timedelta(hours=5))

# Базовая длительность процедуры в барбершопе по умолчанию (1 час = 60 минут)
SERVICE_DURATION_MINUTES = 60

# Рабочие часы барбершопа
WORK_START_HOUR = getattr(settings, "WORK_START_HOUR", 8)   # 08:00
WORK_END_HOUR = getattr(settings, "WORK_END_HOUR", 23)    # 23:00

# Горизонт бронирования вперед
MAX_BOOKING_DAYS_AHEAD = getattr(settings, "MAX_BOOKING_DAYS_AHEAD", 7)

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


class SlotCheckResult:
    """
    Результат проверки доступности временного слота.
    Поддерживает распаковку как 3 элементов: (is_available, error_message, conflicting_booking)
    для полной обратной совместимости, а также предоставляет свойства для мульти-мастера:
    assigned_barber_id, assigned_barber_name.
    """
    def __init__(
        self,
        is_available: bool,
        error_message: Optional[str] = None,
        conflicting_booking: Optional[Dict] = None,
        assigned_barber_id: Optional[int] = None,
        assigned_barber_name: Optional[str] = None,
    ):
        self.is_available = is_available
        self.error_message = error_message
        self.conflicting_booking = conflicting_booking
        self.assigned_barber_id = assigned_barber_id
        self.assigned_barber_name = assigned_barber_name

    def __iter__(self):
        # Распаковка по умолчанию в 3 элемента (совместимость с существующим кодом)
        return iter((self.is_available, self.error_message, self.conflicting_booking))

    def __getitem__(self, index):
        items = (self.is_available, self.error_message, self.conflicting_booking, self.assigned_barber_id)
        return items[index]

    def __repr__(self):
        return (
            f"SlotCheckResult(is_available={self.is_available}, "
            f"error_message={self.error_message!r}, "
            f"assigned_barber_id={self.assigned_barber_id}, "
            f"assigned_barber_name={self.assigned_barber_name!r})"
        )


def get_now_tashkent() -> datetime:
    """Возвращает текущую дату и время в часовом поясе Ташкента (UTC+5)."""
    return datetime.now(TASHKENT_TZ)


def format_tashkent_now_for_prompt() -> str:
    """
    Форматирует текущую дату и время для передачи в системный промпт нейросети.
    Пример: '16 октября 2026 г., 00:15 (Пятница, Ташкент UTC+5)'
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
    dt1: datetime,
    dt2: datetime,
    duration1: int = SERVICE_DURATION_MINUTES,
    duration2: int = SERVICE_DURATION_MINUTES,
    duration_minutes: Optional[int] = None
) -> bool:
    """
    Проверяет, пересекаются ли два интервала времени: [dt1, dt1 + duration1] и [dt2, dt2 + duration2].
    Два отрезка [A_start, A_end] и [B_start, B_end] пересекаются,
    если A_start < B_end и B_start < A_end.
    """
    if duration_minutes is not None:
        duration1 = duration_minutes
        duration2 = duration_minutes

    t1_start = dt1.astimezone(TASHKENT_TZ) if dt1.tzinfo else dt1.replace(tzinfo=TASHKENT_TZ)
    t2_start = dt2.astimezone(TASHKENT_TZ) if dt2.tzinfo else dt2.replace(tzinfo=TASHKENT_TZ)
    t1_end = t1_start + timedelta(minutes=duration1)
    t2_end = t2_start + timedelta(minutes=duration2)

    return t1_start < t2_end and t2_start < t1_end


def is_booking_past(raw_booking_time: str, grace_minutes: int = SERVICE_DURATION_MINUTES) -> bool:
    """
    Проверяет, завершилась ли процедура записи (время начала + длительность grace_minutes < текущее время Ташкента).
    """
    dt = parse_booking_datetime(raw_booking_time)
    if not dt:
        return False
    now = get_now_tashkent()
    end_time = dt + timedelta(minutes=grace_minutes)
    return end_time < now


def is_barber_vip(barber: Optional[Dict]) -> bool:
    """Определяет, является ли барбер VIP-мастером с ручным согласованием."""
    if not barber:
        return False
    if barber.get("is_vip") is not None:
        return bool(barber.get("is_vip"))
    role = (barber.get("role_title") or "").lower()
    name = (barber.get("name") or "").lower()
    return bool("шеф" in role or "директор" in role or "главный" in role or "владелец" in role or "akmal" in name)


def resolve_barber_match(
    query_or_name: Optional[str],
    barbers: List[Dict],
    fallback_text: Optional[str] = None
) -> Optional[Dict]:
    """
    Интеллектуальное сопоставление барбера по имени/запросу на русском, узбекском или английском.
    Поддерживает: 'Akmal', 'mr akmal', 'Mr. Akmal', 'Акмаль', 'акмал', 'Akmalga', 'Akmalning',
    'Dilshod', 'Дильшод', 'Abbos', 'Аббос', 'Nodir', 'Нодир'.
    """
    if not barbers:
        return None

    def _normalize(text: str) -> str:
        t = text.lower()
        replacements = [
            ("акмаль", "akmal"), ("акмал", "akmal"),
            ("дильшод", "dilshod"), ("дилшод", "dilshod"),
            ("аббос", "abbos"), ("исмаилов", "ismailov"),
            ("нодир", "nodir"), ("турсунов", "tursunov"),
            ("иброхим", "ibrohim"), ("ибрагим", "ibrohim"),
            ("жасур", "jasur"), ("сардор", "sardor"),
        ]
        for src, dst in replacements:
            t = t.replace(src, dst)
        t = re.sub(r"\b(mr\.?|мистер|уста|мастер|барбер)\b", "", t)
        t = re.sub(r"(ning|ga|ka|qa|ni|dan|da)\b", "", t)
        return t.strip()

    search_targets = []
    if query_or_name and query_or_name.strip():
        search_targets.append(_normalize(query_or_name))
    if fallback_text and fallback_text.strip():
        search_targets.append(_normalize(fallback_text))

    for target in search_targets:
        if not target:
            continue
        target_words = set(w for w in re.split(r"\W+", target) if len(w) >= 3)
        for b in barbers:
            b_norm = _normalize(b.get("name", ""))
            b_words = set(w for w in re.split(r"\W+", b_norm) if len(w) >= 3)
            if b_words & target_words:
                return b
            for bw in b_words:
                if any(bw in tw or tw in bw for tw in target_words):
                    return b
            if b_norm and b_norm in target:
                return b

    return None


def is_barber_working_on_date(barber: Dict, target_date: date) -> bool:
    """
    Проверяет, работает ли барбер в указанную дату:
    1. Барбер должен быть активен (is_active == 1).
    2. Если дата сегодняшняя, проверяется тумблер смены is_working_today (1 = на смене).
    3. День недели target_date.weekday() (0=Пн .. 6=Вс) должен входить в work_days.
    """
    if not barber.get("is_active", 1):
        return False

    now_date = get_now_tashkent().date()
    if target_date == now_date and barber.get("is_working_today", 1) == 0:
        return False

    work_days_raw = barber.get("work_days", "0,1,2,3,4,5,6")
    if isinstance(work_days_raw, str):
        days_list = [int(d.strip()) for d in work_days_raw.split(",") if d.strip().isdigit()]
    elif isinstance(work_days_raw, (list, tuple, set)):
        days_list = [int(x) for x in work_days_raw]
    else:
        days_list = list(range(7))

    return target_date.weekday() in days_list


def check_slot_availability(
    target_dt: datetime,
    existing_bookings: List[Dict],
    barber_id: Optional[int] = None,
    available_barbers: Optional[List[Dict]] = None,
    duration_minutes: int = SERVICE_DURATION_MINUTES,
    ignore_booking_id: Optional[int] = None,
    work_start_hour: Optional[int] = None,
    work_end_hour: Optional[int] = None,
    max_booking_days: Optional[int] = None
) -> SlotCheckResult:
    """
    Мульти-мастер проверка доступности слота:
    1. Проверка на прошедшее время.
    2. Проверка горизонта планирования (до max_booking_days дней).
    3. Проверка рабочих часов салона с учетом длительности.
    4. Если указан конкретный barber_id:
       - Проверяется график этого мастера на target_dt.date().
       - Проверяются только записи этого мастера.
    5. Если barber_id не указан («Любой мастер»):
       - Проверяются все активные мастера на смене в эту дату.
       - Выбирается первый свободный мастер.
       - Если все заняты, возвращается сообщение о занятости всех мастеров.
    """
    target = target_dt.astimezone(TASHKENT_TZ) if target_dt.tzinfo else target_dt.replace(tzinfo=TASHKENT_TZ)
    now = get_now_tashkent()
    start_hour = work_start_hour if work_start_hour is not None else WORK_START_HOUR
    end_hour = work_end_hour if work_end_hour is not None else WORK_END_HOUR
    max_days = max_booking_days if max_booking_days is not None else MAX_BOOKING_DAYS_AHEAD

    # 1. Прошедшее время
    if target < now - timedelta(minutes=15):
        return SlotCheckResult(False, "Выбранное время уже прошло.", None)

    # 2. Горизонт планирования (максимум max_days дней)
    diff_days = (target.date() - now.date()).days
    if diff_days > max_days:
        max_allowed_date = now.date() + timedelta(days=max_days)
        max_month = MONTHS_RU[max_allowed_date.month] if max_allowed_date.month < len(MONTHS_RU) else ""
        max_str = f"{max_allowed_date.day} {max_month}"
        return SlotCheckResult(
            False,
            f"Запись открыта максимум на {max_days} дней вперед (до {max_str}). "
            f"Пожалуйста, выберите удобный день на ближайшей неделе, ака!",
            None
        )

    # 3. Рабочие часы
    if target.hour < start_hour or target.hour > end_hour or (target.hour == end_hour and target.minute > 0):
        return SlotCheckResult(
            False,
            f"Салон работает с {start_hour:02d}:00 до {end_hour:02d}:00. Выберите время в этом интервале.",
            None
        )

    end_time_dt = target + timedelta(minutes=duration_minutes)
    closing_dt = target.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if end_time_dt > closing_dt:
        return SlotCheckResult(
            False,
            f"Салон закрывается в {end_hour:02d}:00, а процедура длится {duration_minutes} мин. Выберите более раннее время.",
            None
        )

    # 4. Проверка конкретного мастера
    if barber_id:
        target_barber = None
        if available_barbers:
            target_barber = next((b for b in available_barbers if b.get("id") == barber_id), None)

        b_name = target_barber.get("name") if target_barber else "Мастер"

        if target_barber and not is_barber_working_on_date(target_barber, target.date()):
            return SlotCheckResult(
                False,
                f"Мастер {b_name} в этот день отдыхает (выходной). Пожалуйста, выберите другого мастера или другой день.",
                None
            )

        # Проверяем конфликты с записями этого мастера
        for b in existing_bookings:
            if ignore_booking_id and b.get("id") == ignore_booking_id:
                continue
            if b.get("status") in ("cancelled", "completed"):
                continue

            # Сравниваем с записями этого мастера (или записями без мастера)
            b_barber_id = b.get("barber_id")
            if b_barber_id is not None and b_barber_id != barber_id:
                continue

            raw_time = b.get("booking_time")
            b_dt = parse_booking_datetime(raw_time)
            if not b_dt:
                continue

            if is_slot_overlapping(target, b_dt, duration_minutes, SERVICE_DURATION_MINUTES):
                b_end = (b_dt + timedelta(minutes=SERVICE_DURATION_MINUTES)).strftime("%H:%M")
                b_start = b_dt.strftime("%H:%M")
                client_name = b.get("full_name") or "клиент"
                reason = (
                    f"У мастера {b_name} в это время идет стрижка: с {b_start} до {b_end} "
                    f"записан {client_name}."
                )
                return SlotCheckResult(False, reason, b, barber_id, b_name)

        return SlotCheckResult(True, None, None, barber_id, b_name)

    # 5. Выбор любого свободного мастера
    if available_barbers:
        # Находим всех мастеров, работающих в эту дату
        working_barbers = [b for b in available_barbers if is_barber_working_on_date(b, target.date())]
        if not working_barbers:
            return SlotCheckResult(
                False,
                "На выбранный день нет работающих мастеров. Выберите другой день.",
                None
            )

        # Исключаем Шеф-мастера / Директора (Mr. Akmal) из общего пула автоподбора
        stylists = [
            b for b in working_barbers
            if "шеф" not in (b.get("role_title") or "").lower()
            and "директор" not in (b.get("role_title") or "").lower()
        ]
        candidates_pool = stylists if stylists else working_barbers

        # Ищем всех свободных мастеров из пула на это время
        free_candidates = []
        for barber in candidates_pool:
            b_id = barber["id"]
            has_conflict = False
            for b in existing_bookings:
                if ignore_booking_id and b.get("id") == ignore_booking_id:
                    continue
                if b.get("status") in ("cancelled", "completed"):
                    continue

                b_barber_id = b.get("barber_id")
                if b_barber_id is not None and b_barber_id != b_id:
                    continue

                raw_time = b.get("booking_time")
                b_dt = parse_booking_datetime(raw_time)
                if not b_dt:
                    continue

                if is_slot_overlapping(target, b_dt, duration_minutes, SERVICE_DURATION_MINUTES):
                    has_conflict = True
                    break

            if not has_conflict:
                free_candidates.append(barber)

        if free_candidates:
            # Считаем текущую загрузку мастеров на этот день (количество активных записей)
            def _get_load_for_barber(barber_id: int) -> int:
                count = 0
                for b in existing_bookings:
                    if ignore_booking_id and b.get("id") == ignore_booking_id:
                        continue
                    if b.get("status") in ("cancelled", "completed"):
                        continue
                    if b.get("barber_id") != barber_id:
                        continue
                    raw_time = b.get("booking_time")
                    b_dt = parse_booking_datetime(raw_time)
                    if b_dt and b_dt.date() == target.date():
                        count += 1
                return count

            loads = {b["id"]: _get_load_for_barber(b["id"]) for b in free_candidates}
            min_load = min(loads.values())
            least_busy = [b for b in free_candidates if loads[b["id"]] == min_load]

            # Выбираем случайного среди наименее загруженных для честного баланса
            chosen = random.choice(least_busy)
            return SlotCheckResult(True, None, None, chosen["id"], chosen["name"])

        return SlotCheckResult(
            False,
            "В это время все мастера заняты. Пожалуйста, выберите другое время.",
            existing_bookings[0] if existing_bookings else None
        )

    # 6. Fallback (если список мастеров не передан)
    for b in existing_bookings:
        if ignore_booking_id and b.get("id") == ignore_booking_id:
            continue
        if b.get("status") in ("cancelled", "completed"):
            continue

        raw_time = b.get("booking_time")
        b_dt = parse_booking_datetime(raw_time)
        if not b_dt:
            continue

        if is_slot_overlapping(target, b_dt, duration_minutes, SERVICE_DURATION_MINUTES):
            b_end = (b_dt + timedelta(minutes=SERVICE_DURATION_MINUTES)).strftime("%H:%M")
            b_start = b_dt.strftime("%H:%M")
            client_name = b.get("full_name") or "клиент"
            reason = (
                f"В это время мастер занят: с {b_start} до {b_end} оформлена запись "
                f"({client_name})."
            )
            return SlotCheckResult(False, reason, b)

    return SlotCheckResult(True, None, None)


def get_slots_for_date(
    target_date: date,
    existing_bookings: List[Dict],
    barber_id: Optional[int] = None,
    available_barbers: Optional[List[Dict]] = None,
    duration_minutes: int = SERVICE_DURATION_MINUTES,
    step_minutes: int = 30,
    work_start_hour: Optional[int] = None,
    work_end_hour: Optional[int] = None,
    max_booking_days: Optional[int] = None
) -> List[Dict]:
    """
    Возвращает список слотов с шагом step_minutes (по умолчанию 30 мин)
    для указанной даты и мастера с учетом рабочих часов.
    """
    now = get_now_tashkent()
    slots = []
    start_hour = work_start_hour if work_start_hour is not None else WORK_START_HOUR
    end_hour = work_end_hour if work_end_hour is not None else WORK_END_HOUR

    start_min = start_hour * 60
    end_min = (end_hour - 1) * 60

    for total_m in range(start_min, end_min + 1, step_minutes):
        hour = total_m // 60
        minute = total_m % 60
        slot_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=TASHKENT_TZ)
        time_str = f"{hour:02d}:{minute:02d}"

        if slot_dt < now:
            slots.append({
                "time": time_str,
                "is_available": False,
                "reason": "Время уже прошло",
                "barber_id": None,
                "barber_name": None
            })
            continue

        res = check_slot_availability(
            target_dt=slot_dt,
            existing_bookings=existing_bookings,
            barber_id=barber_id,
            available_barbers=available_barbers,
            duration_minutes=duration_minutes,
            work_start_hour=start_hour,
            work_end_hour=end_hour,
            max_booking_days=max_booking_days
        )
        slots.append({
            "time": time_str,
            "is_available": res.is_available,
            "reason": res.error_message if not res.is_available else None,
            "barber_id": res.assigned_barber_id,
            "barber_name": res.assigned_barber_name
        })

    return slots


def build_schedule_context_for_prompt(
    bookings: List[Dict],
    barbers: Optional[List[Dict]] = None,
    services: Optional[List[Dict]] = None,
    branches: Optional[List[Dict]] = None,
    salon_settings: Optional[Dict[str, str]] = None,
) -> str:
    """
    Генерирует актуальный текстовый блок с филиалами, графиком, мастерами и занятыми окнами
    для системного промпта нейросети на основе реальной базы SQLite.
    """
    now = get_now_tashkent()
    now_formatted = format_tashkent_now_for_prompt()
    start_h = int(salon_settings.get("work_start_hour", WORK_START_HOUR)) if salon_settings else WORK_START_HOUR
    end_h = int(salon_settings.get("work_end_hour", WORK_END_HOUR)) if salon_settings else WORK_END_HOUR
    max_days = int(salon_settings.get("max_booking_days_ahead", MAX_BOOKING_DAYS_AHEAD)) if salon_settings else MAX_BOOKING_DAYS_AHEAD

    lines = [
        f"ТЕКУЩЕЕ ВРЕМЯ И ДАТА В ТАШКЕНТЕ: {now_formatted}",
        f"РЕЖИМ РАБОТЫ БАРБЕРШОПА: ежедневно с {start_h}:00 до {end_h}:00.",
        f"ГОРИЗОНТ ЗАПИСИ: максимум на {max_days} дней вперед.",
        ""
    ]

    # Секция филиалов
    if branches:
        lines.append("АКТУАЛЬНЫЕ ФИЛИАЛЫ И ЛОКАЦИИ САЛОНА (ИЗ БАЗЫ ДАННЫХ):")
        for b in branches:
            phone_str = f", телефон: {b['phone']}" if b.get("phone") else ""
            landmark_str = f" (ориентир: {b['landmark']})" if b.get("landmark") else ""
            lines.append(f"- Филиал #{b['id']} «{b['name']}»: {b['address']}{landmark_str}{phone_str}")
        lines.append("")

    # Секция мастеров
    if barbers:
        lines.append("КОМАНДА БАРБЕРОВ САЛОНА (ИЗ БАЗЫ ДАННЫХ):")
        for b in barbers:
            status_text = "на смене" if b.get("is_working_today", 1) else "выходной сегодня"
            role = b.get("role_title") or "Барбер"
            is_vip = is_barber_vip(b)
            vip_tag = " [👑 VIP-ШЕФ: Требует личного согласования! Не обещай прямую бронь, сообщай что запрос отправлен на личное согласование Шефу]" if is_vip else " [Стандартная автозапись]"
            lines.append(f"- ID {b['id']}: {b['name']} ({role}) — {status_text}{vip_tag}")
        lines.append("")

    # Секция услуг
    if services:
        lines.append("АКТУАЛЬНЫЙ ПРАЙС-ЛИСТ И ДЛИТЕЛЬНОСТЬ УСЛУГ (ИЗ БАЗЫ ДАННЫХ):")
        for s in services:
            badge_str = f" [{s['badge']}]" if s.get("badge") else ""
            dur = f" ({s.get('duration_min', 60)} мин)" if s.get("duration_min") else ""
            desc = f" — {s.get('description', '')}" if s.get("description") else ""
            price_formatted = f"{s['price']:,} сум".replace(",", " ")
            lines.append(f"- {s['name']}{badge_str}: {price_formatted}{dur}{desc}")
        lines.append("")

    lines.append("АКТУАЛЬНЫЕ ЗАПИСИ ИЗ БАЗЫ ДАННЫХ (ЗАНЯТЫЕ ОКНА):")

    active_by_date: Dict[date, List[str]] = {}
    for b in bookings:
        if b.get("status") in ("cancelled", "completed"):
            continue
        raw_time = b.get("booking_time")
        b_dt = parse_booking_datetime(raw_time)
        if not b_dt or b_dt.date() < now.date():
            continue

        end_dt = b_dt + timedelta(minutes=SERVICE_DURATION_MINUTES)
        client = b.get("full_name") or "Клиент"
        service = b.get("service") or "Стрижка"
        b_master = b.get("barber_name") or "Мастер"
        desc = f"{b_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')} (Мастер: {b_master}, Клиент: {client}, {service})"
        active_by_date.setdefault(b_dt.date(), []).append(desc)

    if not active_by_date:
        lines.append("- На ближайшие дни все окна с 10:00 до 21:00 полностью свободны у всех мастеров!")
    else:
        for d in sorted(active_by_date.keys())[:7]:
            day_name = DAYS_RU[d.weekday()]
            diff_days = (d - now.date()).days
            rel_label = ""
            if diff_days == 0:
                rel_label = " (Сегодня)"
            elif diff_days == 1:
                rel_label = " (Завтра)"

            d_str = f"{d.strftime('%Y-%m-%d')} ({day_name}){rel_label}:"
            lines.append(d_str)
            for item in active_by_date[d]:
                lines.append(f"  • {item}")

    lines.extend([
        "",
        "ПРАВИЛА КОНСУЛЬТАЦИИ КЛИЕНТОВ ПО ВРЕМЕНИ И МАСТЕРАМ:",
        "1. Любая стрижка или комплекс длится ровно 1 час (если не указано иное в услугах).",
        "2. Если клиент спрашивает про занятое время (например: 'Свободно ли в 15:30?'):",
        "   'Ака, на 15:30 не получится — у мастера в это время идет стрижка (с 15:00 до 16:00 занято). Могу предложить на 14:00 или на 16:00 — какое время удобнее?'",
        "3. Если время СВОБОДНО — скажи, что время свободно, и спроси, к какому мастеру клиент хочет записаться (или предложить любого свободного).",
        "4. Если клиент спрашивает: 'Какие есть окна на завтра?' — назови свободные часы с 10:00 до 21:00."
    ])

    return "\n".join(lines)
