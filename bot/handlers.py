import io
import logging
from aiogram import Router, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction

from config import settings
from database import (
    clear_history,
    save_booking,
    has_recent_booking,
    get_recent_messages,
    get_active_bookings,
    get_barbers,
    get_barber_by_telegram_id,
    get_barber_by_id,
)
from services.gemini_service import GeminiService
from services.schedule_service import (
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
)

logger = logging.getLogger(__name__)

router = Router(name="main_router")

# Инициализация сервиса Gemini
gemini_service = GeminiService()

# Инлайн-кнопка для брендинга после записи
AI_BUSINESS_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🤖 Подключить ИИ в свой бизнес",
                url="https://t.me/asatkhanov",
            )
        ]
    ]
)


async def get_user_role(user_id: int):
    """Определение роли: 'admin', 'barber', или 'client'."""
    if settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID:
        return "admin", None
    barber = await get_barber_by_telegram_id(user_id)
    if barber:
        return "barber", barber
    return "client", None


def get_main_keyboard(role: str = "client", barber: dict = None, is_admin: bool = None) -> ReplyKeyboardMarkup:
    """Генерация основной клавиатуры для клиентов, администратора и мастеров (барберов)"""
    if is_admin is not None:
        role = "admin" if is_admin else (role or "client")
    if not role:
        role = "client"

    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    has_https = base_url.startswith("https://")

    if role == "admin":
        keyboard = [
            [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
            [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Сбросить диалог")],
        ]
        if has_https:
            keyboard.append([
                KeyboardButton(
                    text="📊 Панель записей (TMA)",
                    web_app=WebAppInfo(url=f"{base_url}/admin"),
                )
            ])
        else:
            keyboard.append([KeyboardButton(text="📊 Панель записей (/admin)")])

    elif role == "barber":
        # Для барбера — прямая кнопка в его личный кабинет (Моя смена)
        if has_https:
            keyboard = [
                [
                    KeyboardButton(
                        text="💈 Моя смена (Кабинет)",
                        web_app=WebAppInfo(url=f"{base_url}/barber"),
                    ),
                    KeyboardButton(text="✂️ Прайс-лист"),
                ],
                [KeyboardButton(text="📍 Где мы находимся?"), KeyboardButton(text="🔄 Сбросить диалог")],
            ]
        else:
            keyboard = [
                [KeyboardButton(text="💈 Моя смена (/barber)"), KeyboardButton(text="✂️ Прайс-лист")],
                [KeyboardButton(text="📍 Где мы находимся?"), KeyboardButton(text="🔄 Сбросить диалог")],
            ]

    else:
        # Для обычных клиентов — кнопка онлайн-записи через Client Mini App
        if has_https:
            keyboard = [
                [
                    KeyboardButton(
                        text="📅 Онлайн-запись (TMA)",
                        web_app=WebAppInfo(url=f"{base_url}/book"),
                    ),
                    KeyboardButton(text="✂️ Прайс-лист"),
                ],
                [KeyboardButton(text="📍 Где мы находимся?"), KeyboardButton(text="🔄 Сбросить диалог")],
            ]
        else:
            keyboard = [
                [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
                [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Сбросить диалог")],
            ]

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=True)


async def update_chat_menu_button(message: Message, role: str):
    """Настройка постоянной кнопки 'Menu' в левом нижнем углу Telegram под роль пользователя"""
    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    if not base_url.startswith("https://") or not message.bot or not hasattr(message.bot, "set_chat_menu_button"):
        return
    try:
        if role == "admin":
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(text="Панель", web_app=WebAppInfo(url=f"{base_url}/admin"))
            )
        elif role == "barber":
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(text="Моя смена", web_app=WebAppInfo(url=f"{base_url}/barber"))
            )
        else:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(text="Записаться", web_app=WebAppInfo(url=f"{base_url}/book"))
            )
    except Exception as e:
        logger.debug(f"Не удалось обновить menu button: {e}")


