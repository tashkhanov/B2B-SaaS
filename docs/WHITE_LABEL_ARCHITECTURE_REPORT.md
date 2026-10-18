# 🏢 Архитектурный отчет: White-Label трансформация B2B-SaaS

> **Дата выполнения:** 18 октября 2026 г.  
> **Статус:** Выполнено на 100% (95 из 95 тестов успешно пройдены)  
> **Ветка Git:** `feat/tier-full-stack-features`  
> **Коммиты этапов:** `f088cff` -> `c381907` -> `2c78fb5` -> `a64b7c3` -> `29e62c1` -> `91d4a1e`

---

## 1. Цель и контекст рефакторинга

Изначально кодовая база проекта разрабатывалась под одного конкретного клиента — премиальный мужской салон **Brohouse** (Ташкент). В кодовой базе присутствовали десятки жестко зашитых констант:
- Название салона ("Brohouse", "Брохаус", "Brohouse Tashkent");
- Адрес филиалов (ул. Шота Руставели 53б, ул. Мирабадская);
- Имена и роли мастеров (Акмал, шеф-барбер, топ-барбер);
- Цены и услуги (Мужская стрижка — 120 000 сум, моделирование бороды — 80 000 сум);
- Контакты, ссылки на социальные сети, Instagram, 2GIS/Яндекс.Карты;
- Системные промты нейросети Gemini для текстового и голосового ассистента;
- Интерфейсы Telegram Mini Apps (клиентский, админский, кабинет мастера).

### Бизнес-задача:
Сделать платформу **100% White-Label**, чтобы при подключении любого второго (или N-го) клиента-салона не требовалось лезть в код и вручную переписывать логику. Все параметры должны управляться **через единый конфигурационный файл `.env`**.

### Главное бизнес-ограничение:
Значения по умолчанию в коде (`config.py`) **полностью сохранены за Brohouse**. Завтрашняя презентация Brohouse готова «из коробки» без необходимости ручной настройки.

---

## 2. Архитектура White-Label системы

```
                         ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: .env
                                      │
                                      ▼
                        config.py (Pydantic Settings)
                                      │
     ┌───────────────┬────────────────┼───────────────┬───────────────┐
     ▼               ▼                ▼               ▼               ▼
database.py     prompts.py      bot/handlers.py  services/*.py    web_app.py
(Автосидинг    (Генерация       (Динамические    (Voice, Push,    (SSR-инъекция
 филиалов,      промтов под      кнопки, чеки,    Schedule,        в HTML TMA,
 мастеров,      Tone of Voice    контакты и       Tier-лимиты)     API конфига)
 услуг)         клиента)         уведомления)
```

### 2.1. Конфигурационный слой (`config.py` & `.env.example`)
Все параметры сгруппированы по смысловым блокам с валидацией через `pydantic_settings.BaseSettings`:

| Группа настроек | Ключевые переменные `.env` | Назначение |
|---|---|---|
| **Брендинг** | `SALON_NAME`, `SALON_SHORT_NAME`, `SALON_TYPE`, `SALON_CITY` | Официальное и краткое название, тип заведения ("мужской салон", "барбершоп", "студия красоты"), город |
| **Контакты** | `SALON_PHONE`, `SALON_INSTAGRAM`, `SALON_SUPPORT_TELEGRAM` | Телефон для связи, соцсети, контакт поддержки |
| **Локации** | `SALON_DEFAULT_ADDRESS`, `SALON_MAPS_URL`, `SALON_YANDEX_MAPS_URL` | Адрес главного филиала, прямые ссылки на 2GIS и Яндекс.Карты |
| **График** | `SALON_WORK_START_HOUR`, `SALON_WORK_END_HOUR`, `BOOKING_SLOT_DURATION_MINUTES`, `BOOKING_HORIZON_DAYS` | Время открытия/закрытия, шаг записи (30/45/60 мин), глубина бронирования |
| **Валюта** | `SALON_CURRENCY_SYMBOL`, `SALON_CURRENCY_CODE` | Символ (`сум`, `₽`, `$`) и ISO-код (`UZS`, `RUB`, `USD`) |
| **Медиа** | `SALON_LOGO_URL`, `SALON_PROMO_TITLE`, `SALON_PROMO_SUBTITLE` | Ссылка на логотип, промо-заголовок и подзаголовок в TMA |
| **Подписка** | `DEFAULT_TENANT_ID`, `TENANT_SLUG`, `TENANT_TIER`, `CARD_NUMBER`, `CARD_HOLDER` | Идентификатор арендатора, тариф (START/PRO/ENTERPRISE), реквизиты оплаты |
| **Авторство** | `DEVELOPER_NAME`, `DEVELOPER_PORTFOLIO_URL`, `PLATFORM_NAME` | Подпись в футере ("Powered by Khan") |
| **JSON-сиды** | `INITIAL_BRANCHES_JSON`, `INITIAL_BARBERS_JSON`, `INITIAL_SERVICES_JSON` | Стартовые данные в формате JSON (если переданы, переопределяют дефолтные) |

