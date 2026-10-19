"""
Модульный роутер для клиентов (Client Router).
Обрабатывает:
- Текстовые диалоги с клиентами (handle_user_text) с поддержкой Gemini Tool Calling
- Голосовые сообщения (handle_voice_message) с rate-limiting и транскрибацией
- Запись, перенос и отмену визита (process_client_intent)
- Подтверждение и отмену за 2 часа до визита (confirm_visit, cancel_visit)
- Открытие диалога записи (open_booking_dialog)
"""

import io
import time
import html
import logging
from typing import Dict, Optional
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.enums import ChatAction

from config import settings
from database import (
    save_booking,
    BookingConflictError,
    has_recent_booking,
    has_recent_booking_any_time,
    get_recent_messages,
    get_active_bookings,
    get_barbers,
    get_barber_by_id,
    get_booking_by_id,
    get_user_active_booking,
    cancel_user_booking,
    confirm_booking_by_client,
    cancel_booking_by_client,
)
from services.gemini_service import GeminiService
from services.schedule_service import (
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
    resolve_barber_match,
    is_barber_vip,
)
from services.tier_service import is_feature_enabled
from services.booking_service import (
    resolve_branch,
    resolve_service_info,
    dispatch_vip_approval_card,
)
from bot.routers.common_router import (
    get_user_role,
    get_main_keyboard,
    is_super_admin,
    AI_BUSINESS_KEYBOARD,
)
from bot.routers.admin_router import process_admin_intent

logger = logging.getLogger(__name__)

router = Router(name="client_router")
gemini_service = GeminiService()

_voice_rate_limit: Dict[int, float] = {}


@router.message(F.voice)
async def handle_voice_message(message: Message):
    """
    Обработчик голосовых сообщений:
    1. Проверка rate-limiting (защита от аудио-спама)
    2. Скачивание аудиофайла
    3. Вызов GeminiService.generate_response_from_voice с Function Calling
    4. Отправка ответа клиенту
    5. Проверка недавних броней во избежание дублирования
    """
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None
    role, barber = await get_user_role(user_id)
    is_staff = role in ("admin", "barber", "super_admin")

    now = time.time()
    last_voice = _voice_rate_limit.get(user_id, 0.0)
    if now - last_voice < 3.0:
        await message.answer("Братан, давай помедленнее, обрабатываю предыдущее аудио! ⏳")
        return
    _voice_rate_limit[user_id] = now

    voice = message.voice
    if not voice:
        await message.answer("Не удалось распознать голосовое сообщение, повторите, пожалуйста!")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)

    file_bytes_io = io.BytesIO()
    try:
        file_info = await message.bot.get_file(voice.file_id)
        await message.bot.download_file(file_info.file_path, destination=file_bytes_io)
        voice_data = file_bytes_io.getvalue()
    except Exception as e:
        logger.error(f"Не удалось скачать голосовое сообщение: {e}")
        await message.answer("Братан, голосовое не загрузилось, отправь еще раз, пожалуйста!")
        return
    finally:
        file_bytes_io.close()

    ai_reply, user_transcript = await gemini_service.generate_response_from_voice(
        user_id=user_id,
        voice_bytes=voice_data,
        mime_type="audio/ogg",
        is_admin=is_staff,
        username=username,
        bot=message.bot,
    )

    # 3. Если Gemini Tool Calling уже создал бронь, отправляем ответ и завершаем
    recent_booking_exists = await has_recent_booking_any_time(user_id=user_id, seconds=30)
    if recent_booking_exists:
        logger.info("Voice booking handled via Gemini Tool Calling for user %s; skipping legacy intent extraction.", user_id)
        await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))
        return

    # 4. Проверка и выполнение интента (для клиента или персонала)
    handled_by_intent = False
    if is_staff:
        handled_by_intent = await process_admin_intent(message, admin_query=f"[🎙 Голос]: {user_transcript}", ai_reply=ai_reply)
    else:
        handled_by_intent = await process_client_intent(message, client_query=f"[🎙 Голос]: {user_transcript}", ai_reply=ai_reply)

    # 5. Отправка ответа пользователю, если действие не было обработано специализированным хэндлером
    if not handled_by_intent:
        await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.text)
