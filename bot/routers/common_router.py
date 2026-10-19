"""
Модульный роутер общих команд и навигации (Common Router).
Обрабатывает:
- /start (с персонализацией по роли и TMA кнопкой)
- /about (информация о салоне и разработчике)
- /reset (очистка контекста диалога)
- /price (актуальный прайс-лист)
- локации и адреса филиалов
"""

import logging
from typing import Optional, Tuple
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

from config import settings
from database import (
    clear_history,
    get_barber_by_telegram_id,
    get_services,
    get_branches,
    get_salon_setting,
)

logger = logging.getLogger(__name__)

router = Router(name="common_router")

# Фирменная инлайн-клавиатура разработчика
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


async def get_user_role(user_id: int) -> Tuple[str, Optional[dict]]:
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
    """Генерация прозрачной inline-кнопки под сообщением /start для вызова Telegram Mini App"""
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
    """Обработчик команды /start с автоопределением роли и Mini App кнопкой"""
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
    """Информация об авторстве и коммерческой разработке AI-систем"""
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


@router.message(Command("clear"))
@router.message(Command("reset"))
@router.message(F.text == "🔄 Сбросить диалог")
async def handle_reset(message: Message):
    """Секретная команда /clear (и /reset) для очистки контекста диалога текущего пользователя."""
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
    """Быстрый просмотр актуального прайс-листа из базы данных без хардкода."""
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
        lines.append("\n_В стрижку включены мытье головы, премиум-стайлинг и напитки._ ☕\n")
    else:
        lines.append("Информация об услугах обновляется администрацией.\n")

    lines.append("На какой день и время вас записать, ака?")
    price_text = "\n".join(lines)
    await message.answer(price_text, parse_mode="Markdown", reply_markup=get_main_keyboard(role=role, barber=barber))


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """Информация о локациях барбершопа из базы данных без хардкода."""
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
