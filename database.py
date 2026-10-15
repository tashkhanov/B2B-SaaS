import aiosqlite
from typing import List, Dict, Optional
from config import settings

DEFAULT_DB_PATH = settings.DB_NAME if settings.DB_NAME else "barbershop.db"


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Инициализация схемы таблиц базы данных SQLite.
    Создает таблицы для хранения сообщений и зафиксированных записей.
    """
    async with aiosqlite.connect(db_path) as db:
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
                details TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()


async def add_message(user_id: int, role: str, text: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Сохранение реплики пользователя ('user') или ассистента ('model') в историю.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text)
        )
        await db.commit()


async def get_recent_messages(user_id: int, limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, str]]:
    """
    Получение последних N реплик пользователя (по умолчанию 10 = 5 пар вопрос-ответ).
    Возвращает список сообщений в хронологическом порядке (от старых к новым).
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, text FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            # Инвертируем, чтобы получить хронологический порядок для Gemini
            return [{"role": row["role"], "text": row["text"]} for row in reversed(rows)]


async def clear_history(user_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Очистка контекста диалога пользователя (команда /reset).
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()


async def save_booking(
    user_id: int,
    username: Optional[str],
    full_name: str,
    details: str,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    """
    Сохранение заявки на запись клиента в барбершоп.
    Возвращает ID созданной записи.
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO bookings (user_id, username, full_name, details) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, details)
        )
        await db.commit()
        return cursor.lastrowid