---

## 3. Детализация изменений по компонентам

### 3.1. База данных (`database.py`)
- **Динамический сидинг:** Функции `init_db()` и `reseed_brohouse_data()` (также добавлен алиас `reseed_tenant_data()`) больше не содержат фиксированных строк. Они считывают `settings.get_initial_branches()`, `settings.get_initial_barbers()`, `settings.get_initial_services()`, `settings.SALON_PHONE`, `settings.SALON_DEFAULT_ADDRESS`, `settings.SALON_CITY`, `settings.SALON_NAME`.
- **Таблица `salon_settings`:** Ключ `salon_name` заполняется значением `settings.SALON_NAME`, `phone` — `settings.SALON_PHONE`, `address` — `settings.SALON_DEFAULT_ADDRESS`.
- **Подписка тенанта:** При инициализации создается подписка для `settings.TENANT_SLUG` с тарифом `settings.TENANT_TIER` и лимитами филиалов/мастеров/голосовых сообщений.
- **Определение VIP-мастера:** Логика выявления VIP-мастера отвязана от имени ("Акмал") и проверяет флаг `is_vip=1` либо наличие ключевых слов в `role_title` ("шеф", "директор", "главный").

### 3.2. Системные промты ИИ (`prompts.py` и `services/gemini_service.py`)
- Заменены статические тексты на динамические фабрики:
  - `build_client_system_prompt()`: формирует промт для клиентского бота с подстановкой названия салона, адреса, телефона, города, типа салона, доступных команд.
  - `build_admin_system_prompt()`: адаптирует бизнес-аналитика под текущий салон.
  - `build_creator_identity_prompt()`: динамически подставляет имя разработчика и ссылку на портфолио.
- Сервис `gemini_service.py` использует динамические промты и передает параметры текущего салона во все функции анализа диалогов, генерации рекомендаций и аудио-транскрипции.

### 3.3. Telegram-бот (`bot/handlers.py` и `main.py`)
- Команды `/start`, `/about`, `/price`, `/where` динамически собирают текст:
  - `/start`: использует `settings.SALON_TYPE` и `settings.SALON_NAME`.
  - `/about`: выводит адрес, телефон, Instagram, время работы салона.
  - `/where`: генерирует инлайн-кнопки с прямыми ссылками на 2GIS и Яндекс.Карты текущего салона.
  - Подтверждения бронирования и чеки содержат название салона и правильный символ валюты.
  - Уведомления об отмене и напоминания клиентам используют брендинг салона.
- `main.py`: лог запуска выводит `settings.SALON_SHORT_NAME` и `settings.ENV`.

### 3.4. Сервисы удержания, голоса и расписания (`services/`)
- `schedule_service.py`: интервалы работы (10:00–21:00) и шаг бронирования (30 мин) берутся из `settings.SALON_WORK_START_HOUR`, `settings.SALON_WORK_END_HOUR`, `settings.BOOKING_SLOT_DURATION_MINUTES`.
- `retention_service.py`: тексты повторного вовлечения (Winback push-уведомления) содержат `settings.SALON_SHORT_NAME` и персонализированные кнопки.
- `voice_call_service.py`: синтез и распознавание речи приветствуют клиента от имени текущего салона.
- `tier_service.py`: дефолтный `tenant_id` привязан к `settings.TENANT_SLUG`.

### 3.5. Telegram Mini Apps и веб-сервер (`web_app.py`, `webapp/*.html`, `static/js/admin.js`)
- **SSR Token Injection:** Создана функция `apply_html_tokens()`, которая на лету заменяет токены в HTML до отправки в браузер Telegram:
  - `{{SALON_NAME}}`
  - `{{SALON_SHORT_NAME}}`
  - `{{SALON_TYPE}}`
  - `{{SALON_CITY}}`
  - `{{SALON_PHONE}}`
  - `{{LOGO_URL}}`
  - `{{PROMO_TITLE}}`
  - `{{PROMO_SUBTITLE}}`
  - `{{CURRENCY_SYMBOL}}`
  - `{{CURRENCY_CODE}}`
  - `{{PLATFORM_NAME}}`
  - `{{DEVELOPER_NAME}}`
  - `{{PORTFOLIO_URL}}`
