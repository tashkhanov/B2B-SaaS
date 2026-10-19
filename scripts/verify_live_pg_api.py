"""
Скрипт сквозной верификации API на реальной базе PostgreSQL b2b_saas.
Тестирует реальный aiohttp web-сервер с подключением к боевой базе данных b2b_saas.
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import settings
from web_app import create_web_app
from database import init_db, get_barbers, get_branches, close_db
from services.schedule_service import get_now_tashkent


async def run_e2e_verification():
    print("=" * 60)
    print("ЭТАП 3: СКВОЗНАЯ ВЕРИФИКАЦИЯ БИЗНЕС-СЦЕНАРИЕВ API (b2b_saas)")
    print("=" * 60)

    # Убеждаемся что работаем с боевой базой b2b_saas
    settings.DB_NAME = settings.PG_NAME
    await init_db(settings.PG_NAME)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. GET /api/config
        resp_cfg = await client.get("/api/config")
        assert resp_cfg.status == 200, f"Config status: {resp_cfg.status}"
        cfg = await resp_cfg.json()
        print(f" [OK] 1. GET /api/config -> 200 OK (Салон: {cfg.get('salon_name')})")

        # 2. GET /api/branches
        resp_br = await client.get("/api/branches")
        assert resp_br.status == 200
        branches = await resp_br.json()
        assert len(branches) >= 2, f"Expected at least 2 branches, got {len(branches)}"
        print(f" [OK] 2. GET /api/branches -> 200 OK (Филиалов: {len(branches)})")

        # 3. GET /api/services
        resp_srv = await client.get("/api/services")
        assert resp_srv.status == 200
        services = await resp_srv.json()
        assert len(services) >= 12, f"Expected at least 12 services, got {len(services)}"
        print(f" [OK] 3. GET /api/services -> 200 OK (Услуг: {len(services)})")

        # 4. GET /api/barbers
        resp_brb = await client.get("/api/barbers")
        assert resp_brb.status == 200
        barbers = await resp_brb.json()
        assert len(barbers) >= 4, f"Expected at least 4 barbers, got {len(barbers)}"
        print(f" [OK] 4. GET /api/barbers -> 200 OK (Мастеров: {len(barbers)})")

        # 5. GET /api/slots
        tomorrow = (get_now_tashkent().date() + timedelta(days=2)).isoformat()
        resp_slots = await client.get(f"/api/slots?date={tomorrow}&branch_id=1")
        assert resp_slots.status == 200
        slots_data = await resp_slots.json()
        assert "slots" in slots_data and len(slots_data["slots"]) > 0
        print(f" [OK] 5. GET /api/slots -> 200 OK (Доступных слотов на {tomorrow}: {len(slots_data['slots'])})")

        # 6. POST /api/bookings (Создание успешной записи)
        slot_time = f"{tomorrow} 15:00"
        booking_payload = {
            "full_name": "Тестовый Клиент E2E",
            "phone": "+998901112233",
            "service": "Стрижка волос",
            "booking_time": slot_time,
            "branch_id": 1,
            "barber_id": barbers[0]["id"]
        }
        resp_book = await client.post("/api/bookings", json=booking_payload)
        assert resp_book.status == 200, f"Booking status: {resp_book.status}"
        book_res = await resp_book.json()
        assert book_res.get("ok") is True
        print(f" [OK] 6. POST /api/bookings -> 200 OK (ID записи: {book_res.get('booking_id')})")

        # 7. Защита от двойной записи на тот же слот
        resp_dup = await client.post("/api/bookings", json=booking_payload)
        assert resp_dup.status == 409, f"Expected 409 Conflict, got {resp_dup.status}"
        print(" [OK] 7. POST /api/bookings (дубликат на то же время) -> 409 Conflict (Защита от овербукинга активна)")

        print("\n" + "=" * 60)
        print("[SUCCESS] ВСЕ СКВОЗНЫЕ ТЕСТЫ API НА БОЕВОЙ БАЗЕ POSTGRESQL ПРОЙДЕНЫ!")
        print("=" * 60)

    finally:
        await client.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(run_e2e_verification())
