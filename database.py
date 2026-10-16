import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config import settings


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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 4. Таблица записей клиентов
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
        ]:
            if col_name not in columns:
                await db.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type} {col_def};")

        # Начальный seed для services, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM services")
        services_count = (await cursor.fetchone())[0]
        if services_count == 0:
            default_services = [
                ("Стрижка волос", 100000, 45, "Классическая мужская стрижка, мытье головы и премиальный стайлинг", "ТОП", 1),
                ("Стрижка под машинку", 50000, 30, "Стрижка одной или несколькими насадками с четкой окантовкой", "", 1),
                ("Стрижка на лысо", 30000, 20, "Гладкое бритье под ноль с освежающим уходом", "", 1),
                ("Детская стрижка от 5 лет", 60000, 40, "Стильная и аккуратная стрижка для юных джентльменов", "", 1),
                ("Укладка", 60000, 30, "Мытье головы и укладка премиум-стайлингом", "", 1),
                ("Мойка, массаж", 50000, 30, "Релакс-мытье головы и расслабляющий массаж", "РЕЛАКС", 1),
                ("Укладка, мойка, массаж", 80000, 45, "Полный СПА-уход: мытье, массаж головы и стильная укладка", "СПА СЕТ", 1),
                ("Бритье опасной бритвой", 70000, 35, "Традиционное королевское бритье с распариванием горячим полотенцем", "", 1),
                ("Оформление бороды", 50000, 30, "Моделирование контуров, стрижка бороды, горячий компресс и масло", "ПОПУЛЯРНО", 1),
                ("Покраска бороды", 30000, 30, "Камуфляж седины и тонирование бороды профессиональными составами", "", 1),
                ("Воск (одна из зон)", 20000, 15, "Удаление нежелательных волос воском (нос, уши или щеки)", "", 1),
                ("Черная маска", 30000, 20, "Глубокое очищение пор лица от черных точек и загрязнений", "УХОД", 1),
            ]
            await db.executemany(
                """
                INSERT INTO services (name, price, duration_min, description, badge, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                default_services
            )

        # Начальный seed для barbers, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM barbers")
        barbers_count = (await cursor.fetchone())[0]
        if barbers_count == 0:
            default_barbers = [
                ("Mr. Akmal", "Директор / Шеф-мастер", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
                ("Mr. Dilshod", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
                ("Ismailov Abbos", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
                ("Tursunov Nodir", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
            ]
            await db.executemany(
                """
                INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                default_barbers
            )

        # 5. Таблица подписок салонов (Billing / Multi-tenant SaaS / Kill Switch)
        await db.execute("""
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
        """)

        # Начальный seed для tenant_subscription, если таблица пуста
        cursor = await db.execute("SELECT COUNT(*) FROM tenant_subscription")
        tenants_count = (await cursor.fetchone())[0]
        if tenants_count == 0:
            now_dt = datetime.now()
            default_expires_at = (now_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                """
                INSERT INTO tenant_subscription (
                    tenant_slug, name, is_active, expires_at, grace_period_days,
                    admin_chat_id, contact_phone, contact_name, last_notification_stage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "brohouse",
                    "Brohouse Barbershop",
                    1,
                    default_expires_at,
                    2,
                    settings.ADMIN_CHAT_ID,
                    "+998 97 012-55-55",
                    "Brohouse Admin",
                    "none",
                )
            )

        await db.commit()


async def reseed_brohouse_data(db_path: Optional[str] = None) -> None:
    """Полная очистка и переинициализация базы актуальными данными салона Brohouse."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute("DELETE FROM services;")
        await db.execute("DELETE FROM barbers;")
        await db.execute("DELETE FROM bookings;")
        await db.execute("DELETE FROM messages;")

        default_services = [
            ("Стрижка волос", 100000, 45, "Классическая мужская стрижка, мытье головы и премиальный стайлинг", "ТОП", 1),
            ("Стрижка под машинку", 50000, 30, "Стрижка одной или несколькими насадками с четкой окантовкой", "", 1),
            ("Стрижка на лысо", 30000, 20, "Гладкое бритье под ноль с освежающим уходом", "", 1),
            ("Детская стрижка от 5 лет", 60000, 40, "Стильная и аккуратная стрижка для юных джентльменов", "", 1),
            ("Укладка", 60000, 30, "Мытье головы и укладка премиум-стайлингом", "", 1),
            ("Мойка, массаж", 50000, 30, "Релакс-мытье головы и расслабляющий массаж", "РЕЛАКС", 1),
            ("Укладка, мойка, массаж", 80000, 45, "Полный СПА-уход: мытье, массаж головы и стильная укладка", "СПА СЕТ", 1),
            ("Бритье опасной бритвой", 70000, 35, "Традиционное королевское бритье с распариванием горячим полотенцем", "", 1),
            ("Оформление бороды", 50000, 30, "Моделирование контуров, стрижка бороды, горячий компресс и масло", "ПОПУЛЯРНО", 1),
            ("Покраска бороды", 30000, 30, "Камуфляж седины и тонирование бороды профессиональными составами", "", 1),
            ("Воск (одна из зон)", 20000, 15, "Удаление нежелательных волос воском (нос, уши или щеки)", "", 1),
            ("Черная маска", 30000, 20, "Глубокое очищение пор лица от черных точек и загрязнений", "УХОД", 1),
        ]
        await db.executemany(
            """
            INSERT INTO services (name, price, duration_min, description, badge, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_services
        )

        default_barbers = [
            ("Mr. Akmal", "Директор / Шеф-мастер", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
            ("Mr. Dilshod", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
            ("Ismailov Abbos", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
            ("Tursunov Nodir", "Стилист", "+998 97 012-55-55", None, "0,1,2,3,4,5,6", 1, 1),
        ]
        await db.executemany(
            """
            INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            default_barbers
        )
        await db.commit()



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
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание новой услуги салона администратором."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO services (name, price, duration_min, description, badge, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), price, duration_min, description.strip(), badge.strip(), is_active)
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
    is_active: int = 1,
    db_path: Optional[str] = None
) -> bool:
    """Обновление существующей услуги салона."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            UPDATE services 
            SET name = ?, price = ?, duration_min = ?, description = ?, badge = ?, is_active = ?
            WHERE id = ?
            """,
            (name.strip(), price, duration_min, description.strip(), badge.strip(), is_active, service_id)
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