async def handle_user_text(message: Message):
    """
    Основной обработчик текстовых сообщений:
    1. Показывает статус 'печатает...' (typing action).
    2. Отправляет запрос в GeminiService с историей сообщений, контекстом роли и Function Calling.
    3. Отправляет ответ пользователю (или выполняет действие через экстрактор).
    """
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None
    user_text = message.text.strip()
    role, barber = await get_user_role(user_id)
    is_staff = role in ("admin", "barber", "super_admin")

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    ai_reply = await gemini_service.generate_response(
        user_id=user_id,
        user_message=user_text,
        is_admin=is_staff,
        username=username,
        bot=message.bot,
    )

    # 3. Если Gemini Tool Calling уже создал бронь, отправляем ответ и завершаем
    recent_booking_exists = await has_recent_booking_any_time(user_id=user_id, seconds=30)
    if recent_booking_exists:
        logger.info("Text booking handled via Gemini Tool Calling for user %s; skipping legacy intent extraction.", user_id)
        await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))
        return

    # 4. Проверка и выполнение намерения
    handled_by_intent = False
    if is_staff:
        handled_by_intent = await process_admin_intent(message, admin_query=user_text, ai_reply=ai_reply)
    else:
        handled_by_intent = await process_client_intent(message, client_query=user_text, ai_reply=ai_reply)

    # 5. Отправка ответа пользователю, если действие не было обработано специализированным хэндлером
    if not handled_by_intent:
        await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))