def get_start_inline_keyboard(role: str = "client", barber: dict = None) -> InlineKeyboardMarkup:
    """
    Генерация прозрачной inline-кнопки, прикрепленной прямо под сообщением /start,
    для открытия соответствующего Telegram Mini App меню.
    """
    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    has_https = base_url.startswith("https://")

    if role == "admin":
        url = f"{base_url}/admin" if base_url else "http://localhost:8000/admin"
        btn = (
            InlineKeyboardButton(text="📊 Открыть панель управления", web_app=WebAppInfo(url=url))
            if has_https
            else InlineKeyboardButton(text="📊 Открыть панель управления", url=url)
        )
    elif role == "barber":
        url = f"{base_url}/barber" if base_url else "http://localhost:8000/barber"
        btn = (
            InlineKeyboardButton(text="💈 Моя смена (Кабинет)", web_app=WebAppInfo(url=url))
            if has_https
            else InlineKeyboardButton(text="💈 Моя смена (Кабинет)", url=url)
        )
    else:
        url = f"{base_url}/book" if base_url else "http://localhost:8000/book"
        btn = (
            InlineKeyboardButton(text="✂️ Открыть меню записи", web_app=WebAppInfo(url=url))
            if has_https
            else InlineKeyboardButton(text="✂️ Открыть меню записи", url=url)
        )

    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.message(CommandStart())
