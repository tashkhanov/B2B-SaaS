"""
Сервис бронирования, сопоставления филиалов, услуг и инструментов ИИ.
Обеспечивает детерминированное выполнение операций записи в БД (SQLite)
с защитой от коллизий, VIP-согласованием Шеф-мастера и Telegram-уведомлениями.
"""

import re
import html
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, date, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database import (
    get_branches,
    get_services,
    get_barbers,
    get_active_bookings,
    save_booking,
    BookingConflictError,
    get_user_active_booking,
    cancel_user_booking,
    update_booking_status,
    find_booking_by_client_name,
    get_branch_by_id,
    get_salon_setting,
)
from services.schedule_service import (
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
    get_slots_for_date,
    resolve_barber_match,
    is_barber_vip,
    get_now_tashkent,
)
from services.tier_service import is_feature_enabled

logger = logging.getLogger(__name__)


def is_super_admin_user(user_id: Optional[int]) -> bool:
    """Проверка, является ли пользователь Создателем или Супер-Админом салона"""
    if not user_id:
        return False
    target_super = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    if target_super and user_id == target_super:
        return True
    return bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)


INVALID_CUSTOMER_NAMES = {
    # Русские вопросительные и относительные слова
    "каким", "какой", "какая", "какое", "какие", "какого", "какому", "каком", "какими",
    "кто", "кого", "кому", "кем", "ком", "что", "чего", "чему", "чем", "чём",
    "где", "куда", "откуда", "когда", "почему", "зачем", "как", "каков", "сколько",
    # Местоимения
    "меня", "мне", "мной", "мною", "себя", "себе", "собой", "собою", "я", "он", "она", "оно", "они",
    "мой", "моя", "мое", "моё", "мои", "наш", "наша", "наше", "наши", "сам", "сама", "сами",
    "его", "её", "ее", "их", "нас", "вам", "вами", "тобой", "тебе", "тебя", "ты", "вы",
    # Узбекские вопросительные и местоимения
    "qanday", "qanaqa", "kim", "kimga", "kimni", "kimda", "nima", "nimaga", "qayerda", "qayerga",
    "qachon", "nega", "meni", "menga", "menda", "o'zim", "ozim", "o'zi", "ozi", "biz", "siz",
    # Заполнители, роли, слова общего назначения
    "гость", "малы", "клиент", "шеф", "братан", "друг", "человек", "пользователь", "клиента",
    "guest", "user", "client", "customer", "admin", "админ", "барбер", "мастер", "парикмахер",
    "неизвестно", "никто", "тест", "test", "none", "null", "undefined", "нет", "отмена",
    "запись", "бронь", "время", "окно", "день", "сегодня", "завтра"
}


def is_invalid_customer_name(name: Optional[str], barbers: Optional[List[Dict[str, Any]]] = None) -> bool:
    """
    Строгая валидация имени клиента. Отсекает вопросительные слова ('Каким'),
    местоимения ('меня'), шаблонные слова ('малы', 'клиент') и совпадения с мастерами.
    """
    if not name:
        return True
    clean = name.strip().lower()
    if len(clean) < 2:
        return True
    if clean in INVALID_CUSTOMER_NAMES:
        return True
    clean_stripped = re.sub(r"^[^\w]+|[^\w]+$", "", clean)
    if clean_stripped in INVALID_CUSTOMER_NAMES:
        return True
    if not re.search(r"[a-zA-Zа-яА-ЯёЁўқғҳЎҚҒҲ]", clean):
        return True
    if barbers:
        for b in barbers:
            b_name = (b.get("name") or "").strip().lower()
            if not b_name:
                continue
            if clean == b_name:
                return True
            b_parts = [p.strip(".") for p in b_name.split() if len(p.strip(".")) >= 3]
            if clean in b_parts or clean in [f"мастер {p}" for p in b_parts] or clean in [f"mr {p}" for p in b_parts]:
                return True
    return False


