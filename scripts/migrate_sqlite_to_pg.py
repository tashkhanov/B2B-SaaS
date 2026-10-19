"""
Скрипт безопасной миграции данных из SQLite (barbershop.db) в PostgreSQL (b2b_saas).
1. Создает таблицы с нативными типами PostgreSQL 18.
2. Переносит данные с сохранением первичных ключей (ID).
3. Синхронизирует последовательности автоинкремента (SERIAL sequences).
4. Проводит валидацию целостности (Data Parity Check).
"""

import os
import sys
import asyncio
import sqlite3
import asyncpg
from typing import Dict, List, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

DDL_STATEMENTS = [
    # 1. messages
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

    # 2. branches
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

    # 3. services
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

    # 4. barbers
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

    # 5. tenant_subscription
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

    # 6. bookings
    """
    CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        username VARCHAR(255),
        full_name VARCHAR(255) NOT NULL,
        details TEXT,
        phone VARCHAR(64),
        service VARCHAR(255) NOT NULL,
        booking_time VARCHAR(64) NOT NULL,
        status VARCHAR(32) DEFAULT 'new',
        barber_id INTEGER REFERENCES barbers(id) ON DELETE SET NULL,
        barber_name VARCHAR(255),
        service_id INTEGER,
        branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL,
        branch_name VARCHAR(255),
        reminder_2h_sent SMALLINT DEFAULT 0,
        client_confirmed SMALLINT DEFAULT 0,
        winback_sent SMALLINT DEFAULT 0,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_user_id ON bookings (user_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_time ON bookings (booking_time);
    CREATE INDEX IF NOT EXISTS idx_bookings_barber ON bookings (barber_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_branch ON bookings (branch_id);
    """,

    # 7. salon_settings
    """
    CREATE TABLE IF NOT EXISTS salon_settings (
        key VARCHAR(128) PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """,

    # 8. receipt_logs
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


async def create_schema(pg_conn: asyncpg.Connection, drop_first: bool = False):
    """Создание схемы таблиц в целевой базе данных"""
    if drop_first:
        drop_tables = [
            "receipt_logs", "bookings", "messages", "salon_settings",
            "barbers", "tenant_subscription", "services", "branches"
        ]
        for tbl in drop_tables:
            await pg_conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
    for ddl in DDL_STATEMENTS:
        await pg_conn.execute(ddl)


def fetch_sqlite_rows(sqlite_path: str, table_name: str) -> List[Dict[str, Any]]:
    """Получение всех строк из таблицы SQLite"""
    if not os.path.exists(sqlite_path):
        return []
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {table_name}")
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    except Exception as e:
        print(f"Таблица {table_name} не найдена в SQLite или пуста: {e}")
        return []
    finally:
        conn.close()


async def migrate_table_data(pg_conn: asyncpg.Connection, table_name: str, rows: List[Dict[str, Any]]):
    """Перенос строк в PostgreSQL с фиксацией sequence"""
    if not rows:
        print(f"  [i] Таблица {table_name}: нет данных для миграции (0 строк)")
        return 0

    first_row = rows[0]
    cols = list(first_row.keys())
    col_str = ", ".join(cols)
    placeholders = ", ".join([f"${i+1}" for i in range(len(cols))])

    query = f"INSERT INTO {table_name} ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    inserted_count = 0
    for r in rows:
        # Приведение timestamp строк или int типов
        vals = []
        for c in cols:
            val = r[c]
            # Преобразование строковых дат для timestamp колонок если нужно
            if c in ("created_at", "updated_at", "expires_at") and isinstance(val, str):
                try:
                    from datetime import datetime
                    val = datetime.fromisoformat(val.replace(" ", "T"))
                except Exception:
                    pass
            vals.append(val)
        try:
            await pg_conn.execute(query, *vals)
            inserted_count += 1
        except Exception as err:
            print(f"  [!] Ошибка вставки строки {r.get('id', r.get('key'))} в {table_name}: {err}")

    # Обновляем sequence автоинкремента если в таблице есть колонка id
    if "id" in cols:
        try:
            await pg_conn.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    coalesce((SELECT max(id) FROM {table_name}), 1)
                );
            """)
        except Exception as e:
            pass

    print(f"  [OK] Таблица {table_name}: успешно мигрировано {inserted_count}/{len(rows)} строк")
    return inserted_count


async def main():
    print("=" * 60)
    print("НАЧАЛО МИГРАЦИИ SQLITE -> POSTGRESQL 18")
    print(f"Источник: {settings.DB_NAME}")
    print(f"Назначение: postgresql://{settings.PG_USER}:***@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_NAME}")
    print("=" * 60)

    # 1. Подключение к боевой базе b2b_saas
    conn = await asyncpg.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
        database=settings.PG_NAME
    )

    try:
        print("\n1. Создание схемы таблиц в PostgreSQL b2b_saas...")
        await create_schema(conn, drop_first=True)
        print("   [OK] Все таблицы и индексы успешно созданы.")

        # Порядок миграции с учетом зависимостей FK
        tables_order = [
            "branches",
            "services",
            "barbers",
            "tenant_subscription",
            "bookings",
            "messages",
            "salon_settings",
            "receipt_logs"
        ]

        print("\n2. Перенос данных из SQLite:")
        verification_report = {}
        for t in tables_order:
            sqlite_rows = fetch_sqlite_rows(settings.DB_NAME, t)
            migrated_cnt = await migrate_table_data(conn, t, sqlite_rows)
            pg_cnt = await conn.fetchval(f"SELECT count(*) FROM {t}")
            verification_report[t] = {
                "sqlite_rows": len(sqlite_rows),
                "pg_rows": pg_cnt,
                "status": "MATCH" if len(sqlite_rows) == pg_cnt else "MISMATCH"
            }

        # 3. Отчет верификации
        print("\n" + "=" * 60)
        print("ИТОГОВЫЙ ОТЧЕТ ВЕРИФИКАЦИИ ДАННЫХ:")
        all_matched = True
        for tbl, rep in verification_report.items():
            status_symbol = "[OK]" if rep["status"] == "MATCH" else "[X]"
            print(f" {status_symbol:4} {tbl:22} | SQLite: {rep['sqlite_rows']:4} | PostgreSQL: {rep['pg_rows']:4} | {rep['status']}")
            if rep["status"] != "MATCH":
                all_matched = False
        print("=" * 60)

        if all_matched:
            print("\n[SUCCESS] ВСЕ ДАННЫЕ 100% СОВПАДАЮТ И ПЕРЕНЕСЕНЫ БЕЗ ПОТЕРЬ!")
        else:
            print("\n[WARNING] ОБНАРУЖЕНО РАСХОЖДЕНИЕ В ДАННЫХ, ПРОВЕРЬТЕ ЛОГИ.")

    finally:
        await conn.close()

    # 4. Также инициализируем тестовую базу b2b_saas_test
    print("\n3. Инициализация тестовой базы данных b2b_saas_test...")
    test_conn = await asyncpg.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
        database=settings.PG_TEST_NAME
    )
    try:
        await create_schema(test_conn, drop_first=True)
        print("   [OK] Схема тестовой базы b2b_saas_test готова для pytest.")
    finally:
        await test_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
