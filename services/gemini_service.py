import re
import json
import logging
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from config import settings
from database import get_recent_messages, add_message, get_active_bookings, get_all_bookings
from prompts import (
    CLIENT_SYSTEM_PROMPT,
    ADMIN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    FALLBACK_RESPONSE,
    ADMIN_FALLBACK_RESPONSE,
)
from services.schedule_service import (
    build_schedule_context_for_prompt,
    format_tashkent_now_for_prompt,
)

logger = logging.getLogger(__name__)


def clean_voice_disclaimers(text: str) -> str:
    """
    Удаляет случайные шаблонные оговорки нейросети про 'не слышу голосовые'.
    """
    patterns = [
        r"(?i)голосовы[ея]\s+сообщения?\s+(пока\s+)?не\s+слышу,?\s*(но\s+)?(текст\s+твой\s+понял|всё\s+понял|текст\s+понял)?,?\s*(от\s+души!?)?",
        r"(?i)голосово[ея]\s+не\s+слышу,?\s*(но\s+)?(всё\s+понял|текст\s+понял)?,?\s*(гап\s+йўқ!?)?",
        r"(?i)я\s+не\s+(умею\s+)?слушать\s+голосовы[ея],?\s*",
        r"(?i)не\s+могу\s+слушать\s+аудио,?\s*",
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    cleaned = re.sub(r"^\s*[,!.;:]\s*", "", cleaned).strip()
    return cleaned if cleaned else text


class VoiceResult(BaseModel):
    """
    Результат обработки голосового сообщения:
    транскрипция слов пользователя + ответ ассистента
    """
    user_transcript: str = Field(
        default="",
        description="Точный распознанный текст голосового сообщения клиента на русском или узбекском языке",
    )
    bot_reply: str = Field(
        default="",
        description="Ответ ассистента пользователю с учетом контекста диалога",
    )


class ClientAction(BaseModel):
    """
    Структура намерения клиента в чате с ИИ
    """
    action: str = Field(
        default="chat",
        description="Тип действия клиента: 'create_booking', 'cancel_my_booking', 'check_my_booking', 'chat'",
    )
    is_booking_confirmed: bool = Field(
        default=False,
        description="True, если названы и согласованы имя И время (для create_booking)",
    )
    customer_name: Optional[str] = Field(
        default=None,
        description="Имя клиента",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Номер телефона",
    )
    service: Optional[str] = Field(
        default="Стрижка / Комплекс",
        description="Выбранная услуга",
    )
    booking_time: Optional[str] = Field(
        default=None,
        description="Каноническая дата и время 'YYYY-MM-DD HH:MM (ДеньНедели)'",
    )


class AdminAction(BaseModel):
    """
    Структура намерения администратора в чате с ИИ
    """
    action: str = Field(
        default="chat",
        description="Действие админа: 'query_schedule', 'create_booking', 'confirm_booking', 'cancel_booking', 'get_stats', 'chat'",
    )
    client_query: Optional[str] = Field(
        default=None,
        description="Имя или @username клиента, о котором идет речь (например, 'Рустам', '@shadowwcode')",
    )
    target_date: Optional[str] = Field(
        default=None,
        description="Целевая дата для расписания (например, 'сегодня', 'завтра', '2026-10-16')",
    )
    service: Optional[str] = Field(
        default="Стрижка / Комплекс",
        description="Услуга при ручном создании записи",
    )
    booking_time: Optional[str] = Field(
        default=None,
        description="Канонические дата и время 'YYYY-MM-DD HH:MM' при ручной записи",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Телефон клиента при ручной записи",
    )


# Для обратной совместимости с существующими тестами
class ExtractedBooking(BaseModel):
    is_booking_confirmed: bool = Field(default=False)
    customer_name: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)
    service: Optional[str] = Field(default="Стрижка / Комплекс")
    booking_time: Optional[str] = Field(default=None)


