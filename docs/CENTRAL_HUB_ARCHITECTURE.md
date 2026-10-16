# 👑 Khan AI Systems: Архитектура Центрального Хаба (SaaS Control Plane)

> **Документ для сохранения долгосрочного контекста разработки**  
> **Автор и Создатель платформы:** Иброхим Асатханов ([@asatkhanov](https://t.me/asatkhanov), Telegram ID: `1243374131`)  
> **Основной домен:** `asatkhanov.uz` (Django-монолит)  
> **Дата фиксации концепта:** 17 октября 2026 г.

---

## 1. Главная идея и бизнес-модель

Платформа **Khan AI Systems** автоматизирует мужские салоны, барбершопы и рестораны через связку:
- **Telegram Mini Apps (TMA)**: онлайн-запись клиентов, личные кабинеты мастеров, витрина услуг.
- **Голосовой и текстовый ИИ (Google Gemini API)**: живой администратор, зеркалящий язык (русский / узбекский).
- **Жесткий биллинг и Kill Switch**: автоматическая блокировка при неоплате подписки заведением.

### Проблема MVP Фазы 1:
В первой версии (Brohouse) код супер-админки (`/super-admin`, `tenant_subscription`) был временно вшит внутрь локального проекта одного конкретного барбершопа (`d:/IT/B2B-SaaS`).  
Это отлично подошло для быстрой демонстрации первого пилота, но **не масштабируется на город**:
1. Если подключить второй салон (*Top Gun*, *Chop-Chop*), у каждого будет изолированная локальная база SQLite. Супер-админка Brohouse не увидит другие заведения.
2. Владелец салона не должен иметь даже теоретического доступа к коду управления другими сетями.
3. Управляющий пульт Создателя должен быть **единым и независимым**.

---

## 2. Целевая архитектура (Фаза 2: Центральный Хаб на `asatkhanov.uz`)

```
                        👑 ИБРОХИМ (СОЗДАТЕЛЬ)
                                  │
                                  ▼
           [ Центральный Хаб: hub.asatkhanov.uz (Django) ]
           ├── Единая база заведений (PostgreSQL / SQLite)
           ├── Личный пульт Super-Admin (Kill Switch, продление)
           └── REST API проверки лицензий (/api/v1/license/check)
                                  │
         ┌────────────────────────┼────────────────────────┐
         │ (HTTP ping раз в 15м)  │ (HTTP ping раз в 15м)  │ (HTTP ping раз в 15м)
         ▼                        ▼                        ▼
  [ Бот 1: Brohouse ]      [ Бот 2: Top Gun ]       [ Бот 3: Chop-Chop ]
  ├── Локальный AI         ├── Локальный AI         ├── Локальный AI
  ├── Клиентский TMA       ├── Клиентский TMA       ├── Клиентский TMA
  └── Локальная база БД    └── Локальная база БД    └── Локальная база БД
```

### Разделение на два контура:
1. **Control Plane (Центр управления):**
   - Находится на личном домене Иброхима: `asatkhanov.uz` (поддомен `hub.asatkhanov.uz` или путь `/hub/`).
   - Работает на существующем Django-бэкенде сайта `asatkhanov.uz`.
   - Хранит реестр всех подключенных бизнесов, тарифы, контакты владельцев, сроки подписок и рубильники Kill Switch.
2. **Data Plane (Инстансы заведений):**
   - Каждый салон имеет свой изолированный бот, свой токен Telegram, свои локальные записи и расписание мастеров.
   - Бот салона не знает о существовании других салонов.

---

## 3. Технический анализ нагрузки: почему `asatkhanov.uz` не ляжет?

### Распределение нагрузки:
- **Тяжёлая нагрузка (99.9%) остаётся на стороне бота салона:**
  - Скачивание голосовых сообщений (OGG Opus).
  - Прямые вызовы Google Gemini API (обработка аудио, генерация текста).
  - Отдача статики TMA (картинки, прайсы) клиентам через CDN или туннель.
- **Нагрузка на `asatkhanov.uz` (0.01%):**
  - Бот салона раз в 15–30 минут отправляет 1 легковесный HTTP-запрос:
    ```http
    GET /api/v1/license/check?slug=brohouse HTTP/1.1
    Authorization: Bearer <tenant_secret_token>
    ```
  - Django отвечает JSON-объектом размером ~120 байт:
    ```json
    {
      "status": "active",
      "expires_at": "2026-11-17T00:00:00+05:00",
      "kill_switch": false,
      "message": "Лицензия активна"
    }
    ```

### Расчет RPS (запросов в секунду):
- При **10 заведениях**: 10 запросов за 30 минут = **0.005 RPS**.
- При **100 заведениях**: 100 запросов за 30 минут = **0.05 RPS** (~3 запроса в минуту).
- Стандартный Django на gunicorn/uvicorn держит **от 100 до 500 RPS** даже на сервере за $5/мес.  
**Вывод:** Нагрузка для сайта `asatkhanov.uz` абсолютно незаметна.

### Защита от сбоев интернета (Offline Grace Cache):
Чтобы бот салона не блокировался, если на сервере `asatkhanov.uz` идёт перезагрузка или техработы:
- Бот салона кэширует статус лицензии локально на **2–4 часа**.
- Если сервер лицензий временно недоступен — бот продолжает штатно обслуживать клиентов на основе последнего успешного ответа.

---

## 4. План реализации на Django (`asatkhanov.uz`)

### Шаг 1. Создание Django App `tenants`
В проекте `asatkhanov.uz` создается изолированное приложение:
```bash
python manage.py startapp tenants
```

### Шаг 2. Модель данных (Django Models)
```python
# tenants/models.py
from django.db import models
from django.utils import timezone
from datetime import timedelta

class Tenant(models.Model):
    name = models.CharField(max_length=150, verbose_name="Название заведения")
    slug = models.SlugField(unique=True, verbose_name="Слаг (ID для API)")
    contact_name = models.CharField(max_length=100, verbose_name="Контактное лицо")
    contact_phone = models.CharField(max_length=30, verbose_name="Телефон владельца")
    admin_telegram_id = models.BigIntegerField(null=True, blank=True, verbose_name="Telegram ID владельца")
    monthly_price_uzs = models.IntegerField(default=1000000, verbose_name="Ежемесячная оплата (сум)")
    api_key = models.CharField(max_length=64, unique=True, verbose_name="Секретный ключ бота")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"

class Subscription(models.Model):
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="subscription")
    is_active = models.BooleanField(default=True, verbose_name="Подписка включена")
    expires_at = models.DateTimeField(verbose_name="Дата окончания лицензии")
    grace_period_days = models.IntegerField(default=2, verbose_name="Льготный период (дней)")
    kill_switch_active = models.BooleanField(default=False, verbose_name="Ручная блокировка (Kill Switch)")

    @property
    def status(self):
        if self.kill_switch_active:
            return "LOCKED"
        now = timezone.now()
        if now < self.expires_at:
            if (self.expires_at - now).days <= 3:
                return "WARNING"
            return "ACTIVE"
        grace_end = self.expires_at + timedelta(days=self.grace_period_days)
        if now <= grace_end:
            return "GRACE_PERIOD"
        return "LOCKED"
```

### Шаг 3. API Эндпоинт проверки лицензии
```python
# tenants/views.py
from django.http import JsonResponse
from .models import Tenant

def check_license(request):
    slug = request.GET.get("slug")
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    
    try:
        tenant = Tenant.objects.select_related("subscription").get(slug=slug, api_key=token)
        sub = tenant.subscription
        return JsonResponse({
            "status": sub.status,
            "is_locked": sub.status == "LOCKED",
            "expires_at": sub.expires_at.isoformat(),
            "name": tenant.name,
            "contact_phone": tenant.contact_phone
        })
    except Tenant.DoesNotExist:
        return JsonResponse({"error": "invalid_credentials"}, status=403)
```

### Шаг 4. Интерфейс Super-Admin
- **Вариант А (Быстрый):** Встроенный Django Admin (`/admin/tenants/`). В 1 клик в списке заведений кнопка «+30 дней» и переключатель `kill_switch_active`.
- **Вариант Б (Премиум TMA):** Тот самый готовый файл `webapp/super_admin.html` (на Plus Jakarta Sans и Lucide SVGs), который мы уже создали. Он просто монтируется как Django Template или SPA на `hub.asatkhanov.uz`.

---

## 5. Как адаптируется код клиента (`B2B-SaaS`)

В репозитории клиента (`d:/IT/B2B-SaaS`):
1. В `.env` добавляются переменные:
   ```env
   CENTRAL_HUB_URL=https://hub.asatkhanov.uz
   TENANT_SLUG=brohouse
   TENANT_API_KEY=kh_sec_brohouse_2026_xyz
   ```
2. В `services/billing_service.py`:
   - Метод `check_tenant_access()` сначала проверяет быстрый локальный кэш (память/файл).
   - Если кэш устарел (>15 минут) — делает фоновый асинхронный `aiohttp.ClientSession.get` к `CENTRAL_HUB_URL`.
   - Если центральный хаб вернул `LOCKED` — активируется красивый экран блокировки (`render_lockout_page`), созданный 17 октября.

---

## 6. Почему привязка к `asatkhanov.uz` — стратегически лучший ход:
1. **Репутация и доверие:** Владельцы заведений в Ташкенте видят реальный сайт разработчика, портфолио, кейсы и отзывы. Это внушает максимальное доверие к надежности системы.
2. **Нулевые лишние расходы:** Не нужно оплачивать и поддерживать отдельные виртуальные сервера под SaaS-биллинг.
3. **Единое окно контроля:** Иброхим заходит со своего смартфона на `asatkhanov.uz`, видит все свои проекты, фриланс-кейсы, и тут же в один клик управляет подписками заведений.
4. **Легкий ребрендинг в будущем:** Если когда-либо проект вырастет до отдельного юридического лица или инвесторов, к этому Django-приложению за 10 минут подключается домен `khansystems.uz` без переписывания бэкенда.

---

*Документ подготовлен AI-ассистентом Antigravity в рамках ночной сессии архитектурного планирования. Готов к исполнению при переходе к Фазе 2.*