async def resolve_branch(
    query_text: str = "",
    branch_id: Optional[int] = None,
    branch_name: Optional[str] = None
) -> Tuple[int, str, str]:
    """
    Динамически сопоставляет филиал салона из базы данных без хардкода.
    Возвращает: (branch_id, branch_name, branch_address)
    """
    branches = await get_branches(active_only=True)
    if not branches:
        return 1, branch_name or "Основной филиал", ""

    # 1. По ID
    if branch_id:
        for b in branches:
            if b["id"] == branch_id:
                return b["id"], b["name"], b["address"]

    # 2. По названию
    if branch_name:
        b_name_lower = branch_name.lower()
        for b in branches:
            if b["name"].lower() in b_name_lower or b_name_lower in b["name"].lower():
                return b["id"], b["name"], b["address"]

    # 3. По упоминанию в тексте запроса (название, ориентир, адрес)
    q = (query_text or "").lower()
    for b in branches:
        name_lower = b["name"].lower()
        landmark = (b.get("landmark") or "").lower()
        addr = (b.get("address") or "").lower()
        if name_lower in q or (landmark and landmark in q) or (addr and addr in q):
            return b["id"], b["name"], b["address"]
        for token in name_lower.split():
            if len(token) > 3 and token in q:
                return b["id"], b["name"], b["address"]

    first = branches[0]
    return first["id"], first["name"], first["address"]


async def resolve_service_info(service_name_or_query: Optional[str]) -> Tuple[Optional[Dict], int]:
    """
    Динамически сопоставляет услугу из каталога базы данных.
    Возвращает: (matched_service_dict, duration_min)
    """
    services = await get_services(active_only=True)
    if not services:
        return None, 60

    if not service_name_or_query:
        return services[0], services[0].get("duration_min") or 60

    q = service_name_or_query.strip().lower()

    # 1. Точное совпадение
    for s in services:
        if s["name"].lower() == q:
            return s, s.get("duration_min") or 60

    # 2. Подстрока
    for s in services:
        s_name_lower = s["name"].lower()
        if s_name_lower in q or q in s_name_lower:
            return s, s.get("duration_min") or 60

    # 3. Совпадение по корням слов
    q_words = [w for w in re.split(r'\W+', q) if len(w) > 3]
    for s in services:
        s_name_lower = s["name"].lower()
        if any(w in s_name_lower for w in q_words):
            return s, s.get("duration_min") or 60

    return services[0], services[0].get("duration_min") or 60


