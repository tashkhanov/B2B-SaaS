import io
import logging
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
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
    get_booking_by_id,
    update_booking_status,
    find_booking_by_client_name,
    get_branches,
    get_branch_by_id,
    get_user_active_booking,
    cancel_user_booking,
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


def is_super_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь Создателем и Супер-Администратором"""
    if not user_id:
        return False
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    return bool(super_admin_id and user_id == super_admin_id)


async def get_user_role(user_id: int):
    """Определение роли: 'super_admin', 'admin', 'barber', или 'client'."""
    if is_super_admin(user_id):
        return "super_admin", None
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

    if role in ("admin", "super_admin"):
        keyboard = [
            [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
            [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Сбросить диалог")],
        ]
        if has_https:
            row = [
                KeyboardButton(
                    text="📊 Панель записей (TMA)",
                    web_app=WebAppInfo(url=f"{base_url}/admin"),
                )
            ]
            if role == "super_admin":
                hub_url = getattr(settings, "SAAS_HUB_URL", "https://hub.asatkhanov.uz")
                row.append(
                    KeyboardButton(
                        text="👑 SaaS Command Center",
                        web_app=WebAppInfo(url=hub_url) if hub_url.startswith("https://") else None,
                    )
                )
            keyboard.append(row)
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
        if role in ("admin", "super_admin"):
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

    if role in ("admin", "super_admin"):
        url = f"{base_url}/admin" if base_url else "http://localhost:8000/admin"
        btns = [
            InlineKeyboardButton(text="📊 Открыть панель управления", web_app=WebAppInfo(url=url))
            if has_https
            else InlineKeyboardButton(text="📊 Открыть панель управления", url=url)
        ]
        if role == "super_admin":
            hub_url = getattr(settings, "SAAS_HUB_URL", "https://hub.asatkhanov.uz")
            has_hub_https = hub_url.startswith("https://")
            btns.append(
                InlineKeyboardButton(text="👑 SaaS Command Center", web_app=WebAppInfo(url=hub_url))
                if has_hub_https
                else InlineKeyboardButton(text="👑 SaaS Command Center", url=hub_url)
            )
        return InlineKeyboardMarkup(inline_keyboard=[btns])
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
    Обработчик команды /start с автоопределением роли (суперадмин, админ, барбер, клиент)
    и прозрачной inline-кнопкой под сообщением для открытия веб-меню.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    await update_chat_menu_button(message, role)

    if role == "super_admin":
        welcome_text = (
            "Салам алейкум, Иброхим ака! 👑\n\n"
            "Вы авторизованы в системе как **Создатель платформы и Супер-Администратор** (Khan AI Systems).\n"
            "Вам открыт полный доступ к расписанию заведения, панели Brohouse и пульту SaaS `/super`.\n\n"
            "💡 *Чтобы записаться на стрижку лично, просто напишите или скажите голосом:* "
            "«**Запиши меня на завтра в 16:00**» — *я сразу оформлю бронь на ваше имя без лишних вопросов!*"
        )
    elif role == "admin":
        welcome_text = (
            "Салам алейкум, Управляющий! 👑\n\n"
            "Вы вошли в систему как **Администратор** мужского салона Brohouse.\n"
            "Вам доступно голосовое и текстовое управление записями, а также кнопка **«📊 Открыть панель управления»** прямо под этим сообщением!"
        )
    elif role == "barber":
        b_role = barber.get("role_title") or barber.get("role") or "Стилист"
        welcome_text = (
            f"Салам алейкум, {barber['name']}! 💈\n\n"
            f"Вы авторизованы как мастер салона Brohouse (**{b_role}**).\n\n"
            "Прямо под этим сообщением для вас доступна кнопка **«💈 Моя смена (Кабинет)»** — "
            "нажмите её, чтобы посмотреть очередь клиентов на сегодня или изменить статус смены!"
        )
    else:
        user_name = message.from_user.first_name if message.from_user else "братан"
        welcome_text = (
            f"Салам алейкум, {user_name}! 🤝\n\n"
            "Добро пожаловать в мужской салон **Brohouse** в Ташкенте!\n"
            "Я — Brohouse Ассистент. Нажмите кнопку **«✂️ Открыть меню записи»** под сообщением, "
            "чтобы выбрать услугу, мастера и время, либо просто напишите или отправьте голосовое сообщение прямо сюда в чат!"
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
        "💈 **AI-Администратор для Мужского Салона 'Brohouse' (Ташкент)**\n\n"
        "📍 **Филиалы в Ташкенте:**\n"
        "1. метро «Миллий бог», 8B (ориентир: станция метро Milliy Bog')\n"
        "2. массив Чиланзар, 2-й квартал, 58/5 (ориентир: 2-й квартал)\n\n"
        "📞 **Единый телефон:** +998 97 012-55-55\n"
        "⏰ **График:** 08:00 – 23:00 ежедневно без выходных\n"
        "📸 **Instagram:** @brohouseuz | **Академия:** @asbro_academy_\n\n"
        "🎁 **Акция:** Скидка 30% на первое посещение для новых клиентов!\n\n"
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
            "🛠 **Панель управления Brohouse:**\n"
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
            "🛠 **Панель управления Brohouse:**\n\n"
            f"🔗 Локальный адрес панели: `{admin_url}`\n\n"
            "💡 *Telegram требует HTTPS для встраивания кнопок Mini App.*\n"
            "Чтобы панель открывалась прямо внутри Telegram:\n"
            "1. Запустите в новом терминале бесплатный туннель: `ngrok http 8000` (или `npx localtunnel --port 8000`)\n"
            "2. Скопируйте полученную https-ссылку (например, `https://xyz.ngrok-free.app`)\n"
            "3. Укажите её в `.env`: `WEBAPP_URL=https://xyz.ngrok-free.app` и перезапустите бота.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )


@router.message(Command("super"))
async def handle_super(message: Message):
    """
    Команда /super для вызова пульта управления Super-Admin (SaaS Multi-Tenant).
    Доступна только владельцу платформы (SUPER_ADMIN_CHAT_ID или ADMIN_CHAT_ID).
    """
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
        "🔥 **Прайс-лист мужского салона 'Brohouse' (Ташкент):**\n\n"
        "🎁 **СПЕЦПРЕДЛОЖЕНИЕ:** 30% OFF на первую стрижку!\n\n"
        "✂️ **СТРИЖКИ:**\n"
        "• Стрижка волос — 100 000 сум\n"
        "• Стрижка под машинку — 50 000 сум\n"
        "• Стрижка на лысо — 30 000 сум\n"
        "• Детская стрижка от 5 лет — 60 000 сум\n"
        "• Укладка — 60 000 сум\n"
        "• Мойка, массаж — 50 000 сум\n"
        "• Укладка, мойка, массаж (СПА СЕТ) — 80 000 сум\n\n"
        "🧔 **УСЫ И БОРОДА / УХОД:**\n"
        "• Бритье опасной бритвой — 70 000 сум\n"
        "• Оформление бороды — 50 000 сум\n"
        "• Покраска бороды — 30 000 сум\n"
        "• Воск (одна из зон) — 20 000 сум\n"
        "• Черная маска — 30 000 сум\n\n"
        "_В стрижку включены мытье головы, премиум-стайлинг и напитки._ ☕\n\n"
        "На какой день и время вас записать, ака?"
    )
    await message.answer(price_text, parse_mode="Markdown", reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """
    Информация о локациях барбершопа (2 филиала).
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    location_text = (
        "💈 **Мужской салон Brohouse ждет вас в двух удобных локациях Ташкента:**\n\n"
        "1️⃣ **Филиал «Миллий бог»:**\n"
        "📍 г. Ташкент, метро «Миллий бог», 8B\n"
        "🚇 Ориентир: станция метро Milliy Bog'\n\n"
        "2️⃣ **Филиал «Чиланзар (2-й кв.)»:**\n"
        "📍 г. Ташкент, Чиланзарский район, массив Чиланзар, 2-й квартал, 58/5\n"
        "🏢 Ориентир: Чиланзар, 2-й квартал, дом 58/5\n\n"
        "📞 **Единый телефон для связи:** +998 97 012-55-55\n"
        "⏰ **График работы:** ежедневно с 08:00 до 23:00 без выходных.\n\n"
        "Мастера подстроятся под вас в любом из филиалов. Ждем в гости, ака! 🤝"
    )
    await message.answer(location_text, reply_markup=get_main_keyboard(role=role, barber=barber), parse_mode="Markdown")


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
    is_staff = role in ("admin", "barber", "super_admin")

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
        is_admin=is_staff,
    )

    # 3. Отправка ответа пользователю
    await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))

    # 4. Проверка и выполнение интента (для клиента или персонала)
    if is_staff:
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
    is_staff = role in ("admin", "barber", "super_admin")

    # 1. Индикация "печатает..." во время запроса к AI
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 2. Получение ответа от Gemini с учетом роли
    ai_reply = await gemini_service.generate_response(user_id=user_id, user_message=user_text, is_admin=is_staff)

    # 3. Отправка ответа пользователю
    await message.answer(ai_reply, reply_markup=get_main_keyboard(role=role, barber=barber))

    # 4. Проверка и выполнение намерения
    if is_staff:
        await process_admin_intent(message, admin_query=user_text, ai_reply=ai_reply)
    else:
        await process_client_intent(message, client_query=user_text, ai_reply=ai_reply)


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
        personal_markers = ["меня", "мне", "себя", "запиши на", "хочу записаться", "meni yoz", "menga yoz"]
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
                branch_addr = branch_info["address"] if branch_info else "г. Ташкент, метро «Миллий бог», 8B"
                branch_title = booking.get("branch_name") or (branch_info["name"] if branch_info else "Миллий бог")
                try:
                    await message.bot.send_message(
                        chat_id=booking["user_id"],
                        text=(
                            f"✅ **Ваша запись в Brohouse подтверждена!**\n\n"
                            f"✂️ **Услуга:** {booking['service']}\n"
                            f"⏰ **Время:** {booking['booking_time']}\n"
                            f"📍 **Филиал:** {branch_title} ({branch_addr})\n"
                            f"📞 **Телефон:** +998 97 012-55-55\n\n"
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

    # 3. Создание записи: личная бронь Создателя или ручная запись клиента
    if admin_action.action == "create_booking" and admin_action.booking_time:
        if not admin_action.client_query and not is_personal_creator_booking:
            return

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
                salutation = "Иброхим ака" if is_personal_creator_booking else "Шеф"
                await message.answer(f"⚠️ {salutation}, на это время записать нельзя: {res.error_message}")
                return

            normalized_time = format_booking_datetime(target_dt)
            final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
            final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

            # Определение филиала
            target_branch_id = 1
            target_branch_name = "Миллий бог"
            branch_check_str = f"{getattr(admin_action, 'branch_name', '') or ''} {admin_query}".lower()
            if any(k in branch_check_str for k in ("чиланзар", "chilonzor", "2-й", "2 квартал", "квартал 2", "58/5")):
                target_branch_id = 2
                target_branch_name = "Чиланзар (2-й кв.)"
            elif getattr(admin_action, "branch_id", None) in (1, 2):
                target_branch_id = admin_action.branch_id
                target_branch_name = "Чиланзар (2-й кв.)" if target_branch_id == 2 else "Миллий бог"

            branch_obj = await get_branch_by_id(target_branch_id)
            branch_address = branch_obj["address"] if branch_obj else target_branch_name

            if is_personal_creator_booking:
                full_name = "Иброхим"
                booking_user_id = user_id
                username = f"@{message.from_user.username}" if (message.from_user and message.from_user.username) else "@asatkhanov"
                details = f"Личная запись Создателя/Супер-Админа (Иброхим) | Филиал: {target_branch_name}"
                status = "confirmed"
            else:
                full_name = admin_action.client_query
                booking_user_id = 0
                username = None
                details = f"Ручная запись администратором через чат с ИИ | Филиал: {target_branch_name}"
                status = "confirmed"

            await save_booking(
                user_id=booking_user_id,
                username=username,
                full_name=full_name,
                details=details,
                phone=admin_action.phone,
                service=admin_action.service or "Стрижка / Комплекс",
                booking_time=normalized_time,
                status=status,
                barber_id=final_barber_id,
                barber_name=final_barber_name,
                branch_id=target_branch_id,
                branch_name=target_branch_name,
            )

            # Оповещение назначенному барберу в Telegram
            if matched_barber and matched_barber.get("telegram_id"):
                try:
                    title_text = "👑 **К вам записался Создатель платформы / Шеф!**" if is_personal_creator_booking else "💈 **Шеф записал к вам клиента!**"
                    await message.bot.send_message(
                        chat_id=matched_barber["telegram_id"],
                        text=(
                            f"{title_text}\n\n"
                            f"👤 **Гость:** {full_name}\n"
                            f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                            f"✂️ **Услуга:** {admin_action.service or 'Стрижка'}\n"
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
                    f"✂️ **Услуга:** {admin_action.service or 'Стрижка / Комплекс'}\n"
                    f"⏰ **Время:** {normalized_time}\n"
                    f"{master_str}\n"
                    f"📍 **Филиал:** {target_branch_name} ({branch_address})\n\n"
                    "Всегда рады видеть вас в Brohouse, Создатель! 🤝",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"💈 Запись клиента **{full_name}**{master_str} на **{normalized_time}** ({admin_action.service or 'Стрижка'}) в филиал **{target_branch_name}** успешно зафиксирована в графике!"
                )


async def process_client_intent(message: Message, client_query: str, ai_reply: str) -> None:
    """
    Интеллектуальная обработка намерений клиента:
    1. Отмена своей брони ('отмени запись', 'не смогу прийти')
    2. Проверка своей брони ('когда я записан?')
    3. Создание новой записи с защитой от спама и горизонтом 7 дней
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
            "Иброхим"
            if is_super_admin(user_id)
            else (
                client_action.customer_name
                or f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
                or "Гость"
            )
        )
        username = f"@{message.from_user.username}" if message.from_user.username else None
        phone = client_action.phone or None
        service = client_action.service or "Стрижка / Комплекс"
        final_barber_id = res.assigned_barber_id or (matched_barber["id"] if matched_barber else None)
        final_barber_name = res.assigned_barber_name or (matched_barber["name"] if matched_barber else None)

        # Определение филиала
        target_branch_id = 1
        target_branch_name = "Миллий бог"
        branch_check_str = f"{getattr(client_action, 'branch_name', '') or ''} {client_query} {ai_reply}".lower()
        if any(k in branch_check_str for k in ("чиланзар", "chilonzor", "2-й", "2 квартал", "квартал 2", "58/5")):
            target_branch_id = 2
            target_branch_name = "Чиланзар (2-й кв.)"
        elif getattr(client_action, "branch_id", None) in (1, 2):
            target_branch_id = client_action.branch_id
            target_branch_name = "Чиланзар (2-й кв.)" if target_branch_id == 2 else "Миллий бог"

        branch_obj = await get_branch_by_id(target_branch_id)
        branch_address = branch_obj["address"] if branch_obj else target_branch_name

        # Сохранение в базу
        booking_id = await save_booking(
            user_id=user_id,
            username=username,
            full_name=full_name,
            details=f"Запрос: {client_query} | Ответ: {ai_reply[:200]} | Филиал: {target_branch_name}",
            phone=phone,
            service=service,
            booking_time=booking_time,
            status="new",
            barber_id=final_barber_id,
            barber_name=final_barber_name,
            branch_id=target_branch_id,
            branch_name=target_branch_name,
        )

        # Отправляем уведомление администратору в личку
        if settings.ADMIN_CHAT_ID:
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_card = (
                "💈 **НОВАЯ ЗАПИСЬ В BROHOUSE!**\n\n"
                f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
                f"{master_str}"
                f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                f"✂️ **Услуга:** {service}\n"
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

        # Уведомление назначенному барберу в Telegram
        if final_barber_id:
            barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None)
            if barber_obj and barber_obj.get("telegram_id"):
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
                            "💈 **У ВАС НОВАЯ ЗАПИСЬ В BROHOUSE!**\n\n"
                            f"👤 **Клиент:** {full_name}\n"
                            f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                            f"✂️ **Услуга:** {service}\n"
                            f"⏰ **Время:** {booking_time}\n"
                            f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                            "📱 _Подтвердите или отклоните прямо кнопками ниже:_"
                        ),
                        parse_mode="Markdown",
                        reply_markup=action_kb_barber,
                    )
                except Exception as e:
                    logger.warning(f"Не удалось уведомить барбера {barber_obj['name']}: {e}")

        # Предлагаем аккуратную кнопку брендинга разработчика
        await message.answer(
            "🤝 Мы ценим ваше доверие! До встречи в Brohouse.",
            reply_markup=AI_BUSINESS_KEYBOARD,
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

    new_status = parts[2]  # confirmed / cancelled

    booking = await get_booking_by_id(booking_id)
    if not booking:
        await callback.answer("Запись не найдена в базе.", show_alert=True)
        return

    # Если действие совершает мастер, проверяем, что запись его
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

    # Уведомляем клиента в Telegram
    if client_tg_id and client_tg_id != 0:
        b_time = booking.get("booking_time") or ""
        service = booking.get("service") or "Стрижка"
        b_name = booking.get("barber_name") or (barber["name"] if barber else "Мастер")

        if new_status == "confirmed":
            b_branch_id = booking.get("branch_id") or 1
            branch_info = await get_branch_by_id(b_branch_id)
            branch_addr = branch_info["address"] if branch_info else "г. Ташкент, метро «Миллий бог», 8B"
            branch_title = booking.get("branch_name") or (branch_info["name"] if branch_info else "Миллий бог")
            msg = (
                "✅ **Ваша запись в Brohouse ПОДТВЕРЖДЕНА!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"💈 **Мастер:** {b_name}\n"
                f"⏰ **Время:** {b_time}\n"
                f"📍 **Филиал:** {branch_title} ({branch_addr})\n"
                "📞 **Телефон:** +998 97 012-55-55\n\n"
                "Мы уже ждем вас к назначенному времени, ака! 💈"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Не удалось отправить клиенту уведомление: {e}")
        elif new_status == "cancelled":
            msg = (
                "ℹ️ **Ваша запись в Brohouse была отменена.**\n\n"
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


# Для обратной совместимости
process_booking_intent = process_client_intent