- **Zero-FOUC гарантия:** Пользователь в Telegram Mini App не видит мерцания или замены одного текста другим — страница отдается сервером уже с нужным брендингом.
- **Шаблоны TMA:** `client.html`, `index.html` и `barber.html` полностью переведены на использование токенов.
- **API Конфигурации:** Эндпоинт `/api/config` возвращает фронтенду полные брендинговые метаданные.
- **Кабинет администратора (`static/js/admin.js`):** Устранена проверка `name.toLowerCase().includes('akmal')`. VIP-статус теперь базируется на флаге `is_vip` или `role_title` мастера.
- **Динамические cookies авторизации:** Имя сессионной куки генерируется как `f"session_{settings.TENANT_SLUG}"`, исключая конфликт авторизаций разных клиентов на одном домене.

---

## 4. Инструкция: Как запустить нового клиента (Клиент #2) за 3 минуты

Когда появляется новый клиент (например, барбершоп *"Chop-Chop"* или салон красоты *"Mon Amour"*):

### Шаг 1. Скопировать проект
```bash
git clone <ваш_репозиторий> salon_chopchop
cd salon_chopchop
cp .env.example .env
```

### Шаг 2. Заполнить `.env` данными нового клиента
```ini
TELEGRAM_BOT_TOKEN="987654321:XYZ..."
WEBAPP_BASE_URL="https://chopchop.yourdomain.uz"
SALON_ADMIN_PASSWORD="secure_admin_password_here"

# Брендинг
SALON_NAME="Chop-Chop Barbershop"
SALON_SHORT_NAME="Chop-Chop"
SALON_TYPE="барбершоп"
SALON_CITY="Ташкент"
SALON_PHONE="+998 71 123 45 67"
SALON_INSTAGRAM="@chopchop_tashkent"
SALON_DEFAULT_ADDRESS="ул. Амира Темура, 42"
SALON_MAPS_URL="https://2gis.uz/tashkent/firm/..."
SALON_YANDEX_MAPS_URL="https://yandex.uz/maps/..."

# Идентификатор тенанта
TENANT_SLUG="chopchop"
TENANT_TIER="PRO"
```

*(Опционально)* Если хотите сразу предзагрузить их мастеров и услуги в базу:
```ini
INITIAL_BARBERS_JSON='[{"name": "Дамир", "phone": "+998901112233", "role_title": "Топ-барбер", "is_vip": false}]'
INITIAL_SERVICES_JSON='[{"name": "Стрижка", "category": "Стрижки", "price": 100000, "duration": 45}]'
```

### Шаг 3. Запустить проект
```bash
python main.py
```
**Результат:** База данных автоматически инициализируется с брендингом, филиалами, мастерами и услугами нового клиента. Бот, Telegram Mini Apps, кабинет мастера, панель администратора и ИИ-ассистент начинают работать под брендом нового клиента. **В коде не меняется ни единой строчки!**

---

## 5. Результаты верификации

В процессе рефакторинга запускались все тестовые наборы проекта.

```
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.7.0
plugins: anyio-4.15.1, asyncio-1.4.0
collected 95 items

tests\test_billing_ocr.py ...                                            [  3%]
tests\test_billing_service.py ......                                     [  9%]
tests\test_capacity_limits.py ....                                       [ 13%]
tests\test_concurrency.py .                                              [ 14%]
tests\test_config.py ..                                                  [ 16%]
tests\test_database.py .........                                         [ 26%]
tests\test_desktop_auth.py .                                             [ 27%]
tests\test_enterprise_services.py ....                                   [ 31%]
tests\test_gemini_service.py ......                                      [ 37%]
tests\test_handlers.py ....................                              [ 58%]
tests\test_retention_service.py .....                                    [ 64%]
tests\test_schedule_service.py .............                             [ 77%]
tests\test_telegram_auth.py .....                                        [ 83%]
tests\test_tier_service.py ....                                          [ 87%]
tests\test_vip_schedule.py ....                                          [ 91%]
tests\test_voice_throttling.py ...                                       [ 94%]
tests\test_web_app.py .....                                              [100%]

============================= 95 passed in 6.40s ==============================
```

- **95 тестов из 95 пройдены успешно (100%)**
- 0 регрессий, 0 сломанных зависимостей.
- Brohouse работает «из коробки» на 100% со всеми адресами, мастерами и ценами.
