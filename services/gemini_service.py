import logging
from typing import List, Dict, Optional
from google import genai
from google.genai import types

from config import settings
from database import get_recent_messages, add_message
from prompts import SYSTEM_PROMPT, FALLBACK_RESPONSE

logger = logging.getLogger(__name__)


class GeminiService:
    """
    Сервис для работы с Google Gemini API.
    Обеспечивает формирование истории диалога, валидацию очередности ролей
    и асинхронный вызов модели gemini-2.5-flash через client.aio.
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
        self, raw_history: List[Dict[str, str]], current_message: str
    ) -> List[types.Content]:
        """
        Преобразует список сообщений из базы данных в формат types.Content для Gemini API.
        
        Особенности Gemini API:
        1. Диалог должен начинаться с реплики 'user'.
        2. Роли должны строго чередоваться ('user' -> 'model' -> 'user' -> 'model').
        3. Если в истории две одинаковые роли подряд, объединяем их текст.
        """
        contents: List[types.Content] = []

        # Собираем все реплики: история из БД + текущее сообщение пользователя
        all_turns = list(raw_history) + [{"role": "user", "text": current_message}]

        for turn in all_turns:
            role = turn["role"]
            text = turn["text"].strip()
            if not text:
                continue

            # Если список пуст и первая реплика - от модели, пропускаем её,
            # так как Gemini требует, чтобы контекст начинался с 'user'
            if not contents and role != "user":
                continue

            # Если предыдущая реплика была от той же роли — объединяем текст
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
        Основной метод генерации ответа:
        1. Извлекает последние 10 сообщений (5-6 пар реплик) из SQLite.
        2. Формирует структурированный контекст types.Content.
        3. Передает запрос в Gemini через client.aio.models.generate_content с системным промптом.
        4. Сохраняет реплику пользователя и ответ модели в SQLite.
        5. При сбое сети/API возвращает вежливый fallback.
        """
        try:
            # 1. Загрузка последних сообщений диалога из БД
            raw_history = await get_recent_messages(user_id=user_id, limit=10)

            # 2. Преобразование истории в объекты Content для Gemini
            contents = self.format_history_to_contents(
                raw_history=raw_history, current_message=user_message
            )

            # 3. Конфигурация вызова: системный промпт Иброхима + температура
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            )

            # 4. Асинхронный вызов Gemini API без блокировки event loop
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            bot_reply = (response.text or "").strip()
            if not bot_reply:
                bot_reply = FALLBACK_RESPONSE

            # 5. Сохраняем диалог в базу для будущих обращений
            await add_message(user_id=user_id, role="user", text=user_message)
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при вызове Gemini API для user_id {user_id}: {e}", exc_info=True)
            return FALLBACK_RESPONSE
