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


async def get_all_bookings(db_path: Optional[str] = None) -> List[Dict]:
    """
    Получение всех записей для панели администратора (TMA).
    Сортировка по ID по убыванию (новые сверху).
    """
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


async def update_booking_status(booking_id: int, status: str, db_path: Optional[str] = None) -> bool:
    """
    Обновление статуса записи (new -> confirmed -> cancelled).
    """
    target_db = get_db_path(db_path)
    async with aiosqlite.connect(target_db) as db:
        cursor = await db.execute(
            "UPDATE bookings SET status = ? WHERE id = ?",
            (status, booking_id)
        )
        await db.commit()
        return cursor.rowcount > 0
