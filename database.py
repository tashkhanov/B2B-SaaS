"""
Модуль асинхронного взаимодействия с PostgreSQL 18 через asyncpg.
Включает:
- Пул соединений (Connection Pool) с поддержкой боевой (b2b_saas) и тестовой (b2b_saas_test) баз данных.
- Полный CRUD для мастеров, услуг, филиалов, бронирований, подписок и квитанций.
- Защиту от состояния гонки и овербукинга (BookingConflictError).
- Автоматический сидинг начальных данных при инициализации.
"""

import logging
import asyncio
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Any
import asyncpg
from config import settings

logger = logging.getLogger(__name__)


class BookingConflictError(Exception):
    """Исключение при попытке записаться на уже занятый слот (состояние гонки / овербукинг)."""
    pass


def serialize_record(record: Any) -> Dict[str, Any]:
    """Преобразует asyncpg.Record в словарь с безопасной сериализацией дат и времени в строки."""
    d = dict(record)
    for k, v in d.items():
        if isinstance(v, (datetime, date)):
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    return d


def get_tashkent_now_naive() -> datetime:
    """Текущее время в часовом поясе Asia/Tashkent (UTC+5) без tzinfo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tashkent")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def get_db_path(db_path: Optional[str] = None) -> str:
    """Динамическое определение целевой базы данных PostgreSQL."""
    if db_path and ("test" in db_path or db_path.endswith(".db") or db_path.endswith(".sqlite3")):
        return settings.PG_TEST_NAME or "b2b_saas_test"
    if settings.DB_NAME and ("test" in settings.DB_NAME or settings.DB_NAME.endswith(".sqlite3")):
        return settings.PG_TEST_NAME or "b2b_saas_test"
    return db_path or settings.PG_NAME


# Глобальный реестр пулов соединений по имени базы
_pools: Dict[str, asyncpg.Pool] = {}
_pool_lock = asyncio.Lock()


async def get_pool(db_path: Optional[str] = None) -> asyncpg.Pool:
    """Получение или создание пула соединений к указанной базе PostgreSQL с привязкой к текущему event loop."""
    target_db = get_db_path(db_path)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if target_db in _pools:
        p = _pools[target_db]
        if not p._closed and (current_loop is None or p._loop is current_loop):
            return p
        _pools.pop(target_db, None)

    async with _pool_lock:
        if target_db in _pools:
            p = _pools[target_db]
            if not p._closed and (current_loop is None or p._loop is current_loop):
                return p
            _pools.pop(target_db, None)

        pool = await asyncpg.create_pool(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            database=target_db,
            min_size=1,
            max_size=20,
            command_timeout=60,
        )
        _pools[target_db] = pool
        return pool


async def close_db() -> None:
    """Корректное закрытие всех открытых пулов соединений."""
    global _pools
    async with _pool_lock:
        for name, pool in list(_pools.items()):
            try:
                await pool.close()
                logger.info("Пул соединений PostgreSQL для '%s' успешно закрыт.", name)
            except Exception as e:
                logger.warning("Ошибка при закрытии пула '%s': %s", name, e)
        _pools.clear()


async def truncate_all_tables(db_path: Optional[str] = None) -> None:
    """Быстрая очистка всех таблиц в тестовой базе (используется фикстурами pytest)."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE TABLE 
                receipt_logs, bookings, messages, salon_settings, 
                barbers, tenant_subscription, services, branches 
            RESTART IDENTITY CASCADE;
        """)


# =============================================================================
# БАЗОВЫЕ SQL ХЕЛПЕРЫ
# =============================================================================

async def fetch_all(query: str, *args, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Выполнение SELECT запроса с возвратом списка словарей."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        records = await conn.fetch(query, *args)
        return [serialize_record(r) for r in records]


async def fetch_one(query: str, *args, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Выполнение SELECT запроса с возвратом одного словаря или None."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        record = await conn.fetchrow(query, *args)
        return serialize_record(record) if record else None


async def fetch_val(query: str, *args, db_path: Optional[str] = None) -> Any:
    """Выполнение SELECT запроса с возвратом скалярного значения (например count(*) или RETURNING id)."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args, db_path: Optional[str] = None) -> str:
    """Выполнение INSERT/UPDATE/DELETE запроса."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


# =============================================================================
# ИНИЦИАЛИЗАЦИЯ И СИДИНГ (DDL)
# =============================================================================

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        role VARCHAR(32) NOT NULL,
        text TEXT NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages (user_id);
    """,

    """
    CREATE TABLE IF NOT EXISTS branches (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        address TEXT NOT NULL,
        landmark TEXT,
        phone VARCHAR(64),
        is_active SMALLINT DEFAULT 1,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS services (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        price INTEGER NOT NULL,
        duration_min INTEGER DEFAULT 60,
        description TEXT,
        badge VARCHAR(64) DEFAULT '',
        category VARCHAR(64) DEFAULT 'hair',
        is_active SMALLINT DEFAULT 1,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS barbers (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        role_title VARCHAR(128) NOT NULL DEFAULT 'Барбер',
        phone VARCHAR(64),
        telegram_id BIGINT,
        work_days VARCHAR(64) DEFAULT '0,1,2,3,4,5,6',
        is_working_today SMALLINT DEFAULT 1,
        is_active SMALLINT DEFAULT 1,
        is_vip SMALLINT DEFAULT 0,
        branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS tenant_subscription (
        id SERIAL PRIMARY KEY,
        tenant_slug VARCHAR(128) UNIQUE NOT NULL,
        name VARCHAR(255) NOT NULL,
        is_active SMALLINT DEFAULT 1,
        expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        grace_period_days INTEGER DEFAULT 2,
        admin_chat_id BIGINT,
        contact_phone VARCHAR(64),
        contact_name VARCHAR(255),
        last_notification_stage VARCHAR(32) DEFAULT 'none',
        tier VARCHAR(32) DEFAULT 'START',
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username VARCHAR(255),
        full_name VARCHAR(255) DEFAULT '',
        details TEXT,
        phone VARCHAR(64),
        service VARCHAR(255) DEFAULT 'Стрижка',
        booking_time VARCHAR(64),
        status VARCHAR(32) DEFAULT 'new',
        barber_id INTEGER REFERENCES barbers(id) ON DELETE SET NULL,
        barber_name VARCHAR(255),
        service_id INTEGER,
        branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL,
        branch_name VARCHAR(255),
        reminder_2h_sent SMALLINT DEFAULT 0,
        client_confirmed SMALLINT DEFAULT 0,
        winback_sent SMALLINT DEFAULT 0,
        approval_message_ids TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_user_id ON bookings (user_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_time ON bookings (booking_time);
    CREATE INDEX IF NOT EXISTS idx_bookings_barber ON bookings (barber_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_branch ON bookings (branch_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_booking 
    ON bookings (barber_id, booking_time) 
    WHERE status NOT IN ('cancelled') AND barber_id IS NOT NULL AND booking_time IS NOT NULL;
    """,

    """
    CREATE TABLE IF NOT EXISTS salon_settings (
        key VARCHAR(128) PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS receipt_logs (
        id SERIAL PRIMARY KEY,
        tenant_id INTEGER REFERENCES tenant_subscription(id) ON DELETE CASCADE,
        transaction_id VARCHAR(128) UNIQUE,
        amount INTEGER NOT NULL,
        payment_method VARCHAR(64) DEFAULT 'p2p',
        sender_name VARCHAR(255),
        recipient_card_last4 VARCHAR(8),
        raw_ocr_json TEXT,
        status VARCHAR(64) DEFAULT 'pending_verification',
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """
]


