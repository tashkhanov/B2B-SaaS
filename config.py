import os
import json
from typing import Optional, List, Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


# Дефолтные филиалы (Brohouse)
BROHOUSE_DEFAULT_BRANCHES: List[Dict[str, Any]] = [
    {
        "id": 1,
        "name": "Миллий бог",
        "address": "г. Ташкент, метро «Миллий бог», 8B",
        "landmark": "станция метро Milliy Bog'",
        "phone": "+998 97 012-55-55",
        "is_active": 1,
    },
    {
        "id": 2,
        "name": "Чиланзар (2-й кв.)",
        "address": "г. Ташкент, Чиланзарский район, массив Чиланзар, 2-й квартал, 58/5",
        "landmark": "Чиланзар, 2-й квартал, дом 58/5",
        "phone": "+998 97 012-55-55",
        "is_active": 1,
    },
]

# Дефолтные мастера (Brohouse)
BROHOUSE_DEFAULT_BARBERS: List[Dict[str, Any]] = [
    {"name": "Mr. Akmal", "role_title": "Директор / Шеф-мастер", "phone": "+998 97 012-55-55", "telegram_id": None, "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1, "is_vip": 1},
    {"name": "Mr. Dilshod", "role_title": "Стилист", "phone": "+998 97 012-55-55", "telegram_id": None, "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1, "is_vip": 0},
    {"name": "Ismailov Abbos", "role_title": "Стилист", "phone": "+998 97 012-55-55", "telegram_id": None, "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1, "is_vip": 0},
    {"name": "Tursunov Nodir", "role_title": "Стилист", "phone": "+998 97 012-55-55", "telegram_id": None, "work_days": "0,1,2,3,4,5,6", "is_working_today": 1, "is_active": 1, "is_vip": 0},
]

# Дефолтные услуги (Brohouse)
BROHOUSE_DEFAULT_SERVICES: List[Dict[str, Any]] = [
    {"name": "Стрижка волос", "price": 100000, "duration_min": 45, "description": "Классическая мужская стрижка, мытье головы и премиальный стайлинг", "badge": "ТОП", "category": "hair", "is_active": 1},
    {"name": "Стрижка под машинку", "price": 50000, "duration_min": 30, "description": "Стрижка одной или несколькими насадками с четкой окантовкой", "badge": "", "category": "hair", "is_active": 1},
    {"name": "Стрижка на лысо", "price": 30000, "duration_min": 20, "description": "Гладкое бритье под ноль с освежающим уходом", "badge": "", "category": "hair", "is_active": 1},
    {"name": "Детская стрижка от 5 лет", "price": 60000, "duration_min": 40, "description": "Стильная и аккуратная стрижка для юных джентльменов", "badge": "", "category": "hair", "is_active": 1},
    {"name": "Укладка", "price": 60000, "duration_min": 30, "description": "Мытье головы и укладка премиум-стайлингом", "badge": "", "category": "hair", "is_active": 1},
    {"name": "Мойка, массаж", "price": 50000, "duration_min": 30, "description": "Релакс-мытье головы и расслабляющий массаж", "badge": "РЕЛАКС", "category": "spa", "is_active": 1},
    {"name": "Укладка, мойка, массаж", "price": 80000, "duration_min": 45, "description": "Полный СПА-уход: мытье, массаж головы и стильная укладка", "badge": "СПА СЕТ", "category": "spa", "is_active": 1},
    {"name": "Бритье опасной бритвой", "price": 70000, "duration_min": 35, "description": "Традиционное королевское бритье с распариванием горячим полотенцем", "badge": "", "category": "beard", "is_active": 1},
    {"name": "Оформление бороды", "price": 50000, "duration_min": 30, "description": "Моделирование контуров, стрижка бороды, горячий компресс и масло", "badge": "ПОПУЛЯРНО", "category": "beard", "is_active": 1},
    {"name": "Покраска бороды", "price": 30000, "duration_min": 30, "description": "Камуфляж седины и тонирование бороды профессиональными составами", "badge": "", "category": "beard", "is_active": 1},
    {"name": "Воск (одна из зон)", "price": 20000, "duration_min": 15, "description": "Удаление нежелательных волос воском (нос, уши или щеки)", "badge": "", "category": "spa", "is_active": 1},
    {"name": "Черная маска", "price": 30000, "duration_min": 20, "description": "Глубокое очищение пор лица от черных точек и загрязнений", "badge": "УХОД", "category": "spa", "is_active": 1},
]


class Settings(BaseSettings):
    """
    Класс настроек приложения с автоматической загрузкой из .env.
    Поддерживает 100% White-Label: переопределение через .env адаптирует проект
    под любой барбершоп/салон без вмешательства в исходный код.
    """
    # 1. Заведение и брендинг (White-Label)
    TENANT_SLUG: str = "brohouse"
    TENANT_NAME: str = "Brohouse Barbershop"
    SALON_SHORT_NAME: str = "Brohouse"
    SALON_TYPE: str = "мужской салон"
    SALON_CITY: str = "Ташкент"
    SALON_PHONE: str = "+998 97 012-55-55"
    CURRENCY_SYMBOL: str = "сум"
    CURRENCY_CODE: str = "UZS"
    LOGO_URL: str = "/static/logo.png"

    # 2. Руководство, создатель и платформа
    OWNER_NAME: str = "Mr. Akmal"
    OWNER_ROLE: str = "Директор / Шеф-мастер"
    SUPER_ADMIN_NAME: str = "Иброхим"
    SUPER_ADMIN_TELEGRAM: str = "https://t.me/asatkhanov"
    DEVELOPER_NAME: str = "Асатханов (@asatkhanov)"
    PLATFORM_NAME: str = "Khan AI Systems"
    PORTFOLIO_URL: str = "https://asatkhanov.uz"

    # 3. Маркетинг, соцсети и промо-акции
    INSTAGRAM_URL: str = "@brohouseuz"
    ACADEMY_URL: str = "@asbro_academy_"
    PROMO_DISCOUNT_PERCENT: int = 30
    PROMO_TITLE: str = "30% OFF на первую стрижку"
    PROMO_SUBTITLE: str = "Скидка на первую стрижку для новых клиентов!"
    PROMO_PHONE: str = "+998 97 012-55-55"

    # 4. Рабочие часы и слоты
    WORK_START_HOUR: int = 8
    WORK_END_HOUR: int = 23
    SLOT_STEP_MINUTES: int = 30
    MAX_BOOKING_DAYS_AHEAD: int = 7
    TIMEZONE: str = "Asia/Tashkent"

    # 5. Telegram Bot & AI
    BOT_TOKEN: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    ADMIN_CHAT_ID: Optional[int] = None
    DB_NAME: str = "barbershop.db"

    # 6. Telegram Mini App (TMA) & Сервер
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = 8000
    WEBAPP_URL: str = "http://localhost:8000"

    # 7. Безопасность и пароли
    ADMIN_PASSWORD: str = "brohouse2026"
    ADMIN_COOKIE_NAME: Optional[str] = None
    SUPER_ADMIN_CHAT_ID: Optional[int] = None
    SUPER_ADMIN_PASSWORD: str = ""

    # 8. Мультитенантность и биллинг (SaaS Super-Admin)
    SAAS_HUB_URL: str = "https://hub.asatkhanov.uz"
    P2P_CARD_NUMBER: str = "8600 1234 5678 9012"
    P2P_CARD_HOLDER: str = "Akmal A."
    DEFAULT_SUBSCRIPTION_PRICE: int = 1190000

    # 9. SMS интеграция (ENTERPRISE)
    ESKIZ_EMAIL: str = ""
    ESKIZ_PASSWORD: str = ""

    # 10. Кастомные начальные сиды (JSON строки для переопределения в .env)
    INITIAL_BRANCHES_JSON: Optional[str] = None
    INITIAL_BARBERS_JSON: Optional[str] = None
    INITIAL_SERVICES_JSON: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def get_admin_cookie_name(self) -> str:
        """Имя cookie сессии администратора заведения"""
        return self.ADMIN_COOKIE_NAME or f"{self.TENANT_SLUG}_admin_session"

    def get_initial_branches(self) -> List[Dict[str, Any]]:
        """Возвращает список филиалов (из INITIAL_BRANCHES_JSON или Brohouse по умолчанию)"""
        if self.INITIAL_BRANCHES_JSON:
            try:
                data = json.loads(self.INITIAL_BRANCHES_JSON)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception:
                pass
        return BROHOUSE_DEFAULT_BRANCHES

    def get_initial_barbers(self) -> List[Dict[str, Any]]:
        """Возвращает список мастеров (из INITIAL_BARBERS_JSON или Brohouse по умолчанию)"""
        if self.INITIAL_BARBERS_JSON:
            try:
                data = json.loads(self.INITIAL_BARBERS_JSON)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception:
                pass
        return BROHOUSE_DEFAULT_BARBERS

    def get_initial_services(self) -> List[Dict[str, Any]]:
        """Возвращает каталог услуг (из INITIAL_SERVICES_JSON или Brohouse по умолчанию)"""
        if self.INITIAL_SERVICES_JSON:
            try:
                data = json.loads(self.INITIAL_SERVICES_JSON)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception:
                pass
        return BROHOUSE_DEFAULT_SERVICES


# Глобальный экземпляр настроек
settings = Settings()