async def handle_start(message: Message):
    """
    Обработчик команды /start с автоопределением роли (админ, барбер, клиент)
    и прозрачной inline-кнопкой под сообщением для открытия веб-меню.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    await update_chat_menu_button(message, role)

    if role == "admin":
        welcome_text = (
            "Салам алейкум, Управляющий! 👑\n\n"
            "Вы вошли в систему как **Администратор** барбершопа Black Beard.\n"
            "Вам доступно голосовое и текстовое управление записями, а также кнопка **«📊 Открыть панель управления»** прямо под этим сообщением!"
        )
    elif role == "barber":
        b_role = barber.get("role_title") or barber.get("role") or "Барбер"
        welcome_text = (
            f"Салам алейкум, {barber['name']}! 💈\n\n"
            f"Вы авторизованы как мастер барбершопа Black Beard (**{b_role}**).\n\n"
            "Прямо под этим сообщением для вас доступна кнопка **«💈 Моя смена (Кабинет)»** — "
            "нажмите её, чтобы посмотреть очередь клиентов на сегодня или изменить статус смены!"
        )
    else:
        user_name = message.from_user.first_name if message.from_user else "братан"
        welcome_text = (
            f"Салам алейкум, {user_name}! 🤝\n\n"
            "Добро пожаловать в премиум-барбершоп **Black Beard** в Ташкенте!\n"
            "Я Иброхим — администратор. Нажмите кнопку **«✂️ Открыть меню записи»** под сообщением, "
            "чтобы выбрать мастера и время, либо напишите или отправьте голосовое сообщение прямо сюда в чат!"
        )

    await message.answer(
        welcome_text,
        reply_markup=get_start_inline_keyboard(role=role, barber=barber),
        parse_mode="Markdown"
    )


@router.message(Command("about"))
async def handle_about(message: Message):
    """
    Информация об авторстве и коммерческой разработке AI-систем
    """
    about_text = (
        "💈 **AI-Администратор для Барбершопа 'Black Beard' (Ташкент)**\n\n"
        "Интеллектуальная B2B-система автоматизации первой линии клиентов: "
        "консультирование по прайсу, обработка голоса на русском и узбекском языках, "
        "интеллектуальная память контекста и панель управления Telegram Mini App.\n\n"
        "👨‍💻 **Разработка:** Асатханов (@asatkhanov)\n"
        "⚡ **Платформа:** Khan AI Systems\n"
        "🌐 **Сайт / Портфолио:** [asatkhanov.uz](https://asatkhanov.uz)\n\n"
        "_Хотите автоматизировать продажи или клиентский сервис в вашем бизнесе с помощью ИИ?_"
    )
    about_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Написать разработчику", url="https://t.me/asatkhanov"),
                InlineKeyboardButton(text="🌐 asatkhanov.uz", url="https://asatkhanov.uz"),
            ]
        ]
    )
    await message.answer(about_text, reply_markup=about_keyboard, parse_mode="Markdown")


@router.message(Command("admin"))
@router.message(F.text == "📊 Панель записей (/admin)")
async def handle_admin(message: Message):
    """
    Команда /admin для вызова Telegram Mini App панели управления
    """
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
            "🛠 **Панель управления барбершопом Black Beard:**\n"
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
            "🛠 **Панель управления барбершопом Black Beard:**\n\n"
            f"🔗 Локальный адрес панели: `{admin_url}`\n\n"
            "💡 *Telegram требует HTTPS для встраивания кнопок Mini App.*\n"
            "Чтобы панель открывалась прямо внутри Telegram:\n"
            "1. Запустите в новом терминале бесплатный туннель: `ngrok http 8000` (или `npx localtunnel --port 8000`)\n"
            "2. Скопируйте полученную https-ссылку (например, `https://xyz.ngrok-free.app`)\n"
            "3. Укажите её в `.env`: `WEBAPP_URL=https://xyz.ngrok-free.app` и перезапустите бота.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )


@router.message(Command("barber"))
async def handle_barber(message: Message):
    """
    Команда /barber для вызова TMA кабинета мастера ('Моя смена').
    Доступна барберам салона и администратору.
    """
    user_id = message.from_user.id if message.from_user else 0
    barber = await get_barber_by_telegram_id(user_id)
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)

    if not barber and not is_admin:
        await message.answer(
            "⚠️ Ваш Telegram ID не привязан ни к одному мастеру салона.\n"
            "Попросите администратора указать ваш Telegram ID в панели управления!"
        )
        return

    base_url = (settings.WEBAPP_URL or "").rstrip("/")
    b_name = barber["name"] if barber else "Администратор"

    if base_url.startswith("https://"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💈 Открыть Мою Смену (TMA)",
                        web_app=WebAppInfo(url=f"{base_url}/barber"),
                    )
                ]
            ]
        )
        await message.answer(
            f"Салам, {b_name}! 💈\n\n"
            "В кабинете мастера вы можете управлять статусом смены ('На смене / Отдыхаю') и видеть всех своих клиентов на сегодня.",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    else:
        barber_url = f"{base_url}/barber" if base_url else "http://localhost:8000/barber"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть в браузере", url=barber_url)]]
        )
        await message.answer(
            f"Салам, {b_name}! 💈\n"
            f"Ссылка на кабинет смены: `{barber_url}`",
            reply_markup=kb,
            parse_mode="Markdown"
        )


@router.message(Command("reset"))
@router.message(F.text == "🔄 Сбросить диалог")
async def handle_reset(message: Message):
    """
    Очистка контекста диалога текущего пользователя в базе данных.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    await clear_history(user_id)
    await message.answer(
        "История диалога очищена, братан! Начнем с чистого листа. О чем поболтаем? 💈",
        reply_markup=get_main_keyboard(role=role, barber=barber),
    )


@router.message(Command("price"))
@router.message(F.text == "✂️ Прайс-лист")
async def handle_price(message: Message):
    """
    Быстрый просмотр прайс-листа без ожидания нейросети.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    price_text = (
        "🔥 **Прайс-лист барбершопа 'Black Beard' (Ташкент):**\n\n"
        "✂️ **Мужская стрижка:** 100 000 сум\n"
        "🧔 **Оформление бороды:** 50 000 сум\n"
        "👑 **Комплекс 'Black Beard' (Стрижка + Борода):** 130 000 сум *(Выгодный сет!)*\n\n"
        "_В каждую услугу включены мытье головы, премиум-стайлинг и ароматный кофе или чай._ ☕\n\n"
        "На какой день и время вас записать, ака?"
    )
    await message.answer(price_text, parse_mode="Markdown", reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """
    Информация о локации барбершопа.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    location_text = (
        "📍 Мы находимся в центре Ташкента: ул. Амира Темура (ориентир — гостиница 'Узбекистан').\n\n"
        "Работаем ежедневно с 10:00 до 22:00 без выходных. Ждем в гости, ака!"
    )
    await message.answer(location_text, reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.voice)
