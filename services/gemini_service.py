import json
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from config import settings
from database import get_recent_messages, add_message
from prompts import SYSTEM_PROMPT, FALLBACK_RESPONSE

logger = logging.getLogger(__name__)


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
        description="Желаемые день и время записи (например, 'Завтра в 16:00', 'Пятница 18:30')",
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

            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            bot_reply = (response.text or "").strip()
            if not bot_reply:
                bot_reply = FALLBACK_RESPONSE

            await add_message(user_id=user_id, role="user", text=user_message)
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при вызове Gemini API для user_id {user_id}: {e}", exc_info=True)
            return FALLBACK_RESPONSE

    async def generate_response_from_voice(
        self, user_id: int, voice_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> str:
        """
        Прямая обработка голосового сообщения через Google GenAI SDK:
        Gemini нативно слушает аудиозапись (на русском или узбекском),
        учитывает контекст предыдущего диалога и генерирует ответ в образе Иброхима.
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(raw_history=raw_history)

            # Добавляем аудио в текущий ход пользователя
            audio_part = types.Part.from_bytes(data=voice_bytes, mime_type=mime_type)
            instruction_part = types.Part.from_text(
                text="[Клиент отправил голосовое сообщение. Внимательно прослушай его на русском или узбекском языке и ответь в своем фирменном ташкентском стиле администратора Иброхима.]"
            )

            contents.append(
                types.Content(
                    role="user",
                    parts=[audio_part, instruction_part],
                )
            )

            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            )

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            bot_reply = (response.text or "").strip()
            if not bot_reply:
                bot_reply = FALLBACK_RESPONSE

            # Сохраняем пометку о голосовом сообщении и ответ модели
            await add_message(user_id=user_id, role="user", text="[🎙 Голосовое сообщение]")
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при обработке голоса в Gemini для user_id {user_id}: {e}", exc_info=True)
            return FALLBACK_RESPONSE

    async def extract_booking_info(self, dialog_context: str) -> Optional[ExtractedBooking]:
        """
        Структурированное извлечение данных записи (имя, услуга, время, телефон)
        с использованием response_schema в Gemini 2.5 Flash.
        """
        try:
            prompt = (
                "Проанализируй диалог клиента и администратора барбершопа Black Beard. "
                "Определи, подтвердил ли клиент свое намерение записаться, назвал ли он свое имя и желаемое время. "
                "Если да — извлеки имя, телефон (если есть), выбранную услугу и желаемое время/дату:\n\n"
                f"{dialog_context}"
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