async def create_barber(
    name: str,
    role_title: str = "Барбер",
    phone: Optional[str] = None,
    telegram_id: Optional[int] = None,
    work_days: str = "0,1,2,3,4,5,6",
    is_working_today: int = 1,
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Создание нового мастера администратором."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO barbers (name, role_title, phone, telegram_id, work_days, is_working_today, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), role_title.strip(), phone, telegram_id, work_days, is_working_today, is_active)
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
    db_path: Optional[str] = None
) -> bool:
    """Обновление карточки мастера."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
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
    db_path: Optional[str] = None
) -> int:
    """Сохранение структурированной заявки на запись клиента в барбершоп."""
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO bookings (
                user_id, username, full_name, phone, service, booking_time, status, details,
                barber_id, barber_name, service_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, full_name, phone, service, booking_time, status, details,
             barber_id, barber_name, service_id)
        )
        await db.commit()
        return cursor.lastrowid


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
                   barber_id, barber_name, service_id, created_at 
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
                   barber_id, barber_name, service_id, created_at 
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
                   barber_id, barber_name, service_id, created_at
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
                   barber_id, barber_name, service_id, created_at 
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
                   barber_id, barber_name, service_id, created_at
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
                   barber_id, barber_name, service_id, created_at
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
    now_dt = datetime.now()
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
        "admin_chat_id", "contact_phone", "contact_name", "last_notification_stage"
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


async def extend_tenant_subscription_30_days(tenant_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """
    Продление подписки заведения на 30 дней.
    Если срок еще не истек — добавляет 30 дней к текущей дате expires_at.
    Если срок уже истек или блокирован — добавляет 30 дней от текущего момента.
    Автоматически переводит is_active = 1 и сбрасывает статус уведомлений.
    """
    tenant = await get_tenant_by_id(tenant_id, db_path)
    if not tenant:
        return None

    now_dt = datetime.now()
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


async def toggle_tenant_active(tenant_id: int, is_active: Optional[int] = None, db_path: Optional[str] = None) -> bool:
    """Переключение ручного выключателя Kill Switch для арендатора."""
    tenant = await get_tenant_by_id(tenant_id, db_path)
    if not tenant:
        return False

    if is_active is None:
        new_active = 0 if tenant.get("is_active", 1) == 1 else 1
    else:
        new_active = 1 if is_active else 0

    return await update_tenant_subscription(tenant_id, {"is_active": new_active}, db_path=db_path)

