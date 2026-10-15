import re
import json
import logging
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from config import settings
from database import get_recent_messages, add_message, get_active_bookings
from prompts import SYSTEM_PROMPT, FALLBACK_RESPONSE
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
    # Убираем двойные пробелы и висячие знаки препинания в начале
    cleaned = re.sub(r"^\s*[,!.;:]\s*", "", cleaned).strip()
    return cleaned if cleaned else text


class VoiceResult(BaseModel):
    """
    Результат обработки голосового сообщения:
    транскрипция слов пользователя + ответ Иброхима
    """
    user_transcript: str = Field(
        default="",
        description="Точный распознанный текст голосового сообщения клиента на русском или узбекском языке",
    )
    bot_reply: str = Field(
        default="",
        description="Ответ администратора Иброхима клиенту с учетом контекста диалога",
    )


class ExtractedBooking(BaseModel):
    """
    Структура данных для парсинга записи клиента через Gemini
    """
    is_booking_confirmed: bool = Field(
        default=False,
        description="True, если клиент назвал имя и время/день, и запись подтверждена или готова к фиксации",
    )
    customer_name: Optional[str] = Field(
        default=None,
        description="Имя клиента, которое он назвал",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Номер телефона клиента (если клиент написал его)",
    )
    service: Optional[str] = Field(
        default="Стрижка / Комплекс",
        description="Выбранная услуга: Стрижка (100к), Борода (50к), Комплекс (130к)",
    )
    booking_time: Optional[str] = Field(
        default=None,
        description="Желаемые день и время записи (например, 'Завтра в 16:00', 'Вторник 15:00')",
    )


