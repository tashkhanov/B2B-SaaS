import aiosqlite
from typing import List, Dict, Optional
from config import settings


def get_db_path(db_path: Optional[str] = None) -> str:
    """Динамическое получение пути к БД с учетом актуального значения settings.DB_NAME"""
    return db_path or (settings.DB_NAME if settings.DB_NAME else "barbershop.db")


async def init_db(db_path: Optional[str] = None) -> None:
    """
    Инициализация схемы таблиц базы данных SQLite.
    Создает таблицы для хранения сообщений и зафиксированных записей.
    Также проводит миграцию колонок, если таблица bookings уже существовала.
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Миграция колонок на случай, если таблица создавалась ранее с меньшим числом полей
        cursor = await db.execute("PRAGMA table_info(bookings);")
        columns = [row[1] for row in await cursor.fetchall()]
        
        for col_name, col_type, col_def in [
            ("phone", "TEXT", ""),
            ("service", "TEXT", ""),
            ("booking_time", "TEXT", ""),
            ("status", "TEXT", "DEFAULT 'new'"),
        ]:
            if col_name not in columns:
                await db.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type} {col_def};")

        await db.commit()


async def add_message(user_id: int, role: str, text: str, db_path: Optional[str] = None) -> None:
    """
    Сохранение реплики пользователя ('user') или ассистента ('model') в историю.
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text)
        )
        await db.commit()


async def get_recent_messages(user_id: int, limit: int = 10, db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Получение последних N реплик пользователя (по умолчанию 10 = 5 пар вопрос-ответ).
    Возвращает список сообщений в хронологическом порядке (от старых к новым).
    """
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
    """
    Очистка контекста диалога пользователя (команда /reset).
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()


async def save_booking(
    user_id: int,
    username: Optional[str],
    full_name: str,
    details: str,
    phone: Optional[str] = None,
    service: Optional[str] = None,
    booking_time: Optional[str] = None,
    status: str = "new",
    db_path: Optional[str] = None
) -> int:
    """
    Сохранение структурированной заявки на запись клиента в барбершоп.
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO bookings (
                user_id, username, full_name, phone, service, booking_time, status, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, full_name, phone, service, booking_time, status, details)
        )
        await db.commit()
        return cursor.lastrowid


async def has_recent_booking(
    user_id: int,
    booking_time: str,
    db_path: Optional[str] = None
) -> bool:
    """
    Проверяет, создавал ли данный пользователь запись на такое же время за последние 15 минут,
    чтобы исключить дублирование записей при продолжении диалога.
    """
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
    в статус 'completed', если время окончания процедуры (начало + 60 мин) уже прошло.
    Возвращает количество обновленных записей.
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
    """
    Получение всех записей для панели администратора (TMA).
    Предварительно актуализирует статусы прошедших записей ('completed').
    Сортировка по ID по убыванию (новые сверху).
    """
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details, created_at 
            FROM bookings ORDER BY id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_active_bookings(db_path: Optional[str] = None) -> List[Dict]:
    """
    Получение всех активных (не отмененных и не завершенных) записей для проверки пересечений и расписания.
    Предварительно актуализирует статусы прошедших записей ('completed').
    """
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details, created_at 
            FROM bookings 
            WHERE status IN ('new', 'confirmed') AND booking_time IS NOT NULL AND booking_time != ''
            ORDER BY id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_booking_status(booking_id: int, status: str, db_path: Optional[str] = None) -> bool:
    """
    Обновление статуса записи (new -> confirmed -> cancelled -> completed).
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET status = ? WHERE id = ?",
            (status, booking_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_booking_by_id(booking_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """
    Получение конкретной записи по её ID.
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, username, full_name, phone, service, booking_time, status, details, created_at FROM bookings WHERE id = ?",
            (booking_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_active_booking(user_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    """
    Получение ближайшей активной (предстоящей) брони пользователя.
    """
    await auto_complete_past_bookings(db_path)
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details, created_at
            FROM bookings 
            WHERE user_id = ? AND status IN ('new', 'confirmed')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def cancel_user_booking(user_id: int, booking_id: Optional[int] = None, db_path: Optional[str] = None) -> Optional[Dict]:
    """
    Отмена активной брони пользователя (клиентом).
    Возвращает словарь отмененной брони или None, если активной брони не найдено.
    """
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
    """
    Поиск активной брони клиента по имени или @username (для голосовых/текстовых команд администратора).
    """
    await auto_complete_past_bookings(db_path)
    clean_query = query.strip().replace("@", "").lower()
    target_db = get_db_path(db_path)

    async with aiosqlite.connect(target_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, username, full_name, phone, service, booking_time, status, details, created_at
            FROM bookings 
            WHERE status IN ('new', 'confirmed')
              AND (LOWER(full_name) LIKE ? OR LOWER(username) LIKE ?)
            ORDER BY id DESC LIMIT 1
            """,
            (f"%{clean_query}%", f"%{clean_query}%")
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