async def dispatch_vip_approval_card(
    bot: Optional[Bot],
    booking_id: int,
    barber_obj: Optional[dict],
    final_barber_name: str,
    full_name: str,
    username: Optional[str],
    user_id: Optional[int],
    canonical_service: str,
    booking_time: str,
    phone: Optional[str],
    target_branch_name: str,
    branch_address: str,
) -> None:
    """Отправка интерактивной карточки согласования VIP-записи Шефу Mr. Akmal и Администрации."""
    if not bot:
        logger.debug("Bot instance not provided to dispatch_vip_approval_card, skipped telegram push.")
        return

    target_chats = set()
    if barber_obj and barber_obj.get("telegram_id"):
        try:
            target_chats.add(int(barber_obj["telegram_id"]))
        except (ValueError, TypeError):
            pass
    if settings.ADMIN_CHAT_ID:
        try:
            target_chats.add(int(settings.ADMIN_CHAT_ID))
        except (ValueError, TypeError):
            pass
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    if super_admin_id:
        try:
            target_chats.add(int(super_admin_id))
        except (ValueError, TypeError):
            pass

    vip_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Принять ✅", callback_data=f"vip_accept:{booking_id}"),
            InlineKeyboardButton(text="Отклонить ❌", callback_data=f"vip_reject:{booking_id}"),
        ]]
    )

    safe_full_name = html.escape(str(full_name or "Клиент"))
    safe_username = html.escape(str(username or "нет username"))
    safe_barber_name = html.escape(str(final_barber_name or "Шеф"))
    safe_branch_name = html.escape(str(target_branch_name or "Миллий бог"))
    safe_branch_address = html.escape(str(branch_address or ""))
    safe_service = html.escape(str(canonical_service or "Стрижка"))
    safe_time = html.escape(str(booking_time or ""))
    safe_phone = html.escape(str(phone or "Не указан"))
    safe_user_id = html.escape(str(user_id or ""))

    clean_un = (username or "").replace("@", "").strip()
    if clean_un:
        tg_username_line = f"📱 <b>TG Username:</b> @{html.escape(clean_un)}\n"
        plain_tg_line = f"📱 TG Username: @{clean_un}\n"
    elif user_id:
        tg_username_line = f"📱 <b>TG Username:</b> отсутствует (ID: <code>{safe_user_id}</code>)\n"
        plain_tg_line = f"📱 TG Username: отсутствует (ID: {user_id})\n"
    else:
        tg_username_line = "📱 <b>TG Username:</b> отсутствует\n"
        plain_tg_line = "📱 TG Username: отсутствует\n"

    branch_line = f"📍 <b>Филиал:</b> {safe_branch_name}" + (f" ({safe_branch_address})\n" if safe_branch_address else "\n")
    vip_card_html = (
        "👑 <b>ЗАПРОС НА ЗАПИСЬ К ШЕФ-МАСТЕРУ!</b>\n\n"
        f"👤 <b>Клиент:</b> {safe_full_name}\n"
        f"{tg_username_line}"
        f"💈 <b>Мастер:</b> {safe_barber_name}\n"
        f"{branch_line}"
        f"✂️ <b>Услуга:</b> {safe_service}\n"
        f"⏰ <b>Время:</b> {safe_time}\n"
        f"📞 <b>Телефон:</b> {safe_phone}\n\n"
        "👑 <i>На тарифе PRO действует защита VIP-графика: требуется ваше личное согласование кнопками ниже:</i>"
    )

    plain_branch = f"📍 Филиал: {target_branch_name}" + (f" ({branch_address})\n" if branch_address else "\n")
    plain_card = (
        "👑 ЗАПРОС НА ЗАПИСЬ К ШЕФ-МАСТЕРУ!\n\n"
        f"👤 Клиент: {full_name}\n"
        f"{plain_tg_line}"
        f"💈 Мастер: {final_barber_name}\n"
        f"{plain_branch}"
        f"✂️ Услуга: {canonical_service}\n"
        f"⏰ Время: {booking_time}\n"
        f"📞 Телефон: {phone or 'Не указан'}\n\n"
        "👑 На тарифе PRO действует защита VIP-графика: требуется ваше личное согласование кнопками ниже."
    )

    for chat_id in target_chats:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=vip_card_html,
                reply_markup=vip_kb,
                parse_mode="HTML"
            )
        except Exception as e_html:
            logger.warning(f"Не удалось отправить HTML карточку VIP чату {chat_id}: {e_html}")
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=plain_card,
                    reply_markup=vip_kb,
                    parse_mode=None
                )
            except Exception as e_plain:
                logger.error(f"Ошибка при отправке карточки VIP чату {chat_id}: {e_plain}")


