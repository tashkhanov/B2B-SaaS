"""
Модульный роутер для администрации и супер-администратора (Admin Router).
Обрабатывает:
- /admin и кнопка вызова панели записей (TMA)
- /super (SaaS Multi-Tenant Command Center)
- Обработка интентов администратора (process_admin_intent: подтверждение, отмена, бронирование)
- Инлайн-кнопки изменения статуса записи (bstatus:<id>:<status>)
- Подтверждение и отклонение платежных квитанций (approve_receipt, reject_receipt)
"""

import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.filters import Command

from config import settings
from database import (
    save_booking,
    get_active_bookings,
    get_barbers,
    get_branch_by_id,
    get_salon_setting,
    get_booking_by_id,
    update_booking_status,
    find_booking_by_client_name,
    update_receipt_log_status,
    get_receipt_log_by_id,
    extend_tenant_subscription_30_days,
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
from bot.routers.common_router import is_super_admin, get_user_role

logger = logging.getLogger(__name__)

router = Router(name="admin_router")
gemini_service = GeminiService()


@router.message(Command("admin"))
@router.message(F.text == "📊 Панель записей (/admin)")
async def handle_admin(message: Message):
    """Команда /admin для вызова Telegram Mini App панели управления"""
    user_id = message.from_user.id if message.from_user else 0
    if settings.ADMIN_CHAT_ID and user_id != settings.ADMIN_CHAT_ID:
        await message.answer("Братан, эта команда только для управляющего барбершопом!")
        return

    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    if base_url.startswith("https://"):
        admin_url = f"{base_url}/admin"
        admin_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📊 Открыть панель записей (TMA)",
                        web_app=WebAppInfo(url=admin_url),
                    )
                ]
            ]
        )
        await message.answer(
            f"🛠 **Панель управления {settings.SALON_SHORT_NAME}:**\n"
            "Нажмите кнопку ниже, чтобы открыть Telegram Mini App со всеми актуальными записями клиентов.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )
    else:
        admin_url = f"{base_url}/admin" if base_url else "http://localhost:8000/admin"
        admin_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Открыть панель в браузере",
                        url=admin_url,
                    )
                ]
            ]
        )
        await message.answer(
            f"🛠 **Панель управления {settings.SALON_SHORT_NAME}:**\n\n"
            f"🔗 Локальный адрес панели: `{admin_url}`\n\n"
            "💡 *Telegram требует HTTPS для встраивания кнопок Mini App.*\n"
            "Чтобы панель открывалась прямо внутри Telegram:\n"
            "1. Запустите в новом терминале: `ngrok http 8000` (или `npx localtunnel --port 8000`)\n"
            "2. Скопируйте полученную https-ссылку (например, `https://xyz.ngrok-free.app`)\n"
            "3. Укажите её в `.env`: `WEBAPP_URL=https://xyz.ngrok-free.app` и перезапустите бота.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )


@router.message(Command("super"))
async def handle_super(message: Message):
    """Команда /super для вызова пульта управления Super-Admin (SaaS Multi-Tenant)."""
    user_id = message.from_user.id if message.from_user else 0
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    allowed = (super_admin_id and user_id == super_admin_id) or (user_id == settings.ADMIN_CHAT_ID)
    if not allowed:
        return

    hub_url = getattr(settings, "SAAS_HUB_URL", "https://hub.asatkhanov.uz")
    has_hub_https = hub_url.startswith("https://")
    super_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Открыть SaaS Command Center",
                    web_app=WebAppInfo(url=hub_url) if has_hub_https else None,
                    url=hub_url if not has_hub_https else None,
                )
            ]
        ]
    )
    await message.answer(
        "👑 **Панель управления Super-Admin (SaaS Multi-Tenant):**\n\n"
        "Здесь вы можете управлять всеми подключенными заведениями, "
        "продлевать подписки (+30 дней в 1 клик) и активировать Kill Switch.",
        reply_markup=super_keyboard,
        parse_mode="Markdown",
    )


