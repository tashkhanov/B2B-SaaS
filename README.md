# 💈 AI-Администратор для Барбершопа "Black Beard" (Ташкент)

Коммерческий B2B MVP Telegram-бота — AI-администратора премиум-барбершопа "Black Beard" в Ташкенте на базе **Telegram Bot API (aiogram 3.x)** и **Google Gemini API (google-genai)**.

---

## 🎯 Возможности бота

- **Харизматичный персонаж "Иброхим"**: Вежливый, уверенный администратор с ташкентским вайбом ("ака", "братан", "без проблем", "от души").
- **Двуязычность**: Автоматически зеркалит язык клиента (русский или узбекский).
- **Консультация по прайсу**:
  - Стрижка — **100 000 сум**
  - Оформление бороды — **50 000 сум**
  - Комплекс (Стрижка + Борода) — **130 000 сум** *(Хит продаж)*
  - Мытье головы, стайлинг и премиальный кофе/чай включены.
- **Интеллектуальная память (Context)**: Автоматическое сохранение и передача истории последних 5-6 реплик (до 10 сообщений) через локальную асинхронную БД `SQLite` (`aiosqlite`).
- **Сброс контекста**: Команда `/reset` или кнопка "🔄 Сбросить диалог".
- **Фиксация лидов**: При согласии клиента на запись данные сохраняются в таблицу `bookings` и опционально пересылаются в личку администратору/владельцу (`ADMIN_CHAT_ID`).
- **Живой UX**: Индикация статуса `печатает...` (`ChatAction.TYPING`) во время обращений к нейросети.
- **Отказоустойчивость**: Аутентичный in-character fallback при сбоях сети/квот.

---

## 🏗 Архитектура проекта

```
B2B-SaaS/
│
├── .env.example              # Шаблон настроек и токенов
├── requirements.txt          # Зависимости Python
├── README.md                 # Документация проекта
│
├── config.py                 # Pydantic BaseSettings (чтение .env)
├── database.py               # Асинхронный слой SQLite (история и брони)
├── prompts.py                # Системный промпт Иброхима ("Black Beard")
│
├── services/
│   └── gemini_service.py     # Интеграция с Gemini 2.5 Flash через client.aio
│
├── bot/
│   └── handlers.py           # Маршрутизация Telegram-событий (aiogram 3.x)
│
└── main.py                   # Точка входа: запуск бота и фонового polling
```

---

## 🚀 Быстрый старт (Локальный запуск)

### 1. Клонирование и создание виртуального окружения

```bash
# Создание виртуального окружения
python -m venv .venv

# Активация окружения (Windows PowerShell)
.venv\Scripts\Activate.ps1
# Или в cmd:
# .venv\Scripts\activate.bat
# В Linux/macOS:
# source .venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt
```

### 2. Настройка файла `.env`

Скопируйте шаблон `.env.example` в `.env`:

```bash
copy .env.example .env
```

Откройте `.env` и вставьте ваши ключи:
- `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather) в Telegram.
- `GEMINI_API_KEY` — бесплатный ключ в [Google AI Studio](https://aistudio.google.com/).
- `ADMIN_CHAT_ID` — (необязательно) ваш числовой Telegram Chat ID для получения заявок о записи. Узнать свой ID можно в [@userinfobot](https://t.me/userinfobot).

### 3. Запуск бота

```bash
python main.py
```

В консоли отобразится:
```
🚀 Бот 'Иброхим (Black Beard)' успешно запущен! Ожидание сообщений клиентов...
```

---

## 🧠 Как работает вызов Gemini с историей сообщений

1. Клиент отправляет сообщение в Telegram.
2. Бот активирует статус `typing...` (`ChatAction.TYPING`).
3. Метод `GeminiService.generate_response()` запрашивает последние 10 сообщений диалога из SQLite (`SELECT ... ORDER BY id DESC LIMIT 10`).
4. Метод `format_history_to_contents()` валидирует очередность ролей (`user` -> `model` -> `user`), обеспечивая строгое соответствие протоколу Gemini API.
5. Запрос отправляется асинхронно в модель `gemini-2.5-flash` через официальный клиент `google-genai`:
   ```python
   response = await client.aio.models.generate_content(
       model="gemini-2.5-flash",
       contents=contents,
       config=types.GenerateContentConfig(
           system_instruction=SYSTEM_PROMPT,
           temperature=0.7,
       )
   )
   ```
6. Полученный ответ и входящий запрос пользователя сохраняются в SQLite для поддержания непрерывного контекста.