class GeminiBookingTools:
    """
    Инструменты Gemini (Function Calling) для детерминированного выполнения операций
    бронирования, проверки слотов и отмены записи.
    """

    async def check_available_slots(
        self,
        target_date: str,
        branch_name_or_id: Optional[str] = None,
        barber_name_or_id: Optional[str] = None,
        service_name_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Проверка доступных окон для записи на указанную дату.
        
        Args:
            target_date: Дата ('сегодня', 'завтра', '2026-10-20', 'вторник' и т.д.)
            branch_name_or_id: Название или ID филиала
            barber_name_or_id: Имя мастера или None
            service_name_or_id: Название услуги для учета длительности
        """
        try:
            now_dt = get_now_tashkent()
            parsed_dt = parse_booking_datetime(target_date)
            calc_date = parsed_dt.date() if parsed_dt else now_dt.date()

            active_bookings = await get_active_bookings()
            barbers = await get_barbers(active_only=True)
            branches = await get_branches(active_only=True)

            target_branch_id, target_branch_name, _ = await resolve_branch(
                query_text=str(branch_name_or_id or ""),
                branch_name=str(branch_name_or_id or "") if not str(branch_name_or_id).isdigit() else None,
                branch_id=int(branch_name_or_id) if str(branch_name_or_id).isdigit() else None,
            )

            matched_barber = resolve_barber_match(barber_name_or_id, barbers) if barber_name_or_id else None
            barber_id = matched_barber["id"] if matched_barber else None

            matched_srv, duration_min = await resolve_service_info(service_name_or_id)

            slots = get_slots_for_date(
                target_date=calc_date,
                barber_id=barber_id,
                existing_bookings=active_bookings,
                available_barbers=barbers,
                duration_minutes=duration_min,
            )

            return {
                "available": bool(slots),
                "target_date": calc_date.isoformat(),
                "slots": slots[:12],
                "total_slots_count": len(slots),
                "barber_name": matched_barber["name"] if matched_barber else "Любой свободный мастер",
                "branch_name": target_branch_name,
                "service": matched_srv["name"] if matched_srv else "Стрижка волос",
                "message": (
                    f"На {calc_date.isoformat()} доступно {len(slots)} окон в филиале {target_branch_name}."
                    if slots else f"На {calc_date.isoformat()} свободных окон нет."
                )
            }
        except Exception as e:
            logger.error(f"Ошибка в check_available_slots: {e}", exc_info=True)
            return {"available": False, "slots": [], "error": str(e), "message": "Ошибка проверки слотов"}

    async def create_booking_reservation(
        self,
        customer_name: str,
        booking_time: str,
        branch_name_or_id: Optional[str] = None,
        barber_name_or_id: Optional[str] = None,
        service_name_or_id: Optional[str] = None,
        phone: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
    ) -> Dict[str, Any]:
        """
        Создание реальной записи клиента в базе данных. Вызывать ТОЛЬКО когда клиент согласен на запись!

        Args:
            customer_name: Имя клиента (для Создателя всегда 'Иброхим')
            booking_time: Дата и время записи (например '2026-10-20 14:00 (Вторник)' или 'завтра 14:00')
            branch_name_or_id: Филиал салона ('Миллий бог', 'Чиланзар' или ID)
            barber_name_or_id: Имя мастера ('Mr. Akmal', 'Dilshod' или 'Любой')
            service_name_or_id: Название выбранной услуги
            phone: Номер телефона клиента
            user_id: Telegram ID клиента
            username: Telegram @username клиента
            bot: Экземпляр бота для отправки уведомлений
        """
        try:
            # 1. Проверка лимита: строго 1 активная запись на клиента
            if user_id:
                active_user_b = await get_user_active_booking(user_id)
                if active_user_b and active_user_b.get("id"):
                    b_time = active_user_b.get("booking_time", "ранее")
                    b_srv = active_user_b.get("service", "Стрижка")
                    b_barber = active_user_b.get("barber_name", "мастер")
                    return {
                        "success": False,
                        "error": "active_booking_exists",
                        "message": (
                            f"У вас уже есть активная запись на {b_time} ({b_srv}, мастер: {b_barber}). "
                            "По правилам салона Brohouse, у одного клиента может быть только одна активная запись. "
                            "Чтобы записаться на другое время, пожалуйста, сначала перенесите или отмените текущую запись."
                        )
                    }

            target_dt = parse_booking_datetime(booking_time)
            if not target_dt:
                return {
                    "success": False,
                    "error": "invalid_datetime",
                    "message": f"Не удалось распознать точное время '{booking_time}'. Уточните дату и время (например: Завтра в 14:00)."
                }

            active_bookings = await get_active_bookings()
            barbers = await get_barbers(active_only=True)

            # Очистка имени от галлюцинаций и ошибочных местоимений / вопросительных слов
            clean_name = (customer_name or "").strip()
            clean_un = (username or "").strip().lstrip("@")
            is_creator = (
                is_super_admin_user(user_id)
                or user_id == 1243374131
                or clean_un.lower() == "tashkhanov"
            )

            if is_invalid_customer_name(clean_name, barbers):
                if is_creator:
                    clean_name = "Иброхим"
                elif clean_un:
                    clean_name = clean_un
                else:
                    clean_name = "Клиент"
            elif is_creator and clean_name.lower() in ("меня", "мне", "себя", "иброхим", "каким"):
                clean_name = "Иброхим"

            # Очистка телефона от галлюцинаций и подстановки служебных номеров заведения
            clean_phone = phone
            if clean_phone:
                raw_phone_str = str(clean_phone).strip()
                phone_digits = re.sub(r"\D", "", raw_phone_str)

                # Собираем номера заведения, филиалов и барберов
                business_phone_digits = set()
                if getattr(settings, "SALON_PHONE", None):
                    business_phone_digits.add(re.sub(r"\D", "", str(settings.SALON_PHONE)))
                if getattr(settings, "PROMO_PHONE", None):
                    business_phone_digits.add(re.sub(r"\D", "", str(settings.PROMO_PHONE)))
                for b in barbers:
                    if b.get("phone"):
                        business_phone_digits.add(re.sub(r"\D", "", str(b["phone"])))
                branches_all = await get_branches(active_only=False)
                for br in branches_all:
                    if br.get("phone"):
                        business_phone_digits.add(re.sub(r"\D", "", str(br["phone"])))
                db_salon_phone = await get_salon_setting("salon_phone", None)
                if db_salon_phone:
                    business_phone_digits.add(re.sub(r"\D", "", str(db_salon_phone)))

                business_phone_digits = {d for d in business_phone_digits if len(d) >= 7}

                is_business_phone = (
                    phone_digits in business_phone_digits
                    or any(b_num in phone_digits for b_num in business_phone_digits)
                    or any(phone_digits in b_num for b_num in business_phone_digits if len(phone_digits) >= 9)
                )

                is_dummy_phone = (
                    len(phone_digits) < 7
                    or "1234567" in phone_digits
                    or (len(phone_digits) >= 7 and len(set(phone_digits[-7:])) <= 1)
                )

                if is_business_phone or is_dummy_phone:
                    clean_phone = None

            # Сопоставление барбера
            matched_barber = resolve_barber_match(
                query_or_name=barber_name_or_id,
                barbers=barbers,
                fallback_text=f"{clean_name} {booking_time}"
            )

            # Сопоставление филиала
            target_branch_id, target_branch_name, branch_address = await resolve_branch(
                query_text=str(branch_name_or_id or ""),
                branch_name=str(branch_name_or_id or "") if not str(branch_name_or_id).isdigit() else None,
                branch_id=int(branch_name_or_id) if str(branch_name_or_id).isdigit() else None,
            )

            # Сопоставление услуги
            matched_service, srv_duration = await resolve_service_info(service_name_or_id)
            canonical_service = matched_service["name"] if matched_service else "Стрижка волос"
            matched_service_id = matched_service["id"] if matched_service else None

            # Проверка занятости слота
            res = check_slot_availability(
                target_dt=target_dt,
                existing_bookings=active_bookings,
                barber_id=matched_barber["id"] if matched_barber else None,
                available_barbers=barbers,
                duration_minutes=srv_duration,
            )
            if not res.is_available:
                return {
                    "success": False,
                    "error": "slot_occupied",
                    "message": f"К сожалению, время {booking_time} уже недоступно: {res.error_message}. Предложите клиенту другое свободное окошко."
                }

            normalized_time = format_booking_datetime(target_dt)
            final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
            final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

            # Определение VIP-статуса
            barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None) if final_barber_id else matched_barber
            is_vip_master = is_barber_vip(barber_obj) if barber_obj else False
            has_vip_protection = await is_feature_enabled("has_vip_protection")

            # Статус записи: pending_vip для Akmal/VIP, иначе confirmed (или new)
            booking_status = "pending_vip" if (is_vip_master and has_vip_protection) else "confirmed"

            # Сохранение в базу данных
            booking_id = await save_booking(
                user_id=user_id or 0,
                username=username,
                full_name=clean_name,
                details=f"Запись через ИИ-Ассистента | Филиал: {target_branch_name} | Мастер: {final_barber_name}",
                phone=clean_phone,
                service=canonical_service,
                booking_time=normalized_time,
                status=booking_status,
                barber_id=final_barber_id,
                barber_name=final_barber_name,
                service_id=matched_service_id,
                branch_id=target_branch_id,
                branch_name=target_branch_name,
            )

            # Если VIP — отправляем карточку согласования
            if booking_status == "pending_vip":
                await dispatch_vip_approval_card(
                    bot=bot,
                    booking_id=booking_id,
                    barber_obj=barber_obj,
                    final_barber_name=final_barber_name or "Mr. Akmal",
                    full_name=customer_name,
                    username=username,
                    user_id=user_id,
                    canonical_service=canonical_service,
                    booking_time=normalized_time,
                    phone=phone,
                    target_branch_name=target_branch_name,
                    branch_address=branch_address,
                )
                msg = (
                    f"👑 Запрос на бронирование к Шеф-мастеру {final_barber_name} успешно зарегистрирован "
                    f"и отправлен на личное согласование Шефу. Слот будет подтвержден в течение 15 минут."
                )
            else:
                # Оповещение администратора и барбера
                clean_un = (username or "").replace("@", "").strip()
                if clean_un:
                    tg_un_line = f"📱 <b>TG Username:</b> @{clean_un}\n"
                elif user_id:
                    tg_un_line = f"📱 <b>TG Username:</b> отсутствует (ID: {user_id})\n"
                else:
                    tg_un_line = "📱 <b>TG Username:</b> отсутствует\n"

                if bot and settings.ADMIN_CHAT_ID:
                    try:
                        await bot.send_message(
                            chat_id=settings.ADMIN_CHAT_ID,
                            text=(
                                f"💈 <b>НОВАЯ ЗАПИСЬ ЧЕРЕЗ ИИ!</b>\n\n"
                                f"👤 <b>Клиент:</b> {customer_name}\n"
                                f"{tg_un_line}"
                                f"💈 <b>Мастер:</b> {final_barber_name}\n"
                                f"📍 <b>Филиал:</b> {target_branch_name}\n"
                                f"✂️ <b>Услуга:</b> {canonical_service}\n"
                                f"⏰ <b>Время:</b> {normalized_time}\n"
                                f"📞 <b>Телефон:</b> {phone or 'Не указан'}"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление админу: {e}")

                if bot and barber_obj and barber_obj.get("telegram_id"):
                    try:
                        await bot.send_message(
                            chat_id=barber_obj["telegram_id"],
                            text=(
                                f"💈 <b>К вам записался клиент через ИИ!</b>\n\n"
                                f"👤 <b>Гость:</b> {customer_name}\n"
                                f"{tg_un_line}"
                                f"📍 <b>Филиал:</b> {target_branch_name}\n"
                                f"✂️ <b>Услуга:</b> {canonical_service}\n"
                                f"⏰ <b>Время:</b> {normalized_time}\n"
                                f"📞 <b>Телефон:</b> {phone or 'Не указан'}"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить уведомление барберу: {e}")

                msg = f"Запись клиента {customer_name} на {normalized_time} ({canonical_service}) в филиал {target_branch_name} успешно подтверждена и внесена в базу."

            return {
                "success": True,
                "booking_id": booking_id,
                "status": booking_status,
                "barber_name": final_barber_name,
                "branch_name": target_branch_name,
                "branch_address": branch_address,
                "service": canonical_service,
                "booking_time": normalized_time,
                "requires_manual_approval": (booking_status == "pending_vip"),
                "message": msg
            }

        except BookingConflictError as bce:
            logger.warning(f"Booking collision in create_booking_reservation: {bce}")
            return {
                "success": False,
                "error": "slot_occupied",
                "message": f"К сожалению, время {booking_time} у мастера {final_barber_name} уже занято другим клиентом. Пожалуйста, предложите клиенту выбрать другое свободное время или мастера."
            }
        except Exception as e:
            logger.error(f"Ошибка в create_booking_reservation: {e}", exc_info=True)
            return {"success": False, "error": "server_error", "message": f"Ошибка создания брони: {str(e)}"}

    async def cancel_booking_reservation(
        self,
        booking_id: Optional[int] = None,
        user_id: Optional[int] = None,
        customer_name: Optional[str] = None,
        bot: Optional[Bot] = None,
    ) -> Dict[str, Any]:
        """
        Отмена существующей записи клиента и освобождение окна.
        """
        try:
            cancelled = None
            if user_id:
                cancelled = await cancel_user_booking(user_id)
            if not cancelled and booking_id:
                await update_booking_status(booking_id, "cancelled")
                cancelled = {"id": booking_id, "status": "cancelled"}
            if not cancelled and customer_name:
                b = await find_booking_by_client_name(customer_name)
                if b:
                    await update_booking_status(b["id"], "cancelled")
                    cancelled = b
            if not cancelled and (is_super_admin_user(user_id) or user_id == 1243374131):
                b = await find_booking_by_client_name("Иброхим")
                if b:
                    await update_booking_status(b["id"], "cancelled")
                    cancelled = b

            if not cancelled:
                return {"success": False, "message": "Активная бронь для отмены не найдена."}

            if bot and settings.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=f"🔔 <b>ОТМЕНА ЗАПИСИ (через ИИ):</b> Запись #{cancelled.get('id')} успешно отменена, слот освобожден.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Ошибка уведомления админа об отмене: {e}")

            return {
                "success": True,
                "booking_id": cancelled.get("id"),
                "message": "Запись успешно отменена, окно освобождено."
            }
        except Exception as e:
            logger.error(f"Ошибка в cancel_booking_reservation: {e}", exc_info=True)
            return {"success": False, "error": str(e), "message": "Ошибка отмены записи"}

    async def get_salon_info(self, topic: Optional[str] = None) -> Dict[str, Any]:
        """
        Получение актуальной справочной информации о салоне (филиалы, прайс-лист, мастера).
        """
        branches = await get_branches(active_only=True)
        services = await get_services(active_only=True)
        barbers = await get_barbers(active_only=True)

        return {
            "branches": [
                {"id": b["id"], "name": b["name"], "address": b["address"], "landmark": b.get("landmark")}
                for b in branches
            ],
            "services": [
                {"id": s["id"], "name": s["name"], "price": s["price"], "duration_min": s["duration_min"], "badge": s.get("badge")}
                for s in services
            ],
            "barbers": [
                {"id": b["id"], "name": b["name"], "role": b["role_title"], "is_vip": bool(b.get("is_vip"))}
                for b in barbers
            ],
            "promo": {
                "title": settings.PROMO_TITLE,
                "subtitle": settings.PROMO_SUBTITLE,
                "discount_percent": settings.PROMO_DISCOUNT_PERCENT,
            }
        }
