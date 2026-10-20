import io
import re
import html
import time
import logging
from typing import Optional, Tuple, List, Dict
from aiogram import Router, F

_voice_rate_limit: Dict[int, float] = {}

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
    BookingConflictError,
    has_recent_booking,
    has_recent_booking_any_time,
    get_recent_messages,
    get_active_bookings,
    get_barbers,
    get_barber_by_telegram_id,
    get_barber_by_id,
    get_services,
    get_booking_by_id,
    update_booking_status,
    find_booking_by_client_name,
    get_branches,
    get_branch_by_id,
    get_user_active_booking,
    cancel_user_booking,
    get_salon_setting,
    update_receipt_log_status,
    get_receipt_log_by_id,
    extend_tenant_subscription_30_days,
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
from bot.routers import (
    common_router,
    barber_router,
    admin_router,
    client_router,
)

logger = logging.getLogger(__name__)

router = Router(name="main_router")


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
    q = query_text.lower()
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
    if not service_name_or_query:
        return None, 60

    services = await get_services(active_only=True)
    if not services:
        return None, 60

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

    return None, 60


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
            [KeyboardButton(text="📅 Записаться")],
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
                [KeyboardButton(text="📍 Где мы находимся?")],
            ]
        else:
            keyboard = [
                [KeyboardButton(text="💈 Моя смена (/barber)"), KeyboardButton(text="✂️ Прайс-лист")],
                [KeyboardButton(text="📍 Где мы находимся?")],
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
                [KeyboardButton(text="📍 Где мы находимся?")],
            ]
        else:
            keyboard = [
                [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
                [KeyboardButton(text="📅 Записаться")],
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
            f"Салам алейкум, {settings.SUPER_ADMIN_NAME} ака! 👑\n\n"
            f"Вы авторизованы в системе как **Создатель платформы и Супер-Администратор** ({settings.PLATFORM_NAME}).\n"
            f"Вам открыт полный доступ к расписанию заведения, панели {settings.SALON_SHORT_NAME} и пульту SaaS `/super`.\n\n"
            "💡 *Чтобы записаться на стрижку лично, просто напишите или скажите голосом:* "
            "«**Запиши меня на завтра в 16:00**» — *я сразу оформлю бронь на ваше имя без лишних вопросов!*"
        )
    elif role == "admin":
        welcome_text = (
            "Салам алейкум, Управляющий! 👑\n\n"
            f"Вы вошли в систему как **Администратор** ({settings.SALON_NAME}).\n"
            "Вам доступно голосовое и текстовое управление записями, а также кнопка **«📊 Открыть панель управления»** прямо под этим сообщением!"
        )
    elif role == "barber":
        b_role = barber.get("role_title") or barber.get("role") or "Стилист"
        welcome_text = (
            f"Салам алейкум, {barber['name']}! 💈\n\n"
            f"Вы авторизованы как мастер салона {settings.SALON_SHORT_NAME} (**{b_role}**).\n\n"
            "Прямо под этим сообщением для вас доступна кнопка **«💈 Моя смена (Кабинет)»** — "
            "нажмите её, чтобы посмотреть очередь клиентов на сегодня или изменить статус смены!"
        )
    else:
        user_name = message.from_user.first_name if (message.from_user and message.from_user.first_name) else "гость"
        welcome_text = (
            f"Assalomu alaykum, {user_name}! Xush kelibsiz! / Здравствуйте!\n\n"
            f"Добро пожаловать в {settings.SALON_TYPE} **{settings.SALON_SHORT_NAME}** ({settings.SALON_CITY})!\n\n"
            f"🌐 **Muloqot tili / Язык общения:**\n"
            f"Siz bot bilan **o'zbek**, **rus** yoki istalgan tilda bemalol yozma yoki ovozli xabar orqali muloqot qilishingiz mumkin.\n"
            f"Вы можете писать или отправлять голосовые сообщения на **узбекском**, **русском** или любом удобном вам языке.\n\n"
            f"Нажмите кнопку **«✂️ Открыть меню записи»** под сообщением для выбора услуги и времени, либо просто напишите свой запрос прямо сюда в чат!"
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
    branches = await get_branches(active_only=True)
    branch_lines = []
    contact_phone = await get_salon_setting("salon_phone", "+998 97 012-55-55")
    start_h = await get_salon_setting("work_start_hour", "8")
    end_h = await get_salon_setting("work_end_hour", "23")
    promo_text = await get_salon_setting("promo_discount_text", "30% OFF")
    promo_sub = await get_salon_setting("promo_subtitle", "Скидка на первое посещение для новых клиентов!")
    sched_str = f"{int(start_h):02d}:00 – {int(end_h):02d}:00 ежедневно без выходных"

    if branches:
        for idx, b in enumerate(branches, 1):
            landmark_str = f" (ориентир: {b['landmark']})" if b.get("landmark") else ""
            branch_lines.append(f"{idx}. {b['name']}: {b['address']}{landmark_str}")
    branch_block = "\n".join(branch_lines) if branch_lines else "г. Ташкент"

    social_parts = []
    if getattr(settings, "INSTAGRAM_URL", None):
        social_parts.append(f"**Instagram:** {settings.INSTAGRAM_URL}")
    if getattr(settings, "ACADEMY_URL", None):
        social_parts.append(f"**Академия:** {settings.ACADEMY_URL}")
    social_block = f"📸 {' | '.join(social_parts)}\n\n" if social_parts else ""

    portfolio_short = settings.PORTFOLIO_URL.replace("https://", "").replace("http://", "").rstrip("/")

    about_text = (
        f"💈 **AI-Администратор для салона '{settings.SALON_NAME}' ({settings.SALON_CITY})**\n\n"
        f"📍 **Филиалы в г. {settings.SALON_CITY}:**\n"
        f"{branch_block}\n\n"
        f"📞 **Единый телефон:** {contact_phone}\n"
        f"⏰ **График:** {sched_str}\n"
        f"{social_block}"
        f"🎁 **Акция:** {promo_text} • {promo_sub}\n\n"
        f"👨‍💻 **Разработка:** {settings.DEVELOPER_NAME}\n"
        f"⚡ **Платформа:** {settings.PLATFORM_NAME}\n"
        f"🌐 **Сайт / Портфолио:** [{portfolio_short}]({settings.PORTFOLIO_URL})\n\n"
        "_Хотите автоматизировать продажи или клиентский сервис в вашем бизнесе с помощью ИИ?_"
    )
    about_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Написать разработчику", url=settings.SUPER_ADMIN_TELEGRAM),
                InlineKeyboardButton(text=f"🌐 {portfolio_short}", url=settings.PORTFOLIO_URL),
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


@router.message(Command("clear"))
@router.message(Command("reset"))
@router.message(F.text == "🔄 Сбросить диалог")
async def handle_reset(message: Message):
    """
    Очистка контекста диалога текущего пользователя в базе данных (секретная команда /clear).
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    await clear_history(user_id)
    await message.answer(
        "История диалога очищена! Начнем с чистого листа. Чем могу помочь? 💈",
        reply_markup=get_main_keyboard(role=role, barber=barber),
    )

handle_clear = handle_reset


@router.message(Command("price"))
@router.message(F.text == "✂️ Прайс-лист")
async def handle_price(message: Message):
    """
    Быстрый просмотр актуального прайс-листа из базы данных без хардкода.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    services = await get_services(active_only=True)
    promo_title = await get_salon_setting("promo_title", settings.PROMO_TITLE)

    lines = [
        f"🔥 **Прайс-лист салона '{settings.SALON_NAME}' ({settings.SALON_CITY}):**\n",
        f"🎁 **СПЕЦПРЕДЛОЖЕНИЕ:** {promo_title} для новых клиентов!\n",
    ]
    if services:
        for s in services:
            badge_str = f" 🔥 [{s['badge']}]" if s.get("badge") else ""
            dur_str = f" ({s['duration_min']} мин)" if s.get("duration_min") else ""
            price_val = f"{s['price']:,}".replace(",", " ")
            desc_str = f"\n   _{s['description']}_" if s.get("description") else ""
            lines.append(f"• **{s['name']}**{badge_str} — {price_val} {settings.CURRENCY_SYMBOL}{dur_str}{desc_str}")
        lines.append("\n_В стрижку включено мытье головы._ 💈\n")
    else:
        lines.append("Информация об услугах обновляется администрацией.\n")

    lines.append("На какой день и время вас записать, ака?")
    price_text = "\n".join(lines)
    await message.answer(price_text, parse_mode="Markdown", reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """
    Информация о локациях барбершопа из базы данных без хардкода.
    """
    user_id = message.from_user.id if message.from_user else 0
    role, barber = await get_user_role(user_id)
    branches = await get_branches(active_only=True)

    lines = [
        f"💈 **Локации салона {settings.SALON_SHORT_NAME} ждут вас в г. {settings.SALON_CITY}:**\n"
    ]
    salon_phone = await get_salon_setting("salon_phone", settings.SALON_PHONE)
    start_h = await get_salon_setting("work_start_hour", str(settings.WORK_START_HOUR))
    end_h = await get_salon_setting("work_end_hour", str(settings.WORK_END_HOUR))
    sched_str = f"ежедневно с {int(start_h):02d}:00 до {int(end_h):02d}:00 без выходных"

    if branches:
        for idx, b in enumerate(branches, 1):
            lines.append(f"{idx}️⃣ **Филиал «{b['name']}»:**")
            lines.append(f"📍 {b['address']}")
            if b.get("landmark"):
                lines.append(f"🚇 Ориентир: {b['landmark']}")
            lines.append("")
    else:
        lines.append(f"📍 г. {settings.SALON_CITY}\n")

    lines.extend([
        f"📞 **Единый телефон для связи:** {salon_phone}",
        f"⏰ **График работы:** {sched_str}.\n",
        "Мастера подстроятся под вас в любом из филиалов. Ждем в гости, ака! 🤝"
    ])
    location_text = "\n".join(lines)
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

    voice = message.voice
    if not voice:
        return

    # Проверка доступности функции голосового ИИ по тарифу заведения (Feature-Gating)
    if not is_staff and not await is_feature_enabled("has_voice_ai", tenant_identifier=settings.TENANT_SLUG):
        await message.answer(
            "Здравствуйте! 🙏 На данный момент я принимаю сообщения только текстом.\n\n"
            "Напишите, пожалуйста, какую услугу и в какое удобное время вы хотели бы забронировать — я сразу проверю свободные окна!"
        )
        admin_id = getattr(settings, "ADMIN_CHAT_ID", None)
        if admin_id and user_id != admin_id:
            client_mention = f"@{message.from_user.username}" if message.from_user and message.from_user.username else f"ID {user_id}"
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💡 **Уведомление для владельца салона**\n\n"
                        f"Клиент {client_mention} отправил голосовое сообщение в бот.\n"
                        "На вашем текущем тарифе (START) автоматическое распознавание речи выключено.\n\n"
                        "⚡ Чтобы бот сам автоматически записывал клиентов по их голосовым сообщениям — перейдите на тариф **PRO**."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление администратору о голосовом сообщении: {e}")
        return

    # Anti-DoS: проверка размера и длительности аудио
    MAX_VOICE_DURATION = 60  # максимум 60 секунд
    MAX_VOICE_SIZE = 2 * 1024 * 1024  # максимум 2 МБ
    if voice.duration and voice.duration > MAX_VOICE_DURATION:
        await message.answer("⚠️ Голосовое сообщение слишком длинное (максимум 60 секунд). Пожалуйста, запишите короче или напишите текстом.")
        return
    if voice.file_size and voice.file_size > MAX_VOICE_SIZE:
        await message.answer("⚠️ Аудиофайл превышает лимит размера 2 МБ. Пожалуйста, отправьте более короткое сообщение.")
        return

    # Anti-DoS: троттлинг (не чаще 1 голосового раз в 5 секунд от одного пользователя)
    now_ts = time.time()
    last_voice_ts = _voice_rate_limit.get(user_id, 0.0)
    if (now_ts - last_voice_ts) < 5.0:
        await message.answer("⏳ Пожалуйста, подождите 5 секунд перед отправкой следующего голосового сообщения.")
        return
    _voice_rate_limit[user_id] = now_ts

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 1. Скачивание аудиофайла голосового сообщения
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

    # 2. Обработка голоса через Gemini с учетом роли
    username = message.from_user.username if message.from_user else None
    ai_reply, user_transcript = await gemini_service.generate_response_from_voice(
        user_id=user_id,
        voice_bytes=voice_data,
        mime_type="audio/ogg",
        is_admin=is_staff,
        username=username,
        bot=message.bot,
    )

    # 3. Если Gemini Tool Calling выполнил операции (бронь, отмена и т.д.), отправляем ответ и завершаем
    recent_booking_exists = await has_recent_booking_any_time(user_id=user_id, seconds=30)
    tools_called = gemini_service.had_tool_calls(user_id)
    if recent_booking_exists or tools_called:
        logger.info("Voice request handled via Gemini Tool Calling for user %s (recent=%s, tools=%s); skipping legacy intent extraction.", user_id, recent_booking_exists, tools_called)
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
    1. Показывает статус 'печатает...' (typing action) в Telegram.
    2. Отправляет запрос в GeminiService с историей сообщений, учетом роли и Tool Calling.
    3. Отправляет ответ пользователю (или выполняет действие через экстрактор).
    """
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None
    user_text = message.text.strip()
    role, barber = await get_user_role(user_id)
    is_staff = role in ("admin", "barber", "super_admin")

    # 1. Индикация "печатает..." во время запроса к AI
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 2. Получение ответа от Gemini с учетом роли и Tool Calling
    ai_reply = await gemini_service.generate_response(
        user_id=user_id,
        user_message=user_text,
        is_admin=is_staff,
        username=username,
        bot=message.bot,
    )

    # 3. Если Gemini Tool Calling выполнил операции (бронь, отмена и т.д.), отправляем ответ и завершаем
    recent_booking_exists = await has_recent_booking_any_time(user_id=user_id, seconds=30)
    tools_called = gemini_service.had_tool_calls(user_id)
    if recent_booking_exists or tools_called:
        logger.info("Text request handled via Gemini Tool Calling for user %s (recent=%s, tools=%s); skipping legacy intent extraction.", user_id, recent_booking_exists, tools_called)
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


async def process_admin_intent(message: Message, admin_query: str, ai_reply: str) -> bool:
    """
    Обработка команд администратора через естественный язык или голос:
    1. Подтверждение записи клиента ('подтверди запись Рустама')
    2. Отмена записи клиента ('отмени запись Рустама')
    3. Создание записи: личная бронь Создателя (Иброхим) или ручная запись клиента
    Возвращает True, если намерение было успешно обработано и ответ отправлен в чат.
    """
    user_id = message.from_user.id if message.from_user else 0
    is_super = is_super_admin(user_id)

    # Нормализация типичных опечаток ASR при распознавании речи
    clean_query = admin_query.replace("от мини", "отмени").replace("От мини", "отмени").replace("от-мини", "отмени")

    combined = f"{clean_query} {ai_reply}".lower()
    admin_triggers = ["подтверд", "отмен", "запиш", "бронь", "tasdiq", "bekor", "yoz", "стрижк", "время", "окно"]
    if not any(t in combined for t in admin_triggers):
        return False

    cancel_triggers = ["отмен", "bekor", "удалить", "удалите", "снять запись"]
    is_cancel_intent = any(ct in clean_query.lower() for ct in cancel_triggers)

    admin_action = await gemini_service.extract_admin_action(clean_query, user_id=user_id)

    # Проверка: является ли запрос личной записью Создателя / Супер-Админа (Иброхим)
    # КРИТИЧЕСКИ ВАЖНО: Если запрос на отмену, то это НЕ создание брони!
    is_personal_creator_booking = False
    if is_super and not is_cancel_intent:
        personal_markers = ["меня", "мне", "себя", "запиши на", "хочу записаться", "meni yoz", "menga yoz"]
        query_has_self = any(m in clean_query.lower() for m in personal_markers)
        client_is_self = (
            admin_action
            and (
                (admin_action.client_query or "").strip().lower() in ("меня", "мне", "себя", "иброхим", "шеф", "создатель")
                or (not admin_action.client_query and admin_action.action == "create_booking")
            )
        )
        if query_has_self or client_is_self:
            is_personal_creator_booking = True

    # Если admin_action не распознал create_booking, но Иброхим просит записать его (и это НЕ отмена!)
    # ВНИМАНИЕ: Если ассистент уже переспрашивает или уточняет дату/время/мастера, НЕ создаем бронь принудительно!
    is_clarifying = "?" in ai_reply or any(w in ai_reply.lower() for w in ["уточните", "какой именно день", "на какое время", "какому мастеру", "несколько вариантов"])
    if not is_cancel_intent and is_personal_creator_booking and not is_clarifying and (not admin_action or admin_action.action != "create_booking"):
        client_act = await gemini_service.extract_client_action(f"Клиент: {clean_query}\nАссистент: {ai_reply}", user_id=user_id)
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

    # Если это запрос на отмену, но admin_action не cancel_booking:
    if is_cancel_intent and (not admin_action or admin_action.action != "cancel_booking"):
        from services.gemini_service import AdminAction
        admin_action = AdminAction(
            action="cancel_booking",
            client_query="меня" if ("меня" in clean_query.lower() or "мне" in clean_query.lower() or "себя" in clean_query.lower()) else "",
        )

    if not admin_action or admin_action.action in ("chat", "query_schedule", "get_stats"):
        return False

    # 1. Администратор подтверждает запись клиента
    if admin_action.action == "confirm_booking" and admin_action.client_query:
        booking = await find_booking_by_client_name(admin_action.client_query)
        if booking and booking["status"] in ("new", "pending_vip"):
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
                            f"⏰ **Время:** {booking['booking_time']}\n"
                            f"📍 **Филиал:** {branch_title} ({branch_addr})\n"
                            f"📞 **Телефон:** {branch_phone}\n\n"
                            "Ждем вас, ака! 💈"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление клиенту о подтверждении: {e}")
            await message.answer(f"✅ Запись клиента **{booking['full_name']}** на **{booking['booking_time']}** успешно подтверждена!")
            return True
        elif booking and booking["status"] == "confirmed":
            await message.answer(f"ℹ️ Запись клиента **{booking['full_name']}** на **{booking['booking_time']}** уже подтверждена.")
            return True

    # 2. Администратор отменяет запись клиента (или свою)
    if admin_action.action == "cancel_booking":
        booking = None
        q = (admin_action.client_query or "").strip().lower()
        if q and q not in ("меня", "мне", "себя", "мою"):
            booking = await find_booking_by_client_name(q)
        else:
            booking = await get_user_active_booking(user_id)
            if not booking and q:
                booking = await find_booking_by_client_name(q)

        if booking and booking["status"] != "cancelled":
            await update_booking_status(booking["id"], "cancelled")
            # Уведомляем клиента в личку Telegram
            if booking.get("user_id") and booking["user_id"] != user_id:
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
            return True
        elif booking and booking["status"] == "cancelled":
            await message.answer(f"ℹ️ Запись на **{booking['booking_time']}** уже была отменена ранее.")
            return True
        elif is_cancel_intent:
            await message.answer("⚠️ Не удалось найти активную запись для отмены. Пожалуйста, уточните имя клиента, номер телефона или время записи.")
            return True

    # 3. Создание записи: личная бронь Создателя или ручная запись клиента
    if admin_action.action == "create_booking" and admin_action.booking_time:
        if not admin_action.client_query and not is_personal_creator_booking:
            return False

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
                return True

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
                    full_name = creator_name or "Шеф"
                booking_user_id = user_id
                username = f"@{message.from_user.username}" if (message.from_user and message.from_user.username) else None
                details = f"Личная запись Создателя/Супер-Админа ({full_name}) | Филиал: {target_branch_name}"

                # Строгая проверка лимита: 1 активная бронь на Создателя / Супер-Админа
                active_b = await get_user_active_booking(booking_user_id)
                if active_b and active_b.get("id"):
                    b_time = active_b.get("booking_time", "ранее")
                    b_barber = active_b.get("barber_name", "мастер")
                    b_srv = active_b.get("service", "Стрижка")
                    await message.answer(
                        f"⚠️ **Иброхим ака, у вас уже есть активная запись на {b_time}!**\n\n"
                        f"✂️ Услуга: {b_srv}\n"
                        f"💈 Мастер: {b_barber}\n\n"
                        "По правилам салона Brohouse, разрешена только одна активная бронь. "
                        "Чтобы записаться на другое время, пожалуйста, сначала перенесите или отмените текущую запись (напишите *«Отмени мою запись»*). 💈",
                        parse_mode="Markdown"
                    )
                    return True
            else:
                full_name = admin_action.client_query
                booking_user_id = 0
                username = None
                details = f"Ручная запись администратором через чат с ИИ | Филиал: {target_branch_name}"

            try:
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
            except BookingConflictError as bce:
                logger.warning(f"Booking collision in process_admin_intent: {bce}")
                await message.answer(
                    f"⚠️ **Шеф, этот слот уже занят!**\n\n"
                    f"Мастер **{final_barber_name}** на время `{normalized_time}` уже забронирован другим клиентом.\n"
                    "Пожалуйста, выберите другое свободное время или мастера. 💈",
                    parse_mode="Markdown"
                )
                return True

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
                return True

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
                    f"Всегда рады видеть вас в {settings.SALON_SHORT_NAME}, Создатель! 🤝",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"💈 Запись клиента **{full_name}**{master_str} на **{normalized_time}** ({admin_action.service or 'Стрижка'}) в филиал **{target_branch_name}** успешно зафиксирована в графике!"
                )
            return True

    return False


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

        # Динамическое определение услуги и её длительности
        matched_service, srv_duration = await resolve_service_info(client_action.service)
        canonical_service = matched_service["name"] if matched_service else (client_action.service or "Стрижка / Комплекс")
        matched_service_id = matched_service["id"] if matched_service else None

        # Проверяем доступность слота с мульти-мастер подбором и реальной длительностью
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

        # Определение филиала динамически из базы данных
        target_branch_id, target_branch_name, branch_address = await resolve_branch(
            query_text=f"{client_query} {ai_reply}",
            branch_id=getattr(client_action, "branch_id", None),
            branch_name=getattr(client_action, "branch_name", None),
        )

        # Определение VIP-мастера и защиты графика (по флагу или руководящей роли)
        barber_obj = next((b for b in barbers if b["id"] == final_barber_id), None) if final_barber_id else matched_barber
        is_vip_master = is_barber_vip(barber_obj) if barber_obj else False

        has_vip_protection = await is_feature_enabled("has_vip_protection")
        booking_status = "pending_vip" if (is_vip_master and has_vip_protection) else "new"

        # Сохранение в базу
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
            logger.warning(f"Booking collision in process_client_intent: {bce}")
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

        # Отправляем уведомление администратору в личку
        if settings.ADMIN_CHAT_ID:
            clean_un = (username or "").replace("@", "").strip()
            tg_un_line = f"📱 **TG Username:** @{clean_un}\n" if clean_un else (f"📱 **TG Username:** отсутствует (ID: `{user_id}`)\n" if user_id else "📱 **TG Username:** отсутствует\n")
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_card = (
                f"💈 **НОВАЯ ЗАПИСЬ В {settings.SALON_SHORT_NAME.upper()}!**\n\n"
                f"👤 **Клиент:** {full_name}\n"
                f"{tg_un_line}"
                f"{master_str}"
                f"📍 **Филиал:** {target_branch_name} ({branch_address})\n"
                f"✂️ **Услуга:** {canonical_service}\n"
                f"⏰ **Время:** {booking_time}\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
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

        # Предлагаем аккуратную кнопку брендинга разработчика
        await message.answer(
            f"🤝 Мы ценим ваше доверие! До встречи в {settings.SALON_SHORT_NAME}.",
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
    if role not in ("admin", "barber", "super_admin") and not is_super_admin(user_id):
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

        # Оповещаем мастера или админа
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

        # Оповещаем мастера и админа об освободившемся слоте
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


@router.callback_query(F.data.startswith("vip_accept:"))
async def handle_vip_accept_callback(callback: CallbackQuery):
    """Шеф-мастер (Mr. Akmal) или админ подтверждает VIP-запись."""
    user_id = callback.from_user.id if callback.from_user else 0
    role, barber = await get_user_role(user_id)
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if role not in ("admin", "barber", "super_admin") and user_id not in (admin_id, super_admin_id):
        await callback.answer("У вас нет прав для подтверждения этой VIP-записи.", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        if booking.get("status") == "cancelled":
            try:
                old_text = callback.message.text or ""
                await callback.message.edit_text(
                    f"{old_text}\n\n⚠️ **ЗАПИСЬ УЖЕ БЫЛА ОТМЕНЕНА КЛИЕНТОМ**",
                    reply_markup=None,
                    parse_mode="Markdown"
                )
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await callback.answer("⚠️ Эта запись уже была отменена клиентом!", show_alert=True)
            return

        await update_booking_status(booking_id, "confirmed")

        actor_name = barber["name"] if barber else (callback.from_user.first_name or "Шеф")
        try:
            old_text = callback.message.text or ""
            await callback.message.edit_text(
                f"{old_text}\n\n✅ **ВИЗИТ ПРИНЯТ ШЕФОМ!** (Подтвердил: {actor_name})",
                reply_markup=None,
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Уведомляем клиента
        client_tg_id = booking.get("user_id")
        if client_tg_id and client_tg_id != 0:
            b_name = booking.get("barber_name") or settings.OWNER_NAME
            b_time = booking.get("booking_time") or ""
            service = booking.get("service") or "Стрижка"
            branch = booking.get("branch_name") or settings.SALON_SHORT_NAME
            msg = (
                "👑 **Шеф-мастер ПОДТВЕРДИЛ вашу запись!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"💈 **Мастер:** {b_name}\n"
                f"⏰ **Время:** {b_time}\n"
                f"📍 **Филиал:** {branch}\n\n"
                f"{b_name} ждет вас в назначенное время, ака! 💈"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning("Failed to notify client on vip_accept: %s", e)

        await callback.answer("VIP-запись подтверждена! Клиент уведомлен.")
    except Exception as e:
        logger.error("Error in handle_vip_accept_callback: %s", e)
        await callback.answer("Произошла ошибка при подтверждении VIP-записи.", show_alert=True)


@router.callback_query(F.data.startswith("vip_reject:"))
async def handle_vip_reject_callback(callback: CallbackQuery):
    """Шеф-мастер (Mr. Akmal) или админ отклоняет VIP-запись."""
    user_id = callback.from_user.id if callback.from_user else 0
    role, barber = await get_user_role(user_id)
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = settings.ADMIN_CHAT_ID

    if role not in ("admin", "barber", "super_admin") and user_id not in (admin_id, super_admin_id):
        await callback.answer("У вас нет прав для отклонения этой VIP-записи.", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split(":")[1])
        booking = await get_booking_by_id(booking_id)
        if not booking:
            await callback.answer("Запись не найдена в базе.", show_alert=True)
            return

        if booking.get("status") == "cancelled":
            try:
                old_text = callback.message.text or ""
                await callback.message.edit_text(
                    f"{old_text}\n\n⚠️ **ЗАПИСЬ УЖЕ БЫЛА ОТМЕНЕНА КЛИЕНТОМ**",
                    reply_markup=None,
                    parse_mode="Markdown"
                )
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await callback.answer("⚠️ Эта запись уже была отменена клиентом!", show_alert=True)
            return

        await update_booking_status(booking_id, "cancelled")

        actor_name = barber["name"] if barber else (callback.from_user.first_name or "Шеф")
        try:
            old_text = callback.message.text or ""
            await callback.message.edit_text(
                f"{old_text}\n\n❌ **ВИЗИТ ОТКЛОНЕН ШЕФОМ** (Кем: {actor_name})",
                reply_markup=None,
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Уведомляем клиента с предложением перезаписи
        client_tg_id = booking.get("user_id")
        if client_tg_id and client_tg_id != 0:
            b_name = booking.get("barber_name") or settings.OWNER_NAME
            b_time = booking.get("booking_time") or ""
            base_url = (getattr(settings, "WEBAPP_URL", "") or "").rstrip("/")
            has_https = base_url.startswith("https://")
            if has_https:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="✂️ Выбрать другое время / мастера", web_app=WebAppInfo(url=f"{base_url}/book"))
                    ]]
                )
            else:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="✂️ Выбрать другое время", callback_data="open_booking_dialog")
                    ]]
                )

            msg = (
                f"ℹ️ **К сожалению, Шеф-мастер {b_name} не сможет принять вас в назначенное время ({b_time}).**\n\n"
                "График шефа очень плотный. Пожалуйста, выберите другое удобное окно или запишитесь к любому из наших топ-стилистов:"
            )
            try:
                await callback.bot.send_message(chat_id=client_tg_id, text=msg, reply_markup=kb, parse_mode="Markdown")
            except Exception as e:
                logger.warning("Failed to notify client on vip_reject: %s", e)

        await callback.answer("VIP-запись отклонена. Клиент уведомлен.")
    except Exception as e:
        logger.error("Error in handle_vip_reject_callback: %s", e)
        await callback.answer("Произошла ошибка при отклонении VIP-записи.", show_alert=True)


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

        # 1. Обновляем статус квитанции
        await update_receipt_log_status(receipt_id, "approved")

        # 2. Продлеваем подписку заведения на 30 дней
        tenant_id = receipt.get("tenant_id") or 1
        updated = await extend_tenant_subscription_30_days(tenant_id)

        # 3. Обновляем сообщение в Telegram
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


# Для обратной совместимости
process_booking_intent = process_client_intent

# Регистрация маршрутов в модульных роутерах
common_router.message.register(handle_start, CommandStart())
common_router.message.register(handle_about, Command("about"))
common_router.message.register(handle_reset, Command("clear"))
common_router.message.register(handle_reset, Command("reset"))
common_router.message.register(handle_reset, F.text == "🔄 Сбросить диалог")
common_router.message.register(handle_price, Command("price"))
common_router.message.register(handle_price, F.text == "✂️ Прайс-лист")
common_router.message.register(handle_location, F.text == "📍 Где мы находимся?")

barber_router.message.register(handle_barber, Command("barber"))
barber_router.callback_query.register(handle_vip_accept_callback, F.data.startswith("vip_accept:"))
barber_router.callback_query.register(handle_vip_reject_callback, F.data.startswith("vip_reject:"))

admin_router.message.register(handle_admin, Command("admin"))
admin_router.message.register(handle_admin, F.text == "📊 Панель записей (/admin)")
admin_router.message.register(handle_super, Command("super"))
admin_router.callback_query.register(handle_booking_status_callback, F.data.startswith("bstatus:"))
admin_router.callback_query.register(handle_approve_receipt, F.data.startswith("approve_receipt:"))
admin_router.callback_query.register(handle_reject_receipt, F.data.startswith("reject_receipt:"))

client_router.message.register(handle_voice_message, F.voice)
client_router.message.register(handle_user_text, F.text)
client_router.callback_query.register(handle_confirm_visit_callback, F.data.startswith("confirm_visit:"))
client_router.callback_query.register(handle_cancel_visit_callback, F.data.startswith("cancel_visit:"))
client_router.callback_query.register(handle_open_booking_dialog, F.data == "open_booking_dialog")



