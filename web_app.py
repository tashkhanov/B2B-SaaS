import os
import json
import logging
from aiohttp import web

from config import settings
from database import get_all_bookings, update_booking_status

logger = logging.getLogger(__name__)

INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "index.html")


async def handle_index(request: web.Request) -> web.Response:
    """Отдача HTML-страницы Telegram Mini App"""
    if os.path.exists(INDEX_HTML_PATH):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    return web.Response(text="<h1>WebApp index.html not found</h1>", content_type="text/html", status=404)


async def handle_get_bookings(request: web.Request) -> web.Response:
    """API: Получение списка всех записей клиентов в формате JSON"""
    try:
        bookings = await get_all_bookings()
        return web.json_response(bookings)
    except Exception as e:
        logger.error(f"Ошибка при получении записей из БД: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_status(request: web.Request) -> web.Response:
    """API: Смена статуса записи (new, confirmed, cancelled)"""
    try:
        booking_id = int(request.match_info.get("id", 0))
        data = await request.json()
        new_status = data.get("status", "confirmed")
        
        success = await update_booking_status(booking_id, new_status)
        return web.json_response({"ok": success, "id": booking_id, "status": new_status})
    except Exception as e:
        logger.error(f"Ошибка при смене статуса записи: {e}")
        return web.json_response({"error": str(e)}, status=500)


def create_web_app() -> web.Application:
    """Фабрика создания приложения aiohttp для TMA"""
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/webapp", handle_index)
    app.router.add_get("/api/bookings", handle_get_bookings)
    app.router.add_post("/api/bookings/{id}/status", handle_update_status)
    return app


async def start_web_server(host: str = None, port: int = None) -> web.AppRunner:
    """
    Запуск асинхронного веб-сервера aiohttp на том же event loop, что и бот
    """
    h = host or settings.WEBAPP_HOST or "0.0.0.0"
    p = port or settings.WEBAPP_PORT or 8000

    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, h, p)
    await site.start()
    logger.info("🌐 Сервер Telegram Mini App успешно запущен: http://%s:%s", h, p)
    return runner