async def process_admin_intent(message: Message, admin_query: str, ai_reply: str) -> None:
    """
    Обработка команд администратора через естественный язык или голос:
    1. Подтверждение записи клиента ('подтверди запись Рустама')
    2. Отмена записи клиента ('отмени запись Рустама')
    3. Создание записи: личная бронь Создателя (Иброхим) или ручная запись клиента
    """
    user_id = message.from_user.id if message.from_user else 0
    is_super = is_super_admin(user_id)

    combined = f"{admin_query} {ai_reply}".lower()
    admin_triggers = ["подтверд", "отмен", "запиш", "бронь", "tasdiq", "bekor", "yoz", "стрижк", "время", "окно"]
    if not any(t in combined for t in admin_triggers):
        return

    admin_action = await gemini_service.extract_admin_action(admin_query, user_id=user_id)

    # Проверка: является ли запрос личной записью Создателя / Супер-Админа (Иброхим)
    is_personal_creator_booking = False
    if is_super:
        personal_markers = [
            "меня", "мне", "себя", "запиши на", "хочу записаться",
            "meni yoz", "menga yoz", "yozib qo'y", "yozib qo'ying", "soch oldirish", "soch oldirishga"
        ]
        query_has_self = any(m in admin_query.lower() for m in personal_markers)
        client_is_self = (
            admin_action
            and (
                (admin_action.client_query or "").strip().lower() in ("меня", "мне", "себя", "иброхим", "шеф", "создатель")
                or (not admin_action.client_query and admin_action.action == "create_booking")
            )
        )
        if query_has_self or client_is_self:
            is_personal_creator_booking = True

    # Если admin_action не распознал create_booking, но Иброхим просит записать его
    if is_personal_creator_booking and (not admin_action or admin_action.action != "create_booking"):
        client_act = await gemini_service.extract_client_action(f"Клиент: {admin_query}\nАссистент: {ai_reply}", user_id=user_id)
        if client_act and (client_act.action == "create_booking" or client_act.booking_time):
            from services.gemini_service import AdminAction
            admin_action = AdminAction(
                action="create_booking",
                client_query="Иброхим",
                booking_time=client_act.booking_time,
                service=client_act.service or "Стрижка / Комплекс",
                phone=client_act.phone,
                barber_name=client_act.barber_name,
            )

    if not admin_action or admin_action.action in ("chat", "query_schedule", "get_stats"):
        return

    # 1. Администратор подтверждает запись клиента
    if admin_action.action == "confirm_booking" and admin_action.client_query:
        booking = await find_booking_by_client_name(admin_action.client_query)
        if booking and booking["status"] == "new":
            await update_booking_status(booking["id"], "confirmed")
            # Уведомляем клиента в личку Telegram
            if booking.get("user_id"):
                b_branch_id = booking.get("branch_id") or 1
                branch_info = await get_branch_by_id(b_branch_id)
                branch_addr = branch_info["address"] if branch_info else "г. Ташкент"
                branch_title = booking.get("branch_name") or (branch_info["name"] if branch_info else settings.SALON_SHORT_NAME)
                branch_phone = (branch_info.get("phone") if branch_info else None) or await get_salon_setting("salon_phone", settings.SALON_PHONE)
                try:
                    await message.bot.send_message(
                        chat_id=booking["user_id"],
                        text=(
                            f"✅ **Ваша запись в {settings.SALON_SHORT_NAME} подтверждена!**\n\n"
                            f"✂️ **Услуга:** {booking['service']}\n"
                            f"💈 **Мастер:** {booking['barber_name']}\n"
                            f"⏰ **Время:** {booking['booking_time']}\n"
                            f"📍 **Филиал:** {branch_title} ({branch_addr})\n"
                            f"📞 **Телефон:** {branch_phone}\n\n"
                            "Ждем вас в назначенное время, ака! 💈"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление клиенту о подтверждении: {e}")
            await message.answer(f"✅ Запись клиента **{booking['full_name']}** на **{booking['booking_time']}** успешно подтверждена!")
            return

    # 2. Администратор отменяет запись клиента
    if admin_action.action == "cancel_booking" and admin_action.client_query:
        booking = await find_booking_by_client_name(admin_action.client_query)
        if booking and booking["status"] != "cancelled":
            await update_booking_status(booking["id"], "cancelled")
            # Уведомляем клиента в личку Telegram
            if booking.get("user_id"):
                try:
                    await message.bot.send_message(
                        chat_id=booking["user_id"],
                        text=(
                            f"❌ **Ваша запись на {booking['booking_time']} была отменена администратором.**\n\n"
                            "Если у вас изменились планы, вы всегда можете выбрать другое время через онлайн-меню! 💈"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление клиенту об отмене: {e}")
            await message.answer(f"❌ Запись клиента **{booking['full_name']}** на **{booking['booking_time']}** отменена. Окно свободно!")
            return

    # 3. Создание записи: личная бронь Создателя или ручная запись клиента
    if admin_action.action == "create_booking" and admin_action.booking_time:
        if not admin_action.client_query and not is_personal_creator_booking:
            return

        target_dt = parse_booking_datetime(admin_action.booking_time)
        if target_dt:
            active_bookings = await get_active_bookings()
            barbers = await get_barbers(active_only=True)
            matched_barber = resolve_barber_match(
                query_or_name=admin_action.barber_name,
                barbers=barbers,
                fallback_text=f"{admin_query} {ai_reply}"
            )
            if admin_action.client_query and resolve_barber_match(admin_action.client_query, barbers):
                if not matched_barber:
                    matched_barber = resolve_barber_match(admin_action.client_query, barbers)
                admin_action.client_query = "Иброхим" if is_super else None
                is_personal_creator_booking = is_super

            matched_service, srv_duration = await resolve_service_info(admin_action.service)
            canonical_service = matched_service["name"] if matched_service else (admin_action.service or "Стрижка / Комплекс")
            matched_service_id = matched_service["id"] if matched_service else None

            res = check_slot_availability(
                target_dt=target_dt,
                existing_bookings=active_bookings,
                barber_id=matched_barber["id"] if matched_barber else None,
                available_barbers=barbers,
                duration_minutes=srv_duration
            )
            if not res.is_available:
                salutation = "Иброхим ака" if is_personal_creator_booking else "Шеф"
                await message.answer(f"⚠️ {salutation}, на это время записать нельзя: {res.error_message}")
                return

            normalized_time = format_booking_datetime(target_dt)
            final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
            final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

            # Определение филиала динамически из базы данных
            target_branch_id, target_branch_name, branch_address = await resolve_branch(
                query_text=admin_query,
                branch_id=getattr(admin_action, "branch_id", None),
                branch_name=getattr(admin_action, "branch_name", None),
            )

            barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None) if final_barber_id else matched_barber
            is_vip_master = is_barber_vip(barber_obj) if barber_obj else False
            has_vip_protection = await is_feature_enabled("has_vip_protection")
            booking_status = "pending_vip" if (is_vip_master and has_vip_protection) else "confirmed"

            if is_personal_creator_booking:
                creator_name = (f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()) if message.from_user else ""
                if "ibrohim" in creator_name.lower() or "иброхим" in creator_name.lower():
                    full_name = "Иброхим"
                else:
                    full_name = creator_name or "Иброхим"
                booking_user_id = user_id
                username = f"@{message.from_user.username}" if (message.from_user and message.from_user.username) else None
                details = f"Личная запись Создателя/Супер-Админа ({full_name}) | Филиал: {target_branch_name}"
            else:
                full_name = admin_action.client_query or "Гость"
                booking_user_id = 0
                username = None
                details = f"Ручная запись администратором через чат с ИИ | Филиал: {target_branch_name}"

            booking_id = await save_booking(
                user_id=booking_user_id,
                username=username,
                full_name=full_name,
                details=details,
                phone=admin_action.phone,
                service=canonical_service,
                booking_time=normalized_time,
                status=booking_status,
                barber_id=final_barber_id,
                barber_name=final_barber_name,
                service_id=matched_service_id,
                branch_id=target_branch_id,
                branch_name=target_branch_name,
            )

            if booking_status == "pending_vip":
                await dispatch_vip_approval_card(
                    bot=message.bot,
                    booking_id=booking_id,
                    barber_obj=barber_obj,
                    final_barber_name=final_barber_name,
                    full_name=full_name,
                    username=username,
                    user_id=booking_user_id,
                    canonical_service=canonical_service,
                    booking_time=normalized_time,
                    phone=admin_action.phone,
                    target_branch_name=target_branch_name,
                    branch_address=branch_address,
                )
                await message.answer(
                    f"👑 **Запрос к Шеф-мастеру {final_barber_name} отправлен на личное согласование!**\n\n"
                    f"⏰ **Время:** {normalized_time}\n"
                    f"✂️ **Услуга:** {canonical_service}\n"
                    f"📍 **Филиал:** {target_branch_name} ({branch_address})\n\n"
                    "Шеф лично согласует слот в течение 15 минут, и вам сразу придет подтверждение здесь в чате! 🙏",
                    parse_mode="Markdown"
                )
                return

            # Оповещение назначенному барберу в Telegram
            if barber_obj and barber_obj.get("telegram_id"):
                try:
                    title_text = "👑 **К вам записался Создатель платформы / Шеф!**" if is_personal_creator_booking else "💈 **Шеф записал к вам клиента!**"
                    await message.bot.send_message(
                        chat_id=barber_obj["telegram_id"],
                        text=(
                            f"{title_text}\n\n"
                            f"👤 **Гость:** {full_name}\n"
                            f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                            f"✂️ **Услуга:** {canonical_service}\n"
                            f"⏰ **Время:** {normalized_time}\n"
                            f"📞 **Телефон:** {admin_action.phone or 'Не указан'}"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление барберу: {e}")

            master_str = f" к мастеру **{final_barber_name}**" if final_barber_name else ""
            if is_personal_creator_booking:
                await message.answer(
                    f"👑 **Иброхим ака, вы успешно записаны!**\n\n"
                    f"✂️ **Услуга:** {canonical_service}\n"
                    f"⏰ **Время:** {normalized_time}\n"
                    f"{master_str}\n"
                    f"📍 **Филиал:** {target_branch_name} ({branch_address})\n\n"
                    f"Всегда рады видеть вас в {settings.SALON_SHORT_NAME}, Создатель! 🤝",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"💈 Запись клиента **{full_name}**{master_str} на **{normalized_time}** ({canonical_service}) в филиал **{target_branch_name}** успешно зафиксирована в графике!"
                )


@router.callback_query(F.data.startswith("bstatus:"))
async def handle_booking_status_callback(callback: CallbackQuery):
    """
    Обработка инлайн-кнопок подтверждения / отклонения записи мастером или админом:
    Формат callback_data: bstatus:<booking_id>:<new_status>
    """
    user_id = callback.from_user.id if callback.from_user else 0
    role, barber = await get_user_role(user_id)
    if role not in ("admin", "barber"):
        await callback.answer("У вас нет прав для управления этой записью.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Неверные данные действия.", show_alert=True)
        return

    try:
        booking_id = int(parts[1])
    except ValueError:
        await callback.answer("Некорректный ID записи.", show_alert=True)
        return

    new_status = parts[2]

    booking = await get_booking_by_id(booking_id)
    if not booking:
        await callback.answer("Запись не найдена в базе.", show_alert=True)
        return

    if role == "barber" and barber:
        if booking.get("barber_id") and booking.get("barber_id") != barber.get("id"):
            await callback.answer("Эта запись назначена другому мастеру.", show_alert=True)
            return

    success = await update_booking_status(booking_id, new_status)
    if not success:
        await callback.answer("Не удалось обновить статус записи.", show_alert=True)
        return

    actor_name = barber["name"] if barber else "Администратор"
    client_tg_id = booking.get("user_id")

    if client_tg_id and client_tg_id != 0:
        b_time = booking.get("booking_time") or ""
        service = booking.get("service") or "Стрижка"
        b_name = booking.get("barber_name") or (barber["name"] if barber else "Мастер")

        if new_status == "confirmed":
            b_branch_id = booking.get("branch_id") or 1
            branch_info = await get_branch_by_id(b_branch_id)
            branch_addr = branch_info["address"] if branch_info else "г. Ташкент"
            branch_title = booking.get("branch_name") or (branch_info["name"] if branch_info else settings.SALON_SHORT_NAME)
            branch_phone = (branch_info.get("phone") if branch_info else None) or await get_salon_setting("salon_phone", settings.SALON_PHONE)
            msg = (
                f"✅ **Ваша запись в {settings.SALON_SHORT_NAME} ПОДТВЕРЖДЕНА!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"💈 **Мастер:** {b_name}\n"
                f"⏰ **Время:** {b_time}\n"
                f"📍 **Филиал:** {branch_title} ({branch_addr})\n"
                f"📞 **Телефон:** {branch_phone}\n\n"
                "Мы уже ждем вас к назначенному времени, ака! 💈"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Не удалось отправить клиенту уведомление: {e}")
        elif new_status == "cancelled":
            msg = (
                f"ℹ️ **Ваша запись в {settings.SALON_SHORT_NAME} была отменена.**\n\n"
                f"⏰ Время: {b_time}\n"
                f"✂️ Услуга: {service}\n\n"
                "Если вы хотите выбрать другое время или мастера — просто откройте онлайн-меню или напишите мне в чат."
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Не удалось отправить клиенту уведомление: {e}")

    status_ru = "✅ ПОДТВЕРЖДЕНА" if new_status == "confirmed" else "❌ ОТМЕНЕНА"
    try:
        old_text = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            f"{old_text}\n\n👉 **Статус:** {status_ru} (кем: {actor_name})",
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer(f"Запись успешно {status_ru.lower()}!")


@router.callback_query(F.data.startswith("approve_receipt:"))
async def handle_approve_receipt(callback: CallbackQuery):
    """Суперадмин подтверждает оплату чека и начисляет +30 дней подписки"""
    user_id = callback.from_user.id if callback.from_user else 0
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if user_id != super_admin_id and user_id != admin_id:
        await callback.answer("У вас нет прав для подтверждения оплат.", show_alert=True)
        return

    try:
        receipt_id = int(callback.data.split(":")[1])
        receipt = await get_receipt_log_by_id(receipt_id)
        if not receipt:
            await callback.answer("Чек не найден в базе данных.", show_alert=True)
            return

        if receipt.get("status") == "approved":
            await callback.answer("Этот чек уже был подтвержден ранее.", show_alert=True)
            return

        await update_receipt_log_status(receipt_id, "approved")
        tenant_id = receipt.get("tenant_id") or 1
        await extend_tenant_subscription_30_days(tenant_id)

        old_text = callback.message.text or callback.message.caption or ""
        new_text = (
            f"{old_text}\n\n"
            "✅ **ОПЛАТА ПОДТВЕРЖДЕНА!**\n"
            f"🎉 Лицензия заведения {settings.SALON_SHORT_NAME} успешно продлена на **+30 дней**!\n"
            f"👤 Подтвердил: {callback.from_user.first_name}"
        )
        await callback.message.edit_text(new_text, reply_markup=None, parse_mode="Markdown")
        await callback.answer("Оплата подтверждена! Подписка продлена на 30 дней.")
    except Exception as e:
        logger.error(f"Ошибка при подтверждении чека: {e}")
        await callback.answer("Произошла ошибка при обработке чека.", show_alert=True)


@router.callback_query(F.data.startswith("reject_receipt:"))
async def handle_reject_receipt(callback: CallbackQuery):
    """Суперадмин отклоняет чек"""
    user_id = callback.from_user.id if callback.from_user else 0
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if user_id != super_admin_id and user_id != admin_id:
        await callback.answer("У вас нет прав для отклонения оплат.", show_alert=True)
        return

    try:
        receipt_id = int(callback.data.split(":")[1])
        await update_receipt_log_status(receipt_id, "rejected")

        old_text = callback.message.text or callback.message.caption or ""
        new_text = (
            f"{old_text}\n\n"
            "❌ **ОПЛАТА ОТКЛОНЕНА!**\n"
            f"👤 Отклонил: {callback.from_user.first_name}"
        )
        await callback.message.edit_text(new_text, reply_markup=None, parse_mode="Markdown")
        await callback.answer("Чек успешно отклонён.")
    except Exception as e:
        logger.error(f"Ошибка при отклонении чека: {e}")
        await callback.answer("Произошла ошибка при отклонении чека.", show_alert=True)
