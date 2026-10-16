# Спецификация: Система биллинга, Kill Switch и Пульт Super-Admin'а

## 1. Введение и цели
Цель подсистемы — обеспечить монетизацию и контроль доступа к B2B SaaS платформе для сети барбершопов и салонов красоты.
Система реализует:
1. Реестр арендаторов (тенfilter/multi-tenant registry) в SQLite (`tenant_subscription`).
2. Механизм мгновенной блокировки («Kill Switch») при окончании подписки и превышении 2-дневного льготного периода (grace period).
3. Дифференцированные ограничения в Telegram-боте (клиенты, мастера, локальные админы) и в WebApp TMA (403 в API, полноэкранная заглушка в браузере).
4. Асинхронный cron-планировщик ежедневной проверки подписок и отправки предупреждений за 3 дня, в день окончания и в момент блокировки.
5. Веб-панель управления Super-Admin'а (`/super-admin` и `/super` TMA) для владельца платформы (управление подписками, кнопка `+30 дней`, ручной Kill Switch, регистрация новых точек сети, полный bypass блокировок).

---

## 2. Модель данных SQLite (`tenant_subscription`)

Таблица создается в `database.py`:

```sql
CREATE TABLE IF NOT EXISTS tenant_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    expires_at TIMESTAMP NOT NULL,
    grace_period_days INTEGER DEFAULT 2,
    admin_chat_id INTEGER,
    contact_phone TEXT,
    contact_name TEXT,
    last_notification_stage TEXT DEFAULT 'none',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Поля:
- `tenant_slug`: системный слаг салона (например, `brohouse`).
- `name`: название заведения для отображения в панели.
- `is_active`: флаг ручной активации (1 = активен, 0 = принудительно заблокирован суперадмином).
- `expires_at`: дата и время окончания платного периода (`YYYY-MM-DD HH:MM:SS` UTC+5).
- `grace_period_days`: количество дней льготного периода до отключения (по умолчанию 2).
- `admin_chat_id`: Telegram Chat ID локального управляющего для отправки предупреждений.
- `contact_phone`, `contact_name`: контакты владельца.
- `last_notification_stage`: стадия последнего отправленного уведомления (`none`, `warning_3d`, `expired_0d`, `locked`).
- `created_at`, `updated_at`: временные метки создания и обновления.

### Начальный Seed:
При старте, если таблица пуста, создается запись для текущего салона:
- `tenant_slug`: `brohouse`
- `name`: `Brohouse Barbershop`
- `is_active`: 1
- `expires_at`: `now + 30 days`
- `grace_period_days`: 2
- `admin_chat_id`: значение из `settings.ADMIN_CHAT_ID`
- `contact_phone`: `+998 97 012-55-55`
- `contact_name`: `Brohouse Admin`

---

## 3. Логика расчета статуса подписки (`services/billing_service.py`)

Статус подписки вычисляется относительно текущего времени по Ташкенту (`get_now_tashkent()`):

1. **`is_active == 0`**:
   - Статус: `LOCKED` (принудительная ручная блокировка суперадмином).
2. **`now < expires_at`**:
   - Если `expires_at - now <= 3 days`: Статус `WARNING` (активен, но скоро закончится).
   - Иначе: Статус `ACTIVE`.
3. **`expires_at <= now < expires_at + grace_period_days`**:
   - Статус: `GRACE_PERIOD` (льготный период 2 дня. Функционал работает без прерываний, но выводится предупреждение в админке).
4. **`now >= expires_at + grace_period_days`**:
   - Статус: `LOCKED` (полная блокировка).

---

## 4. Механизм Kill Switch

### 4.1. Telegram Bot (aiogram 3 middleware)
Создается `SubscriptionCheckMiddleware` для сообщений и callback-запросов:
- Если `user_id == settings.SUPER_ADMIN_CHAT_ID`: полный пропуск (bypass).
- Если статус салона `LOCKED`:
  - **Для клиента** (любое сообщение, команда записи или инлайн-кнопка):
    - Ответ бота: *«Система онлайн-бронирования временно недоступна. Пожалуйста, свяжитесь с салоном напрямую.»*
    - Обработка сообщения прерывается.
  - **Для мастера** (команда `/barber` или кнопки управления сменой):
    - Ответ бота: *«Кабинет мастера временно заблокирован. Обратитесь к руководству салона.»*
  - **Для админа салона** (`ADMIN_CHAT_ID`):
    - Ответ бота: *«❌ Доступ к управлению заблокирован. Срок действия подписки и льготного периода истек. Свяжитесь с суперадминистратором для оплаты.»*

### 4.2. WebApp TMA & API (aiohttp middleware)
В `web_app.py` регистрируется middleware `@web.middleware async def subscription_middleware(request, handler)`:
- Запросы на `/static/*`, `/super-admin*`, `/api/super/*` и запросы от суперадмина пропускаются всегда.
- Для остальных запросов проверяется статус подписки салона:
  - Если `LOCKED`:
    - Для API-эндпоинтов (`/api/*` кроме super): возвращается HTTP `403 Forbidden` с телом:
      ```json
      {
        "error": "subscription_expired",
        "message": "Доступ приостановлен. Оплатите подписку."
      }
      ```
    - Для HTML-страниц (`/book`, `/admin`, `/barber`, `/webapp`, `/`): возвращается специально сгенерированная HTML-заглушка с кодом 403:
      - Стильный темный экран в палитре Brohouse/SaaS.
      - Иконка замка 🔒.
      - Заголовок: «Доступ приостановлен».
      - Пояснение: «Срок действия лицензии салона истек. Пожалуйста, оплатите подписку для возобновления работы системы.»
      - Кнопка вызова/связи с поддержкой.

---

## 5. Планировщик уведомлений (Asyncio Background Task)

В `services/billing_service.py` реализуется функция `run_billing_notifier(bot: Bot)`:
- Запускается в фоновом цикле `asyncio.create_task` при старте бота в `main.py`.
- Проверяет базу данных раз в 24 часа (первая проверка сразу при старте).
- По каждому арендатору:
  - **За 3 дня до expires_at** (если `last_notification_stage != 'warning_3d'`):
    - Отправляет в `admin_chat_id` и `SUPER_ADMIN_CHAT_ID`:
      *«⚠️ Ваша подписка на систему истекает через 3 дня. Пожалуйста, произведите оплату для бесперебойной работы.»*
    - Обновляет `last_notification_stage = 'warning_3d'`.
  - **В день окончания (`now >= expires_at` и `now < expires_at + grace_period_days`)**:
    - Отправляет в `admin_chat_id` и `SUPER_ADMIN_CHAT_ID`:
      *«❌ Подписка истекла. У вас есть 2 дня для оплаты до полной блокировки системы.»*
    - Обновляет `last_notification_stage = 'expired_0d'`.
  - **В момент наступления полной блокировки (`now >= expires_at + grace_period_days`)**:
    - Отправляет в `admin_chat_id` и `SUPER_ADMIN_CHAT_ID`:
      *«🚫 Доступ к системе заблокирован из-за окончания срока оплаты. Онлайн-запись и кабинеты мастеров отключены.»*
    - Обновляет `last_notification_stage = 'locked'`.
  - **При продлении подписки (`now < expires_at - 3 days`)**:
    - Сбрасывает `last_notification_stage = 'none'`.

---

## 6. Пульт управления Super-Admin'а (`/super-admin` и `/super`)

### 6.1. Авторизация
- Конфигурация в `.env`:
  - `SUPER_ADMIN_CHAT_ID`: Telegram Chat ID суперадмина.
  - `SUPER_ADMIN_PASSWORD`: секретный пароль для веб-доступа (по умолчанию генерируется или задается в `.env`).
- Способы входа:
  1. **Telegram Mini App**: команда `/super` открывает WebApp `/super-admin` с `initData` суперадмина.
  2. **Обычный браузер**: переход на `/super-admin` показывает форму ввода пароля. После ввода выдается сессионный токен (сохраняется в `localStorage` и передается в заголовке `X-Super-Admin-Token` или `Cookie`).

### 6.2. Интерфейс (UI)
- Выдержан в фирменной Charcoal & Gold стилистике платформы (`bg-[#121212]`, карточки `#1e1e1e`, акценты `#c5a059`).
- **Блок метрик**:
  - Всего салонов в сети.
  - Активных подписок.
  - В льготном периоде / истекают.
  - Заблокированных.
- **Таблица арендаторов**:
  - Название салона и слаг (`Brohouse` / `brohouse`).
  - Контактное лицо и телефон.
  - Telegram ID админа.
  - Дата окончания подписки (`expires_at`) и дней осталось.
  - Бейдж статуса: 🟢 Активна / 🟡 Grace Period / 🔴 Заблокирована.
- **Действия**:
  - ⚡ **Кнопка `+30 дней`**: продлевает подписку на 30 дней и устанавливает `is_active = 1`.
  - 🔒 **Кнопка `Kill Switch`**: мгновенное включение / отключение доступа.
  - ➕ **Кнопка `Добавить салон`**: модальное окно для регистрации нового филиала или клиента.
  - 👁️ **Быстрый переход**: открыть клиентскую запись (`/book`) или админку салона (`/admin`) с суперадминским обходом.

### 6.3. API эндпоинты суперадмина
- `GET /super-admin` — HTML страница дашборда.
- `POST /api/super/auth` — проверка пароля и выдача токена.
- `GET /api/super/tenants` — список всех арендаторов со статусами.
- `POST /api/super/tenants` — создание нового арендатора.
- `POST /api/super/tenants/{id}/extend-30` — продление подписки на 30 дней.
- `POST /api/super/tenants/{id}/toggle-active` — переключение Kill Switch.
- `POST /api/super/tenants/{id}/update` — редактирование контактов и дат.