async def init_db(db_path: Optional[str] = None) -> None:
    """Инициализация таблиц базы данных и начального сидинга."""
    target_db = get_db_path(db_path)
    if target_db == (settings.PG_TEST_NAME or "b2b_saas_test"):
        try:
            await truncate_all_tables(target_db)
        except Exception:
            pass

    pool = await get_pool(target_db)
    async with pool.acquire() as conn:
        for ddl in DDL_STATEMENTS:
            await conn.execute(ddl)

        # Автоматическая миграция схемы для существующих баз данных
        try:
            await conn.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS approval_message_ids TEXT;")
        except Exception as e:
            logger.debug(f"Migrate approval_message_ids notice: {e}")

        # Проверка и создание начальных филиалов
        branches_count = await conn.fetchval("SELECT count(*) FROM branches;")
        if branches_count == 0:
            for b in settings.get_initial_branches():
                await conn.execute("""
                    INSERT INTO branches (id, name, address, landmark, phone, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO NOTHING;
                """, b.get("id"), b.get("name"), b.get("address"), b.get("landmark", ""),
                     b.get("phone", settings.SALON_PHONE), b.get("is_active", 1))
            await conn.execute("SELECT setval(pg_get_serial_sequence('branches', 'id'), coalesce((SELECT max(id) FROM branches), 1));")

        # Проверка и создание начальных услуг
        services_count = await conn.fetchval("SELECT count(*) FROM services;")
        if services_count == 0:
            for s in settings.get_initial_services():
                await conn.execute("""
                    INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, s.get("name"), s.get("price"), s.get("duration_min", 45),
                     s.get("description", ""), s.get("badge", ""), s.get("category", "hair"), s.get("is_active", 1))

        # Проверка и создание начальных мастеров
        barbers_count = await conn.fetchval("SELECT count(*) FROM barbers;")
        if barbers_count == 0:
            for b in settings.get_initial_barbers():
                await conn.execute("""
                    INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip, branch_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """, b.get("name"), b.get("role_title", "Стилист"), b.get("phone", settings.SALON_PHONE),
                     b.get("telegram_id"), b.get("work_days", "0,1,2,3,4,5,6"), b.get("is_working_today", 1),
                     b.get("is_active", 1), b.get("is_vip", 0), b.get("branch_id"))

        # Проверка и создание дефолтной подписки Brohouse
        tenants_count = await conn.fetchval("SELECT count(*) FROM tenant_subscription;")
        if tenants_count == 0:
            now_dt = get_tashkent_now_naive()
            default_expires_at = now_dt + timedelta(days=30)
            await conn.execute("""
                INSERT INTO tenant_subscription (
                    tenant_slug, name, is_active, expires_at, grace_period_days,
                    admin_chat_id, contact_phone, contact_name, last_notification_stage, tier
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (tenant_slug) DO NOTHING;
            """, settings.TENANT_SLUG, settings.TENANT_NAME, 1, default_expires_at, 2,
                 settings.ADMIN_CHAT_ID, settings.SALON_PHONE, settings.OWNER_NAME, "none", "PRO")

        # Дефолтные настройки салона
        default_settings = [
            ("work_start_hour", str(settings.WORK_START_HOUR)),
            ("work_end_hour", str(settings.WORK_END_HOUR)),
            ("slot_step_minutes", str(settings.SLOT_STEP_MINUTES)),
            ("max_booking_days_ahead", str(settings.MAX_BOOKING_DAYS_AHEAD)),
            ("promo_discount_text", f"{settings.PROMO_DISCOUNT_PERCENT}% OFF"),
            ("promo_subtitle", settings.PROMO_SUBTITLE),
            ("salon_phone", settings.SALON_PHONE),
        ]
        for k, v in default_settings:
            await conn.execute("""
                INSERT INTO salon_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING;
            """, k, v)


async def reseed_brohouse_data(db_path: Optional[str] = None) -> None:
    """Полная очистка и переинициализация базы актуальными данными салона из конфигурации settings."""
    pool = await get_pool(db_path)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM services;")
        await conn.execute("DELETE FROM barbers;")
        await conn.execute("DELETE FROM branches;")
        await conn.execute("DELETE FROM bookings;")
        await conn.execute("DELETE FROM messages;")

        for b in settings.get_initial_branches():
            await conn.execute("""
                INSERT INTO branches (id, name, address, landmark, phone, is_active)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO NOTHING;
            """, b.get("id"), b.get("name"), b.get("address"), b.get("landmark", ""),
                 b.get("phone", settings.SALON_PHONE), b.get("is_active", 1))

        for s in settings.get_initial_services():
            await conn.execute("""
                INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, s.get("name"), s.get("price"), s.get("duration_min", 45),
                 s.get("description", ""), s.get("badge", ""), s.get("category", "hair"), s.get("is_active", 1))

        for b in settings.get_initial_barbers():
            await conn.execute("""
                INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip, branch_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """, b.get("name"), b.get("role_title", "Стилист"), b.get("phone", settings.SALON_PHONE),
                 b.get("telegram_id"), b.get("work_days", "0,1,2,3,4,5,6"), b.get("is_working_today", 1),
                 b.get("is_active", 1), b.get("is_vip", 0), b.get("branch_id"))


reseed_tenant_data = reseed_brohouse_data


# =============================================================================
# ИСТОРИЯ ДИАЛОГОВ (MESSAGES)
# =============================================================================

async def add_message(user_id: int, role: str, text: str, db_path: Optional[str] = None) -> None:
    """Сохранение реплики пользователя ('user') или ассистента ('model') в историю."""
    await execute(
        "INSERT INTO messages (user_id, role, text) VALUES ($1, $2, $3);",
        user_id, role, text, db_path=db_path
    )


async def get_recent_messages(user_id: int, limit: int = 10, db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Получение последних N сообщений пользователя в хронологическом порядке."""
    rows = await fetch_all(
        "SELECT role, text FROM messages WHERE user_id = $1 ORDER BY id DESC LIMIT $2;",
        user_id, limit, db_path=db_path
    )
    return list(reversed(rows))


async def clear_history(user_id: int, db_path: Optional[str] = None) -> None:
    """Удаление всей истории диалога конкретного пользователя."""
    await execute("DELETE FROM messages WHERE user_id = $1;", user_id, db_path=db_path)


# =============================================================================
# ФИЛИАЛЫ (BRANCHES)
# =============================================================================

async def get_branches(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение списка филиалов барбершопа."""
    query = "SELECT * FROM branches"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY id ASC;"
    return await fetch_all(query, db_path=db_path)


async def get_branch_by_id(branch_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение информации о филиале по ID."""
    return await fetch_one("SELECT * FROM branches WHERE id = $1;", branch_id, db_path=db_path)


async def create_branch(
    name: str,
    address: str,
    landmark: Optional[str] = None,
    phone: Optional[str] = None,
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание нового филиала."""
    return await fetch_val(
        """
        INSERT INTO branches (name, address, landmark, phone, is_active)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id;
        """,
        name, address, landmark, phone, is_active, db_path=db_path
    )


# =============================================================================
# КАТАЛОГ УСЛУГ (SERVICES CRUD)
# =============================================================================

async def get_services(active_only: bool = True, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение списка всех услуг."""
    query = "SELECT * FROM services"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY id ASC;"
    return await fetch_all(query, db_path=db_path)


async def get_service_by_id(service_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение одной услуги по ID."""
    return await fetch_one("SELECT * FROM services WHERE id = $1;", service_id, db_path=db_path)


async def create_service(
    name: str,
    price: int,
    duration_min: int = 60,
    description: Optional[str] = None,
    badge: Optional[str] = "",
    category: str = "hair",
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание новой услуги."""
    return await fetch_val(
        """
        INSERT INTO services (name, price, duration_min, description, badge, category, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id;
        """,
        name, price, duration_min, description, badge, category, is_active, db_path=db_path
    )


async def update_service(
    service_id: int,
    name: Optional[str] = None,
    price: Optional[int] = None,
    duration_min: Optional[int] = None,
    description: Optional[str] = None,
    badge: Optional[str] = None,
    category: Optional[str] = None,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> bool:
    """Обновление услуги по ID."""
    fields = []
    params = []
    idx = 1

    for col, val in [
        ("name", name), ("price", price), ("duration_min", duration_min),
        ("description", description), ("badge", badge), ("category", category),
        ("is_active", is_active)
    ]:
        if val is not None:
            fields.append(f"{col} = ${idx}")
            params.append(val)
            idx += 1

    if not fields:
        return False

    params.append(service_id)
    query = f"UPDATE services SET {', '.join(fields)} WHERE id = ${idx};"
    res = await execute(query, *params, db_path=db_path)
    return "UPDATE 1" in res


async def delete_service(service_id: int, hard: bool = False, db_path: Optional[str] = None) -> bool:
    """Удаление услуги (мягкое по умолчанию)."""
    if hard:
        res = await execute("DELETE FROM services WHERE id = $1;", service_id, db_path=db_path)
    else:
        res = await execute("UPDATE services SET is_active = 0 WHERE id = $1;", service_id, db_path=db_path)
    return "UPDATE 1" in res or "DELETE 1" in res


# =============================================================================
# БАРБЕРЫ / МАСТЕРА (BARBERS CRUD)
# =============================================================================

async def get_barbers(
    active_only: bool = True,
    branch_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Получение списка мастеров с названием филиала.
    Если передан branch_id: возвращаются мастера этого филиала + мастера, работающие во всех филиалах (branch_id IS NULL).
    """
    query = """
        SELECT b.*, br.name AS branch_name
        FROM barbers b
        LEFT JOIN branches br ON b.branch_id = br.id
        WHERE 1=1
    """
    params = []
    idx = 1

    if active_only:
        query += " AND b.is_active = 1"

    if branch_id is not None:
        query += f" AND (b.branch_id = ${idx} OR b.branch_id IS NULL)"
        params.append(branch_id)
        idx += 1

    query += " ORDER BY b.id ASC;"
    return await fetch_all(query, *params, db_path=db_path)


async def get_barber_by_id(barber_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение информации о мастере по его ID с названием филиала."""
    return await fetch_one(
        """
        SELECT b.*, br.name AS branch_name
        FROM barbers b
        LEFT JOIN branches br ON b.branch_id = br.id
        WHERE b.id = $1;
        """,
        barber_id, db_path=db_path
    )


async def get_barber_by_telegram_id(telegram_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение информации о мастере по его telegram_id."""
    return await fetch_one(
        """
        SELECT b.*, br.name AS branch_name
        FROM barbers b
        LEFT JOIN branches br ON b.branch_id = br.id
        WHERE b.telegram_id = $1;
        """,
        telegram_id, db_path=db_path
    )


async def set_barber_vip_status(barber_id: int, is_vip: int, db_path: Optional[str] = None) -> bool:
    """Установка VIP-статуса для мастера."""
    res = await execute("UPDATE barbers SET is_vip = $1 WHERE id = $2;", is_vip, barber_id, db_path=db_path)
    return "UPDATE 1" in res


async def create_barber(
    name: str,
    role_title: str = "Барбер",
    phone: Optional[str] = None,
    telegram_id: Optional[int] = None,
    work_days: str = "0,1,2,3,4,5,6",
    is_working_today: int = 1,
    is_active: int = 1,
    is_vip: int = 0,
    branch_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> int:
    """Создание нового барбера."""
    return await fetch_val(
        """
        INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip, branch_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id;
        """,
        name, role_title, phone, telegram_id, work_days, is_working_today, is_active, is_vip, branch_id, db_path=db_path
    )


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
    branch_id: Optional[int] = None,
    update_branch: bool = True,
    db_path: Optional[str] = None
) -> bool:
    """Обновление мастера по ID."""
    fields = [
        "name = $1", "role_title = $2", "phone = $3", "telegram_id = $4",
        "work_days = $5", "is_working_today = $6", "is_active = $7"
    ]
    params = [name.strip(), role_title.strip(), phone, telegram_id, work_days, is_working_today, is_active]
    idx = 8

    if is_vip is not None:
        fields.append(f"is_vip = ${idx}")
        params.append(is_vip)
        idx += 1

    if update_branch:
        fields.append(f"branch_id = ${idx}")
        params.append(branch_id)
        idx += 1

    params.append(barber_id)
    query = f"UPDATE barbers SET {', '.join(fields)} WHERE id = ${idx};"
    res = await execute(query, *params, db_path=db_path)
    return "UPDATE 1" in res


async def toggle_barber_shift(
    barber_id: int,
    is_working_today: Optional[int] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Переключение статуса смены барбера ('На смене' / 'Выходной')."""
    barber = await get_barber_by_id(barber_id, db_path=db_path)
    if not barber:
        return None

    new_status = (1 if barber.get("is_working_today", 1) == 0 else 0) if is_working_today is None else is_working_today
    await execute("UPDATE barbers SET is_working_today = $1 WHERE id = $2;", new_status, barber_id, db_path=db_path)
    return await get_barber_by_id(barber_id, db_path=db_path)


async def delete_barber(barber_id: int, hard: bool = False, db_path: Optional[str] = None) -> bool:
    """Удаление барбера (мягкое по умолчанию)."""
    if hard:
        res = await execute("DELETE FROM barbers WHERE id = $1;", barber_id, db_path=db_path)
    else:
        res = await execute("UPDATE barbers SET is_active = 0 WHERE id = $1;", barber_id, db_path=db_path)
    return "UPDATE 1" in res or "DELETE 1" in res


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
    """Сохранение новой записи клиента с обработкой конфликтов."""
    if not branch_name:
        b_info = await get_branch_by_id(branch_id, db_path=db_path)
        branch_name = b_info["name"] if b_info else f"Филиал #{branch_id}"

    try:
        booking_id = await fetch_val(
            """
            INSERT INTO bookings (
                user_id, username, full_name, phone, service, booking_time, status, details,
                barber_id, barber_name, service_id, branch_id, branch_name
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id;
            """,
            user_id, username, full_name, phone, service, booking_time, status, details,
            barber_id, barber_name, service_id, branch_id, branch_name, db_path=db_path
        )
        return booking_id
    except (asyncpg.IntegrityConstraintViolationError, asyncpg.UniqueViolationError) as err:
        logger.warning("Collision detected while saving booking: %s", err)
        raise BookingConflictError("Выбранный слот уже занят другим клиентом. Пожалуйста, выберите другое время.") from err


async def has_recent_booking(
    user_id: int,
    booking_time: str,
    db_path: Optional[str] = None
) -> bool:
    """Проверяет, создавал ли пользователь запись на это же время за последние 15 минут."""
    row = await fetch_one(
        """
        SELECT id FROM bookings 
        WHERE user_id = $1 AND booking_time = $2 
          AND created_at >= NOW() - INTERVAL '15 minutes'
        LIMIT 1;
        """,
        user_id, booking_time, db_path=db_path
    )
    return row is not None


async def has_recent_booking_any_time(
    user_id: int,
    seconds: int = 60,
    db_path: Optional[str] = None
) -> bool:
    """Проверяет, создавал ли пользователь запись за последние N секунд (защита от Tool Calling дублирования)."""
    if not user_id:
        return False
    row = await fetch_one(
        """
        SELECT id FROM bookings 
        WHERE user_id = $1 
          AND created_at >= NOW() - ($2 || ' seconds')::INTERVAL
        LIMIT 1;
        """,
        user_id, str(seconds), db_path=db_path
    )
    return row is not None


async def auto_complete_past_bookings(db_path: Optional[str] = None) -> int:
    """Автоматически переводит все брони со статусом 'new' или 'confirmed' в 'completed', если время процедуры прошло."""
    from services.schedule_service import is_booking_past

    rows = await fetch_all(
        "SELECT id, booking_time FROM bookings WHERE status IN ('new', 'confirmed');",
        db_path=db_path
    )
    expired_ids = [r["id"] for r in rows if r.get("booking_time") and is_booking_past(r["booking_time"])]

    if expired_ids:
        res = await execute(
            "UPDATE bookings SET status = 'completed' WHERE id = ANY($1::int[]);",
            expired_ids, db_path=db_path
        )
        try:
            return int(res.split(" ")[-1])
        except Exception:
            return len(expired_ids)
    return 0


async def get_all_bookings(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение всех записей клиентов для админки (с авто-завершением)."""
    await auto_complete_past_bookings(db_path)
    return await fetch_all(
        """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, created_at,
               reminder_2h_sent, client_confirmed, winback_sent
        FROM bookings
        ORDER BY id DESC;
        """,
        db_path=db_path
    )


async def get_active_bookings(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение только активных записей ('new', 'confirmed') для валидатора слотов."""
    await auto_complete_past_bookings(db_path)
    return await fetch_all(
        """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, created_at
        FROM bookings
        WHERE status IN ('new', 'confirmed')
        ORDER BY id DESC;
        """,
        db_path=db_path
    )


async def get_bookings_for_barber(
    barber_id: int,
    target_date: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Получение записей конкретного мастера для его личного кабинета (/barber)."""
    await auto_complete_past_bookings(db_path)
    query = """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, created_at
        FROM bookings
        WHERE (barber_id = $1 OR barber_id IS NULL)
    """
    params = [barber_id]
    if target_date:
        query += " AND booking_time LIKE $2"
        params.append(f"{target_date}%")

    query += " ORDER BY id DESC;"
    return await fetch_all(query, *params, db_path=db_path)


async def update_booking_status(booking_id: int, status: str, db_path: Optional[str] = None) -> bool:
    """Обновление статуса записи (new -> confirmed -> cancelled -> completed)."""
    res = await execute("UPDATE bookings SET status = $1 WHERE id = $2;", status, booking_id, db_path=db_path)
    return "UPDATE 1" in res


async def get_booking_by_id(booking_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение конкретной записи по её ID."""
    return await fetch_one(
        """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, approval_message_ids, created_at
        FROM bookings 
        WHERE id = $1;
        """,
        booking_id, db_path=db_path
    )


async def get_user_active_booking(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение текущей активной записи пользователя для отмены или подтверждения через бота."""
    await auto_complete_past_bookings(db_path)
    return await fetch_one(
        """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, approval_message_ids, created_at
        FROM bookings 
        WHERE user_id = $1 AND status IN ('new', 'confirmed', 'pending_vip')
        ORDER BY id DESC LIMIT 1;
        """,
        user_id, db_path=db_path
    )


async def save_booking_approval_messages(
    booking_id: int,
    messages: List[Dict[str, int]],
    db_path: Optional[str] = None
) -> bool:
    """Сохраняет список {chat_id, message_id} отправленных карточек согласования в виде JSON строки."""
    import json
    raw_json = json.dumps(messages)
    res = await execute(
        "UPDATE bookings SET approval_message_ids = $1 WHERE id = $2;",
        raw_json, booking_id, db_path=db_path
    )
    return "UPDATE 1" in res


async def get_booking_approval_messages(
    booking_id: int,
    db_path: Optional[str] = None
) -> List[Dict[str, int]]:
    """Получает список {chat_id, message_id} для отзыва отправленных карточек согласования."""
    import json
    booking = await get_booking_by_id(booking_id, db_path=db_path)
    if not booking or not booking.get("approval_message_ids"):
        return []
    try:
        data = json.loads(booking["approval_message_ids"])
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def cancel_user_booking(
    user_id: int,
    booking_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Отмена активной брони пользователя."""
    booking = await get_booking_by_id(booking_id, db_path=db_path) if booking_id else await get_user_active_booking(user_id, db_path=db_path)
    if not booking:
        return None

    await execute("UPDATE bookings SET status = 'cancelled' WHERE id = $1;", booking["id"], db_path=db_path)
    booking["status"] = "cancelled"
    return booking


async def get_bookings_due_for_2h_reminder(
    window_start_minutes: int = 105,
    window_end_minutes: int = 135,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Выборка активных записей, до начала которых осталось примерно 2 часа."""
    from services.schedule_service import parse_booking_datetime, get_now_tashkent

    now = get_now_tashkent()
    rows = await fetch_all(
        """
        SELECT * FROM bookings 
        WHERE status IN ('new', 'confirmed') 
          AND (reminder_2h_sent IS NULL OR reminder_2h_sent = 0)
          AND booking_time IS NOT NULL AND booking_time != ''
        ORDER BY id ASC;
        """,
        db_path=db_path
    )

    due_bookings = []
    for booking in rows:
        raw_time = booking.get("booking_time") or ""
        dt = parse_booking_datetime(raw_time)
        if dt:
            diff_min = (dt - now).total_seconds() / 60.0
            if window_start_minutes <= diff_min <= window_end_minutes:
                due_bookings.append(booking)
    return due_bookings


async def mark_booking_reminder_sent(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отметка об отправке 2-часового пуша клиенту."""
    res = await execute("UPDATE bookings SET reminder_2h_sent = 1 WHERE id = $1;", booking_id, db_path=db_path)
    return "UPDATE 1" in res


async def confirm_booking_by_client(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Подтверждение явки клиентом по кнопке «Буду вовремя ✅»."""
    res = await execute("UPDATE bookings SET client_confirmed = 1, status = 'confirmed' WHERE id = $1;", booking_id, db_path=db_path)
    return "UPDATE 1" in res


async def cancel_booking_by_client(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отмена записи клиентом по кнопке «Не смогу прийти ❌»."""
    res = await execute("UPDATE bookings SET status = 'cancelled' WHERE id = $1;", booking_id, db_path=db_path)
    return "UPDATE 1" in res


async def get_clients_due_for_winback(
    days_threshold: int = 25,
    db_path: Optional[str] = None,
    days_ago: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Выборка клиентов для повторного вовлечения (Win-Back)."""
    from services.schedule_service import parse_booking_datetime, get_now_tashkent, TASHKENT_TZ

    effective_threshold = days_ago if days_ago is not None else days_threshold
    now = get_now_tashkent()

    rows = await fetch_all(
        """
        SELECT * FROM bookings
        WHERE status = 'completed'
          AND (winback_sent IS NULL OR winback_sent = 0)
          AND user_id IS NOT NULL AND user_id > 0
        ORDER BY id DESC;
        """,
        db_path=db_path
    )

    due_clients = []
    seen_users = set()

    for booking in rows:
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
            has_active = await fetch_one(
                "SELECT id FROM bookings WHERE user_id = $1 AND status IN ('new', 'confirmed', 'pending_vip') LIMIT 1;",
                uid, db_path=db_path
            )
            if not has_active:
                seen_users.add(uid)
                due_clients.append(booking)

    return due_clients


async def mark_client_winback_sent(booking_id: int, db_path: Optional[str] = None) -> bool:
    """Отметка об отправке winback-пуша клиенту."""
    res = await execute("UPDATE bookings SET winback_sent = 1 WHERE id = $1 OR user_id = $1;", booking_id, db_path=db_path)
    return "UPDATE" in res


async def find_booking_by_client_name(query: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Поиск активной брони клиента по имени, @username, номеру телефона, времени или ID."""
    await auto_complete_past_bookings(db_path)
    clean_query = query.strip().replace("@", "").lower()
    
    # Сначала проверяем точный ID, если передано число
    if clean_query.isdigit():
        by_id = await fetch_one(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
                   barber_id, barber_name, service_id, branch_id, branch_name, created_at
            FROM bookings 
            WHERE status IN ('new', 'confirmed', 'pending_vip') AND id = $1
            LIMIT 1;
            """,
            int(clean_query), db_path=db_path
        )
        if by_id:
            return by_id

    return await fetch_one(
        """
        SELECT id, user_id, username, full_name, phone, service, booking_time, status, details,
               barber_id, barber_name, service_id, branch_id, branch_name, created_at
        FROM bookings 
        WHERE status IN ('new', 'confirmed', 'pending_vip')
          AND (
            LOWER(full_name) LIKE $1 
            OR LOWER(username) LIKE $1 
            OR phone LIKE $1 
            OR booking_time LIKE $1
            OR LOWER(barber_name) LIKE $1
          )
        ORDER BY id DESC LIMIT 1;
        """,
        f"%{clean_query}%", db_path=db_path
    )


# =============================================================================
# УПРАВЛЕНИЕ ПОДПИСКАМИ САЛОНОВ (BILLING / MULTI-TENANT / KILL SWITCH)
# =============================================================================

async def get_tenant_subscription(tenant_slug: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение подписки салона по слагу (например, 'brohouse')."""
    return await fetch_one("SELECT * FROM tenant_subscription WHERE tenant_slug = $1;", tenant_slug, db_path=db_path)


async def get_tenant_by_id(tenant_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение подписки салона по ID."""
    return await fetch_one("SELECT * FROM tenant_subscription WHERE id = $1;", tenant_id, db_path=db_path)


async def get_tenant_by_id_or_slug(tenant_identifier: Any, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение подписки салона по ID или текстовому слагу."""
    if tenant_identifier is None:
        return None
    ident_str = str(tenant_identifier).strip()
    if ident_str.isdigit():
        return await fetch_one(
            "SELECT * FROM tenant_subscription WHERE id = $1 OR tenant_slug = $2;",
            int(ident_str), ident_str.lower(), db_path=db_path
        )
    return await fetch_one(
        "SELECT * FROM tenant_subscription WHERE tenant_slug = $1;",
        ident_str.lower(), db_path=db_path
    )


async def get_all_tenants(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение списка всех зарегистрированных заведений для пульта Super-Admin."""
    return await fetch_all("SELECT * FROM tenant_subscription ORDER BY id ASC;", db_path=db_path)


async def create_tenant(data: Dict[str, Any], db_path: Optional[str] = None) -> int:
    """Регистрация нового заведения/клиента в SaaS платформе."""
    days = int(data.get("days", 30))
    grace_period_days = int(data.get("grace_period_days", 2))
    now_dt = get_tashkent_now_naive()
    expires_at = now_dt + timedelta(days=days)

    raw_exp = data.get("expires_at", expires_at)
    if isinstance(raw_exp, str):
        try:
            raw_exp = datetime.fromisoformat(raw_exp.replace(" ", "T"))
        except Exception:
            pass

    return await fetch_val(
        """
        INSERT INTO tenant_subscription (
            tenant_slug, name, is_active, expires_at, grace_period_days,
            admin_chat_id, contact_phone, contact_name, last_notification_stage
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id;
        """,
        data["tenant_slug"].strip().lower(),
        data.get("name", data["tenant_slug"]),
        int(data.get("is_active", 1)),
        raw_exp,
        grace_period_days,
        data.get("admin_chat_id"),
        data.get("contact_phone", ""),
        data.get("contact_name", ""),
        "none",
        db_path=db_path
    )


async def update_tenant_subscription(tenant_id: int, updates: Dict[str, Any], db_path: Optional[str] = None) -> bool:
    """Обновление параметров подписки салона."""
    allowed_keys = {
        "name", "is_active", "expires_at", "grace_period_days",
        "admin_chat_id", "contact_phone", "contact_name", "last_notification_stage", "tier"
    }
    filtered = {k: v for k, v in updates.items() if k in allowed_keys}
    if not filtered:
        return False

    set_clauses = []
    values = []
    idx = 1
    for k, v in filtered.items():
        if k in ("expires_at", "created_at", "updated_at") and isinstance(v, str):
            try:
                v = datetime.fromisoformat(v.replace(" ", "T"))
            except Exception:
                pass
        set_clauses.append(f"{k} = ${idx}")
        values.append(v)
        idx += 1

    values.append(tenant_id)
    query = f"UPDATE tenant_subscription SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ${idx};"
    res = await execute(query, *values, db_path=db_path)
    return "UPDATE 1" in res


async def extend_tenant_subscription_30_days(tenant_identifier: Any, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Продление подписки заведения на 30 дней."""
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path)
    if not tenant:
        return None

    tenant_id = tenant["id"]
    now_dt = get_tashkent_now_naive()
    current_exp = tenant["expires_at"]
    if isinstance(current_exp, str):
        try:
            current_exp = datetime.strptime(current_exp, "%Y-%m-%d %H:%M:%S")
        except Exception:
            current_exp = now_dt

    base_dt = current_exp if current_exp > now_dt else now_dt
    new_exp = base_dt + timedelta(days=30)

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


async def toggle_tenant_active(
    tenant_identifier: Any,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> bool:
    """Переключение ручного выключателя Kill Switch для арендатора."""
    tenant = await get_tenant_by_id_or_slug(tenant_identifier, db_path)
    if not tenant:
        return False

    tenant_id = tenant["id"]
    new_active = (0 if tenant.get("is_active", 1) == 1 else 1) if is_active is None else (1 if is_active else 0)
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
    row = await fetch_one("SELECT value FROM salon_settings WHERE key = $1;", key, db_path=db_path)
    return row["value"] if row else default


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
    try:
        rows = await fetch_all("SELECT key, value FROM salon_settings;", db_path=db_path)
        res = dict(defaults)
        res.update({r["key"]: r["value"] for r in rows})
        return res
    except Exception as e:
        logger.warning("Не удалось прочитать salon_settings: %s", e)
        return defaults


async def set_salon_setting(key: str, value: str, db_path: Optional[str] = None) -> bool:
    """Сохранение или обновление настройки салона."""
    await execute(
        """
        INSERT INTO salon_settings (key, value, updated_at)
        VALUES ($1, $2, CURRENT_TIMESTAMP)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP;
        """,
        key, str(value), db_path=db_path
    )
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
    """Сохранение записи о распознанном чеке с защитой от повторного использования."""
    if transaction_id:
        existing = await fetch_one("SELECT id FROM receipt_logs WHERE transaction_id = $1;", transaction_id, db_path=db_path)
        if existing:
            raise ValueError(f"Чек с номером транзакции #{transaction_id} уже был использован ранее!")

    return await fetch_val(
        """
        INSERT INTO receipt_logs (
            tenant_id, transaction_id, amount, payment_method, sender_name,
            recipient_card_last4, raw_ocr_json, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id;
        """,
        tenant_id, transaction_id, amount, payment_method, sender_name,
        recipient_card_last4, raw_ocr_json, status, db_path=db_path
    )


async def get_receipt_log_by_id(receipt_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Получение квитанции по ID."""
    return await fetch_one("SELECT * FROM receipt_logs WHERE id = $1;", receipt_id, db_path=db_path)


async def update_receipt_log_status(receipt_id: int, new_status: str, db_path: Optional[str] = None) -> bool:
    """Обновление статуса квитанции ('approved', 'rejected', 'pending_verification')."""
    res = await execute("UPDATE receipt_logs SET status = $1 WHERE id = $2;", new_status, receipt_id, db_path=db_path)
    return "UPDATE 1" in res


async def get_pending_receipts(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получение списка чеков, ожидающих верификации администратором."""
    return await fetch_all("SELECT * FROM receipt_logs WHERE status = 'pending_verification' ORDER BY id DESC;", db_path=db_path)
