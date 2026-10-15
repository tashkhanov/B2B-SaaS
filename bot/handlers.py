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
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction

from config import settings
from database import clear_history, save_booking, has_recent_booking
from services.gemini_service import GeminiService

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


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Генерация основной клавиатуры для клиентов и администратора"""
    keyboard = [
        [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
        [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Сбросить диалог")],
    ]
    if is_admin:
        # Telegram Bot API требует исключительно HTTPS для кнопок WebApp
        if settings.WEBAPP_URL and settings.WEBAPP_URL.startswith("https://"):
            keyboard.append([
                KeyboardButton(
                    text="📊 Панель записей (TMA)",
                    web_app=WebAppInfo(url=settings.WEBAPP_URL),
                )
            ])
        else:
            keyboard.append([KeyboardButton(text="📊 Панель записей (/admin)")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=True)


@router.message(CommandStart())
async def handle_start(message: Message):
    """
    Обработчик команды /start.
    Приветствие от имени Иброхима и показ клавиатуры.
    """
    user_name = message.from_user.first_name or "братан"
    is_admin = bool(settings.ADMIN_CHAT_ID and message.from_user.id == settings.ADMIN_CHAT_ID)
    
    welcome_text = (
        f"Салам алейкум, {user_name}! 🤝\n\n"
        "Добро пожаловать в премиум-барбершоп **Black Beard** в Ташкенте!\n"
        "Я Иброхим — администратор. Чем могу помочь, ака?\n\n"
        "Подсказать по прайсу или сразу подобрать удобное время для записи?"
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(is_admin=is_admin),
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

    if settings.WEBAPP_URL and settings.WEBAPP_URL.startswith("https://"):
        admin_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📊 Открыть панель записей (TMA)",
                        web_app=WebAppInfo(url=settings.WEBAPP_URL),
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
        admin_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Открыть панель в браузере",
                        url=settings.WEBAPP_URL,
                    )
                ]
            ]
        )
        await message.answer(
            "🛠 **Панель управления барбершопом Black Beard:**\n\n"
            f"🔗 Локальный адрес панели: `{settings.WEBAPP_URL}`\n\n"
            "💡 *Telegram требует HTTPS для встраивания кнопок Mini App.*\n"
            "Чтобы панель открывалась прямо внутри Telegram:\n"
            "1. Запустите в новом терминале бесплатный туннель: `ngrok http 8000` (или `npx localtunnel --port 8000`)\n"
            "2. Скопируйте полученную https-ссылку (например, `https://xyz.ngrok-free.app`)\n"
            "3. Укажите её в `.env`: `WEBAPP_URL=https://xyz.ngrok-free.app` и перезапустите бота.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )


@router.message(Command("reset"))
@router.message(F.text == "🔄 Сбросить диалог")
async def handle_reset(message: Message):
    """
    Очистка контекста диалога текущего пользователя в базе данных.
    """
    is_admin = bool(settings.ADMIN_CHAT_ID and message.from_user.id == settings.ADMIN_CHAT_ID)
    await clear_history(message.from_user.id)
    await message.answer(
        "История диалога очищена, братан! Начнем с чистого листа. О чем поболтаем? 💈",
        reply_markup=get_main_keyboard(is_admin=is_admin),
    )


@router.message(Command("price"))
@router.message(F.text == "✂️ Прайс-лист")
async def handle_price(message: Message):
    """
    Быстрый просмотр прайс-листа без ожидания нейросети.
    """
    user_id = message.from_user.id if message.from_user else 0
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)
    price_text = (
        "🔥 **Прайс-лист барбершопа 'Black Beard' (Ташкент):**\n\n"
        "✂️ **Мужская стрижка:** 100 000 сум\n"
        "🧔 **Оформление бороды:** 50 000 сум\n"
        "👑 **Комплекс 'Black Beard' (Стрижка + Борода):** 130 000 сум *(Выгодный сет!)*\n\n"
        "_В каждую услугу включены мытье головы, премиум-стайлинг и ароматный кофе или чай._ ☕\n\n"
        "На какой день и время вас записать, ака?"
    )
    await message.answer(price_text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin=is_admin))


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """
    Информация о локации барбершопа.
    """
    user_id = message.from_user.id if message.from_user else 0
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)
    location_text = (
        "📍 Мы находимся в центре Ташкента: ул. Амира Темура (ориентир — гостиница 'Узбекистан').\n\n"
        "Работаем ежедневно с 10:00 до 22:00 без выходных. Ждем в гости, ака!"
    )
    await message.answer(location_text, reply_markup=get_main_keyboard(is_admin=is_admin))


@router.message(F.voice)
async def handle_voice_message(message: Message):
    """
    Обработчик голосовых сообщений клиента:
    1. Скачивает аудиофайл (OGG Opus) в память.
    2. Передает байты напрямую в Google GenAI SDK (Gemini).
    3. Отправляет клиенту сгенерированный голосовой ответ Иброхима.
    4. Очищает временные данные из памяти.
    5. Проверяет наличие намерения записи.
    """
    user_id = message.from_user.id
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)

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

    # 2. Обработка голоса через Gemini 2.5 Flash (распознавание + генерация ответа)
    ai_reply, user_transcript = await gemini_service.generate_response_from_voice(
        user_id=user_id,
        voice_bytes=voice_data,
        mime_type="audio/ogg"
    )

    # 3. Отправка ответа клиенту
    await message.answer(ai_reply, reply_markup=get_main_keyboard(is_admin=is_admin))

    # 4. Проверка и сохранение записи с реальным текстом из голосового сообщения
    await process_booking_intent(message, client_query=f"[🎙 Голос]: {user_transcript}", ai_reply=ai_reply)


@router.message(F.text)
async def handle_user_text(message: Message):
    """
    Основной обработчик текстовых сообщений:
    1. Показывает статус 'печатает...' (typing action) в Telegram.
    2. Отправляет запрос в GeminiService с историей сообщений пользователя.
    3. Отправляет ответ клиенту.
    4. При фиксации записи сохраняет структурированный лид и уведомляет администратора.
    """
    user_id = message.from_user.id
    user_text = message.text.strip()
    is_admin = bool(settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID)

    # 1. Индикация "Иброхим печатает..." во время запроса к AI
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 2. Получение ответа от Gemini с учетом истории
    ai_reply = await gemini_service.generate_response(user_id=user_id, user_message=user_text)

    # 3. Отправка ответа клиенту
    await message.answer(ai_reply, reply_markup=get_main_keyboard(is_admin=is_admin))

    # 4. Проверка и фиксация структурированной брони
    await process_booking_intent(message, client_query=user_text, ai_reply=ai_reply)


async def process_booking_intent(message: Message, client_query: str, ai_reply: str) -> None:
    """
    Интеллектуальная проверка и фиксация структурированной записи:
    Анализирует диалог через Gemini. Запись создается ТОЛЬКО если
    в диалоге согласованы ОБА параметра: имя клиента и дата/время.
    """
    user_id = message.from_user.id

    # Проверяем, затрагивает ли диалог тему записи или времени
    combined = f"{client_query} {ai_reply}".lower()
    booking_triggers = [
        "запис", "бронь", "жд", "время", "имя", "завтра", "сегодня", "ок", "договор",
        "yoz", "kut", "soat", "ism", "kun", "ertaga", "bugun", "kel", "vaqt",
        "00", "30", "1", "2", "3", "4", "5", "6", "7", "8", "9"
    ]
    if not any(t in combined for t in booking_triggers):
        return

    # Извлекаем последние реплики диалога
    recent_msgs = await get_recent_messages(user_id=user_id, limit=6)
    dialog_lines = [f"{'Клиент' if m['role'] == 'user' else 'Иброхим'}: {m['text']}" for m in recent_msgs]
    dialog_sample = "\n".join(dialog_lines) if dialog_lines else f"Клиент: {client_query}\nИброхим: {ai_reply}"

    # Структурированный анализ через Gemini
    extracted = await gemini_service.extract_booking_info(dialog_sample)
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: фиксируем запись ТОЛЬКО если подтверждено и имя, и время!
    if not extracted or not extracted.is_booking_confirmed:
        return

    full_name = (
        extracted.customer_name
        or f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
        or "Гость"
    )
    username = f"@{message.from_user.username}" if message.from_user.username else None
    phone = extracted.phone or None
    service = extracted.service or "Стрижка / Комплекс"
    booking_time = extracted.booking_time or "Уточнено в диалоге"

    # Защита от дублей: если за последние 15 минут уже есть запись на это время
    if await has_recent_booking(user_id=user_id, booking_time=booking_time):
        logger.info(f"Запись на {booking_time} для user_id {user_id} уже была сохранена ранее.")
        return

    # Сохраняем в SQLite таблицу bookings со структурированными колонками
    await save_booking(
        user_id=user_id,
        username=username,
        full_name=full_name,
        details=f"Запрос: {client_query} | Ответ: {ai_reply[:200]}",
        phone=phone,
        service=service,
        booking_time=booking_time,
        status="new",
    )

    # Отправляем уведомление администратору в личку (если настроен ADMIN_CHAT_ID)
    if settings.ADMIN_CHAT_ID:
        admin_card = (
            "💈 **НОВАЯ ЗАПИСЬ В БАРБЕРШОП BLACK BEARD!**\n\n"
            f"👤 **Клиент:** {full_name} ({username or 'нет username'})\n"
            f"✂️ **Услуга:** {service}\n"
            f"⏰ **Время:** {booking_time}\n"
            f"📞 **Телефон:** {phone or 'Не указан'}\n"
            f"🆔 **Telegram ID:** `{user_id}`\n\n"
            f"💬 **Контекст:** _{client_query}_\n\n"
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

    # Предлагаем аккуратную кнопку брендинга разработчика
    await message.answer(
        "🤝 Мы ценим ваше доверие! До встречи в Black Beard.",
        reply_markup=AI_BUSINESS_KEYBOARD,
    )
