import logging
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction

from config import settings
from database import clear_history, save_booking
from services.gemini_service import GeminiService

logger = logging.getLogger(__name__)

router = Router(name="main_router")

# Инициализация сервиса Gemini
gemini_service = GeminiService()

# Удобная клавиатура быстрых действий для клиента
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✂️ Прайс-лист"), KeyboardButton(text="📍 Где мы находимся?")],
        [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="🔄 Сбросить диалог")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


@router.message(CommandStart())
async def handle_start(message: Message):
    """
    Обработчик команды /start.
    Приветствие от имени Иброхима и показ клавиатуры.
    """
    user_name = message.from_user.first_name or "братан"
    welcome_text = (
        f"Салам алейкум, {user_name}! 🤝\n\n"
        "Добро пожаловать в премиум-барбершоп **Black Beard** в Ташкенте!\n"
        "Я Иброхим — администратор. Чем могу помочь, ака?\n\n"
        "Подсказать по прайсу или сразу подобрать удобное время для записи?"
    )
    await message.answer(welcome_text, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")


@router.message(Command("reset"))
@router.message(F.text == "🔄 Сбросить диалог")
async def handle_reset(message: Message):
    """
    Очистка контекста диалога текущего пользователя в базе данных.
    """
    await clear_history(message.from_user.id)
    await message.answer(
        "История диалога очищена, братан! Начнем с чистого листа. О чем поболтаем? 💈",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("price"))
@router.message(F.text == "✂️ Прайс-лист")
async def handle_price(message: Message):
    """
    Быстрый просмотр прайс-листа без ожидания нейросети.
    """
    price_text = (
        "🔥 **Прайс-лист барбершопа 'Black Beard' (Ташкент):**\n\n"
        "✂️ **Мужская стрижка:** 100 000 сум\n"
        "🧔 **Оформление бороды:** 50 000 сум\n"
        "👑 **Комплекс 'Black Beard' (Стрижка + Борода):** 130 000 сум *(Выгодный сет!)*\n\n"
        "_В каждую услугу включены мытье головы, премиум-стайлинг и ароматный кофе или чай._ ☕\n\n"
        "На какой день и время вас записать, ака?"
    )
    await message.answer(price_text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


@router.message(F.text == "📍 Где мы находимся?")
async def handle_location(message: Message):
    """
    Информация о локации барбершопа.
    """
    location_text = (
        "📍 Мы находимся в центре Ташкента: ул. Амира Темура (ориентир — гостиница 'Узбекистан').\n\n"
        "Работаем ежедневно с 10:00 до 22:00 без выходных. Ждем в гости, ака!"
    )
    await message.answer(location_text, reply_markup=MAIN_KEYBOARD)


@router.message(F.text)
async def handle_user_text(message: Message):
    """
    Основной обработчик текстовых сообщений:
    1. Показывает статус 'печатает...' (typing action) в Telegram.
    2. Отправляет запрос в GeminiService с историей сообщений пользователя.
    3. Отправляет ответ клиенту.
    4. При фиксации записи (наличие имени и времени) сохраняет лид и уведомляет администратора.
    """
    user_id = message.from_user.id
    user_text = message.text.strip()

    # 1. Индикация "Иброхим печатает..." во время запроса к AI
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # 2. Получение ответа от Gemini с учетом истории
    ai_reply = await gemini_service.generate_response(user_id=user_id, user_message=user_text)

    # 3. Отправка ответа клиенту
    await message.answer(ai_reply, reply_markup=MAIN_KEYBOARD)

    # 4. Проверка на признаки записи и уведомление владельца
    booking_keywords = ["записал", "жду вас", "ждем вас", "бронь подтверждена", "записал вас", "kutamiz", "yozib qo'ydim"]
    if any(keyword in ai_reply.lower() for keyword in booking_keywords):
        # Фиксируем лид в SQLite
        full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "Клиент"
        username = f"@{message.from_user.username}" if message.from_user.username else "Нет username"
        
        await save_booking(
            user_id=user_id,
            username=username,
            full_name=full_name,
            details=f"Запрос клиента: {user_text} | Ответ Иброхима: {ai_reply[:200]}"
        )

        # Если задан ADMIN_CHAT_ID в .env, пересылаем уведомление администратору
        if settings.ADMIN_CHAT_ID:
            admin_notify_text = (
                "🚨 **Новая запись в барбершоп Black Beard!**\n\n"
                f"👤 **Клиент:** {full_name} ({username})\n"
                f"🆔 **User ID:** `{user_id}`\n"
                f"💬 **Сообщение:** {user_text}\n"
                f"🤖 **Ответ Иброхима:** {ai_reply}"
            )
            try:
                await message.bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_notify_text,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление администратору: {e}")