class GeminiService:
    """
    Сервис для работы с Google Gemini API.
    Обеспечивает формирование истории диалога, поддержку текста и голосовых сообщений (audio/ogg),
    валидацию очередности ролей и структурированное извлечение данных записи.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.GEMINI_MODEL or "gemini-2.5-flash"
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        """
        Ленивая инициализация клиента Gemini.
        Позволяет импортировать модули даже до настройки файла .env.
        """
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
        """
        Преобразует список сообщений из базы данных в формат types.Content для Gemini API.
        Гарантирует чередование ролей ('user' -> 'model' -> 'user').
        """
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

    async def generate_response(self, user_id: int, user_message: str) -> str:
        """
        Генерация ответа на текстовое сообщение с учетом истории диалога.
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(
                raw_history=raw_history, current_message=user_message
            )

            # Получаем актуальный график занятости из SQLite для полной осведомленности ИИ
            active_bookings = await get_active_bookings()
            schedule_context = build_schedule_context_for_prompt(active_bookings)
            dynamic_system_prompt = f"{SYSTEM_PROMPT}\n\n{schedule_context}"

            config = types.GenerateContentConfig(
                system_instruction=dynamic_system_prompt,
                temperature=0.4,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            raw_reply = (response.text or "").strip()
            bot_reply = clean_voice_disclaimers(raw_reply) if raw_reply else FALLBACK_RESPONSE

            await add_message(user_id=user_id, role="user", text=user_message)
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при вызове Gemini API для user_id {user_id}: {e}", exc_info=True)
            return FALLBACK_RESPONSE

    async def generate_response_from_voice(
        self, user_id: int, voice_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> Tuple[str, str]:
        """
        Прямая обработка голосового сообщения через Google GenAI SDK:
        1. Распознает точный текст клиента (user_transcript) на русском или узбекском.
        2. Формирует естественный ответ Иброхима (bot_reply) с проверкой базы занятости.
        3. Сохраняет реальный текст в историю SQLite для сквозной памяти между сообщениями.
        Возвращает кортеж (bot_reply, user_transcript).
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(raw_history=raw_history)

            # Добавляем аудио в текущий ход пользователя
            audio_part = types.Part.from_bytes(data=voice_bytes, mime_type=mime_type)
            instruction_part = types.Part.from_text(
                text=(
                    "Ты — администратор Иброхим барбершопа Black Beard. Клиент прислал это голосовое сообщение. "
                    "1. В поле user_transcript укажи точный распознанный текст слов клиента на русском или узбекском языке. "
                    "2. В поле bot_reply дай ответ от имени Иброхима с учетом всей предыдущей истории диалога и графика занятых окон. "
                    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить 'я не слышу голосовые'! Ты прекрасно всё слышишь. "
                    "ВНИМАНИЕ: Сверяйся с занятыми окнами! Длительность процедуры 1 час. Если время занято — вежливо откажи и предложи свободные часы."
                )
            )

            contents.append(
                types.Content(
                    role="user",
                    parts=[audio_part, instruction_part],
                )
            )

            # Получаем актуальный график занятости из SQLite для голосовых сообщений
            active_bookings = await get_active_bookings()
            schedule_context = build_schedule_context_for_prompt(active_bookings)
            dynamic_system_prompt = f"{SYSTEM_PROMPT}\n\n{schedule_context}"

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

            user_transcript = "Голосовое сообщение"
            bot_reply = FALLBACK_RESPONSE

            if response.text:
                try:
                    data = json.loads(response.text)
                    v_res = VoiceResult(**data)
                    user_transcript = v_res.user_transcript or "Голосовое сообщение"
                    bot_reply = clean_voice_disclaimers(v_res.bot_reply) or FALLBACK_RESPONSE
                except Exception:
                    bot_reply = clean_voice_disclaimers(response.text) or FALLBACK_RESPONSE

            # Сохраняем РЕАЛЬНЫЙ транскрибированный текст в историю диалога
            await add_message(user_id=user_id, role="user", text=f"[🎙 Голос]: {user_transcript}")
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply, user_transcript

        except Exception as e:
            logger.error(f"Ошибка при обработке голоса в Gemini для user_id {user_id}: {e}", exc_info=True)
            return FALLBACK_RESPONSE, "Голосовое сообщение"

    async def extract_booking_info(self, dialog_context: str) -> Optional[ExtractedBooking]:
        """
        Структурированное извлечение данных записи (имя, услуга, точное время, телефон)
        с использованием response_schema в Gemini 2.5 Flash с точным вычислением даты по Ташкенту.
        """
        try:
            now_str = format_tashkent_now_for_prompt()
            prompt = (
                "Ты — система фиксации бронирований барбершопа Black Beard.\n"
                f"ТЕКУЩЕЕ ВРЕМЯ В ТАШКЕНТЕ: {now_str}.\n"
                f"Проанализируй диалог клиента и администратора:\n\n{dialog_context}\n\n"
                "Определи, состоялась ли запись клиента.\n"
                "СТРОГОЕ ПРАВИЛО: is_booking_confirmed = true ТОЛЬКО ЕСЛИ в диалоге РЕАЛЬНО названы ОБА параметра: "
                "1) имя клиента И 2) желаемый день или время (например: 'завтра в 15:00'). "
                "Если клиент назвал только время (а имя еще не известно или администратор только спросил имя) — установи is_booking_confirmed = false! "
                "Если клиент назвал и имя, и время — установи is_booking_confirmed = true и извлеки customer_name, booking_time, service, phone.\n\n"
                "КРИТИЧЕСКИ ВАЖНО ДЛЯ booking_time:\n"
                "Никогда не пиши относительные слова вроде 'завтра', 'сегодня', 'bugun', 'ertaga'. "
                "Вычисли ТОЧНУЮ календарную дату по текущему времени Ташкента и запиши в каноническом формате:\n"
                "'YYYY-MM-DD HH:MM (ДеньНедели)'.\n"
                "Например, если сейчас 2026-10-15 (Четверг) и клиент говорит 'завтра в 15:00', то booking_time должен быть строго:\n"
                "'2026-10-16 15:00 (Пятница)'."
            )

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractedBooking,
                temperature=0.1,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )

            if response.text:
                data = json.loads(response.text)
                return ExtractedBooking(**data)
            return None

        except Exception as e:
            logger.warning(f"Не удалось извлечь структурированные данные записи: {e}")
            return None
