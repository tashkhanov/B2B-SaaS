# 💈 AI-Администратор для Мужского Салона "Brohouse" (Ташкент)

Коммерческий B2B продукт: интеллектуальный Telegram-бот и мультиролевая экосистема Telegram Mini Apps (TMA) для премиум мужского салона "Brohouse" (`@brohouseuz`, Ташкент) на базе **Telegram Bot API (aiogram 3.x)**, **Google Gemini 2.5 Flash API**, **aiosqlite** и **aiohttp**.

---

## 🎯 Ключевые возможности системы

1. **Харизматичный персонаж "Brohouse Ассистент"**:
   - Вежливый, уверенный администратор с теплым ташкентским вайбом ("ака", "братан", "без проблем", "от души").
   - Автоматическое зеркалирование языка (русский или узбекский: O'zbekcha / Ўзбекча).
   - Актуальный прайс: Стрижки от 30 000 до 100 000 сум, Усы/Борода/Уход от 20 000 до 70 000 сум.
   - **Спецпредложение:** "30% OFF на первую стрижку" для новых клиентов!
   - Локация: г. Ташкент, метро «Миллий бог», 8B; телефон: `+998 97 012-55-55`.
   - График работы: ежедневно с 08:00 до 23:00 без выходных.
2. **🎙 Мультимодальная обработка голоса (Voice Messages)**:
   - Клиент отправляет голосовое сообщение на русском или узбекском языке.
   - Аудиофайлы (OGG Opus) передаются напрямую в Google GenAI SDK (`types.Part.from_bytes`).
   - Мгновенный ответ с учетом занятых окон мастеров и контекста диалога.
3. **📱 Три Telegram Mini Apps (TMA)**:
   - **`/book` (Клиент)**: выбор из 12 услуг, 4 мастеров (Mr. Akmal, Mr. Dilshod, Ismailov Abbos, Tursunov Nodir), горизонтальный календарь на 7 дней, 30-минутные слоты с 08:00 до 23:00, баннер активной брони с отменой в 1 тап.
   - **`/admin` (Управляющий)**: статистика выручки, управление записями, Full CRUD каталога услуг, управление с авто-переключением смен мастеров.
   - **`/barber` (Мастер)**: вход по Telegram ID, переключатель смены («На смене / Отдыхаю»), очередь клиентов на сегодня и прямой звонок клиенту в 1 тап.
4. **🧠 Движок расписания**:
   - Таймзона `Asia/Tashkent` (UTC+5), мульти-мастер емкость, автозавершение прошедших броней.
5. **⚡ Меню и системный брендинг**:
   - Фирменный логотип Brohouse (`static/logo.png`), золотая премиум-палитра (#c5a059).
   - Команда `/about` с контактами разработчика (`@asatkhanov` / [asatkhanov.uz](https://asatkhanov.uz)).

---

## 🏗 Архитектура проекта

```
B2B-SaaS/
│
├── .env.example              # Шаблон настроек и токенов
├── requirements.txt          # Зависимости Python
├── README.md                 # Документация проекта
│
├── config.py                 # Pydantic BaseSettings (чтение .env и TMA настроек)
├── database.py               # Асинхронный слой SQLite (12 услуг, 4 мастера, брони)
├── prompts.py                # Системные промпты Brohouse (клиентский и админский)
│
├── services/
│   ├── gemini_service.py     # Интеграция с Gemini 2.5 Flash (текст, голос, парсинг)
│   └── schedule_service.py   # Движок слотов (08:00 - 23:00, мультимастерность)
│
├── bot/
│   └── handlers.py           # Хэндлеры команд, текста, голоса и кнопок TMA
│
├── static/
│   └── logo.png              # Официальный логотип Brohouse
│
├── webapp/
│   ├── client.html           # Клиентская онлайн-запись (Luxury Gold & Dark Charcoal)
│   ├── index.html            # Панель управления Brohouse Pro
│   └── barber.html           # Кабинет мастера Brohouse Master
│
├── web_app.py                # aiohttp веб-сервер и REST API
│
├── tests/                    # Полный тестовый набор (28 тестов, 100% успех)
│   ├── test_database.py
│   ├── test_gemini_service.py
│   ├── test_handlers.py
│   ├── test_schedule_service.py
│   └── test_web_app.py
│
└── main.py                   # Главная точка входа (бот + TMA сервер)
```

---

## 🚀 Быстрый старт

### 1. Настройка окружения

```powershell
# Активация виртуального окружения
.\.venv\Scripts\Activate.ps1

# Установка зависимостей (при необходимости)
pip install -r requirements.txt
```

### 2. Настройка файла `.env`

```env
BOT_TOKEN=8683027133:AAFEmuiNYCSZEbbDaK9ApbuX6kRaLEwyfK4
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.5-flash
ADMIN_CHAT_ID=1243374131
DB_NAME=barbershop.db
WEBAPP_PORT=8080
WEBAPP_URL=https://your-tunnel.trycloudflare.com
```

### 3. Запуск тестов

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### 4. Запуск проекта

```powershell
.\.venv\Scripts\python.exe main.py
```
