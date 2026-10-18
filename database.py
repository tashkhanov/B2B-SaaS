import logging
import sqlite3
import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from config import settings

logger = logging.getLogger(__name__)


class BookingConflictError(Exception):
    """Исключение при попытке записаться на уже занятый слот (состояние гонки / овербукинг)."""
    pass



def get_tashkent_now_naive() -> datetime:
    """Текущее время в часовом поясе Asia/Tashkent (UTC+5) без tzinfo для SQLite."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def get_db_path(db_path: Optional[str] = None) -> str:
    """Динамическое получение пути к БД с учетом актуального значения settings.DB_NAME"""
    return db_path or (settings.DB_NAME if settings.DB_NAME else "barbershop.db")


async def init_db(db_path: Optional[str] = None) -> None:
    """
    Инициализация схемы таблиц базы данных SQLite.
    Создает таблицы:
    - messages (история диалогов)
    - services (каталог услуг салона)
    - barbers (мастера, грейды, аватары, график)
    - bookings (записи клиентов)
    Также проводит миграции колонок и начальный seed данных.
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        # 1. Таблица сообщений диалога
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages (user_id);
        """)

        # 2. Таблица услуг салона (Services CRUD)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                duration_min INTEGER DEFAULT 60,
                description TEXT,
                badge TEXT DEFAULT '',
                category TEXT DEFAULT 'hair',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Таблица барберов (мастеров салона)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS barbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role_title TEXT NOT NULL DEFAULT 'Барбер',
                phone TEXT,
                telegram_id INTEGER,
                work_days TEXT DEFAULT '0,1,2,3,4,5,6',
                is_working_today INTEGER DEFAULT 1,
                is_active INTEGER DEFAULT 1,
                is_vip INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Миграция колонок barbers (is_vip)
        cursor = await db.execute("PRAGMA table_info(barbers);")
        barber_cols = [row[1] for row in await cursor.fetchall()]
        if "is_vip" not in barber_cols:
            await db.execute("ALTER TABLE barbers ADD COLUMN is_vip INTEGER DEFAULT 0;")

        # 4. Таблица филиалов барбершопа (Branches)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                landmark TEXT,
                phone TEXT DEFAULT '+998 97 012-55-55',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 5. Таблица записей клиентов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                phone TEXT,
                service TEXT,
                booking_time TEXT,
                status TEXT DEFAULT 'new',
                details TEXT NOT NULL,
                barber_id INTEGER DEFAULT NULL,
                barber_name TEXT DEFAULT NULL,
                service_id INTEGER DEFAULT NULL,
                branch_id INTEGER DEFAULT 1,
                branch_name TEXT DEFAULT 'Миллий бог',
                reminder_2h_sent INTEGER DEFAULT 0,
                client_confirmed INTEGER DEFAULT 0,
                winback_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Миграция колонок bookings (на случай если таблица создавалась ранее)
        cursor = await db.execute("PRAGMA table_info(bookings);")
        columns = [row[1] for row in await cursor.fetchall()]

        for col_name, col_type, col_def in [
            ("phone", "TEXT", ""),
            ("service", "TEXT", ""),
            ("booking_time", "TEXT", ""),
            ("status", "TEXT", "DEFAULT 'new'"),
            ("barber_id", "INTEGER", "DEFAULT NULL"),
            ("barber_name", "TEXT", "DEFAULT NULL"),
            ("service_id", "INTEGER", "DEFAULT NULL"),
            ("branch_id", "INTEGER", "DEFAULT 1"),
            ("branch_name", "TEXT", "DEFAULT 'Миллий бог'"),
            ("reminder_2h_sent", "INTEGER", "DEFAULT 0"),
            ("client_confirmed", "INTEGER", "DEFAULT 0"),
            ("winback_sent", "INTEGER", "DEFAULT 0"),
        ]:
            if col_name not in columns:
                await db.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type} {col_def};")

        # Миграция колонок services (категория hair/beard/spa)
        cursor = await db.execute("PRAGMA table_info(services);")
        srv_columns = [row[1] for row in await cursor.fetchall()]
        if "category" not in srv_columns:
            await db.execute("ALTER TABLE services ADD COLUMN category TEXT DEFAULT 'hair';")

        # Уникальный индекс для исключения параллельных записей на один и тот же слот (Anti-Race Condition)
        try:
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_booking 
                ON bookings (barber_id, booking_time) 
                WHERE status NOT IN ('cancelled') AND barber_id IS NOT NULL AND booking_time IS NOT NULL;
            """)
        except Exception as e:
            logger.warning("Could not create idx_unique_active_booking: %s", e)

        # 6. Таблица настроек салона (Salon Settings)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS salon_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        default_settings = [
            ("work_start_hour", str(settings.WORK_START_HOUR)),
            ("work_end_hour", str(settings.WORK_END_HOUR)),
            ("max_booking_days_ahead", str(settings.MAX_BOOKING_DAYS_AHEAD)),
            ("slot_step_minutes", str(settings.SLOT_STEP_MINUTES)),
            ("promo_discount_percent", str(settings.PROMO_DISCOUNT_PERCENT)),
            ("promo_title", settings.PROMO_TITLE),
            ("promo_subtitle", settings.PROMO_SUBTITLE),
            ("promo_phone", settings.SALON_PHONE),
        ]
        await db.executemany(
            "INSERT OR IGNORE INTO salon_settings (key, value) VALUES (?, ?)",
            default_settings
        )

        # Начальный seed для branches, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM branches")
        branches_count = (await cursor.fetchone())[0]
        if branches_count == 0:
            default_branches = [
                (
                    b.get("id"),
                    b.get("name"),
                    b.get("address"),
                    b.get("landmark", ""),
                    b.get("phone", settings.SALON_PHONE),
                    b.get("is_active", 1),
                )
                for b in settings.get_initial_branches()
            ]
            await db.executemany(
                """
                INSERT OR IGNORE INTO branches (id, name, address, landmark, phone, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                default_branches
            )

        # Начальный seed для services, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM services")
        services_count = (await cursor.fetchone())[0]
        if services_count == 0:
            default_services = [
                (
                    s.get("name"),
                    s.get("price"),
                    s.get("duration_min", 45),
                    s.get("description", ""),
                    s.get("badge", ""),
                    s.get("category", "hair"),
                    s.get("is_active", 1),
                )
                for s in settings.get_initial_services()
            ]
            await db.executemany(
                """
                INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                default_services
            )

        # Начальный seed для barbers, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM barbers")
        barbers_count = (await cursor.fetchone())[0]
        if barbers_count == 0:
            default_barbers = [
                (
                    b.get("name"),
                    b.get("role_title", "Стилист"),
                    b.get("phone", settings.SALON_PHONE),
                    b.get("telegram_id"),
                    b.get("work_days", "0,1,2,3,4,5,6"),
                    b.get("is_working_today", 1),
                    b.get("is_active", 1),
                    b.get("is_vip", 0),
                )
                for b in settings.get_initial_barbers()
            ]
            await db.executemany(
                """
                INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                default_barbers
            )
        else:
            # Гарантируем разметку VIP по роли (Шеф / Директор / Главный) без хардкода конкретных имен
            await db.execute("UPDATE barbers SET is_vip = 1 WHERE role_title LIKE '%Шеф%' OR role_title LIKE '%Директор%' OR role_title LIKE '%Главный%' OR role_title LIKE '%Владелец%';")

        # 5. Таблица подписок салонов (Billing / Multi-tenant SaaS / Kill Switch)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tenant_subscription (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                tier TEXT DEFAULT 'PRO',
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
        """)

        # Миграция: добавление колонки tier, если таблица была создана ранее
        cursor = await db.execute("PRAGMA table_info(tenant_subscription);")
        sub_columns = [row[1] for row in await cursor.fetchall()]
        if "tier" not in sub_columns:
            await db.execute("ALTER TABLE tenant_subscription ADD COLUMN tier TEXT DEFAULT 'PRO';")

        # Начальный seed для tenant_subscription, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM tenant_subscription")
        tenants_count = (await cursor.fetchone())[0]
        if tenants_count == 0:
            now_dt = get_tashkent_now_naive()
            default_expires_at = (now_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                """
                INSERT INTO tenant_subscription (
                    tenant_slug, name, tier, is_active, expires_at, grace_period_days,
                    admin_chat_id, contact_phone, contact_name, last_notification_stage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settings.TENANT_SLUG,
                    settings.TENANT_NAME,
                    "PRO",
                    1,
                    default_expires_at,
                    2,
                    settings.ADMIN_CHAT_ID,
                    settings.SALON_PHONE,
                    f"{settings.SALON_SHORT_NAME} Admin",
                    "none",
                )
            )

        # 6. Таблица журналов загруженных квитанций об оплате (Receipt OCR / Anti-Replay)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipt_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER DEFAULT 1,
                transaction_id TEXT UNIQUE,
                amount INTEGER,
                payment_method TEXT DEFAULT 'p2p',
                sender_name TEXT,
                recipient_card_last4 TEXT,
                raw_ocr_json TEXT,
                status TEXT DEFAULT 'pending_verification',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.commit()



async def reseed_brohouse_data(db_path: Optional[str] = None) -> None:
    """Полная очистка и переинициализация базы актуальными данными салона из конфигурации settings."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute("DELETE FROM services;")
        await db.execute("DELETE FROM barbers;")
        await db.execute("DELETE FROM branches;")
        await db.execute("DELETE FROM bookings;")
        await db.execute("DELETE FROM messages;")

        default_branches = [
            (
                b.get("id"),
                b.get("name"),
                b.get("address"),
                b.get("landmark", ""),
                b.get("phone", settings.SALON_PHONE),
                b.get("is_active", 1),
            )
            for b in settings.get_initial_branches()
        ]
        await db.executemany(
            """
            INSERT OR IGNORE INTO branches (id, name, address, landmark, phone, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_branches
        )

        default_services = [
            (
                s.get("name"),
                s.get("price"),
                s.get("duration_min", 45),
                s.get("description", ""),
                s.get("badge", ""),
                s.get("category", "hair"),
                s.get("is_active", 1),
            )
            for s in settings.get_initial_services()
        ]
        await db.executemany(
            """
            INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            default_services
        )

        default_barbers = [
            (
                b.get("name"),
                b.get("role_title", "Стилист"),
                b.get("phone", settings.SALON_PHONE),
                b.get("telegram_id"),
                b.get("work_days", "0,1,2,3,4,5,6"),
                b.get("is_working_today", 1),
                b.get("is_active", 1),
                b.get("is_vip", 0),
            )
            for b in settings.get_initial_barbers()
        ]
        await db.executemany(
            """
            INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            default_barbers
        )
        await db.commit()


# Алиас для поддержки универсального мультиарендного вызова
reseed_tenant_data = reseed_brohouse_data



# =============================================================================
# ИСТОРИЯ ДИАЛОГОВ
# =============================================================================

async def add_message(user_id: int, role: str, text: str, db_path: Optional[str] = None) -> None:
    """Сохранение реплики пользователя ('user') или ассистента ('model') в историю."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text)
        )
        await db.commit()


async def get_recent_messages(user_id: int, limit: int = 10, db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Получение последних N реплик пользователя (в хронологическом порядке)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, text FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": row["role"], "text": row["text"]} for row in reversed(rows)]


async def clear_history(user_id: int, db_path: Optional[str] = None) -> None:
    """Очистка контекста диалога пользователя (команда /reset)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()


# =============================================================================
# ФИЛИАЛЫ САЛОНА (BRANCHES)
# =============================================================================

async def get_branches(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict]:
    """Получение списка всех филиалов барбершопа."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM branches"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_branch_by_id(branch_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение информации о филиале по его ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM branches WHERE id = ?", (branch_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_branch(
    name: str,
    address: str,
    landmark: Optional[str] = "",
    phone: str = "+998 97 012-55-55",
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание нового филиала салона."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO branches (name, address, landmark, phone, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name.strip(), address.strip(), landmark.strip() if landmark else "", phone.strip(), is_active)
        )
        await db.commit()
        return cursor.lastrowid


# =============================================================================
# УПРАВЛЕНИЕ УСЛУГАМИ (SERVICES CRUD)
# =============================================================================

async def get_services(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict]:
    """Получение списка услуг салона."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM services"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_service_by_id(service_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение услуги по её ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM services WHERE id = ?", (service_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_service(
    name: str,
    price: int,
    duration_min: int = 60,
    description: str = "",
    badge: str = "",
    category: str = "hair",
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание новой услуги салона администратором."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), price, duration_min, description.strip(), badge.strip(), category.strip(), is_active)
        )
        await db.commit()
        return cursor.lastrowid


async def update_service(
    service_id: int,
    name: str,
    price: int,
    duration_min: int = 60,
    description: str = "",
    badge: str = "",
    category: str = "hair",
    is_active: int = 1,
    db_path: Optional[str] = None
) -> bool:
    """Обновление существующей услуги салона."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            UPDATE services 
            SET name = ?, price = ?, duration_min = ?, description = ?, badge = ?, category = ?, is_active = ?
            WHERE id = ?
            """,
            (name.strip(), price, duration_min, description.strip(), badge.strip(), category.strip(), is_active, service_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_service(service_id: int, hard: bool = False, db_path: Optional[str] = None) -> bool:
    """Удаление услуги (по умолчанию soft-delete: is_active = 0)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        if hard:
            cursor = await db.execute("DELETE FROM services WHERE id = ?", (service_id,))
        else:
            cursor = await db.execute("UPDATE services SET is_active = 0 WHERE id = ?", (service_id,))
        await db.commit()
        return cursor.rowcount > 0


# =============================================================================
# УПРАВЛЕНИЕ БАРБЕРАМИ (BARBERS CRUD)
# =============================================================================

async def get_barbers(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict]:
    """Получение списка барберов."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM barbers"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_barber_by_id(barber_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение барбера по ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM barbers WHERE id = ?", (barber_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_barber_by_telegram_id(telegram_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение мастера по его Telegram ID (для авторизации в /barber и алертов)."""
    if not telegram_id:
        return None
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM barbers WHERE telegram_id = ? AND is_active = 1 LIMIT 1",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_barber_by_id(barber_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение информации о мастере по его ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM barbers WHERE id = ?", (barber_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_barber_vip_status(barber_id: int, is_vip: int, db_path: Optional[str] = None) -> bool:
    """Установка VIP-статуса мастера (1=требует ручного подтверждения директором, 0=автопилот)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute("UPDATE barbers SET is_vip = ? WHERE id = ?", (is_vip, barber_id))
        await db.commit()
        return cursor.rowcount > 0


async def create_barber(
    name: str,
    role_title: str = "Барбер",
    phone: Optional[str] = None,
    telegram_id: Optional[int] = None,
    work_days: str = "0,1,2,3,4,5,6",
    is_working_today: int = 1,
    is_active: int = 1,
    is_vip: int = 0,
    db_path: Optional[str] = None
) -> int:
    """Создание нового мастера администратором."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), role_title.strip(), phone, telegram_id, work_days, is_working_today, is_active, is_vip)
        )
        await db.commit()
        return cursor.lastrowid


async def update_barber(
    barber_id: int,
    name: str,
    role_title: str = "Барбер",
    phone: Optional[str] = None,
    telegram_id: Optional[int] = None,
    work_days: str = "0,1,2,3,4,5,6",
    is_working_today: int = 1,
    is_active: int = 1,
    is_vip: Optional[int] = None,
    db_path: Optional[str] = None
) -> bool:
    """Обновление карточки мастера."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        if is_vip is not None:
            cursor = await db.execute(
                """
                UPDATE barbers 
                SET name = ?, role_title = ?, phone = ?, telegram_id = ?, 
                    work_days = ?, is_working_today = ?, is_active = ?, is_vip = ?
                WHERE id = ?
                """,
                (name.strip(), role_title.strip(), phone, telegram_id, work_days, is_working_today, is_active, is_vip, barber_id)
            )
        else:
            cursor = await db.execute(
                """
                UPDATE barbers 
                SET name = ?, role_title = ?, phone = ?, telegram_id = ?, 
                    work_days = ?, is_working_today = ?, is_active = ?
                WHERE id = ?
                """,
                (name.strip(), role_title.strip(), phone, telegram_id, work_days, is_working_today, is_active, barber_id)
            )
        await db.commit()
        return cursor.rowcount > 0


async def toggle_barber_shift(
    barber_id: int,
    is_working_today: Optional[int] = None,
    db_path: Optional[str] = None
) -> bool:
    """Мгновенный тумблер смены мастера (1=на смене, 0=выходной)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        if is_working_today is None:
            # Инвертируем текущее значение
            await db.execute(
                "UPDATE barbers SET is_working_today = CASE WHEN is_working_today = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (barber_id,)
            )
        else:
            await db.execute(
                "UPDATE barbers SET is_working_today = ? WHERE id = ?",
                (1 if is_working_today else 0, barber_id)
            )
        await db.commit()
        return True


async def delete_barber(barber_id: int, hard: bool = False, db_path: Optional[str] = None) -> bool:
    """Удаление барбера (по умолчанию soft-delete: is_active = 0)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        if hard:
            cursor = await db.execute("DELETE FROM barbers WHERE id = ?", (barber_id,))
        else:
            cursor = await db.execute("UPDATE barbers SET is_active = 0 WHERE id = ?", (barber_id,))
        await db.commit()
        return cursor.rowcount > 0


# =============================================================================
# БРОНИРОВАНИЯ (BOOKINGS)
# =============================================================================

async def save_booking(
    user_id: int,
    username: Optional[str],
    full_name: str,
    details: str,
    phone: Optional[str] = None,
    service: Optional[str] = None,
    booking_time: Optional[str] = None,
    status: str = "new",
    barber_id: Optional[int] = None,
    barber_name: Optional[str] = None,
    service_id: Optional[int] = None,
    branch_id: int = 1,
    branch_name: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    if not branch_name:
        b_info = await get_branch_by_id(branch_id, db_path=db_path)
        branch_name = b_info["name"] if b_info else f"Филиал #{branch_id}"

    target_db = get_db_path(db_path)
    try:
        async with aiosqlite.connect(target_db) as db:
            await db.execute("BEGIN IMMEDIATE;")
            cursor = await db.execute(
                """
                INSERT INTO bookings (
                    user_id, username, full_name, phone, service, booking_time, status, details,
                    barber_id, barber_name, service_id, branch_id, branch_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, full_name, phone, service, booking_time, status, details,
                 barber_id, barber_name, service_id, branch_id, branch_name)
            )
            await db.commit()
            return cursor.lastrowid
    except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as err:
        logger.warning("Collision detected while saving booking: %s", err)
        raise BookingConflictError("Выбранный слот уже занят другим клиентом. Пожалуйста, выберите другое время.") from err



async def has_recent_booking(
    user_id: int,
    booking_time: str,
    db_path: Optional[str] = None
) -> bool:
    """Проверяет, создавал ли пользователь запись на это же время за последние 15 минут."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        async with db.execute(
            """
            SELECT id FROM bookings 
            WHERE user_id = ? AND booking_time = ? 
              AND datetime(created_at) >= datetime('now', '-15 minutes')
            LIMIT 1
            """,
            (user_id, booking_time)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def auto_complete_past_bookings(db_path: Optional[str] = None) -> int:
    """
    Автоматически переводит все брони со статусом 'new' или 'confirmed'
    в статус 'completed', если время окончания процедуры уже прошло.
    """
    from services.schedule_service import is_booking_past

    target_db = get_db_path(db_path)
    updated_count = 0

    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, booking_time FROM bookings WHERE status IN ('new', 'confirmed')"
        ) as cursor:
            rows = await cursor.fetchall()

        expired_ids = []
        for row in rows:
            raw_time = row["booking_time"]
            if raw_time and is_booking_past(raw_time):
                expired_ids.append(row["id"])

        if expired_ids:
            placeholders = ",".join("?" for _ in expired_ids)
            cursor = await db.execute(
                f"UPDATE bookings SET status = 'completed' WHERE id IN ({placeholders})",
                expired_ids
            )
            await db.commit()
            updated_count = cursor.rowcount

    return updated_count


async def get_all_bookings(db_path: Optional[str] = None) -> List[Dict]:
    """Получение всех записей клиентов для админки (с предварительным авто-завершением)."""
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at,
                   reminder_2h_sent, client_confirmed, winback_sent
            FROM bookings ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_active_bookings(db_path: Optional[str] = None) -> List[Dict]:
    """Получение активных записей ('new', 'confirmed') для валидации слотов."""
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at 
            FROM bookings 
            WHERE status IN ('new', 'confirmed') AND booking_time IS NOT NULL AND booking_time != ''
            ORDER BY id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_bookings_for_barber(
    barber_id: int,
    target_date: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict]:
    """
    Получение записей конкретного мастера для его личного кабинета (/barber).
    Если target_date указан (формат 'YYYY-MM-DD'), фильтрует по дате.
    """
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at
            FROM bookings
            WHERE (barber_id = ? OR barber_id IS NULL)
        """
        params = [barber_id]
        if target_date:
            query += " AND booking_time LIKE ?"
            params.append(f"{target_date}%")

        query += " ORDER BY id DESC"
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_booking_status(booking_id: int, status: str, db_path: Optional[str] = None) -> bool:
    """Обновление статуса записи (new -> confirmed -> cancelled -> completed)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET status = ? WHERE id = ?",
            (status, booking_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_booking_by_id(booking_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение конкретной записи по ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at 
            FROM bookings WHERE id = ?
            """,
            (booking_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_active_booking(user_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение ближайшей активной (предстоящей) брони пользователя."""
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at
            FROM bookings 
            WHERE user_id = ? AND status IN ('new', 'confirmed')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def cancel_user_booking(user_id: int, booking_id: Optional[int] = None, db_path: Optional[str] = None) -> Optional[Dict]:
    """Отмена активной брони пользователя (клиентом)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        if booking_id:
            query = "SELECT * FROM bookings WHERE id = ? AND user_id = ? AND status IN ('new', 'confirmed')"
            params = (booking_id, user_id)
        else:
            query = "SELECT * FROM bookings WHERE user_id = ? AND status IN ('new', 'confirmed') ORDER BY id DESC LIMIT 1"
            params = (user_id,)

        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            booking = dict(row)

        await db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking["id"],))
        await db.commit()
        booking["status"] = "cancelled"
        return booking


async def get_bookings_due_for_2h_reminder(
    window_start_minutes: int = 105,
    window_end_minutes: int = 135,
    db_path: Optional[str] = None
) -> List[Dict]:
    """
    Выборка активных записей, до начала которых осталось примерно 2 часа
    (по умолчанию окно от 105 до 135 минут), которым напоминание еще не отправлялось.
    """
    from services.schedule_service import parse_booking_datetime, get_now_tashkent

    target_db = get_db_path(db_path)
    now = get_now_tashkent()
    due_bookings = []

    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM bookings 
            WHERE status IN ('new', 'confirmed') 
              AND (reminder_2h_sent IS NULL OR reminder_2h_sent = 0)
              AND booking_time IS NOT NULL AND booking_time != ''
            ORDER BY id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            booking = dict(row)
            raw_time = booking.get("booking_time") or ""
            dt = parse_booking_datetime(raw_time)
            if dt:
                diff_sec = (dt - now).total_seconds()
                diff_min = diff_sec / 60.0
                if window_start_minutes <= diff_min <= window_end_minutes:
                    due_bookings.append(booking)

    return due_bookings


async def mark_booking_reminder_sent(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отметка об отправке 2-часового пуша клиенту."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET reminder_2h_sent = 1 WHERE id = ?",
            (booking_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def confirm_booking_by_client(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Подтверждение явки клиентом по кнопке «Буду вовремя ✅»."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET client_confirmed = 1, status = 'confirmed' WHERE id = ?",
            (booking_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def cancel_booking_by_client(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отмена записи клиентом по кнопке «Не смогу прийти ❌»."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ?",
            (booking_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_clients_due_for_winback(
    days_threshold: int = 25,
    db_path: Optional[str] = None,
    days_ago: Optional[int] = None,
) -> List[Dict]:
    """
    Выборка клиентов для повторного вовлечения (Win-Back):
    1. Последняя запись завершена ('completed') не менее days_threshold дней назад.
    2. Клиенту еще не отправлялся winback-пуш по этой записи (winback_sent = 0).
    3. У клиента нет никаких активных будущих записей ('new', 'confirmed', 'pending_vip').
    """
    from services.schedule_service import parse_booking_datetime, get_now_tashkent, TASHKENT_TZ

    effective_threshold = days_ago if days_ago is not None else days_threshold
    target_db = get_db_path(db_path)
    now = get_now_tashkent()
    due_clients = []

    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM bookings
            WHERE status = 'completed'
              AND (winback_sent IS NULL OR winback_sent = 0)
              AND user_id IS NOT NULL AND user_id > 0
            ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()

        seen_users = set()
        for row in rows:
            booking = dict(row)
            uid = booking["user_id"]
            if uid in seen_users:
                continue

            raw_time = booking.get("booking_time") or ""
            dt = parse_booking_datetime(raw_time)
            if not dt and booking.get("created_at"):
                try:
                    dt = datetime.fromisoformat(str(booking["created_at"]).replace("Z", "+00:00"))
                    if not dt.tzinfo:
                        dt = dt.replace(tzinfo=TASHKENT_TZ)
                except Exception:
                    dt = None

            if not dt:
                continue

            diff_days = (now - dt).total_seconds() / 86400.0
            if diff_days >= effective_threshold:
                async with db.execute(
                    """
                    SELECT id FROM bookings
                    WHERE user_id = ? AND status IN ('new', 'confirmed', 'pending_vip')
                    LIMIT 1
                    """,
                    (uid,)
                ) as active_cur:
                    has_active = await active_cur.fetchone()

                if not has_active:
                    seen_users.add(uid)
                    due_clients.append(booking)

    return due_clients


async def mark_client_winback_sent(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отметка об отправке winback-пуша клиенту (по booking_id или user_id)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET winback_sent = 1 WHERE id = ? OR user_id = ?",
            (booking_id, booking_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def find_booking_by_client_name(query: str, db_path: Optional[str] = None) -> Optional[Dict]:
    """Поиск активной брони клиента по имени или @username."""
    await auto_complete_past_bookings(db_path)
    clean_query = query.strip().replace("@", "").lower()
    target_db = get_db_path(db_path)

    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at
            FROM bookings 
            WHERE status IN ('new', 'confirmed')
              AND (LOWER(full_name) LIKE ? OR LOWER(username) LIKE ?)
            ORDER BY id DESC LIMIT 1
            """,
            (f"%{clean_query}%", f"%{clean_query}%")
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# =============================================================================
# УПРАВЛЕНИЕ ПОДПИСКАМИ САЛОНОВ (BILLING / MULTI-TENANT / KILL SWITCH)
# =============================================================================

async def get_tenant_subscription(tenant_slug: str, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение подписки салона по его слагу (например, 'brohouse')."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tenant_subscription WHERE tenant_slug = ?",
            (tenant_slug,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_tenant_by_id(tenant_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение подписки салона по числовому ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tenant_subscription WHERE id = ?",
            (tenant_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_tenant_by_id_or_slug(tenant_identifier: Any, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение подписки салона по числовому ID или текстовому слагу (например, 'brohouse')."""
    if tenant_identifier is None:
        return None
    target_db = get_db_path(db_path)
    ident_str = str(tenant_identifier).strip()
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        if ident_str.isdigit():
            async with db.execute(
                "SELECT * FROM tenant_subscription WHERE id = ? OR tenant_slug = ?",
                (int(ident_str), ident_str.lower())
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
        else:
            async with db.execute(
                "SELECT * FROM tenant_subscription WHERE tenant_slug = ?",
                (ident_str.lower(),)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None


async def get_all_tenants(db_path: Optional[str] = None) -> List[Dict]:
    """Получение списка всех зарегистрированных заведений сети для пульта Super-Admin."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tenant_subscription ORDER BY id ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def create_tenant(data: Dict, db_path: Optional[str] = None) -> int:
    """Регистрация нового заведения/клиента в SaaS платформе."""
    target_db = get_db_path(db_path)
    days = int(data.get("days", 30))
    grace_period_days = int(data.get("grace_period_days", 2))
    now_dt = get_tashkent_now_naive()
    expires_at = (now_dt + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO tenant_subscription (
                tenant_slug, name, is_active, expires_at, grace_period_days,
                admin_chat_id, contact_phone, contact_name, last_notification_stage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["tenant_slug"].strip().lower(),
                data.get("name", data["tenant_slug"]),
                int(data.get("is_active", 1)),
                data.get("expires_at", expires_at),
                grace_period_days,
                data.get("admin_chat_id"),
                data.get("contact_phone", ""),
                data.get("contact_name", ""),
                "none",
            )
        )
        await db.commit()
        return cursor.lastrowid


async def update_tenant_subscription(tenant_id: int, updates: Dict, db_path: Optional[str] = None) -> bool:
    """Обновление параметров подписки салона."""
    allowed_keys = {
        "name", "is_active", "expires_at", "grace_period_days",
        "admin_chat_id", "contact_phone", "contact_name", "last_notification_stage", "tier"
    }
    filtered = {k: v for k, v in updates.items() if k in allowed_keys}
    if not filtered:
        return False

    set_clauses = [f"{k} = ?" for k in filtered.keys()]
    values = list(filtered.values())
    values.append(tenant_id)

    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        query = f"UPDATE tenant_subscription SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        cursor = await db.execute(query, tuple(values))
        await db.commit()
        return cursor.rowcount > 0


async def extend_tenant_subscription_30_days(tenant_identifier: Any, db_path: Optional[str] = None) -> Optional[Dict]:
    """
    Продление подписки заведения на 30 дней.
    Поддерживает tenant_identifier как числовой ID или строковый slug.
    Если срок еще не истек — добавляет 30 дней к текущей дате expires_at.
    Если срок уже истек или блокирован — добавляет 30 дней от текущего момента.
    Автоматически переводит is_active = 1 и сбрасывает статус уведомлений.
    """
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path)
    if not tenant:
        return None

    tenant_id = tenant["id"]
    now_dt = get_tashkent_now_naive()
    try:
        current_exp = datetime.strptime(tenant["expires_at"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        current_exp = now_dt

    base_dt = current_exp if current_exp > now_dt else now_dt
    new_exp = (base_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    await update_tenant_subscription(
        tenant_id,
        {
            "expires_at": new_exp,
            "is_active": 1,
            "last_notification_stage": "none",
        },
        db_path=db_path
    )
    return await get_tenant_by_id(tenant_id, db_path)


async def toggle_tenant_active(tenant_identifier: Any, is_active: Optional[int] = None, db_path: Optional[str] = None) -> bool:
    """Переключение ручного выключателя Kill Switch для арендатора (по id или slug)."""
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path)
    if not tenant:
        return False

    tenant_id = tenant["id"]
    if is_active is None:
        new_active = 0 if tenant.get("is_active", 1) == 1 else 1
    else:
        new_active = 1 if is_active else 0

    return await update_tenant_subscription(tenant_id, {"is_active": new_active}, db_path=db_path)


async def set_tenant_tier(tenant_identifier: Any, tier: str, db_path: Optional[str] = None) -> bool:
    """Установка тарифа заведения ('START', 'PRO', 'ENTERPRISE')."""
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path)
    if not tenant:
        return False
    return await update_tenant_subscription(tenant["id"], {"tier": tier.strip().upper()}, db_path=db_path)



# =============================================================================
# НАСТРОЙКИ САЛОНА (SALON SETTINGS)
# =============================================================================

async def get_salon_setting(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    """Получение значения настройки салона по ключу."""
    target_db = get_db_path(db_path)
    try:
        async with aiosqlite.connect(target_db) as db:
            async with db.execute("SELECT value FROM salon_settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default
    except Exception:
        return default


async def get_all_salon_settings(db_path: Optional[str] = None) -> Dict[str, str]:
    """Получение всех настроек салона единым словарем."""
    defaults = {
        "work_start_hour": str(settings.WORK_START_HOUR),
        "work_end_hour": str(settings.WORK_END_HOUR),
        "slot_step_minutes": str(settings.SLOT_STEP_MINUTES),
        "max_booking_days_ahead": str(settings.MAX_BOOKING_DAYS_AHEAD),
        "promo_discount_text": f"{settings.PROMO_DISCOUNT_PERCENT}% OFF",
        "promo_subtitle": settings.PROMO_SUBTITLE,
        "salon_phone": settings.SALON_PHONE,
    }
    target_db = get_db_path(db_path)
    try:
        async with aiosqlite.connect(target_db) as db:
            async with db.execute("SELECT key, value FROM salon_settings") as cursor:
                rows = await cursor.fetchall()
                res = dict(defaults)
                res.update({row[0]: row[1] for row in rows})
                return res
    except Exception as e:
        logger.warning("Не удалось прочитать salon_settings из БД, используются значения по умолчанию: %s", e)
        return defaults


async def set_salon_setting(key: str, value: str, db_path: Optional[str] = None) -> bool:
    """Сохранение или обновление настройки салона."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute(
            """
            INSERT INTO salon_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, str(value))
        )
        await db.commit()
        return True


# =============================================================================
# УПРАВЛЕНИЕ КВИТАНЦИЯМИ И ЧЕКАМИ ОБ ОПЛАТЕ (RECEIPT OCR LOGS)
# =============================================================================

async def save_receipt_log(
    tenant_id: int,
    transaction_id: str,
    amount: int,
    payment_method: str = "p2p",
    sender_name: Optional[str] = None,
    recipient_card_last4: Optional[str] = None,
    raw_ocr_json: Optional[str] = None,
    status: str = "pending_verification",
    db_path: Optional[str] = None
) -> int:
    """Сохранение записи о распознанном чеке с защитой от повторного использования (Replay Attack)."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        if transaction_id:
            cursor = await db.execute("SELECT id FROM receipt_logs WHERE transaction_id = ?", (transaction_id,))
            existing = await cursor.fetchone()
            if existing:
                raise ValueError(f"Чек с номером транзакции #{transaction_id} уже был использован ранее!")

        cursor = await db.execute(
            """
            INSERT INTO receipt_logs (
                tenant_id, transaction_id, amount, payment_method, sender_name,
                recipient_card_last4, raw_ocr_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, transaction_id, amount, payment_method, sender_name,
             recipient_card_last4, raw_ocr_json, status)
        )
        await db.commit()
        return cursor.lastrowid


async def get_receipt_log_by_id(receipt_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """Получение квитанции по ID."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM receipt_logs WHERE id = ?", (receipt_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_receipt_log_status(receipt_id: int, new_status: str, db_path: Optional[str] = None) -> bool:
    """Обновление статуса квитанции ('approved', 'rejected', 'pending_verification')."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute("UPDATE receipt_logs SET status = ? WHERE id = ?", (new_status, receipt_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_pending_receipts(db_path: Optional[str] = None) -> List[Dict]:
    """Получение списка чеков, ожидающих верификации администратором."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM receipt_logs WHERE status = 'pending_verification' ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