class GeminiService:
    """
    Сервис для работы с Google Gemini API.
    Поддерживает дуальный режим работы (Клиент vs Администратор),
    текстовые и голосовые сообщения, структурированное извлечение намерений.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.GEMINI_MODEL or "gemini-2.5-flash"
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            key = self.api_key or "AIzaSy_dummy_api_key_placeholder"
            self._client = genai.Client(api_key=key)
        return self._client

    @client.setter
    def client(self, value: genai.Client) -> None:
        self._client = value

    def format_history_to_contents(
        self, raw_history: List[Dict[str, str]], current_message: Optional[str] = None
    ) -> List[types.Content]:
        contents: List[types.Content] = []
        all_turns = list(raw_history)
        if current_message:
            all_turns.append({"role": "user", "text": current_message})

        for turn in all_turns:
            role = turn["role"]
            text = turn["text"].strip()
            if not text:
                continue

            if not contents and role != "user":
                continue

            if contents and contents[-1].role == role:
                last_part = contents[-1].parts[0]
                last_part.text = f"{last_part.text}\n{text}"
            else:
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=text)],
                    )
                )

        return contents

    async def generate_response(self, user_id: int, user_message: str, is_admin: bool = False) -> str:
        """
        Генерация ответа на текстовое сообщение с учетом роли (клиент или админ).
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(
                raw_history=raw_history, current_message=user_message
            )

            # Формируем контекст расписания
            active_bookings = await get_active_bookings()
            schedule_context = build_schedule_context_for_prompt(active_bookings)

            base_prompt = ADMIN_SYSTEM_PROMPT if is_admin else CLIENT_SYSTEM_PROMPT
            dynamic_system_prompt = f"{base_prompt}\n\n{schedule_context}"

            config = types.GenerateContentConfig(
                system_instruction=dynamic_system_prompt,
                temperature=0.3 if is_admin else 0.4,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            fallback = ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE
            raw_reply = (response.text or "").strip()
            bot_reply = clean_voice_disclaimers(raw_reply) if raw_reply else fallback

            await add_message(user_id=user_id, role="user", text=user_message)
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при вызове Gemini API для user_id {user_id}: {e}", exc_info=True)
            return ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE

    async def generate_response_from_voice(
        self, user_id: int, voice_bytes: bytes, mime_type: str = "audio/ogg", is_admin: bool = False
    ) -> Tuple[str, str]:
        """
        Обработка голосового сообщения (распознавание + генерация ответа).
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(raw_history=raw_history)

            audio_part = types.Part.from_bytes(data=voice_bytes, mime_type=mime_type)
            role_desc = "AI-Ассистент управляющего" if is_admin else "администратор Иброхим"
            instruction_part = types.Part.from_text(
                text=(
                    f"Ты — {role_desc} барбершопа Black Beard. Получено голосовое сообщение. "
                    "1. В поле user_transcript укажи точный распознанный текст слов на русском или узбекском языке. "
                    "2. В поле bot_reply дай ответ с учетом контекста диалога и графика занятых окон. "
                    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить 'я не слышу голосовые'!"
                )
            )

            contents.append(
                types.Content(
                    role="user",
                    parts=[audio_part, instruction_part],
                )
            )

            active_bookings = await get_active_bookings()
            schedule_context = build_schedule_context_for_prompt(active_bookings)
            base_prompt = ADMIN_SYSTEM_PROMPT if is_admin else CLIENT_SYSTEM_PROMPT
            dynamic_system_prompt = f"{base_prompt}\n\n{schedule_context}"

            config = types.GenerateContentConfig(
                system_instruction=dynamic_system_prompt,
                response_mime_type="application/json",
                response_schema=VoiceResult,
                temperature=0.3,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            fallback = ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE
            user_transcript = "Голосовое сообщение"
            bot_reply = fallback

            if response.text:
                try:
                    data = json.loads(response.text)
                    v_res = VoiceResult(**data)
                    user_transcript = v_res.user_transcript or "Голосовое сообщение"
                    bot_reply = clean_voice_disclaimers(v_res.bot_reply) or fallback
                except Exception:
                    bot_reply = clean_voice_disclaimers(response.text) or fallback

            await add_message(user_id=user_id, role="user", text=f"[🎙 Голос]: {user_transcript}")
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply, user_transcript

        except Exception as e:
            logger.error(f"Ошибка при обработке голоса в Gemini для user_id {user_id}: {e}", exc_info=True)
            fallback = ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE
            return fallback, "Голосовое сообщение"

    async def extract_client_action(self, dialog_context: str) -> Optional[ClientAction]:
        """
        Структурированное определение намерения клиента:
        'create_booking', 'cancel_my_booking', 'check_my_booking', 'chat'.
        """
        try:
            now_str = format_tashkent_now_for_prompt()
            prompt = (
                "Ты — анализатор диалогов клиентов барбершопа Black Beard.\n"
                f"ТЕКУЩЕЕ ВРЕМЯ В ТАШКЕНТЕ: {now_str}.\n\n"
                f"Диалог:\n{dialog_context}\n\n"
                "Определи действие клиента:\n"
                "1. action = 'cancel_my_booking' — если клиент просит отменить бронь ('отмени', 'не приду', 'отмена', 'bekor qil').\n"
                "2. action = 'check_my_booking' — если клиент спрашивает, когда он записан ('когда я записан?', 'на сколько моя стрижка?').\n"
                "3. action = 'create_booking' — если клиент хочет записаться. "
                "СТРОГОЕ ПРАВИЛО: is_booking_confirmed = true ТОЛЬКО ЕСЛИ в диалоге реально названы И имя клиента, И желаемое время!\n"
                "КРИТИЧЕСКИ ВАЖНО: booking_time пиши только в каноническом формате 'YYYY-MM-DD HH:MM (ДеньНедели)' с точной датой.\n"
                "4. action = 'chat' — во всех остальных случаях (вопрос по прайсу, локации и т.д.)."
            )

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ClientAction,
                temperature=0.1,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )

            if response.text:
                data = json.loads(response.text)
                return ClientAction(**data)
            return None

        except Exception as e:
            logger.warning(f"Ошибка extract_client_action: {e}")
            return None

    async def extract_admin_action(self, admin_query: str) -> Optional[AdminAction]:
        """
        Структурированное определение намерения управляющего:
        'query_schedule', 'create_booking', 'confirm_booking', 'cancel_booking', 'get_stats', 'chat'.
        """
        try:
            now_str = format_tashkent_now_for_prompt()
            prompt = (
                "Ты — система управления командами владельца барбершопа Black Beard.\n"
                f"ТЕКУЩЕЕ ВРЕМЯ В ТАШКЕНТЕ: {now_str}.\n\n"
                f"Запрос администратора: {admin_query}\n\n"
                "Определи намерение администратора:\n"
                "- 'query_schedule': запросы расписания ('кто сегодня?', 'что на завтра?', 'покажи пятницу'). Извлеки target_date.\n"
                "- 'create_booking': ручная запись клиента ('запиши Сардора завтра на 16:00, тел +99890...'). Извлеки client_query, booking_time, service, phone.\n"
                "- 'confirm_booking': подтверждение записи ('подтверди запись Рустама', 'подтверди 15:00'). Извлеки client_query или booking_time.\n"
                "- 'cancel_booking': отмена записи ('отмени запись Рустама', 'отмени 14:00'). Извлеки client_query или booking_time.\n"
                "- 'get_stats': статистика ('сколько записей?', 'статистика').\n"
                "- 'chat': обычный вопрос."
            )

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AdminAction,
                temperature=0.1,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )

            if response.text:
                data = json.loads(response.text)
                return AdminAction(**data)
            return None

        except Exception as e:
            logger.warning(f"Ошибка extract_admin_action: {e}")
            return None

    async def extract_booking_info(self, dialog_context: str) -> Optional[ExtractedBooking]:
        """Обратная совместимость"""
        action = await self.extract_client_action(dialog_context)
        if action and (action.action == "create_booking" or action.is_booking_confirmed):
            return ExtractedBooking(
                is_booking_confirmed=action.is_booking_confirmed,
                customer_name=action.customer_name,
                phone=action.phone,
                service=action.service,
                booking_time=action.booking_time,
            )
        return None