async def process_client_intent(message: Message, client_query: str, ai_reply: str) -> None:
    """
    Интеллектуальная обработка намерений клиента:
    1. Отмена своей брони ('отмени запись', 'не смогу прийти')
    2. Создание новой записи с защитой от спама и коллизий
    """
    user_id = message.from_user.id
    combined = f"{client_query} {ai_reply}".lower()
    client_triggers = [
        "запис", "бронь", "жд", "время", "имя", "завтра", "сегодня", "ок", "договор",
        "отмен", "не смогу", "не приду", "yoz", "kut", "soat", "ism", "kun",
        "ertaga", "bugun", "kel", "vaqt", "bekor", "00", "30"
    ]
    if not any(t in combined for t in client_triggers):
        return

    recent_msgs = await get_recent_messages(user_id=user_id, limit=6)
    dialog_lines = [f"{'Клиент' if m['role'] == 'user' else 'Иброхим'}: {m['text']}" for m in recent_msgs]
    dialog_sample = "\n".join(dialog_lines) if dialog_lines else f"Клиент: {client_query}\nИброхим: {ai_reply}"

    client_action = await gemini_service.extract_client_action(dialog_sample)
    if not client_action:
        extracted = await gemini_service.extract_booking_info(dialog_sample)
        if extracted:
            from services.gemini_service import ClientAction
            client_action = ClientAction(
                action="create_booking" if extracted.is_booking_confirmed else "chat",
                is_booking_confirmed=extracted.is_booking_confirmed,
                customer_name=extracted.customer_name,
                phone=extracted.phone,
                service=extracted.service,
                booking_time=extracted.booking_time,
            )
    if not client_action:
        return

    # 1. КЛИЕНТ ОТМЕНЯЕТ СВОЮ ЗАПИСЬ
    if client_action.action == "cancel_my_booking":
        cancelled = await cancel_user_booking(user_id)
        if cancelled:
            logger.info(f"Клиент {user_id} отменил запись {cancelled['id']} ({cancelled['booking_time']})")
            if settings.ADMIN_CHAT_ID:
                try:
                    await message.bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=(
                            f"🔔 **ОТМЕНА ЗАПИСИ (через чат с ИИ):**\n\n"
                            f"👤 **Клиент:** {cancelled['full_name']} (@{message.from_user.username or 'нет'})\n"
                            f"✂️ **Услуга:** {cancelled['service']}\n"
                            f"⏰ **Было забронировано:** {cancelled['booking_time']}\n\n"
                            "🟢 _Окно снова открыто для записи!_"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление админу об отмене: {e}")
        return

    # 2. КЛИЕНТ СОЗДАЕТ НОВУЮ ЗАПИСЬ
    if client_action.action == "create_booking" and client_action.is_booking_confirmed:
        raw_booking_time = client_action.booking_time or "Уточнено в диалоге"
        target_dt = parse_booking_datetime(raw_booking_time)
        if not target_dt:
            return

        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True)
        matched_barber = resolve_barber_match(
            query_or_name=client_action.barber_name,
            barbers=barbers,
            fallback_text=f"{client_query} {ai_reply}"
        )

        active_user_b = await get_user_active_booking(user_id)
        if active_user_b:
            b_time = active_user_b.get("booking_time", "")
            if is_super_admin(user_id) or (matched_barber and active_user_b.get("barber_id") != matched_barber.get("id")):
                await cancel_user_booking(user_id)
            elif b_time == raw_booking_time or await has_recent_booking(user_id=user_id, booking_time=b_time):
                return
            else:
                await message.answer(
                    f"ℹ️ **Ака, у вас уже есть активная запись на {b_time} ({active_user_b.get('service')})!**\n\n"
                    "Если хотите перенести её на другое время, просто напишите мне: *«Перенеси на другое время»* или *«Отмени старую запись»*, и я всё сделаю! 💈",
                    parse_mode="Markdown"
                )
                return

        matched_service, srv_duration = await resolve_service_info(client_action.service)
        canonical_service = matched_service["name"] if matched_service else (client_action.service or "Стрижка / Комплекс")
        matched_service_id = matched_service["id"] if matched_service else None

        res = check_slot_availability(
            target_dt=target_dt,
            existing_bookings=active_bookings,
            barber_id=matched_barber["id"] if matched_barber else None,
            available_barbers=barbers,
            duration_minutes=srv_duration
        )
        if not res.is_available:
            logger.warning(f"Отклонена запись на {raw_booking_time}: {res.error_message}")
            await message.answer(
                f"⚠️ **Ака, на это время ({raw_booking_time}) записать не могу!**\n\n"
                f"{res.error_message}\n\n"
                "Выбери другое свободное окошко на этой неделе, и мы всё оформим! 💈",
                parse_mode="Markdown",
            )
            return

        booking_time = format_booking_datetime(target_dt)
        full_name = (
            client_action.customer_name
            or (f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip())
            or ("Шеф" if is_super_admin(user_id) else "Гость")
        )
        username = f"@{message.from_user.username}" if message.from_user.username else None
        phone = client_action.phone or None
        final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
        final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

        target_branch_id, target_branch_name, branch_address = await resolve_branch(
            query_text=f"{client_query} {ai_reply}",
            branch_id=getattr(client_action, "branch_id", None),
            branch_name=getattr(client_action, "branch_name", None),
        )

        barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None) if final_barber_id else matched_barber
        is_vip_master = is_barber_vip(barber_obj) if barber_obj else False
        has_vip_protection = await is_feature_enabled("has_vip_protection")
        booking_status = "pending_vip" if (is_vip_master and has_vip_protection) else "new"

        try:
            booking_id = await save_booking(
                user_id=user_id,
                username=username,
                full_name=full_name,
                details=f"Запрос: {client_query} | Ответ: {ai_reply[:200]} | Филиал: {target_branch_name}",
                phone=phone,
                service=canonical_service,
                booking_time=booking_time,
                status=booking_status,
                barber_id=final_barber_id,
                barber_name=final_barber_name,
                service_id=matched_service_id,
                branch_id=target_branch_id,
                branch_name=target_branch_name,
            )
        except BookingConflictError as bce:
            logger.warning(f"Booking collision in client_router: {bce}")
            await message.answer(
                f"⚠️ **Ака, на это время ({booking_time}) уже успели записаться!**\n\n"
                f"Мастер **{final_barber_name}** в этот момент уже занят.\n"
                "Пожалуйста, выбери другое удобное окошко или другого мастера! 💈",
                parse_mode="Markdown"
            )
            return

        if booking_status == "pending_vip":
            await dispatch_vip_approval_card(
                bot=message.bot,
                booking_id=booking_id,
                barber_obj=barber_obj,
                final_barber_name=final_barber_name,
                full_name=full_name,
                username=username,
                user_id=user_id,
                canonical_service=canonical_service,
                booking_time=booking_time,
                phone=phone,
                target_branch_name=target_branch_name,
                branch_address=branch_address,
            )
            safe_barber_name = html.escape(str(final_barber_name or "Шеф"))
            safe_branch_name = html.escape(str(target_branch_name or "Миллий бог"))
            safe_service = html.escape(str(canonical_service or "Стрижка"))
            safe_time = html.escape(str(booking_time or ""))
            client_msg_html = (
                f"👑 <b>Запрос к Шеф-мастеру {safe_barber_name} отправлен на личное согласование!</b>\n\n"
                f"⏰ <b>Время:</b> {safe_time}\n"
                f"✂️ <b>Услуга:</b> {safe_service}\n"
                f"📍 <b>Филиал:</b> {safe_branch_name}\n\n"
                "Шеф лично согласует слот в течение 15 минут, и вам сразу придет подтверждение здесь в чате! 🙏"
            )
            try:
                await message.answer(
                    client_msg_html,
                    parse_mode="HTML",
                    reply_markup=AI_BUSINESS_KEYBOARD,
                )
            except Exception:
                await message.answer(
                    f"👑 Запрос к Шеф-мастеру {final_barber_name} отправлен на личное согласование!\n\n"
                    f"⏰ Время: {booking_time}\n"
                    f"✂️ Услуга: {canonical_service}\n"
                    f"📍 Филиал: {target_branch_name}\n\n"
                    "Шеф лично согласует слот в течение 15 минут, и вам сразу придет подтверждение здесь в чате! 🙏",
                    parse_mode=None,
                    reply_markup=AI_BUSINESS_KEYBOARD,
                )
            return

        if settings.ADMIN_CHAT_ID:
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_card = (
                f"💈 **НОВАЯ ЗАПИСЬ В {settings.SALON_SHORT_NAME.upper()}!**\n\n"
                f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
                f"{master_str}"
                f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                f"✂️ **Услуга:** {canonical_service}\n"
                f"⏰ **Время:** {booking_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n"
                f"🆔 **Telegram ID:** `{user_id}`\n\n"
                "📱 _Запись доступна в панели управления (TMA)!_"
            )
            action_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bstatus:{booking_id}:confirmed"),
                        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bstatus:{booking_id}:cancelled"),
                    ]
                ]
            )
            try:
                await message.bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_card,
                    parse_mode="Markdown",
                    reply_markup=action_kb,
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление администратору: {e}")

        if final_barber_id and barber_obj and barber_obj.get("telegram_id"):
            action_kb_barber = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bstatus:{booking_id}:confirmed"),
                        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bstatus:{booking_id}:cancelled"),
                    ]
                ]
            )
            try:
                await message.bot.send_message(
                    chat_id=barber_obj["telegram_id"],
                    text=(
                        f"💈 **У ВАС НОВАЯ ЗАПИСЬ В {settings.SALON_SHORT_NAME.upper()}!**\n\n"
                        f"👤 **Клиент:** {full_name}\n"
                        f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                        f"✂️ **Услуга:** {canonical_service}\n"
                        f"⏰ **Время:** {booking_time}\n"
                        f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                        "📱 _Подтвердите или отклоните прямо кнопками ниже:_"
                    ),
                    parse_mode="Markdown",
                    reply_markup=action_kb_barber,
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить барбера {barber_obj['name']}: {e}")

        await message.answer(
            f"🤝 Мы ценим ваше доверие! До встречи в {settings.SALON_SHORT_NAME}.",
            reply_markup=AI_BUSINESS_KEYBOARD,
        )


@router.callback_query(F.data.startswith("confirm_visit:"))
async def handle_confirm_visit_callback(callback: CallbackQuery):
    """Клиент подтверждает визит за 2 часа до записи по пуш-уведомлению."""
    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        await confirm_booking_by_client(booking_id)

        try:
            old_text = callback.message.text or ""
            await callback.message.edit_text(
                f"{old_text}\n\n✅ **Явка подтверждена!** Мы ждем вас в назначенное время! 💈",
                reply_markup=None,
                parse_mode="Markdown"
            )
        except Exception:
            pass

        client_name = booking.get("full_name") or "Клиент"
        b_time = booking.get("booking_time") or ""
        b_name = booking.get("barber_name") or "Мастер"
        barber_id = booking.get("barber_id")

        alert_text = (
            f"✅ **Клиент подтвердил явку за 2 часа!**\n\n"
            f"👤 Клиент: **{client_name}**\n"
            f"⏰ Время: **{b_time}**\n"
            f"💈 Мастер: {b_name}\n"
            f"📍 Филиал: {booking.get('branch_name', settings.SALON_SHORT_NAME)}"
        )

        notified = False
        if barber_id:
            barber = await get_barber_by_id(barber_id)
            if barber and barber.get("telegram_id"):
                try:
                    await callback.bot.send_message(chat_id=barber["telegram_id"], text=alert_text, parse_mode="Markdown")
                    notified = True
                except Exception as e:
                    logger.warning("Failed to notify barber on confirm_visit: %s", e)

        if not notified and settings.ADMIN_CHAT_ID:
            try:
                await callback.bot.send_message(chat_id=settings.ADMIN_CHAT_ID, text=alert_text, parse_mode="Markdown")
            except Exception as e:
                logger.warning("Failed to notify admin on confirm_visit: %s", e)

        await callback.answer("Явка подтверждена! Ждем вас 💈")
    except Exception as e:
        logger.error("Error in handle_confirm_visit_callback: %s", e)
        await callback.answer("Произошла ошибка при подтверждении.", show_alert=True)


@router.callback_query(F.data.startswith("cancel_visit:"))
async def handle_cancel_visit_callback(callback: CallbackQuery):
    """Клиент сообщает, что не сможет прийти (за 2 часа): слот освобождается и предлагается перезапись."""
    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        await cancel_booking_by_client(booking_id)

        base_url = (getattr(settings, "WEBAPP_URL", "") or "").rstrip("/")
        has_https = base_url.startswith("https://")
        if has_https:
            reschedule_kb = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="📅 Выбрать другое время (TMA)", web_app=WebAppInfo(url=f"{base_url}/book"))
                ]]
            )
        else:
            reschedule_kb = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="📅 Выбрать другое время", callback_data="open_booking_dialog")
                ]]
            )

        try:
            await callback.message.edit_text(
                "❌ **Ваша запись была отменена.**\n\n"
                "Жаль, что у вас не получается прийти! Слот освобожден. "
                "Если захотите выбрать другое удобное время, нажмите кнопку ниже:",
                reply_markup=reschedule_kb,
                parse_mode="Markdown"
            )
        except Exception:
            pass

        client_name = booking.get("full_name") or "Клиент"
        b_time = booking.get("booking_time") or ""
        b_name = booking.get("barber_name") or "Мастер"
        barber_id = booking.get("barber_id")

        alert_text = (
            f"⚠️ **Клиент отменил запись за 2 часа!**\n\n"
            f"👤 Клиент: **{client_name}**\n"
            f"⏰ Время: **{b_time}** (СЛОТ СВОБОДЕН)\n"
            f"💈 Мастер: {b_name}\n"
            f"📍 Филиал: {booking.get('branch_name', settings.SALON_SHORT_NAME)}\n\n"
            "💡 _Слот снова доступен для записи других клиентов!_"
        )

        if barber_id:
            barber = await get_barber_by_id(barber_id)
            if barber and barber.get("telegram_id"):
                try:
                    await callback.bot.send_message(chat_id=barber["telegram_id"], text=alert_text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning("Failed to notify barber on cancel_visit: %s", e)

        if settings.ADMIN_CHAT_ID:
            try:
                await callback.bot.send_message(chat_id=settings.ADMIN_CHAT_ID, text=alert_text, parse_mode="Markdown")
            except Exception as e:
                logger.warning("Failed to notify admin on cancel_visit: %s", e)

        await callback.answer("Запись отменена. Слот освобожден.")
    except Exception as e:
        logger.error("Error in handle_cancel_visit_callback: %s", e)
        await callback.answer("Произошла ошибка при отмене записи.", show_alert=True)


@router.callback_query(F.data == "open_booking_dialog")
async def handle_open_booking_dialog(callback: CallbackQuery):
    """Подсказка клиенту для начала бронирования через диалог."""
    await callback.answer()
    await callback.message.answer(
        "✂️ Напишите прямо в чат, когда вам удобно прийти и к какому мастеру!\n\n"
        "Например:\n"
        "• «Запиши на завтра в 16:00 на стрижку»\n"
        "• «Хочу к Дильшоду в субботу в 14:00»",
        reply_markup=None
    )


# Обратная совместимость
process_booking_intent = process_client_intent
