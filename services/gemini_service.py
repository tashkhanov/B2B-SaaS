import re
import json
import time
import asyncio
import logging
from typing import List, Dict, Optional, Tuple, Any
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from config import settings
from database import (
    get_recent_messages,
    add_message,
    get_active_bookings,
    get_all_bookings,
    get_barbers,
    get_services,
    get_branches,
    get_all_salon_settings,
)
from prompts import (
    CLIENT_SYSTEM_PROMPT,
    ADMIN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    CREATOR_IDENTITY_PROMPT,
    FALLBACK_RESPONSE,
    ADMIN_FALLBACK_RESPONSE,
    build_client_system_prompt,
    build_admin_system_prompt,
    build_creator_identity_prompt,
)
from services.schedule_service import (
    build_schedule_context_for_prompt,
    format_tashkent_now_for_prompt,
)
from services.booking_service import (
    GeminiBookingTools,
    resolve_branch,
    resolve_service_info,
    dispatch_vip_approval_card,
    is_invalid_customer_name,
)

logger = logging.getLogger(__name__)


def is_super_admin_user(user_id: Optional[int]) -> bool:
    """Проверка, является ли пользователь Создателем и Супер-Админом"""
    if not user_id:
        return False
    super_admin_id = getattr(settings, "SUPER_ADMIN_CHAT_ID", None)
    admin_id = getattr(settings, "ADMIN_CHAT_ID", None)
    return bool((super_admin_id and user_id == super_admin_id) or (admin_id and user_id == admin_id) or user_id == 1243374131)


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