async def handle_voice_message(message: Message):
    """
    Обработчик голосовых сообщений:
    1. Скачивает аудиофайл (OGG Opus) в память.
    2. Передает байты напрямую в Google GenAI SDK (Gemini).
    3. Отправляет клиенту/мастеру/админу сгенерированный ответ.
    4. Проверяет наличие намерения записи или управления.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    is_admin = role == "admin"

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)

    # 1. Скачивание аудиофайла голосового сообщения
    file_bytes_io = io.BytesIO()
    try:
        voice = message.voice
        file_info = await message.bot.get_file(voice.file_id)
        await message.bot.download_file(file_info.file_path, destination=file_bytes_io)
        voice_data = file_bytes_io.getvalue()
    except Exception as e:
        logger.error(f"Не удалось скачать голосовое сообщение: {e}")
        await message.answer("Братан, голосовое не загрузилось, отправь еще раз, пожалуйста!")
        return
    finally:
        file_bytes_io.close()

    # 2. Обработка голоса через Gemini с учетом роли
    ai_reply, user_transcript = await gemini_service.generate_response_from_voice(
        user_id=user_id,
        voice_bytes=voice_data,
        mime_type="audio/ogg",
        is_admin=is_admin,
    )

    # 3. Отправка ответа пользователю
    await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))

    # 4. Проверка и выполнение интента (для клиента или администратора)
    if is_admin:
        await process_admin_intent(message, admin_query=f"[🎙 Голос]: {user_transcript}", ai_reply=ai_reply)
    else:
        await process_client_intent(message, client_query=f"[🎙 Голос]: {user_transcript}", ai_reply=ai_reply)


@router.message(F.text)
async def handle_user_text(message: Message):
    """
    Основной обработчик текстовых сообщений:
    1. Показывает статус 'печатает...' (typing action) в Telegram.
    2. Отправляет запрос в GeminiService с историей сообщений и учетом роли.
    3. Отправляет ответ пользователю.
    4. При фиксации намерения выполняет соответствующее действие.
    """
    user_id = message.from_user.id if message.from_user else 0
    user_text = message.text.strip()
    role, barber = await get_user_role(user_id)
    is_admin = role == "admin"

    # 1. Индикация "печатает..." во время запроса к AI
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 2. Получение ответа от Gemini с учетом роли
    ai_reply = await gemini_service.generate_response(user_id=user_id, user_message=user_text, is_admin=is_admin)

    # 3. Отправка ответа пользователю
    await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))

    # 4. Проверка и выполнение намерения
    if is_admin:
        await process_admin_intent(message, admin_query=user_text, ai_reply=ai_reply)
    else:
        await process_client_intent(message, client_query=user_text, ai_reply=ai_reply)


async def process_admin_intent(message: Message, admin_query: str, ai_reply: str) -> None:
    """
    Обработка команд администратора через естественный язык или голос:
    1. Подтверждение записи клиента ('подтверди запись Рустама')
    2. Отмена записи клиента ('отмени запись Рустама')
    3. Ручное создание брони на клиента
    """
    from database import find_booking_by_client_name, update_booking_status, save_booking

    combined = f"{admin_query} {ai_reply}".lower()
    admin_triggers = ["подтверд", "отмен", "запиш", "бронь", "tasdiq", "bekor", "yoz"]
    if not any(t in combined for t in admin_triggers):
        return

    admin_action = await gemini_service.extract_admin_action(admin_query)
    if not admin_action or admin_action.action in ("chat", "query_schedule", "get_stats"):
        return

    # 1. Администратор подтверждает запись клиента
    if admin_action.action == "confirm_booking" and admin_action.client_query:
        booking = await find_booking_by_client_name(admin_action.client_query)
        if booking and booking["status"] == "new":
            await update_booking_status(booking["id"], "confirmed")
            # Уведомляем клиента в личку Telegram
            if booking.get("user_id"):
                try:
                    await message.bot.send_message(
                        chat_id=booking["user_id"],
                        text=(
                            f"✅ **Ваша запись в Black Beard подтверждена!**\n\n"
                            f"✂️ **Услуга:** {booking['service']}\n"
                            f"⏰ **Время:** {booking['booking_time']}\n"
                            f"📍 **Адрес:** ул. Амира Темура\n\n"
                            "Ждем вас, ака! 💈"
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

    # 3. Администратор вручную создает запись на клиента
    if admin_action.action == "create_booking" and admin_action.booking_time and admin_action.client_query:
        target_dt = parse_booking_datetime(admin_action.booking_time)
        if target_dt:
            active_bookings = await get_active_bookings()
            barbers = await get_barbers(active_only=True)
            matched_barber = None
            if admin_action.barber_name:
                matched_barber = next(
                    (b for b in barbers if b["name"].lower() in admin_action.barber_name.lower()),
                    None
                )

            res = check_slot_availability(
                target_dt=target_dt,
                existing_bookings=active_bookings,
                barber_id=matched_barber["id"] if matched_barber else None,
                available_barbers=barbers
            )
            if not res.is_available:
                await message.answer(f"⚠️ Шеф, на это время записать нельзя: {res.error_message}")
                return

            normalized_time = format_booking_datetime(target_dt)
            final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
            final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

            await save_booking(
                user_id=0,
                username=None,
                full_name=admin_action.client_query,
                details="Ручная запись администратором через чат с ИИ",
                phone=admin_action.phone,
                service=admin_action.service or "Стрижка / Комплекс",
                booking_time=normalized_time,
                status="confirmed",
                barber_id=final_barber_id,
                barber_name=final_barber_name,
            )

            # Оповещение назначенному барберу в Telegram
            if matched_barber and matched_barber.get("telegram_id"):
                try:
                    await message.bot.send_message(
                        chat_id=matched_barber["telegram_id"],
                        text=(
                            f"💈 **Шеф записал к вам клиента!**\n\n"
                            f"👤 **Клиент:** {admin_action.client_query}\n"
                            f"✂️ **Услуга:** {admin_action.service or 'Стрижка'}\n"
                            f"⏰ **Время:** {normalized_time}\n"
                            f"📞 **Телефон:** {admin_action.phone or 'Не указан'}"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление барберу: {e}")

            master_str = f" к мастеру **{final_barber_name}**" if final_barber_name else ""
            await message.answer(
                f"💈 Запись клиента **{admin_action.client_query}**{master_str} на **{normalized_time}** ({admin_action.service or 'Стрижка'}) успешно зафиксирована в графике!"
            )


async def process_client_intent(message: Message, client_query: str, ai_reply: str) -> None:
    """
    Интеллектуальная обработка намерений клиента:
    1. Отмена своей брони ('отмени запись', 'не смогу прийти')
    2. Проверка своей брони ('когда я записан?')
    3. Создание новой записи с защитой от спама и горизонтом 7 дней
    """
    from database import (
        get_user_active_booking,
        cancel_user_booking,
        has_recent_booking,
    )

    user_id = message.from_user.id
    combined = f"{client_query} {ai_reply}".lower()
    client_triggers = [
        "запис", "бронь", "жд", "время", "имя", "завтра", "сегодня", "ок", "договор",
        "отмен", "не смогу", "не приду", "yoz", "kut", "soat", "ism", "kun",
        "ertaga", "bugun", "kel", "vaqt", "bekor", "00", "30"
    ]
    if not any(t in combined for t in client_triggers):
        return

    # Извлекаем контекст последних сообщений
    recent_msgs = await get_recent_messages(user_id=user_id, limit=6)
    dialog_lines = [f"{'Клиент' if m['role'] == 'user' else 'Иброхим'}: {m['text']}" for m in recent_msgs]
    dialog_sample = "\n".join(dialog_lines) if dialog_lines else f"Клиент: {client_query}\nИброхим: {ai_reply}"

    client_action = await gemini_service.extract_client_action(dialog_sample)
    if not client_action:
        # Fallback на extract_booking_info (для совместимости)
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
            # Уведомляем администратора в личку
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

        # ЗАЩИТА ОТ СПАМА: проверяем, нет ли уже активной предстоящей брони у этого клиента
        active_user_b = await get_user_active_booking(user_id)
        if active_user_b:
            b_time = active_user_b.get("booking_time", "")
            # Если это та же самая бронь (повтор в диалоге), не дублируем
            if b_time == raw_booking_time or await has_recent_booking(user_id=user_id, booking_time=b_time):
                return
            # Если клиент хочет другое время, вежливо предупреждаем
            await message.answer(
                f"ℹ️ **Ака, у вас уже есть активная запись на {b_time} ({active_user_b.get('service')})!**\n\n"
                "Если хотите перенести её на другое время, просто напишите мне: *«Перенеси на другое время»* или *«Отмени старую запись»*, и я всё сделаю! 💈",
                parse_mode="Markdown"
            )
            return

        # Проверяем доступность слота с мульти-мастер подбором
        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True)
        matched_barber = None
        if client_action.barber_name:
            matched_barber = next(
                (b for b in barbers if b["name"].lower() in client_action.barber_name.lower()),
                None
            )

        res = check_slot_availability(
            target_dt=target_dt,
            existing_bookings=active_bookings,
            barber_id=matched_barber["id"] if matched_barber else None,
            available_barbers=barbers
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
            or f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
            or "Гость"
        )
        username = f"@{message.from_user.username}" if message.from_user.username else None
        phone = client_action.phone or None
        service = client_action.service or "Стрижка / Комплекс"
        final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
        final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

        # Сохранение в базу
        await save_booking(
            user_id=user_id,
            username=username,
            full_name=full_name,
            details=f"Запрос: {client_query} | Ответ: {ai_reply[:200]}",
            phone=phone,
            service=service,
            booking_time=booking_time,
            status="new",
            barber_id=final_barber_id,
            barber_name=final_barber_name,
        )

        # Отправляем уведомление администратору в личку
        if settings.ADMIN_CHAT_ID:
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_card = (
                "💈 **НОВАЯ ЗАПИСЬ В БАРБЕРШОП BLACK BEARD!**\n\n"
                f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
                f"{master_str}"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {booking_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n"
                f"🆔 **Telegram ID:** `{user_id}`\n\n"
                "📱 _Запись доступна в панели управления (TMA)!_"
            )
            try:
                await message.bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_card,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление администратору: {e}")

        # Уведомление назначенному барберу в Telegram
        if final_barber_id:
            barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None)
            if barber_obj and barber_obj.get("telegram_id"):
                try:
                    await message.bot.send_message(
                        chat_id=barber_obj["telegram_id"],
                        text=(
                            "💈 **У ВАС НОВАЯ ЗАПИСЬ НА СМЕНУ!**\n\n"
                            f"👤 **Клиент:** {full_name}\n"
                            f"✂️ **Услуга:** {service}\n"
                            f"⏰ **Время:** {booking_time}\n"
                            f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                            "📱 _Посмотреть свою очередь: /barber_"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить барбера {barber_obj['name']}: {e}")

        # Предлагаем аккуратную кнопку брендинга разработчика
        await message.answer(
            "🤝 Мы ценим ваше доверие! До встречи в Black Beard.",
            reply_markup=AI_BUSINESS_KEYBOARD,
        )


# Для обратной совместимости
process_booking_intent = process_client_intent