def clean_bot_reply(text: str) -> str:
    """
    Очищает ответ бота от шаблонных оговорок про голосовые,
    удаляет галлюцинированные скобки с прозвищами (например '(Акимка)')
    и исправляет опечатки в имени Шеф-мастера ('акал ака' -> 'Акмал ака').
    """
    if not text:
        return text
    cleaned = clean_voice_disclaimers(text)
    # Удаляем скобки с кличками/пояснениями: (Акимка), (акимка), (клиент), (прозвище)
    cleaned = re.sub(r"\s*\((?:Акимка|акимка|Аким|аким|прозвище|кличка|клиент|гость|малы)\)", "", cleaned, flags=re.IGNORECASE)
    # Исправляем опечатки/искажения в имени Акмаля
    cleaned = re.sub(r"(?i)\bакал\s+ака\b", "Акмал ака", cleaned)
    cleaned = re.sub(r"(?i)\bакал\b", "Акмал", cleaned)
    return cleaned.strip()


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
    barber_name: Optional[str] = Field(
        default=None,
        description="Имя выбранного барбера (например 'Иброхим', 'Сардор', 'Жасур') или None/'Любой'",
    )
    barber_id: Optional[int] = Field(
        default=None,
        description="ID барбера, если известен",
    )
    branch_name: Optional[str] = Field(
        default=None,
        description="Название выбранного филиала салона из базы данных",
    )
    branch_id: Optional[int] = Field(
        default=None,
        description="ID выбранного филиала из базы данных",
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
    barber_name: Optional[str] = Field(
        default=None,
        description="Имя мастера (для фильтрации расписания или создания записи)",
    )
    barber_id: Optional[int] = Field(
        default=None,
        description="ID мастера",
    )
    branch_name: Optional[str] = Field(
        default=None,
        description="Название филиала из базы данных",
    )
    branch_id: Optional[int] = Field(
        default=None,
        description="ID филиала из базы данных",
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
        self.model_name = model_name or settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
        self._client: Optional[genai.Client] = None
        self.booking_tools = GeminiBookingTools()
        self.last_user_tools: Dict[int, List[str]] = {}
        self.exhausted_models: Dict[str, float] = {}

    def had_tool_calls(self, user_id: int) -> bool:
        """Проверяет, были ли вызваны инструменты в последнем запросе пользователя."""
        return bool(self.last_user_tools.get(user_id))

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            key = self.api_key or "AIzaSy_dummy_api_key_placeholder"
            self._client = genai.Client(api_key=key)
        return self._client

    @client.setter
    def client(self, value: genai.Client) -> None:
        self._client = value

    def _all_api_keys(self) -> List[str]:
        """Основной ключ + резервные из GEMINI_API_KEYS (через запятую), без дублей."""
        keys: List[str] = [self.api_key or "AIzaSy_dummy_api_key_placeholder"]
        extra = getattr(settings, "GEMINI_API_KEYS", "") or ""
        for k in extra.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    def _client_for(self, key_idx: int) -> genai.Client:
        """Клиент для ключа №key_idx (0 — основной, совместим с подменой .client в тестах)."""
        if key_idx == 0:
            return self.client
        if not hasattr(self, "_extra_clients"):
            self._extra_clients: Dict[int, genai.Client] = {}
        if key_idx not in self._extra_clients:
            self._extra_clients[key_idx] = genai.Client(api_key=self._all_api_keys()[key_idx])
        return self._extra_clients[key_idx]

    def get_tool_declarations(self) -> List[Any]:
        """Возвращает список Python-функций для инструментов Gemini."""
        def check_available_slots(
            target_date: str,
            branch_name_or_id: Optional[str] = None,
            barber_name_or_id: Optional[str] = None,
            service_name_or_id: Optional[str] = None,
        ) -> dict:
            """Проверка доступных свободных окон для записи на указанную дату в филиале салона.
            
            Args:
                target_date: Дата в формате YYYY-MM-DD или слова 'сегодня', 'завтра', 'суббота'
                branch_name_or_id: Название или ID филиала (например 'Миллий бог', 'Чиланзар')
                barber_name_or_id: Имя мастера (например 'Mr. Akmal', 'Dilshod', 'Abbos', 'Nodir') или None
                service_name_or_id: Выбранная услуга для учета длительности процедуры
            """
            return {}

        def create_booking_reservation(
            customer_name: str,
            booking_time: str,
            branch_name_or_id: Optional[str] = None,
            barber_name_or_id: Optional[str] = None,
            service_name_or_id: Optional[str] = None,
            phone: Optional[str] = None,
        ) -> dict:
            """Создание бронирования и запись клиента в базу данных. ВНИМАНИЕ: Вызывать ТОЛЬКО когда клиент согласен на запись и названо время!
            
            Args:
                customer_name: Имя клиента (например 'Сардор', 'Азиз', 'Рустам')
                booking_time: Дата и время записи (например '2026-10-20 14:00 (Вторник)' или 'завтра 14:00')
                branch_name_or_id: Название или ID филиала салона ('Миллий бог', 'Чиланзар')
                barber_name_or_id: Имя мастера ('Mr. Akmal', 'Dilshod', 'Abbos', 'Nodir' или 'Любой')
                service_name_or_id: Название выбранной услуги из прайса (например 'Стрижка волос', 'Оформление бороды')
                phone: Номер телефона клиента если он его четко назвал, иначе None (запрещено выдумывать номер телефона)
            """
            return {}

        def cancel_booking_reservation(
            booking_id: Optional[int] = None,
            customer_name: Optional[str] = None,
        ) -> dict:
            """Отмена существующей брони клиента и освобождение окна в графике.
            
            Args:
                booking_id: ID записи если известен, либо None
                customer_name: Имя клиента если известно
            """
            return {}

        def get_salon_info(
            topic: Optional[str] = None,
        ) -> dict:
            """Получение актуальной информации о салоне: прайс-лист, услуги, филиалы, адреса и мастера.
            
            Args:
                topic: Тема запроса ('branches', 'services', 'barbers', 'promo', 'all')
            """
            return {}

        return [check_available_slots, create_booking_reservation, cancel_booking_reservation, get_salon_info]

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

    async def _generate_content_with_retry(
        self,
        contents: Any,
        config: Any,
        model_name: Optional[str] = None,
        max_retries: int = 3,
    ) -> Any:
        """
        Надежный вызов generate_content с автоматическим повтором при временных сбоях (503 UNAVAILABLE),
        детектированием исчерпания суточной квоты (429 RESOURCE_EXHAUSTED) и МГНОВЕННЫМ переключением
        на рабочую резервную модель без лишних задержек.
        """
        target_model = model_name or self.model_name
        fallback_models = [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
        ]
        all_candidates = [target_model] + [m for m in fallback_models if m != target_model]
        keys_count = len(self._all_api_keys())

        def _ex_key(model: str, key_idx: Any) -> str:
            return f"{key_idx}:{model}"

        now = time.time()
        # Если ВСЕ пары (ключ, модель) заблокированы — сбрасываем бан и пробуем заново
        if all(
            self.exhausted_models.get(_ex_key(m, "*"), 0) >= now
            or all(self.exhausted_models.get(_ex_key(m, i), 0) >= now for i in range(keys_count))
            for m in all_candidates
        ):
            self.exhausted_models.clear()

        last_error = None
        for current_model in all_candidates:
            if self.exhausted_models.get(_ex_key(current_model, "*"), 0) >= time.time():
                continue
            skip_model = False
            for key_idx in range(keys_count):
                if skip_model:
                    break
                if self.exhausted_models.get(_ex_key(current_model, key_idx), 0) >= time.time():
                    continue
                for attempt in range(max_retries):
                    try:
                        # Строгий таймаут 7с на запрос, чтобы бот не висел минутами
                        return await asyncio.wait_for(
                            self._client_for(key_idx).aio.models.generate_content(
                                model=current_model,
                                contents=contents,
                                config=config,
                            ),
                            timeout=15.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Таймаут (15с) у {current_model}. Переключаемся на резервную модель!")
                        self.exhausted_models[_ex_key(current_model, "*")] = time.time() + 300
                        skip_model = True
                        break
                    except Exception as e:
                        err_msg = str(e)
                        is_hard_quota = (
                            "quotaValue" in err_msg
                            or "Quota exceeded" in err_msg
                            or "free_tier_requests" in err_msg
                            or "RESOURCE_EXHAUSTED" in err_msg
                        )
                        is_high_demand = (
                            "503" in err_msg
                            or "UNAVAILABLE" in err_msg
                            or "high demand" in err_msg
                            or "overloaded" in err_msg
                        )
                        is_transient = "429" in err_msg or "ResourceExhausted" in err_msg
                        is_model_missing = "404" in err_msg or "NOT_FOUND" in err_msg
                        last_error = e

                        if is_model_missing:
                            # Модель недоступна этому проекту/ключу — пропускаем её, цепочка продолжается
                            logger.warning(f"Модель {current_model} недоступна (404). Пропускаем.")
                            self.exhausted_models[_ex_key(current_model, "*")] = time.time() + 3600
                            skip_model = True
                            break

                        if is_hard_quota:
                            # Квота этого ключа для этой модели исчерпана — пробуем следующий ключ
                            logger.warning(
                                f"Квота {current_model} исчерпана на ключе #{key_idx + 1}/{keys_count}. "
                                "Переключаемся на следующий ключ/модель."
                            )
                            self.exhausted_models[_ex_key(current_model, key_idx)] = time.time() + 1800
                            break

                        if is_high_demand:
                            logger.warning(f"Модель {current_model} перегружена (503). Переключаемся на резервную!")
                            self.exhausted_models[_ex_key(current_model, "*")] = time.time() + 120
                            skip_model = True
                            break

                        if is_transient and attempt < max_retries - 1:
                            backoff = 0.5 * (1.5 ** attempt)
                            logger.warning(f"Gemini {current_model} временная ошибка ({e}). Повтор {attempt + 1}/{max_retries} через {backoff:.1f}с...")
                            await asyncio.sleep(backoff)
                            continue
                        elif is_transient:
                            self.exhausted_models[_ex_key(current_model, key_idx)] = time.time() + 120
                            break
                        else:
                            raise e

        if last_error:
            raise last_error
        raise RuntimeError("Все модели и ключи Gemini временно недоступны")

    async def generate_response(
        self,
        user_id: int,
        user_message: str,
        is_admin: bool = False,
        username: Optional[str] = None,
        bot: Optional[Any] = None,
    ) -> str:
        """
        Генерация ответа на текстовое сообщение с учетом роли (клиент или админ)
        и детерминированным вызовом инструментов бронирования (Function Calling).
        """
        try:
            raw_history = await get_recent_messages(user_id=user_id, limit=10)
            contents = self.format_history_to_contents(
                raw_history=raw_history, current_message=user_message
            )

            # Формируем контекст расписания, мастеров, филиалов и услуг
            active_bookings = await get_active_bookings()
            barbers = await get_barbers(active_only=True)
            services = await get_services(active_only=True)
            branches = await get_branches(active_only=True)
            salon_settings = await get_all_salon_settings()
            schedule_context = build_schedule_context_for_prompt(
                bookings=active_bookings,
                barbers=barbers,
                services=services,
                branches=branches,
                salon_settings=salon_settings
            )

            base_prompt = build_admin_system_prompt() if is_admin else build_client_system_prompt()
            dynamic_system_prompt = f"{base_prompt}\n\n{schedule_context}"
            if is_super_admin_user(user_id):
                dynamic_system_prompt = f"{dynamic_system_prompt}\n\n{build_creator_identity_prompt()}"

            tools = self.get_tool_declarations()
            config = types.GenerateContentConfig(
                system_instruction=dynamic_system_prompt,
                temperature=0.3 if is_admin else 0.4,
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            )

            response = await self._generate_content_with_retry(
                contents=contents,
                config=config,
            )

            # Инициализация отслеживания вызовов инструментов для этого запроса
            self.last_user_tools[user_id] = []
            executed_tools: List[str] = []
            last_tool_message: Optional[str] = None
            tool_messages: List[str] = []

            # Многошаговый цикл выполнения инструментов (поддержка составных команд вроде "отмени и запиши")
            turn_count = 0
            max_turns = 5

            while getattr(response, "function_calls", None) and turn_count < max_turns:
                turn_count += 1
                if response.candidates and response.candidates[0].content:
                    contents.append(response.candidates[0].content)
                else:
                    fc_parts = []
                    for fc in response.function_calls:
                        if isinstance(fc, types.FunctionCall):
                            fc_parts.append(types.Part(function_call=fc))
                        else:
                            f_name = getattr(fc, "name", "tool")
                            f_args = dict(getattr(fc, "args", {}) or {})
                            f_id = getattr(fc, "id", None)
                            fc_call = types.FunctionCall(
                                name=f_name,
                                args=f_args,
                                id=f_id if isinstance(f_id, str) else None
                            )
                            fc_parts.append(types.Part(function_call=fc_call))
                    contents.append(types.Content(role="model", parts=fc_parts))

                # Если в одном шаге вызваны и отмена, и создание брони — выполняем сначала отмену
                calls = list(response.function_calls)
                calls.sort(key=lambda fc: 0 if fc.name == "cancel_booking_reservation" else 1)

                function_response_parts = []
                for fc in calls:
                    fn_name = fc.name
                    fn_args = dict(fc.args or {})
                    logger.info(f"Gemini Tool Call (Turn {turn_count}): {fn_name} с аргументами: {fn_args}")
                    executed_tools.append(fn_name)

                    if fn_name == "create_booking_reservation":
                        clean_un = (username or "").strip().lstrip("@")
                        is_creator = is_super_admin_user(user_id) or clean_un.lower() == "tashkhanov"
                        c_name = (fn_args.get("customer_name") or "").strip()
                        if is_invalid_customer_name(c_name, barbers):
                            if is_creator:
                                fn_args["customer_name"] = "Иброхим"
                            elif clean_un:
                                fn_args["customer_name"] = clean_un
                        elif is_creator and c_name.lower() in ("меня", "мне", "себя", "иброхим", "каким"):
                            fn_args["customer_name"] = "Иброхим"

                        fn_args["user_id"] = user_id
                        fn_args["username"] = username
                        fn_args["bot"] = bot
                        tool_result = await self.booking_tools.create_booking_reservation(**fn_args)
                    elif fn_name == "check_available_slots":
                        tool_result = await self.booking_tools.check_available_slots(**fn_args)
                    elif fn_name == "cancel_booking_reservation":
                        fn_args["user_id"] = user_id
                        fn_args["bot"] = bot
                        tool_result = await self.booking_tools.cancel_booking_reservation(**fn_args)
                    elif fn_name == "get_salon_info":
                        tool_result = await self.booking_tools.get_salon_info(**fn_args)
                    else:
                        tool_result = {"error": f"Unknown tool {fn_name}"}

                    resp_part = types.Part.from_function_response(name=fn_name, response=tool_result)
                    function_response_parts.append(resp_part)
                    if isinstance(tool_result, dict) and tool_result.get("message"):
                        last_tool_message = tool_result.get("message")
                        tool_messages.append(last_tool_message)

                contents.append(types.Content(role="user", parts=function_response_parts))

                next_response = await self._generate_content_with_retry(
                    contents=contents,
                    config=config,
                )
                response = next_response

            self.last_user_tools[user_id] = executed_tools

            fallback = ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE
            raw_reply = (response.text or "").strip()
            if not raw_reply and getattr(response, "candidates", None) and response.candidates and response.candidates[0].content:
                parts = response.candidates[0].content.parts or []
                text_parts = [p.text for p in parts if getattr(p, "text", None)]
                if text_parts:
                    raw_reply = " ".join(text_parts).strip()
            if not raw_reply and tool_messages:
                raw_reply = "\n\n".join(dict.fromkeys(tool_messages))
            elif not raw_reply and last_tool_message:
                raw_reply = last_tool_message

            bot_reply = clean_bot_reply(raw_reply) if raw_reply else fallback

            await add_message(user_id=user_id, role="user", text=user_message)
            await add_message(user_id=user_id, role="model", text=bot_reply)

            return bot_reply

        except Exception as e:
            logger.error(f"Ошибка при вызове Gemini API для user_id {user_id}: {e}", exc_info=True)
            return ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE

    async def generate_response_from_voice(
        self,
        user_id: int,
        voice_bytes: bytes,
        mime_type: str = "audio/ogg",
        is_admin: bool = False,
        username: Optional[str] = None,
        bot: Optional[Any] = None,
    ) -> Tuple[str, str]:
        """
        Обработка голосового сообщения:
        1. Распознавание аудио через Gemini multimodal.
        2. Обработка через основной диалоговый движок с поддержкой инструментов.
        """
        try:
            audio_part = types.Part.from_bytes(data=voice_bytes, mime_type=mime_type)
            role_desc = "AI-Ассистент управляющего" if is_admin else f"{settings.SALON_SHORT_NAME} Ассистент"
            transcribe_prompt = (
                f"Ты — {role_desc} салона {settings.SALON_NAME}. "
                "Распознай слова клиента из аудиозаписи на русском или узбекском языке. "
                "Верни ТОЛЬКО распознанный текст клиента, без комментариев и оговорок."
            )
            trans_resp = await self._generate_content_with_retry(
                contents=[audio_part, types.Part.from_text(text=transcribe_prompt)],
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
            raw_transcript = (trans_resp.text or "").strip()
            user_transcript = raw_transcript if raw_transcript else "Голосовое сообщение"

            bot_reply = await self.generate_response(
                user_id=user_id,
                user_message=user_transcript,
                is_admin=is_admin,
                username=username,
                bot=bot,
            )
            return bot_reply, user_transcript

        except Exception as e:
            logger.error(f"Ошибка при обработке голоса в Gemini для user_id {user_id}: {e}", exc_info=True)
            fallback = ADMIN_FALLBACK_RESPONSE if is_admin else FALLBACK_RESPONSE
            return fallback, "Голосовое сообщение"

    async def extract_client_action(self, dialog_context: str, user_id: Optional[int] = None) -> Optional[ClientAction]:
        """
        Структурированное определение намерения клиента:
        'create_booking', 'cancel_my_booking', 'check_my_booking', 'chat'.
        """
        try:
            now_str = format_tashkent_now_for_prompt()
            creator_hint = ""
            if is_super_admin_user(user_id):
                creator_hint = (
                    "\nОСОБОЕ ПРАВИЛО ДЛЯ СОЗДАТЕЛЯ (ИБРОХИМ):\n"
                    "Если пользователь просит 'запиши меня', 'мне стрижку', 'забронируй мне' — его имя ИБРОХИМ (customer_name='Иброхим'). "
                    "В таком случае переспрашивать имя НЕ НУЖНО, и если желаемое время названо — ставь is_booking_confirmed = true!\n"
                )

            branches = await get_branches(active_only=True)
            branch_info_str = ", ".join([f"ID {b['id']}: '{b['name']}' ({b['address']})" for b in branches]) if branches else ""
            branch_hint = f"\nДОСТУПНЫЕ ФИЛИАЛЫ САЛОНА: {branch_info_str}.\nЕсли клиент упоминает локацию/филиал/метро, сопоставь с филиалом и заполни branch_id и branch_name.\n" if branch_info_str else ""

            barbers = await get_barbers(active_only=True)
            barber_info_str = ", ".join([f"'{b['name']}'" for b in barbers]) if barbers else ""
            barber_hint = (
                f"\nМАСТЕРА САЛОНА: {barber_info_str}.\n"
                "ВНИМАНИЕ: Если в диалоге упоминается мастер из списка (или формы 'к [Имя]', '[Имя]ga', '[Имя]ning huzuriga') — "
                "запиши его строго в barber_name, а НЕ в customer_name! customer_name — это имя клиента, а не мастера!\n"
            ) if barber_info_str else ""

            creator_admin_name = getattr(settings, "SUPER_ADMIN_NAME", "Иброхим")
            prompt = (
                f"Ты — анализатор диалогов клиентов салона {settings.SALON_NAME}.\n"
                f"ТЕКУЩЕЕ ВРЕМЯ В {settings.SALON_CITY.upper()}: {now_str}.\n"
                f"{branch_hint}"
                f"{barber_hint}"
                f"{creator_hint}\n"
                f"Диалог:\n{dialog_context}\n\n"
                "Определи действие клиента:\n"
                "1. action = 'cancel_my_booking' — если клиент просит отменить бронь ('отмени', 'не приду', 'отмена', 'bekor qil').\n"
                "2. action = 'check_my_booking' — если клиент спрашивает, когда он записан ('когда я записан?', 'на сколько моя стрижка?', 'eslatib yuboring').\n"
                "3. action = 'create_booking' — если клиент хочет записаться или запись согласуется. "
                "Установи is_booking_confirmed = true, если в диалоге названо желаемое время/дата (например 'ertaga soat 14:00', 'завтра в 14:00', 'пятница 16:30'), "
                "ЛИБО если ассистент уже ответил, что записал клиента / передал запрос мастеру на согласование! "
                "ВНИМАНИЕ: Если клиент путается, называет сразу несколько противоречивых дат или времен ('на послезавтра или в пятницу, ой нет в понедельник...'), ЛИБО давит 'я уже всё дал', но точное время не определено — действие СТРОГО 'chat', is_booking_confirmed = false, booking_time = null! Категорически запрещено угадывать время наугад!\n"
                f"customer_name: укажи имя клиента, если названо в диалоге (если Создатель платформы — customer_name='{creator_admin_name}'). Если клиент не называл имя в тексте, оставь customer_name=null (оно возьмется из профиля Telegram).\n"
                "КРИТИЧЕСКИ ВАЖНО: booking_time пиши только в каноническом формате 'YYYY-MM-DD HH:MM (ДеньНедели)' с точной датой.\n"
                "4. action = 'chat' — во всех остальных случаях (вопрос по прайсу, локации, невнятные/противоречивые запросы и т.д.)."
            )

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ClientAction,
                temperature=0.1,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            response = await self._generate_content_with_retry(
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

    async def extract_admin_action(self, admin_query: str, user_id: Optional[int] = None) -> Optional[AdminAction]:
        """
        Структурированное определение намерения управляющего:
        'query_schedule', 'create_booking', 'confirm_booking', 'cancel_booking', 'get_stats', 'chat'.
        """
        try:
            now_str = format_tashkent_now_for_prompt()
            creator_hint = ""
            creator_admin_name = getattr(settings, "SUPER_ADMIN_NAME", "Иброхим")
            if is_super_admin_user(user_id):
                creator_hint = (
                    f"\nОСОБОЕ ПРАВИЛО ДЛЯ СОЗДАТЕЛЯ / СУПЕР-АДМИНА:\n"
                    f"Если в запросе сказано 'запиши меня', 'мне стрижку', 'забронируй мне', либо на узбекском ('...ga yozib qo'y', '...ga yozib qo'ying', 'soch oldirishga yoz') — "
                    f"клиент это сам Создатель ({creator_admin_name}) (client_query='{creator_admin_name}', action='create_booking').\n"
                )

            branches = await get_branches(active_only=True)
            branch_info_str = ", ".join([f"ID {b['id']}: '{b['name']}'" for b in branches]) if branches else ""
            branch_hint = f"\nДОСТУПНЫЕ ФИЛИАЛЫ: {branch_info_str}.\n" if branch_info_str else ""

            barbers = await get_barbers(active_only=True)
            barber_info_str = ", ".join([f"'{b['name']}'" for b in barbers]) if barbers else ""
            barber_hint = (
                f"\nСПИСОК МАСТЕРОВ САЛОНА: {barber_info_str}.\n"
                "ВНИМАНИЕ: Если в запросе упомянут мастер из списка (или формы 'к [Имя]', '[Имя]ga', '[Имя]ning huzuriga') — "
                "запиши его строго в barber_name, а НЕ в client_query! client_query — это клиент, а НЕ барбер!\n"
            ) if barber_info_str else ""

            admin_query_norm = admin_query.replace("от мини", "отмени").replace("От мини", "отмени").replace("от-мини", "отмени")
            prompt = (
                f"Ты — система управления командами владельца салона {settings.SALON_NAME}.\n"
                f"ТЕКУЩЕЕ ВРЕМЯ В {settings.SALON_CITY.upper()}: {now_str}.\n"
                f"{branch_hint}"
                f"{barber_hint}"
                f"{creator_hint}\n"
                f"Запрос администратора: {admin_query_norm}\n\n"
                "Определи намерение администратора:\n"
                "- 'query_schedule': запросы расписания ('кто сегодня?', 'что на завтра?', 'покажи пятницу'). Извлеки target_date.\n"
                "- 'create_booking': ручная запись клиента ('запиши Сардора завтра на 16:00, тел +99890...', или 'запиши меня на 15:00', или 'Ertaga 14:00 ga Mr. Akmalga yozib qo'y'). Извлеки client_query, barber_name, booking_time, service, phone, branch_name, branch_id. ВНИМАНИЕ: Если запрос сбивчивый, содержит сразу несколько противоречивых дат или времен (например: 'послезавтра или в пятницу, то есть в понедельник...'), ЛИБО если пользователь утверждает 'я уже все дал', но точное время не определено — действие СТРОГО 'chat' (booking_time=null)! Категорически запрещено выбирать дату наугад!\n"
                "- 'confirm_booking': подтверждение записи ('подтверди запись Рустама', 'подтверди 15:00'). Извлеки client_query или booking_time. ВНИМАНИЕ: подтверждение записей к Шефу Mr. Akmal (pending_vip) выполняется строго кнопками в Telegram/TMA, не через чат.\n"
                "- 'cancel_booking': отмена записи ('отмени запись Рустама', 'отмени запись на меня', 'отмени 14:00', 'bekor qil'). ВНИМАНИЕ: Если в запросе есть слова отмены ('отмени', 'отмените', 'bekor', 'от мини'), действие СТРОГО 'cancel_booking', даже если сказано 'на меня' или 'мне'. Запрещено указывать 'create_booking'!\n"
                "- 'get_stats': статистика ('сколько записей?', 'статистика').\n"
                "- 'chat': обычный вопрос, либо сбивчивый запрос с несколькими датами/временем."
            )

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AdminAction,
                temperature=0.1,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            response = await self._generate_content_with_retry(
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
