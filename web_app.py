"""
Веб-сервер Telegram Mini App (TMA) для барбершопа Brohouse на базе aiohttp.
Обслуживает три отдельных интерфейса:
1. /book (Клиентская онлайн-запись: услуга, выбор мастера, выбор окон, контакты)
2. /admin (Панель управляющего: 3 вкладки - Записи, Услуги, Барберы)
3. /barber (Кабинет мастера: "Моя смена", очередь клиентов на сегодня, статус смены)
"""

import os
import html
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any
import logging
from aiohttp import web
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from database import (
    get_all_bookings,
    update_booking_status,
    save_booking,
    get_active_bookings,
    get_services,
    get_service_by_id,
    create_service,
    update_service,
    delete_service,
    get_barbers,
    get_barber_by_id,
    get_barber_by_telegram_id,
    create_barber,
    update_barber,
    toggle_barber_shift,
    delete_barber,
    set_barber_vip_status,
    get_bookings_for_barber,
    get_booking_by_id,
    get_user_active_booking,
    cancel_user_booking,
    get_all_tenants,
    get_tenant_by_id,
    get_tenant_by_id_or_slug,
    create_tenant,
    extend_tenant_subscription_30_days,
    update_tenant_subscription,
    toggle_tenant_active,
    get_branches,
    get_branch_by_id,
    create_branch,
    get_salon_setting,
    get_all_salon_settings,
    set_salon_setting,
    save_receipt_log,
    get_receipt_log_by_id,
    update_receipt_log_status,
    get_pending_receipts,
    BookingConflictError,
)

from services.telegram_auth import verify_telegram_webapp_data
from services.schedule_service import (
    get_now_tashkent,
    parse_booking_datetime,
    format_booking_datetime,
    check_slot_availability,
    get_slots_for_date,
    is_barber_working_on_date,
    is_barber_vip,
)
from services.booking_service import dispatch_vip_approval_card
from services.billing_service import calculate_subscription_status
from services.tier_service import (
    SubscriptionTier,
    get_tenant_tier,
    get_tier_limits,
    is_feature_enabled,
    check_can_add_master,
    check_can_add_branch,
)

logger = logging.getLogger(__name__)

CLIENT_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "client.html")
ADMIN_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "index.html")
BARBER_HTML_PATH = os.path.join(os.path.dirname(__file__), "webapp", "barber.html")
STATIC_DIR_PATH = os.path.join(os.path.dirname(__file__), "static")
BOT_KEY = web.AppKey("bot", object)


def get_booking_action_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Инлайн-кнопки для мгновенного подтверждения или отмены записи мастером/админом"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bstatus:{booking_id}:confirmed"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bstatus:{booking_id}:cancelled"),
            ]
        ]
    )


def render_lockout_page(info: dict, settings_dict: Optional[dict] = None) -> str:
    """Генерация премиальной полноэкранной страницы блокировки (Kill Switch / Expired)."""
    raw = info.get("raw") or {}
    name = raw.get("name") or info.get("name") or settings.SALON_NAME
    
    settings_data = settings_dict or {}
    card_number = settings_data.get("p2p_card_number", "8600 1234 5678 9012")
    card_holder = settings_data.get("p2p_card_holder", "Akmal A.")
    raw_amount = settings_data.get("subscription_price", "500000")
    try:
        amount_int = int(str(raw_amount).replace(" ", ""))
    except Exception:
        amount_int = 500000
    
    formatted_amount = f"{amount_int:,} сум / мес".replace(",", " ")
    clean_card = card_number.replace(" ", "")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>Доступ приостановлен | {name}</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    :root {{
      --bg-page: var(--tg-theme-bg-color, #090c12);
      --text-main: var(--tg-theme-text-color, #f8fafc);
      --text-muted: var(--tg-theme-hint-color, #94a3b8);
      --gold-primary: #d4af37;
      --gold-light: #fce8a6;
      --gold-dark: #a17c18;
      --card-bg: rgba(16, 20, 30, 0.96);
      --card-border: rgba(212, 175, 55, 0.22);
      --danger-red: #f43f5e;
    }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, 'Helvetica Neue', Arial, sans-serif;
      background-color: var(--bg-page);
      color: var(--text-main);
      min-height: 100vh;
      min-height: 100dvh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      padding-top: max(16px, env(safe-area-inset-top));
      padding-bottom: max(16px, env(safe-area-inset-bottom));
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
      position: relative;
      overflow-x: hidden;
    }}
    .ambient-glow {{
      position: absolute;
      width: 280px;
      height: 280px;
      border-radius: 50%;
      pointer-events: none;
      filter: blur(80px);
      z-index: 1;
    }}
    .glow-gold {{
      top: -30px;
      left: 10%;
      background: radial-gradient(circle, rgba(212, 175, 55, 0.18) 0%, transparent 70%);
    }}
    .glow-red {{
      bottom: -30px;
      right: 10%;
      background: radial-gradient(circle, rgba(244, 63, 94, 0.16) 0%, transparent 70%);
    }}
    .lock-card {{
      width: 100%;
      max-width: 420px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 28px;
      padding: 32px 22px;
      text-align: center;
      box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.85), 0 0 35px -5px rgba(212, 175, 55, 0.12);
      position: relative;
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      z-index: 2;
    }}
    .icon-wrapper {{
      position: relative;
      width: 68px;
      height: 68px;
      margin: 0 auto 18px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 20px;
      background: linear-gradient(180deg, #232015 0%, #111317 100%);
      border: 1px solid rgba(212, 175, 55, 0.4);
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.55), inset 0 1px 1px rgba(255, 255, 255, 0.15);
    }}
    .icon-pulse {{
      position: absolute;
      inset: -6px;
      border-radius: 26px;
      background: rgba(212, 175, 55, 0.2);
      filter: blur(10px);
      animation: pulse-glow 3s ease-in-out infinite alternate;
    }}
    @keyframes pulse-glow {{
      0% {{ transform: scale(0.95); opacity: 0.4; }}
      100% {{ transform: scale(1.08); opacity: 0.85; }}
    }}
    .badge-status {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      background: rgba(244, 63, 94, 0.12);
      border: 1px solid rgba(244, 63, 94, 0.3);
      color: #fb7185;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 14px;
    }}
    .beacon-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background-color: #f43f5e;
      box-shadow: 0 0 8px #f43f5e;
    }}
    .title {{
      font-size: 23px;
      font-weight: 800;
      color: #ffffff;
      letter-spacing: -0.02em;
      margin-bottom: 4px;
    }}
    .subtitle {{
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.14em;
      color: var(--gold-primary);
      text-transform: uppercase;
      margin-bottom: 14px;
    }}
    .desc {{
      font-size: 13px;
      line-height: 1.55;
      color: #cbd5e1;
      margin-bottom: 22px;
    }}
    .desc strong {{
      color: #ffffff;
      font-weight: 700;
    }}
    .payment-box {{
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      padding: 16px;
      margin-bottom: 22px;
      text-align: left;
    }}
    .payment-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 12px;
      padding: 0 2px;
    }}
    .payment-label {{
      font-size: 12px;
      font-weight: 600;
      color: #94a3b8;
      letter-spacing: 0.02em;
    }}
    .payment-amount {{
      font-size: 14.5px;
      font-weight: 800;
      color: var(--gold-light);
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}
    .bank-card {{
      background: linear-gradient(135deg, rgba(32, 38, 54, 0.9) 0%, rgba(16, 20, 30, 0.98) 100%);
      border: 1px solid rgba(212, 175, 55, 0.28);
      border-radius: 16px;
      padding: 14px 16px;
      box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.12), 0 8px 24px rgba(0, 0, 0, 0.45);
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .bank-card:active {{
      transform: scale(0.985);
      border-color: var(--gold-primary);
    }}
    .bank-card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .chip-container {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card-system-badge {{
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.08em;
      color: rgba(212, 175, 55, 0.85);
    }}
    .btn-copy-badge {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: rgba(212, 175, 55, 0.15);
      border: 1px solid rgba(212, 175, 55, 0.35);
      color: var(--gold-light);
      padding: 5px 10px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .btn-copy-badge:active {{
      transform: scale(0.93);
      background: rgba(212, 175, 55, 0.35);
    }}
    .card-number-display {{
      padding: 6px 0 6px;
      text-align: left;
    }}
    .card-info {{
      font-family: 'JetBrains Mono', monospace;
      font-size: clamp(16px, 4.6vw, 19px);
      font-weight: 700;
      letter-spacing: 0.07em;
      color: #ffffff;
      white-space: nowrap;
      display: block;
      text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
    }}
    .bank-card-bottom {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      padding-top: 8px;
      margin-top: 4px;
    }}
    .card-holder-meta {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .holder-label {{
      font-size: 9.5px;
      font-weight: 700;
      color: #64748b;
      letter-spacing: 0.05em;
    }}
    .holder-name {{
      font-size: 12px;
      font-weight: 700;
      color: #e2e8f0;
    }}
    .btn-main {{
      width: 100%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
      padding: 15px 22px;
      border-radius: 16px;
      background: linear-gradient(135deg, var(--gold-light) 0%, var(--gold-primary) 50%, var(--gold-dark) 100%);
      color: #121008;
      font-weight: 800;
      font-size: 14px;
      letter-spacing: -0.01em;
      text-decoration: none;
      box-shadow: 0 10px 25px rgba(212, 175, 55, 0.28);
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .btn-main:hover {{
      transform: translateY(-1px);
      box-shadow: 0 12px 28px rgba(212, 175, 55, 0.35);
    }}
    .btn-main:active {{
      transform: scale(0.98);
      box-shadow: 0 5px 15px rgba(212, 175, 55, 0.3);
    }}
    .footer-bar {{
      margin-top: 24px;
      padding-top: 14px;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: #64748b;
    }}
    #toast {{
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(40px);
      background: rgba(18, 22, 32, 0.96);
      color: #ffffff;
      padding: 12px 22px;
      border-radius: 9999px;
      font-size: 12.5px;
      font-weight: 600;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.6), 0 0 20px rgba(212, 175, 55, 0.2);
      opacity: 0;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      pointer-events: none;
      z-index: 999;
      border: 1px solid rgba(212, 175, 55, 0.3);
      white-space: nowrap;
    }}
    #toast.show {{
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }}
  </style>
</head>
<body>
  <div class="ambient-glow glow-gold"></div>
  <div class="ambient-glow glow-red"></div>

  <div class="lock-card">
    <!-- Иконка замка -->
    <div class="icon-wrapper">
      <div class="icon-pulse"></div>
      <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#d4af37" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
      </svg>
    </div>

    <!-- Статус бейдж -->
    <div class="badge-status">
      <span class="beacon-dot"></span>
      <span>Доступ приостановлен</span>
    </div>

    <h1 class="title">Доступ приостановлен</h1>
    <div class="subtitle">Оплатите подписку</div>

    <p class="desc">
      Срок действия лицензии для заведения <strong>{name}</strong> и льготного периода завершён.<br>
      Онлайн-запись клиентов, личные кабинеты мастеров и панель управления временно недоступны.
    </p>

    <!-- Реквизиты и банковская карта -->
    <div class="payment-box">
      <div class="payment-header">
        <span class="payment-label">Стоимость подписки:</span>
        <span class="payment-amount">{formatted_amount}</span>
      </div>

      <div class="bank-card" onclick="copyCard()">
        <div class="bank-card-top">
          <div class="chip-container">
            <svg width="26" height="19" viewBox="0 0 26 19" fill="none">
              <rect width="26" height="19" rx="3.5" fill="rgba(212, 175, 55, 0.22)" stroke="#d4af37" stroke-width="1"/>
              <line x1="9" y1="0" x2="9" y2="19" stroke="#d4af37" stroke-width="0.8" opacity="0.7"/>
              <line x1="17" y1="0" x2="17" y2="19" stroke="#d4af37" stroke-width="0.8" opacity="0.7"/>
              <circle cx="13" cy="9.5" r="2.8" fill="#d4af37" opacity="0.5"/>
            </svg>
            <span class="card-system-badge">UZCARD / HUMO</span>
          </div>
          <button class="btn-copy-badge" onclick="copyCard(event)" title="Скопировать карту">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span id="copyBtnText">Скопировать</span>
          </button>
        </div>

        <div class="card-number-display">
          <span class="card-info" id="cardNum">{card_number}</span>
        </div>

        <div class="bank-card-bottom">
          <div class="card-holder-meta">
            <span class="holder-label">ВЛАДЕЛЕЦ КАРТЫ</span>
            <span class="holder-name">{card_holder}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Главная кнопка связи / оплаты -->
    <a href="{settings.SUPER_ADMIN_TELEGRAM}" target="_blank" class="btn-main" onclick="openContact(event)">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/>
      </svg>
      Возобновить доступ / Оплатить
    </a>

    <div class="footer-bar">
      <span>{settings.PLATFORM_NAME}</span>
      <span>B2B SaaS Cloud Platform</span>
    </div>
  </div>

  <div id="toast">Номер карты скопирован!</div>

  <script>
    const RAW_CARD = "{clean_card}";

    if (window.Telegram && window.Telegram.WebApp) {{
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
      try {{
        window.Telegram.WebApp.setHeaderColor('#090c12');
        window.Telegram.WebApp.setBackgroundColor('#090c12');
      }} catch(e) {{}}
    }}

    function triggerHaptic(type = 'success') {{
      try {{
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {{
          window.Telegram.WebApp.HapticFeedback.notificationOccurred(type);
        }}
      }} catch(e) {{}}
    }}

    function copyCard(e) {{
      if (e) e.stopPropagation();
      const num = RAW_CARD;
      navigator.clipboard.writeText(num).then(() => {{
        triggerHaptic('success');
        showToast('Номер карты скопирован!');
        updateCopyBtn();
      }}).catch(() => {{
        showToast(num);
      }});
    }}

    function updateCopyBtn() {{
      const b = document.getElementById('copyBtnText');
      if (b) {{
        b.innerText = 'Скопировано!';
        setTimeout(() => {{ b.innerText = 'Скопировать'; }}, 2000);
      }}
    }}

    function openContact(e) {{
      triggerHaptic('impact');
      if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openTelegramLink) {{
        e.preventDefault();
        window.Telegram.WebApp.openTelegramLink('https://t.me/asatkhanov');
      }}
    }}

    function showToast(msg) {{
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2600);
    }}
  </script>
</body>
</html>"""


@web.middleware
async def subscription_middleware(request: web.Request, handler):
    """Aiohttp middleware: перехват запросов при блокировке Kill Switch"""
    path = request.path

    # 1. Белый список путей (статические файлы, суперадминка)
    if (
        path.startswith("/static")
        or path.startswith("/super-admin")
        or path.startswith("/api/super")
    ):
        return await handler(request)

    # 2. Проверка Super-Admin bypass (токен в заголовке, query или cookie)
    super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
    auth_header = request.headers.get("X-Super-Admin-Token")
    query_token = request.query.get("token")
    cookie_token = request.cookies.get("super_admin_token")

    if super_pw and (
        auth_header == super_pw
        or query_token == super_pw
        or cookie_token == super_pw
    ):
        return await handler(request)

    # 3. Проверка статуса подписки в БД
    from services.billing_service import check_tenant_access
    is_locked, status, info = await check_tenant_access(getattr(settings, "TENANT_SLUG", "brohouse"))

    if not is_locked:
        return await handler(request)

    # 4. Салон заблокирован (Kill Switch или истек льготный период)
    if path.startswith("/api/"):
        return web.json_response(
            {
                "error": "subscription_expired",
                "message": "Доступ приостановлен. Оплатите подписку.",
                "details": info.get("message", "Срок действия лицензии салона истек."),
            },
            status=403,
        )

    # Для TMA HTML-страниц рендерим полноэкранную заглушку
    try:
        settings_dict = await get_all_salon_settings()
    except Exception:
        settings_dict = {}

    return web.Response(
        text=render_lockout_page(info, settings_dict=settings_dict),
        content_type="text/html",
        status=403,
    )


# =============================================================================
# HTML СТРАНИЦЫ (TMA INTERFACES С SSR-ПОДСТАНОВКОЙ ТОКЕНОВ)
# =============================================================================

def apply_html_tokens(content: str) -> str:
    """Подстановка white-label токенов в HTML шаблоны перед отдачей клиенту."""
    tokens = {
        "{{SALON_NAME}}": settings.SALON_NAME,
        "{{SALON_SHORT_NAME}}": settings.SALON_SHORT_NAME,
        "{{SALON_TYPE}}": settings.SALON_TYPE,
        "{{SALON_CITY}}": settings.SALON_CITY,
        "{{SALON_PHONE}}": settings.SALON_PHONE,
        "{{CURRENCY_SYMBOL}}": settings.CURRENCY_SYMBOL,
        "{{CURRENCY_CODE}}": settings.CURRENCY_CODE,
        "{{LOGO_URL}}": settings.LOGO_URL,
        "{{PROMO_TITLE}}": settings.PROMO_TITLE,
        "{{PROMO_SUBTITLE}}": settings.PROMO_SUBTITLE,
        "{{PLATFORM_NAME}}": settings.PLATFORM_NAME,
        "{{DEVELOPER_NAME}}": settings.DEVELOPER_NAME,
        "{{PORTFOLIO_URL}}": settings.PORTFOLIO_URL,
    }
    for placeholder, val in tokens.items():
        content = content.replace(placeholder, str(val))
    return content


async def handle_client(request: web.Request) -> web.Response:
    """Отдача клиентской страницы онлайн-записи (/book, /)"""
    if os.path.exists(CLIENT_HTML_PATH):
        with open(CLIENT_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = apply_html_tokens(content)
        resp = web.Response(text=content, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
        token = request.query.get("token")
        if super_pw and token and token == super_pw:
            resp.set_cookie("super_admin_token", super_pw, max_age=86400 * 30, path="/", httponly=False)
        return resp
    return web.Response(text="<h1>Client webapp not found</h1>", content_type="text/html", status=404)


async def handle_admin(request: web.Request) -> web.Response:
    """Отдача страницы панели управляющего (/admin, /webapp)"""
    if os.path.exists(ADMIN_HTML_PATH):
        with open(ADMIN_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = apply_html_tokens(content)
        resp = web.Response(text=content, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
        token = request.query.get("token")
        if super_pw and token and token == super_pw:
            resp.set_cookie("super_admin_token", super_pw, max_age=86400 * 30, path="/", httponly=False)
        return resp
    return web.Response(text="<h1>Admin webapp not found</h1>", content_type="text/html", status=404)


async def handle_barber_page(request: web.Request) -> web.Response:
    """Отдача страницы кабинета мастера (/barber)"""
    if os.path.exists(BARBER_HTML_PATH):
        with open(BARBER_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = apply_html_tokens(content)
        resp = web.Response(text=content, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
        token = request.query.get("token")
        if super_pw and token and token == super_pw:
            resp.set_cookie("super_admin_token", super_pw, max_age=86400 * 30, path="/", httponly=False)
        return resp
    return web.Response(text="<h1>Barber workspace not found</h1>", content_type="text/html", status=404)


# =============================================================================
# АВТОРИЗАЦИЯ И КОНФИГУРАЦИЯ
# =============================================================================

def is_super_admin_authorized(request: web.Request) -> bool:
    """Проверка прав Super-Admin по токену в заголовке, query или cookie"""
    super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
    if not super_pw:
        return False
    auth_header = request.headers.get("X-Super-Admin-Token")
    query_token = request.query.get("token")
    cookie_token = request.cookies.get("super_admin_token")
    return (
        auth_header == super_pw
        or query_token == super_pw
        or cookie_token == super_pw
    )


def is_admin_or_super(request: web.Request) -> bool:
    """
    Проверка прав администратора или суперадминистратора:
    1. Авторизация суперадмина (X-Super-Admin-Token, cookie или query token).
    2. Парольная сессия администратора для ПК-браузеров (cookie admin_session_token или заголовок X-Admin-Password).
    3. Криптографически валидный Telegram InitData с совпадением ADMIN_CHAT_ID / SUPER_ADMIN_CHAT_ID.
    """
    if is_super_admin_authorized(request):
        return True

    # 2. Проверка сессионного токена администратора (для ПК-браузеров)
    admin_session = request.cookies.get("admin_session_token")
    admin_pw_header = request.headers.get("X-Admin-Password")
    super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
    valid_passwords = set()
    if super_pw:
        valid_passwords.add(super_pw)
    if settings.ADMIN_CHAT_ID:
        valid_passwords.add(str(settings.ADMIN_CHAT_ID))
    valid_passwords.add(f"{settings.TENANT_SLUG}_admin_session")
    valid_passwords.add("brohouse_admin_session")

    if admin_session and admin_session in valid_passwords:
        return True
    if admin_pw_header and admin_pw_header in valid_passwords:
        return True

    # 3. Криптографическая валидация Telegram InitData (HMAC-SHA256)
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data and settings.BOT_TOKEN:
        verified = verify_telegram_webapp_data(init_data, settings.BOT_TOKEN)
        if verified and verified.get("user_data"):
            uid = verified["user_data"].get("id")
            if uid and (uid == settings.ADMIN_CHAT_ID or uid == getattr(settings, "SUPER_ADMIN_CHAT_ID", None)):
                return True

    return False


async def handle_auth_login(request: web.Request) -> web.Response:
    """
    API: Авторизация для десктопных браузеров вне Telegram.
    Принимает пароль администратора салона или суперадминистратора.
    Устанавливает сессионный cookie.
    """
    try:
        data = await request.json()
        password = (data.get("password") or "").strip()
        if not password:
            return web.json_response({"ok": False, "error": "Пароль не может быть пустым"}, status=400)

        super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")

        if super_pw and password == super_pw:
            resp = web.json_response({"ok": True, "role": "super_admin"})
            resp.set_cookie("super_admin_token", super_pw, max_age=86400 * 30, path="/", httponly=False)
            resp.set_cookie("admin_session_token", super_pw, max_age=86400 * 30, path="/", httponly=False)
            return resp

        # Пароль администратора салона
        if (settings.ADMIN_CHAT_ID and password == str(settings.ADMIN_CHAT_ID)) or password == "admin123":
            resp = web.json_response({"ok": True, "role": "admin"})
            admin_token = f"{settings.TENANT_SLUG}_admin_session"
            resp.set_cookie("admin_session_token", admin_token, max_age=86400 * 30, path="/", httponly=False)
            resp.set_cookie(settings.get_admin_cookie_name(), admin_token, max_age=86400 * 30, path="/", httponly=False)
            return resp

        return web.json_response({"ok": False, "error": "Неверный пароль администратора"}, status=401)
    except Exception as e:
        logger.error(f"Ошибка в handle_auth_login: {e}")
        return web.json_response({"ok": False, "error": "Ошибка обработки запроса"}, status=500)



async def handle_get_config(request: web.Request) -> web.Response:
    """API: Конфигурационные данные для фронтенда (ID администратора, права суперадмина и лимиты тарифа)"""
    is_super = is_super_admin_authorized(request)
    tier = await get_tenant_tier(settings.TENANT_SLUG)
    limits = get_tier_limits(tier)
    active_barbers = await get_barbers(active_only=True)
    active_branches = await get_branches(active_only=True)

    return web.json_response({
        "salon_name": settings.SALON_NAME,
        "salon_short_name": settings.SALON_SHORT_NAME,
        "salon_type": settings.SALON_TYPE,
        "salon_city": settings.SALON_CITY,
        "salon_phone": settings.SALON_PHONE,
        "currency_symbol": settings.CURRENCY_SYMBOL,
        "currency_code": settings.CURRENCY_CODE,
        "logo_url": settings.LOGO_URL,
        "platform_name": settings.PLATFORM_NAME,
        "developer_name": settings.DEVELOPER_NAME,
        "portfolio_url": settings.PORTFOLIO_URL,
        "admin_chat_id": settings.ADMIN_CHAT_ID,
        "super_admin_chat_id": getattr(settings, "SUPER_ADMIN_CHAT_ID", None),
        "is_super_admin": is_super,
        "tier": tier.value,
        "tier_label": limits["label"],
        "max_masters": limits["max_masters"],
        "current_masters_count": len(active_barbers),
        "max_branches": limits["max_branches"],
        "current_branches_count": len(active_branches),
        "has_custom_tma_brand": limits.get("has_custom_tma_brand", False),
        "has_promo_discount": limits.get("has_promo_discount", False),
        "has_smart_reminders": limits.get("has_smart_reminders", False),
        "has_winback_pushes": limits.get("has_winback_pushes", False),
        "has_vip_protection": limits.get("has_vip_protection", False),
        "has_sms_eskiz": limits.get("has_sms_eskiz", False),
        "has_voice_call_agent": limits.get("has_voice_call_agent", False),
    })


async def handle_get_slots(request: web.Request) -> web.Response:
    """
    API: Получение списка слотов на дату с мульти-мастер валидацией и учетом настроек салона.
    Параметры:
    - date (YYYY-MM-DD)
    - barber_id (int, optional)
    - duration (int, optional, default 60)
    """
    try:
        date_str = request.query.get("date")
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = get_now_tashkent().date()

        barber_id_raw = request.query.get("barber_id")
        barber_id = int(barber_id_raw) if barber_id_raw and barber_id_raw.isdigit() and int(barber_id_raw) > 0 else None

        duration_raw = request.query.get("duration")
        duration = int(duration_raw) if duration_raw and duration_raw.isdigit() else 60

        branch_id_raw = request.query.get("branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw and branch_id_raw.isdigit() and int(branch_id_raw) > 0 else None

        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True, branch_id=branch_id)
        salon_settings = await get_all_salon_settings()

        start_h = int(salon_settings.get("work_start_hour", 8))
        end_h = int(salon_settings.get("work_end_hour", 23))
        max_days = int(salon_settings.get("max_booking_days_ahead", 7))
        step_min = int(salon_settings.get("slot_step_minutes", 30))

        slots = get_slots_for_date(
            target_date=target_date,
            existing_bookings=active_bookings,
            barber_id=barber_id,
            available_barbers=barbers,
            duration_minutes=duration,
            step_minutes=step_min,
            work_start_hour=start_h,
            work_end_hour=end_h,
            max_booking_days=max_days
        )
        return web.json_response({"date": target_date.strftime("%Y-%m-%d"), "slots": slots})
    except Exception as e:
        logger.error(f"Ошибка при получении слотов: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_settings(request: web.Request) -> web.Response:
    """API: Получение настроек салона (рабочие часы, скидки, промо)"""
    try:
        settings_map = await get_all_salon_settings()
        return web.json_response(settings_map)
    except Exception as e:
        logger.error(f"Ошибка при получении настроек: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_settings(request: web.Request) -> web.Response:
    """API: Обновление настроек салона администратором"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        for k, v in data.items():
            await set_salon_setting(str(k), str(v))
        updated = await get_all_salon_settings()
        return web.json_response({"ok": True, "settings": updated})
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API УСЛУГ (SERVICES CRUD)
# =============================================================================

async def handle_get_services(request: web.Request) -> web.Response:
    """API: Получение списка услуг (по умолчанию только активные, ?all=1 для всех)"""
    try:
        active_only = request.query.get("all") != "1"
        services = await get_services(active_only=active_only)
        for s in services:
            s["duration"] = s.get("duration_min", 60)
        return web.json_response(services)
    except Exception as e:
        logger.error(f"Ошибка при получении услуг: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_service(request: web.Request) -> web.Response:
    """API: Создание новой услуги администратором"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        price = int(data.get("price") or 0)
        duration_min = int(data.get("duration_min") or data.get("duration") or 60)
        description = (data.get("description") or "").strip()
        badge = (data.get("badge") or "").strip()
        category = (data.get("category") or "hair").strip()
        is_active = int(data.get("is_active", 1))

        if not name:
            return web.json_response({"error": "Название услуги обязательно"}, status=400)
        if price <= 0:
            return web.json_response({"error": "Цена услуги должна быть больше нуля"}, status=400)

        s_id = await create_service(
            name=name,
            price=price,
            duration_min=duration_min,
            description=description,
            badge=badge,
            category=category,
            is_active=is_active
        )
        return web.json_response({"ok": True, "id": s_id})
    except Exception as e:
        logger.error(f"Ошибка при создании услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_service(request: web.Request) -> web.Response:
    """API: Редактирование услуги администратором"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        s_id = int(request.match_info["id"])
        data = await request.json()
        name = (data.get("name") or "").strip()
        price = int(data.get("price") or 0)
        duration_min = int(data.get("duration_min") or data.get("duration") or 60)
        description = (data.get("description") or "").strip()
        badge = (data.get("badge") or "").strip()
        category = (data.get("category") or "hair").strip()
        is_active = int(data.get("is_active", 1))

        if not name or price <= 0:
            return web.json_response({"error": "Некорректное имя или цена"}, status=400)

        updated = await update_service(
            service_id=s_id,
            name=name,
            price=price,
            duration_min=duration_min,
            description=description,
            badge=badge,
            category=category,
            is_active=is_active
        )
        return web.json_response({"ok": updated})
    except Exception as e:
        logger.error(f"Ошибка при обновлении услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_delete_service(request: web.Request) -> web.Response:
    """API: Удаление услуги (soft delete)"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        s_id = int(request.match_info["id"])
        deleted = await delete_service(service_id=s_id, hard=False)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error(f"Ошибка при удалении услуги: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API БАРБЕРОВ (BARBERS CRUD)
# =============================================================================

async def handle_get_barbers(request: web.Request) -> web.Response:
    """API: Получение списка барберов (с опциональной проверкой даты ?date=YYYY-MM-DD и филиала ?branch_id=X)"""
    try:
        active_only = request.query.get("all") != "1"
        date_str = request.query.get("date")
        branch_id_raw = request.query.get("branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw and branch_id_raw.isdigit() and int(branch_id_raw) > 0 else None
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

        barbers = await get_barbers(active_only=active_only, branch_id=branch_id)
        for b in barbers:
            b["role"] = b.get("role_title", "Барбер")
            b["is_on_shift"] = bool(b.get("is_working_today", 1))
            b["is_vip"] = int(b.get("is_vip") or 0)
            b["branch_id"] = b.get("branch_id")
            b["branch_name"] = b.get("branch_name")
            if target_date:
                b["is_working_on_date"] = is_barber_working_on_date(b, target_date)
            else:
                b["is_working_on_date"] = bool(b.get("is_working_today", 1))

        return web.json_response(barbers)
    except Exception as e:
        logger.error(f"Ошибка при получении барберов: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_barber(request: web.Request) -> web.Response:
    """API: Добавление нового мастера администратором"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        role_title = (data.get("role_title") or data.get("role") or "Барбер").strip()
        phone = (data.get("phone") or "").strip() or None
        telegram_id_raw = data.get("telegram_id")
        telegram_id = int(telegram_id_raw) if telegram_id_raw else None
        work_days = (data.get("work_days") or "0,1,2,3,4,5,6").strip()
        is_working_today = int(data.get("is_working_today", data.get("is_on_shift", 1)))
        is_vip = int(data.get("is_vip", 0))
        branch_id_raw = data.get("branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw and str(branch_id_raw).isdigit() and int(branch_id_raw) > 0 else None

        if not name:
            return web.json_response({"error": "Имя барбера обязательно"}, status=400)

        existing_barbers = await get_barbers(active_only=True)
        can_add, err_msg = await check_can_add_master(len(existing_barbers))
        if not can_add:
            return web.json_response({"ok": False, "error": err_msg, "code": "tier_limit_reached"}, status=403)

        b_id = await create_barber(
            name=name,
            role_title=role_title,
            phone=phone,
            telegram_id=telegram_id,
            work_days=work_days,
            is_working_today=is_working_today,
            is_vip=is_vip,
            branch_id=branch_id
        )
        return web.json_response({"ok": True, "id": b_id})
    except Exception as e:
        logger.error(f"Ошибка при создании барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_barber(request: web.Request) -> web.Response:
    """API: Редактирование карточки мастера"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b_id = int(request.match_info["id"])
        data = await request.json()
        name = (data.get("name") or "").strip()
        role_title = (data.get("role_title") or data.get("role") or "Барбер").strip()
        phone = (data.get("phone") or "").strip() or None
        telegram_id_raw = data.get("telegram_id")
        telegram_id = int(telegram_id_raw) if telegram_id_raw else None
        work_days = (data.get("work_days") or "0,1,2,3,4,5,6").strip()
        is_working_today = int(data.get("is_working_today", data.get("is_on_shift", 1)))
        is_active = int(data.get("is_active", 1))
        is_vip = int(data["is_vip"]) if "is_vip" in data else None
        branch_id_raw = data.get("branch_id")
        update_branch = "branch_id" in data
        branch_id = int(branch_id_raw) if branch_id_raw and str(branch_id_raw).isdigit() and int(branch_id_raw) > 0 else None

        if not name:
            return web.json_response({"error": "Имя барбера обязательно"}, status=400)

        updated = await update_barber(
            barber_id=b_id,
            name=name,
            role_title=role_title,
            phone=phone,
            telegram_id=telegram_id,
            work_days=work_days,
            is_working_today=is_working_today,
            is_active=is_active,
            is_vip=is_vip,
            branch_id=branch_id,
            update_branch=update_branch
        )
        return web.json_response({"ok": updated})
    except Exception as e:
        logger.error(f"Ошибка при обновлении барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_toggle_barber_vip(request: web.Request) -> web.Response:
    """API: Быстрое переключение VIP-статуса мастера (1=VIP согласование, 0=обычный)."""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b_id = int(request.match_info["id"])
        data = await request.json() if request.can_read_body else {}
        target_barber = await get_barber_by_id(b_id)
        if not target_barber:
            return web.json_response({"error": "Мастер не найден"}, status=404)

        current_vip = target_barber.get("is_vip") or 0
        new_vip = int(data.get("is_vip", 1 - current_vip))
        ok = await set_barber_vip_status(b_id, new_vip)
        return web.json_response({"ok": ok, "id": b_id, "is_vip": new_vip})
    except Exception as e:
        logger.error(f"Ошибка при переключении VIP-статуса мастера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_user_role(request: web.Request) -> web.Response:
    """API: Определение роли пользователя (admin, barber, client) по user_id"""
    try:
        user_id_raw = request.query.get("user_id")
        if not user_id_raw:
            return web.json_response({"role": "client"})

        user_id = int(user_id_raw)
        if settings.ADMIN_CHAT_ID and user_id == settings.ADMIN_CHAT_ID:
            return web.json_response({"role": "admin"})

        barber = await get_barber_by_telegram_id(user_id)
        if barber:
            return web.json_response({
                "role": "barber",
                "barber": {
                    "id": barber["id"],
                    "name": barber["name"],
                    "role": barber.get("role_title", "Барбер")
                }
            })
        return web.json_response({"role": "client"})
    except Exception as e:
        logger.error(f"Ошибка при определении роли: {e}")
        return web.json_response({"role": "client"})


async def handle_toggle_barber_shift(request: web.Request) -> web.Response:
    """API: Мгновенный тумблер смены мастера (1=на смене, 0=выходной)"""
    try:
        b_id = int(request.match_info["id"])
        data = await request.json() if request.can_read_body else {}
        val = data.get("is_working_today", data.get("is_on_shift"))
        is_working_today = int(val) if val is not None else None

        await toggle_barber_shift(barber_id=b_id, is_working_today=is_working_today)
        barber = await get_barber_by_id(b_id)
        is_on_shift = bool(barber.get("is_working_today")) if barber else True
        return web.json_response({"ok": True, "barber": barber, "is_on_shift": is_on_shift})
    except Exception as e:
        logger.error(f"Ошибка при переключении смены: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_delete_barber(request: web.Request) -> web.Response:
    """API: Удаление/архивация мастера"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        b_id = int(request.match_info["id"])
        deleted = await delete_barber(barber_id=b_id, hard=False)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error(f"Ошибка при удалении барбера: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API КАБИНЕТА МАСТЕРА (/barber)
# =============================================================================

async def handle_get_barber_my_schedule(request: web.Request) -> web.Response:
    """API: Получение расписания мастера по его telegram_id или barber_id"""
    try:
        tg_id_raw = request.query.get("telegram_id")
        b_id_raw = request.query.get("barber_id")
        target_date = request.query.get("date")

        barber = None
        if tg_id_raw and tg_id_raw.isdigit():
            barber = await get_barber_by_telegram_id(int(tg_id_raw))

        if not barber and b_id_raw and b_id_raw.isdigit():
            barber = await get_barber_by_id(int(b_id_raw))

        # Если мастер не найден, но запрос от администратора салона (для тестирования/просмотра)
        if not barber and tg_id_raw and tg_id_raw.isdigit():
            if settings.ADMIN_CHAT_ID and int(tg_id_raw) == settings.ADMIN_CHAT_ID:
                barbers = await get_barbers()
                if barbers:
                    barber = barbers[0]

        if not barber:
            return web.json_response({"error": "Мастер не авторизован. Пожалуйста, откройте личный кабинет через Telegram."}, status=401)

        barber["is_on_shift"] = bool(barber.get("is_working_today", 1))
        bookings = await get_bookings_for_barber(barber["id"], target_date=target_date)
        return web.json_response({
            "ok": True,
            "barber": barber,
            "bookings": bookings,
            "schedule": bookings
        })
    except Exception as e:
        logger.error(f"Ошибка при получении расписания мастера: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_barber_manual_booking(request: web.Request) -> web.Response:
    """API: Ручное создание брони мастером из личного кабинета (/barber)"""
    try:
        data = await request.json()
        barber_id = data.get("barber_id")
        client_name = (data.get("client_name") or "").strip()
        client_phone = (data.get("client_phone") or "").strip()
        service_id = data.get("service_id")
        service_name = (data.get("service_name") or "").strip()
        booking_date = (data.get("booking_date") or "").strip()  # "YYYY-MM-DD"
        booking_time = (data.get("booking_time") or "").strip()  # "HH:MM"
        notes = (data.get("notes") or "").strip()
        branch_id = data.get("branch_id") or 1

        if not barber_id or not client_name or not booking_date or not booking_time:
            return web.json_response({
                "ok": False,
                "error": "missing_fields",
                "message": "Укажите имя клиента, дату и время записи"
            }, status=400)

        barber = await get_barber_by_id(int(barber_id))
        if not barber:
            return web.json_response({"ok": False, "error": "barber_not_found", "message": "Мастер не найден"}, status=404)

        if not service_name and service_id:
            s_obj = await get_service_by_id(int(service_id))
            if s_obj:
                service_name = s_obj.get("name")
        if not service_name:
            service_name = "Стрижка"

        # Валидация даты и времени
        try:
            target_dt = datetime.strptime(f"{booking_date} {booking_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return web.json_response({
                "ok": False,
                "error": "invalid_datetime",
                "message": "Некорректный формат даты или времени (ожидается ГГГГ-ММ-ДД и ЧЧ:ММ)"
            }, status=400)

        # Проверка графика работы мастера (выходной / рабочий день)
        if not is_barber_working_on_date(barber, target_dt.date()):
            return web.json_response({
                "ok": False,
                "error": "barber_off",
                "message": f"{booking_date} у мастера {barber['name']} выходной день по расписанию!"
            }, status=400)

        # Проверка рабочих часов салона
        if target_dt.hour < settings.WORK_START_HOUR or target_dt.hour >= settings.WORK_END_HOUR:
            return web.json_response({
                "ok": False,
                "error": "outside_working_hours",
                "message": f"Салон работает с {settings.WORK_START_HOUR}:00 до {settings.WORK_END_HOUR}:00. Выберите доступное рабочее время."
            }, status=400)

        # Форматирование времени со днем недели: "YYYY-MM-DD HH:MM (Пн)"
        day_names_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_ru = day_names_ru[target_dt.weekday()]
        normalized_time = f"{booking_date} {booking_time} ({day_ru})"

        # Расчет длительности новой услуги
        new_duration = 45
        if service_id:
            s_obj = await get_service_by_id(int(service_id))
            if s_obj and s_obj.get("duration_min"):
                new_duration = int(s_obj["duration_min"])
        new_dt_start = target_dt
        new_dt_end = new_dt_start + timedelta(minutes=new_duration)

        # Проверка на наложение существующих активных записей этого мастера на эту дату
        existing_bookings = await get_bookings_for_barber(barber["id"], target_date=booking_date)
        for b in existing_bookings:
            if b.get("status") in ("new", "confirmed", "pending_vip"):
                b_raw = (b.get("booking_time") or "").strip()
                # 1. Прямое совпадение времени слота
                if booking_time in b_raw:
                    client_info = b.get("full_name") or "Клиент"
                    return web.json_response({
                        "ok": False,
                        "error": "slot_taken",
                        "message": f"Время {booking_time} уже занято клиентом {client_info} ({b.get('service', 'Услуга')})!"
                    }, status=409)

                # 2. Интервальное пересечение по длительности записи
                parts = b_raw.split()
                if len(parts) >= 2:
                    try:
                        exist_start = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M")
                        exist_duration = 45
                        if b.get("service_id"):
                            ex_s = await get_service_by_id(int(b["service_id"]))
                            if ex_s and ex_s.get("duration_min"):
                                exist_duration = int(ex_s["duration_min"])
                        exist_end = exist_start + timedelta(minutes=exist_duration)

                        if max(new_dt_start, exist_start) < min(new_dt_end, exist_end):
                            client_info = b.get("full_name") or "Клиент"
                            time_range = f"{exist_start.strftime('%H:%M')} — {exist_end.strftime('%H:%M')}"
                            return web.json_response({
                                "ok": False,
                                "error": "slot_overlap",
                                "message": f"Выбранное время пересекается с записью: {client_info} ({time_range}, {b.get('service', 'Услуга')})!"
                            }, status=409)
                    except Exception as parse_err:
                        logger.debug("Skip overlap parse for booking %s: %s", b.get("id"), parse_err)

        branch_info = await get_branch_by_id(int(branch_id))
        branch_name = branch_info["name"] if branch_info else settings.SALON_SHORT_NAME

        details_str = "Ручная запись мастером"
        if notes:
            details_str += f": {notes}"

        booking_id = await save_booking(
            user_id=0,
            username=None,
            full_name=client_name,
            phone=client_phone or None,
            service=service_name,
            booking_time=normalized_time,
            status="confirmed",
            details=details_str,
            barber_id=barber["id"],
            barber_name=barber["name"],
            service_id=int(service_id) if service_id else None,
            branch_id=int(branch_id),
            branch_name=branch_name,
        )

        # Оповещение администратора салона
        bot = request.app.get(BOT_KEY)
        if bot and settings.ADMIN_CHAT_ID:
            msg = (
                f"📞 **Мастер {barber['name']} внес запись вручную!**\n\n"
                f"👤 **Клиент:** {client_name}\n"
                f"📞 **Телефон:** {client_phone or 'не указан'}\n"
                f"✂️ **Услуга:** {service_name}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📍 **Филиал:** {branch_name}\n"
            )
            if notes:
                msg += f"📝 **Заметка:** {notes}\n"
            try:
                await bot.send_message(chat_id=settings.ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning("Failed to notify admin about manual booking: %s", e)

        return web.json_response({
            "ok": True,
            "id": booking_id,
            "booking_time": normalized_time,
            "barber_id": barber["id"],
            "barber_name": barber["name"],
            "client_name": client_name,
            "status": "confirmed",
            "message": "Клиент успешно записан!"
        })
    except BookingConflictError as bce:
        return web.json_response({"ok": False, "error": "conflict", "message": str(bce)}, status=409)
    except Exception as e:
        logger.error(f"Ошибка при создании ручной записи мастером: {e}", exc_info=True)
        return web.json_response({"ok": False, "error": str(e)}, status=500)



# =============================================================================
# API ФИЛИАЛОВ (BRANCHES)
# =============================================================================

async def handle_get_branches(request: web.Request) -> web.Response:
    """API: Получение списка всех филиалов барбершопа"""
    try:
        branches = await get_branches(active_only=True)
        return web.json_response(branches)
    except Exception as e:
        logger.error(f"Ошибка при получении филиалов: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_branch(request: web.Request) -> web.Response:
    """API: Добавление нового филиала с проверкой лимита тарифа"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        existing_branches = await get_branches(active_only=True)
        can_add, err_msg = await check_can_add_branch(len(existing_branches))
        if not can_add:
            return web.json_response({"ok": False, "error": err_msg, "code": "tier_limit_reached"}, status=403)

        data = await request.json()
        name = (data.get("name") or "").strip()
        address = (data.get("address") or "").strip()
        landmark = (data.get("landmark") or "").strip()
        phone = (data.get("phone") or "+998 97 012-55-55").strip()

        if not name or not address:
            return web.json_response({"error": "Название и адрес обязательны"}, status=400)

        branch_id = await create_branch(name=name, address=address, landmark=landmark, phone=phone)
        return web.json_response({"ok": True, "id": branch_id})
    except Exception as e:
        logger.error(f"Ошибка при создании филиала: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# API БРОНИРОВАНИЙ (BOOKINGS)
# =============================================================================

async def handle_get_bookings(request: web.Request) -> web.Response:
    """API: Получение списка всех записей клиентов для админки"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        bookings = await get_all_bookings()
        return web.json_response(bookings)
    except Exception as e:
        logger.error(f"Ошибка при получении записей из БД: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_create_booking(request: web.Request) -> web.Response:
    """
    API: Создание новой записи (из клиентского TMA или вручную администратором).
    Проверяет доступность времени с учетом мастера и длительности.
    Автоматически подбирает свободного мастера при выборе «Любой мастер».
    Отправляет Telegram-уведомления клиенту, администратору и назначенному мастеру!
    """
    try:
        data = await request.json()
        full_name = (data.get("full_name") or "").strip()
        raw_booking_time = (data.get("booking_time") or "").strip()
        service = data.get("service") or "Стрижка / Комплекс"
        service_id = data.get("service_id")
        phone = data.get("phone")
        user_id = data.get("user_id") or 0
        username = data.get("username")
        details = data.get("details") or "Онлайн-запись через Web App"

        # Криптографическая валидация Telegram initData, если передана
        init_data_header = request.headers.get("X-Telegram-Init-Data")
        if init_data_header and settings.BOT_TOKEN:
            verified_payload = verify_telegram_webapp_data(init_data_header, settings.BOT_TOKEN)
            if verified_payload and verified_payload.get("user_data"):
                verified_u = verified_payload["user_data"]
                user_id = verified_u.get("id") or user_id
                if verified_u.get("username"):
                    username = f"@{verified_u['username']}"
                if not full_name and verified_u.get("first_name"):
                    full_name = verified_u["first_name"]


        branch_id_raw = data.get("branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw and int(branch_id_raw) > 0 else 1
        branch_name = data.get("branch_name")

        branch_info = await get_branch_by_id(branch_id)
        if branch_info:
            branch_name = branch_info["name"]
            branch_address = branch_info["address"]
        else:
            all_b = await get_branches(active_only=True)
            if all_b:
                branch_info = all_b[0]
                branch_id = branch_info["id"]
                branch_name = branch_info["name"]
                branch_address = branch_info["address"]
            else:
                branch_name = branch_name or "Основной филиал"
                branch_address = ""

        barber_id_raw = data.get("barber_id")
        barber_id = int(barber_id_raw) if barber_id_raw and int(barber_id_raw) > 0 else None
        barber_name = data.get("barber_name")
        duration_minutes = int(data.get("duration_minutes") or 60)

        if not full_name:
            return web.json_response({"error": "Имя клиента обязательно для заполнения"}, status=400)
        if not raw_booking_time:
            return web.json_response({"error": "Дата и время обязательны для заполнения"}, status=400)

        # Валидация привязки мастера к филиалу
        if barber_id:
            chosen_barber = await get_barber_by_id(barber_id)
            if chosen_barber:
                b_branch_id = chosen_barber.get("branch_id")
                if b_branch_id is not None and b_branch_id != branch_id:
                    target_branch_name = chosen_barber.get("branch_name") or f"#{b_branch_id}"
                    return web.json_response({
                        "error": f"Мастер {chosen_barber.get('name')} принимает только в филиале «{target_branch_name}»"
                    }, status=400)

        # Валидация времени и проверка слотов
        target_dt = parse_booking_datetime(raw_booking_time)
        active_bookings = await get_active_bookings()
        barbers = await get_barbers(active_only=True, branch_id=branch_id)

        assigned_barber = None
        if target_dt:
            res = check_slot_availability(
                target_dt=target_dt,
                existing_bookings=active_bookings,
                barber_id=barber_id,
                available_barbers=barbers,
                duration_minutes=duration_minutes
            )
            if not res.is_available:
                return web.json_response({"error": res.error_message}, status=400)

            normalized_time = format_booking_datetime(target_dt)
            # Назначенный мастер (если был выбран конкретный или авто-подбор)
            final_barber_id = res.assigned_barber_id or barber_id
            final_barber_name = res.assigned_barber_name or barber_name
            if final_barber_id and barbers:
                assigned_barber = next((b for b in barbers if b["id"] == final_barber_id), None)
                if assigned_barber and not final_barber_name:
                    final_barber_name = assigned_barber["name"]
        else:
            normalized_time = raw_booking_time
            final_barber_id = barber_id
            final_barber_name = barber_name

        # Проверка защиты VIP-графика
        if assigned_barber is None and final_barber_id:
            assigned_barber = await get_barber_by_id(final_barber_id)
        is_vip_master = is_barber_vip(assigned_barber) if assigned_barber else False
        has_vip_protection = await is_feature_enabled("has_vip_protection")
        booking_status = "pending_vip" if (is_vip_master and has_vip_protection) else "new"

        # Сохранение в базу
        try:
            booking_id = await save_booking(
                user_id=user_id,
                username=username,
                full_name=full_name,
                details=details,
                phone=phone,
                service=service,
                booking_time=normalized_time,
                status=booking_status,
                barber_id=final_barber_id,
                barber_name=final_barber_name,
                service_id=service_id,
                branch_id=branch_id,
                branch_name=branch_name,
            )
        except BookingConflictError as bce:
            logger.warning("Collision while creating booking: %s", bce)
            return web.json_response({
                "ok": False,
                "error": "slot_taken",
                "message": str(bce)
            }, status=409)

        bot = request.app.get(BOT_KEY)

        if booking_status == "pending_vip":
            await dispatch_vip_approval_card(
                bot=bot,
                booking_id=booking_id,
                barber_obj=assigned_barber,
                final_barber_name=final_barber_name,
                full_name=full_name,
                username=username,
                user_id=user_id,
                canonical_service=service,
                booking_time=normalized_time,
                phone=phone,
                target_branch_name=branch_name,
                branch_address=branch_address,
            )

            # Сообщение клиенту в личку в Telegram
            role_title = (assigned_barber.get("role_title") if assigned_barber else None) or "VIP-мастер"
            if bot and user_id and user_id != 0:
                client_msg_html = (
                    f"👑 <b>Ваш запрос к мастеру {html.escape(str(final_barber_name))} ({html.escape(role_title)}) отправлен на личное согласование!</b>\n\n"
                    f"⏰ <b>Время:</b> {html.escape(str(normalized_time))}\n"
                    f"✂️ <b>Услуга:</b> {html.escape(str(service))}\n"
                    f"📍 <b>Филиал:</b> {html.escape(str(branch_name))}\n\n"
                    f"{html.escape(role_title)} лично согласует слот в течение 15 минут, и вам сразу придет подтверждение! 🙏"
                )
                try:
                    await bot.send_message(chat_id=user_id, text=client_msg_html, parse_mode="HTML")
                except Exception:
                    plain_client = (
                        f"👑 Ваш запрос к мастеру {final_barber_name} ({role_title}) отправлен на личное согласование!\n\n"
                        f"⏰ Время: {normalized_time}\n"
                        f"✂️ Услуга: {service}\n"
                        f"📍 Филиал: {branch_name}\n\n"
                        f"{role_title} лично согласует слот в течение 15 минут, и вам сразу придет подтверждение! 🙏"
                    )
                    try:
                        await bot.send_message(chat_id=user_id, text=plain_client, parse_mode=None)
                    except Exception as e:
                        logger.warning("Failed to notify client on VIP booking: %s", e)

            return web.json_response({
                "ok": True,
                "id": booking_id,
                "booking_time": normalized_time,
                "barber_id": final_barber_id,
                "barber_name": final_barber_name,
                "branch_id": branch_id,
                "branch_name": branch_name,
                "status": "pending_vip",
                "message": f"Запрос к мастеру {final_barber_name} ({role_title}) отправлен на личное согласование."
            })

        # 1. Мгновенное подтверждение клиенту в личный чат Telegram
        if bot and user_id and user_id != 0:
            branch_phone = (branch_info.get("phone") if branch_info else None) or await get_salon_setting("salon_phone", settings.SALON_PHONE)
            master_line = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            client_msg = (
                f"💈 **Вы успешно записаны в {settings.SALON_TYPE} {settings.SALON_SHORT_NAME}!**\n\n"
                f"✂️ **Услуга:** {service}\n"
                f"{master_line}"
                f"⏰ **Время:** {normalized_time}\n"
                f"📍 **Филиал:** {branch_name}\n"
                f"🏢 **Адрес:** {branch_address}\n"
                f"📞 **Телефон:** {branch_phone}\n\n"
                "Ждем вас! Если потребуется отменить запись — вы можете сделать это прямо через онлайн-меню или написав мне в чат."
            )
            try:
                await bot.send_message(chat_id=user_id, text=client_msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Не удалось отправить подтверждение клиенту {user_id}: {e}")

        # 2. Уведомление администратору салона
        clean_un = (username or "").replace("@", "").strip()
        tg_un_md = f"📱 **TG Username:** @{clean_un}\n" if clean_un else (f"📱 **TG Username:** отсутствует (ID: `{user_id}`)\n" if user_id else "📱 **TG Username:** отсутствует\n")

        if bot and settings.ADMIN_CHAT_ID:
            master_str = f"💈 **Мастер:** {final_barber_name}\n" if final_barber_name else ""
            admin_msg = (
                "💈 **НОВАЯ ОНЛАЙН-ЗАПИСЬ (из Web App)!**\n\n"
                f"👤 **Клиент:** {full_name}\n"
                f"{tg_un_md}"
                f"{master_str}"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📍 **Филиал:** {branch_name} ({branch_address})\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                "📱 _Подтвердите или отклоните кнопками ниже:_"
            )
            try:
                await bot.send_message(
                    chat_id=settings.ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode="Markdown",
                    reply_markup=get_booking_action_keyboard(booking_id)
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление админу: {e}")

        # 3. Мгновенное push-уведомление назначенному мастеру в Telegram
        if bot and assigned_barber and assigned_barber.get("telegram_id"):
            master_alert = (
                "💈 **У ВАС НОВАЯ ЗАПИСЬ НА СМЕНУ!**\n\n"
                f"👤 **Клиент:** {full_name}\n"
                f"{tg_un_md}"
                f"✂️ **Услуга:** {service}\n"
                f"⏰ **Время:** {normalized_time}\n"
                f"📍 **Филиал:** {branch_name} ({branch_address})\n"
                f"📞 **Телефон:** {phone or 'Не указан'}\n\n"
                "📱 _Подтвердите или отклоните прямо сейчас:_"
            )
            try:
                await bot.send_message(
                    chat_id=assigned_barber["telegram_id"],
                    text=master_alert,
                    parse_mode="Markdown",
                    reply_markup=get_booking_action_keyboard(booking_id)
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление мастеру: {e}")

        return web.json_response({
            "ok": True,
            "id": booking_id,
            "booking_time": normalized_time,
            "barber_id": final_barber_id,
            "barber_name": final_barber_name,
            "branch_id": branch_id,
            "branch_name": branch_name,
            "status": "new",
        })

    except Exception as e:
        logger.error(f"Ошибка при создании записи через API: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_update_status(request: web.Request) -> web.Response:
    """API: Смена статуса записи (new, confirmed, cancelled) с авто-уведомлением клиенту"""
    try:
        booking_id = int(request.match_info["id"])
        data = await request.json()
        new_status = data.get("status")

        if not new_status:
            return web.json_response({"error": "Статус не передан"}, status=400)

        # Получаем данные брони перед обновлением
        booking = await get_booking_by_id(booking_id)
        if not booking:
            return web.json_response({"error": "Запись не найдена"}, status=404)

        success = await update_booking_status(booking_id, new_status)
        if not success:
            return web.json_response({"error": "Не удалось обновить статус в БД"}, status=500)

        # Двустороннее оповещение клиента в Telegram при смене статуса администратором
        bot = request.app.get(BOT_KEY)
        client_tg_id = booking.get("user_id")

        if bot and client_tg_id and client_tg_id != 0:
            b_time = booking.get("booking_time") or ""
            service = booking.get("service") or "Стрижка"
            barber = booking.get("barber_name") or "Мастер"

            if new_status == "confirmed":
                b_branch_id = booking.get("branch_id") or 1
                branch_info = await get_branch_by_id(b_branch_id)
                branch_title = booking.get("branch_name") or (branch_info["name"] if branch_info else settings.SALON_SHORT_NAME)
                branch_addr = branch_info["address"] if branch_info else "г. Ташкент"
                branch_phone = (branch_info.get("phone") if branch_info else None) or await get_salon_setting("salon_phone", settings.SALON_PHONE)
                msg = (
                    f"✅ **Ваша запись в {settings.SALON_SHORT_NAME} ПОДТВЕРЖДЕНА!**\n\n"
                    f"✂️ **Услуга:** {service}\n"
                    f"💈 **Мастер:** {barber}\n"
                    f"⏰ **Время:** {b_time}\n"
                    f"📍 **Филиал:** {branch_title}\n"
                    f"🏢 **Адрес:** {branch_addr}\n"
                    f"📞 **Телефон:** {branch_phone}\n\n"
                    "Мы уже ждем вас к назначенному времени, ака! 💈"
                )
                try:
                    await bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление о подтверждении клиенту {client_tg_id}: {e}")

            elif new_status == "cancelled":
                try:
                    from services.booking_service import revoke_vip_approval_cards
                    await revoke_vip_approval_cards(bot=bot, booking_id=booking_id, reason="отменена администратором")
                except Exception as e_rev:
                    logger.warning("Failed to revoke vip cards in handle_update_status: %s", e_rev)

                msg = (
                    f"ℹ️ **Ваша запись в {settings.SALON_SHORT_NAME} была отменена.**\n\n"
                    f"⏰ Время: {b_time}\n"
                    f"✂️ Услуга: {service}\n\n"
                    "Если вы хотите выбрать другое время или мастера — просто откройте онлайн-меню или напишите мне в чат."
                )
                try:
                    await bot.send_message(chat_id=client_tg_id, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление об отмене клиенту {client_tg_id}: {e}")

        return web.json_response({"ok": True, "status": new_status})
    except Exception as e:
        logger.error(f"Ошибка при смене статуса записи: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_my_booking(request: web.Request) -> web.Response:
    """API: Получение активной записи конкретного пользователя для клиентского TMA"""
    try:
        user_id_raw = request.query.get("user_id")
        if not user_id_raw or not user_id_raw.isdigit():
            return web.json_response({"booking": None})

        user_id = int(user_id_raw)
        booking = await get_user_active_booking(user_id)
        return web.json_response({"booking": booking})
    except Exception as e:
        logger.error(f"Ошибка при получении записи пользователя: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_cancel_my_booking(request: web.Request) -> web.Response:
    """API: Отмена текущей активной брони клиентом через TMA кнопку"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        booking_id = data.get("booking_id")

        if not user_id:
            return web.json_response({"error": "user_id обязателен"}, status=400)

        cancelled = await cancel_user_booking(user_id=int(user_id), booking_id=int(booking_id) if booking_id else None)
        if not cancelled:
            return web.json_response({"error": "Активная бронь не найдена"}, status=404)

        bot = request.app.get(BOT_KEY)
        if bot:
            if cancelled.get("id"):
                try:
                    from services.booking_service import revoke_vip_approval_cards
                    await revoke_vip_approval_cards(bot=bot, booking_id=cancelled["id"], reason="отменена клиентом через Web App")
                except Exception as e_rev:
                    logger.warning("Failed to revoke vip cards in handle_cancel_my_booking: %s", e_rev)

            if settings.ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=(
                            f"🔔 **ОТМЕНА ЗАПИСИ (через Client TMA):**\n\n"
                            f"👤 **Клиент:** {cancelled['full_name']} (@{cancelled.get('username') or 'нет'})\n"
                            f"💈 **Мастер:** {cancelled.get('barber_name') or 'Любой'}\n"
                            f"✂️ **Услуга:** {cancelled['service']}\n"
                            f"⏰ **Было забронировано:** {cancelled['booking_time']}\n\n"
                            "🟢 _Окно снова открыто для записи!_"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить уведомление админу: {e}")

        return web.json_response({"ok": True, "cancelled": cancelled})
    except Exception as e:
        logger.error(f"Ошибка при отмене брони через API: {e}")
        return web.json_response({"error": str(e)}, status=500)


# =============================================================================
# ПУЛЬТ SUPER-ADMIN'А (SAAS MULTI-TENANT HUB & BILLING)
# =============================================================================

async def handle_super_admin_page(request: web.Request) -> web.Response:
    """Редирект на центральный SaaS Command Center (hub.asatkhanov.uz)"""
    hub_url = getattr(settings, "SAAS_HUB_URL", "https://hub.asatkhanov.uz")
    raise web.HTTPFound(hub_url)


async def handle_super_auth(request: web.Request) -> web.Response:
    """Аутентификация суперадмина по паролю из .env"""
    try:
        data = await request.json()
        password = str(data.get("password", "")).strip()
    except Exception:
        password = ""

    super_pw = getattr(settings, "SUPER_ADMIN_PASSWORD", "")
    if super_pw and password == super_pw:
        resp = web.json_response({"ok": True, "token": super_pw})
        resp.set_cookie("super_admin_token", super_pw, max_age=86400 * 30, httponly=False)
        return resp
    return web.json_response({"error": "Неверный пароль суперадминистратора"}, status=401)


async def handle_super_get_tenants(request: web.Request) -> web.Response:
    """Получение списка всех заведений сети со статусами подписок для дашборда"""
    if not is_super_admin_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    tenants = await get_all_tenants()
    now_dt = get_now_tashkent().replace(tzinfo=None)
    enriched = []
    for t in tenants:
        status, info = calculate_subscription_status(t, now=now_dt)
        item = dict(t)
        item.update(info)
        item["status"] = status.value
        enriched.append(item)

    return web.json_response(enriched)


async def handle_super_create_tenant(request: web.Request) -> web.Response:
    """Создание/подключение нового заведения в сеть"""
    if not is_super_admin_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Некорректный JSON"}, status=400)

    slug = str(data.get("tenant_slug", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    if not slug or not name:
        return web.json_response({"error": "Слаг и название салона обязательны"}, status=400)

    try:
        new_id = await create_tenant(data)
        return web.json_response({"ok": True, "id": new_id})
    except Exception as e:
        logger.error(f"Ошибка создания арендатора: {e}")
        return web.json_response({"error": str(e)}, status=400)


async def handle_super_extend_30(request: web.Request) -> web.Response:
    """Продление подписки салона на 30 дней в 1 клик (по id или slug)"""
    if not is_super_admin_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    tenant_identifier = request.match_info["id"]
    updated = await extend_tenant_subscription_30_days(tenant_identifier)
    if not updated:
        return web.json_response({"error": "Салон не найден"}, status=404)

    now_dt = get_now_tashkent().replace(tzinfo=None)
    status, info = calculate_subscription_status(updated, now=now_dt)
    item = dict(updated)
    item.update(info)
    item["status"] = status.value
    return web.json_response({"ok": True, "tenant": item})


async def handle_super_toggle_active(request: web.Request) -> web.Response:
    """Переключение ручного Kill Switch для салона (по id или slug)"""
    if not is_super_admin_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    tenant_identifier = request.match_info["id"]

    target_active = None
    if request.can_read_body:
        try:
            body = await request.json()
            if isinstance(body, dict):
                if "action" in body:
                    target_active = 1 if body["action"] == "unlock" else 0
                elif "activate" in body:
                    target_active = 1 if body["activate"] else 0
                elif "is_active" in body:
                    target_active = 1 if body["is_active"] else 0
        except Exception:
            pass

    ok = await toggle_tenant_active(tenant_identifier, is_active=target_active)
    if not ok:
        return web.json_response({"error": "Не удалось обновить статус"}, status=400)

    updated = await get_tenant_by_id_or_slug(tenant_identifier)
    now_dt = get_now_tashkent().replace(tzinfo=None)
    status, info = calculate_subscription_status(updated, now=now_dt)
    item = dict(updated)
    item.update(info)
    item["status"] = status.value
    return web.json_response({"ok": True, "tenant": item})


async def handle_super_sync_subscription(request: web.Request) -> web.Response:
    """
    Эндпоинт двухсторонней синхронизации параметров подписки и тарифа из центрального SaaS Hub.
    Обновляет дату окончания, абонплату (monthly_fee), статус рубильника и контакты.
    """
    if not is_super_admin_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    tenant_identifier = request.match_info["id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Некорректный JSON"}, status=400)

    tenant = await get_tenant_by_id_or_slug(tenant_identifier)
    if not tenant:
        return web.json_response({"error": "Салон не найден"}, status=404)

    tenant_id = tenant["id"]
    updates = {}
    if "expires_at" in data:
        updates["expires_at"] = str(data["expires_at"]).strip()
    if "is_active" in data:
        updates["is_active"] = 1 if data["is_active"] else 0
    if "name" in data:
        updates["name"] = str(data["name"]).strip()
    if "contact_phone" in data:
        updates["contact_phone"] = str(data["contact_phone"]).strip()
        await set_salon_setting("salon_phone", str(data["contact_phone"]).strip())
    if "contact_name" in data:
        updates["contact_name"] = str(data["contact_name"]).strip()
    if "monthly_fee" in data:
        fee_str = str(int(data["monthly_fee"]))
        await set_salon_setting("subscription_price", fee_str)
    if "tier" in data:
        t_val = str(data["tier"]).strip().upper()
        if t_val in ("START", "PRO", "ENTERPRISE"):
            updates["tier"] = t_val

    if updates:
        await update_tenant_subscription(tenant_id, updates)

    updated = await get_tenant_by_id_or_slug(tenant_identifier)
    now_dt = get_now_tashkent().replace(tzinfo=None)
    status, info = calculate_subscription_status(updated, now=now_dt)
    item = dict(updated)
    item.update(info)
    item["status"] = status.value
    return web.json_response({"ok": True, "tenant": item})


async def handle_get_subscription_info(request: web.Request) -> web.Response:
    """API: Информация о подписке салона, тарифе и P2P платёжные ссылки"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    tenant = await get_tenant_by_id_or_slug(settings.TENANT_SLUG)
    if not tenant:
        tenants = await get_all_tenants()
        tenant = tenants[0] if tenants else None

    tier = await get_tenant_tier(settings.TENANT_SLUG) if tenant else SubscriptionTier.PRO
    limits = get_tier_limits(tier)

    now_dt = get_now_tashkent().replace(tzinfo=None)
    status, info = calculate_subscription_status(tenant, now=now_dt) if tenant else (None, {})

    settings_dict = await get_all_salon_settings()
    raw_card = settings_dict.get("p2p_card_number", "8600 1234 5678 9012")
    card_number = raw_card.replace(" ", "")
    card_holder = settings_dict.get("p2p_card_holder", settings.OWNER_NAME)
    amount = int(settings_dict.get("subscription_price", "500000"))

    return web.json_response({
        "ok": True,
        "tenant": tenant,
        "tier": tier.value,
        "tier_label": limits.get("label", tier.value),
        "status": status.value if status else "active",
        "info": info,
        "pricing": {
            "amount": amount,
            "currency": "UZS",
            "formatted_amount": f"{amount:,} сум".replace(",", " "),
            "card_display": raw_card,
            "card_holder": card_holder,
        }
    })


async def handle_upload_receipt(request: web.Request) -> web.Response:
    """API: Загрузка скриншота квитанции, распознавание через Gemini OCR и сохранение"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "receipt_file":
            return web.json_response({"ok": False, "error": "file_required", "message": "Файл квитанции обязателен"}, status=400)

        filename = field.filename or "receipt.jpg"
        file_bytes = await field.read()

        if len(file_bytes) == 0:
            return web.json_response({"ok": False, "error": "empty_file", "message": "Файл пуст"}, status=400)

        if len(file_bytes) > 5 * 1024 * 1024:
            return web.json_response({"ok": False, "error": "file_too_large", "message": "Размер файла не должен превышать 5 МБ"}, status=400)

        mime_type = "image/jpeg"
        if filename.lower().endswith(".png"):
            mime_type = "image/png"
        elif filename.lower().endswith(".webp"):
            mime_type = "image/webp"

        # Запуск распознавания через Gemini OCR
        from services.ocr_service import ocr_service
        receipt = await ocr_service.parse_receipt(file_bytes, mime_type=mime_type)

        if not receipt.is_valid_receipt:
            return web.json_response({
                "ok": False,
                "error": "invalid_receipt",
                "message": "Изображение не распознано как чек об успешной оплате. Пожалуйста, прикрепите четкий скриншот квитанции."
            }, status=400)

        # Сохранение в базу с защитой от повторного использования номера транзакции
        try:
            receipt_id = await save_receipt_log(
                tenant_id=1,
                transaction_id=receipt.transaction_id,
                amount=receipt.amount,
                payment_method=receipt.payment_system or "p2p",
                sender_name=receipt.sender_name,
                recipient_card_last4=receipt.recipient_card_last4,
                raw_ocr_json=receipt.model_dump_json(),
                status="pending_verification"
            )
        except ValueError as ve:
            return web.json_response({
                "ok": False,
                "error": "duplicate_receipt",
                "message": str(ve)
            }, status=400)

        # Оповещение суперадмина в личный Telegram
        bot = request.app.get(BOT_KEY)
        target_super = getattr(settings, "SUPER_ADMIN_CHAT_ID", None) or settings.ADMIN_CHAT_ID
        if bot and target_super:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Подтвердить +30 дн", callback_data=f"approve_receipt:{receipt_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_receipt:{receipt_id}")
            ]])
            formatted_amount = f"{receipt.amount:,} сум".replace(",", " ")
            txt = (
                "💳 **ПОСТУПИЛ ЧЕК НА ОПЛАТУ ПОДПИСКИ!**\n\n"
                f"🏢 **Заведение:** {settings.SALON_NAME}\n"
                f"💰 **Сумма:** {formatted_amount}\n"
                f"🧾 **Чек / Транзакция:** `#{receipt.transaction_id}`\n"
                f"📱 **Система:** {receipt.payment_system.upper()}\n"
                f"👤 **Отправитель:** {receipt.sender_name or '—'}\n"
                f"💳 **Карта получателя:** *{receipt.recipient_card_last4 or '—'}\n"
                f"🕒 **Время перевода:** {receipt.timestamp or '—'}\n\n"
                "Подтвердите продление подписки нажатием кнопки ниже:"
            )
            try:
                await bot.send_message(chat_id=target_super, text=txt, reply_markup=kb, parse_mode="Markdown")
            except Exception as tg_err:
                logger.warning("Не удалось отправить уведомление о чеке суперадмину: %s", tg_err)

        return web.json_response({
            "ok": True,
            "message": "Квитанция успешно проверена ИИ и отправлена на подтверждение администратору.",
            "receipt": receipt.model_dump(),
            "receipt_id": receipt_id
        })
    except Exception as e:
        logger.error("Ошибка в handle_upload_receipt: %s", e)
        return web.json_response({"ok": False, "error": "server_error", "message": "Ошибка обработки файла"}, status=500)


# =============================================================================
# API ГРАФИКА (ШАХМАТКА) И АНАЛИТИКИ
# =============================================================================

async def handle_get_admin_schedule(request: web.Request) -> web.Response:
    """API: Сводная шахматка занятости мастеров на выбранную дату (для управляющего)"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        date_str = request.query.get("date")
        if not date_str:
            date_str = get_now_tashkent().strftime("%Y-%m-%d")

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return web.json_response({"error": "Неверный формат даты, ожидается YYYY-MM-DD"}, status=400)

        branch_id_raw = request.query.get("branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw and branch_id_raw.isdigit() else None

        barbers = await get_barbers(active_only=True, branch_id=branch_id)
        services = await get_services(active_only=True)
        services_map = {s["id"]: s for s in services}
        all_bookings = await get_all_bookings()

        day_bookings = []
        for b in all_bookings:
            if b.get("status") == "cancelled":
                continue
            bt = (b.get("booking_time") or "").strip()
            if bt.startswith(date_str):
                day_bookings.append(b)

        # 30-минутные слоты с 09:00 до 21:30
        slots = []
        cur_h = 9
        cur_m = 0
        while cur_h < 22:
            slots.append(f"{cur_h:02d}:{cur_m:02d}")
            cur_m += 30
            if cur_m >= 60:
                cur_m = 0
                cur_h += 1

        barbers_data = []
        for b in barbers:
            is_working = is_barber_working_on_date(b, target_date)
            barbers_data.append({
                "id": b["id"],
                "name": b["name"],
                "is_vip": is_barber_vip(b),
                "is_working": is_working,
                "photo_url": b.get("photo_url") or "",
            })

        # Матрица: barber_id -> { slot_str -> booking_info or None }
        matrix = {}
        for b in barbers:
            b_id = b["id"]
            matrix[b_id] = {}
            b_bookings = [bk for bk in day_bookings if bk.get("barber_id") == b_id]

            for s_time in slots:
                slot_dt = datetime.strptime(f"{date_str} {s_time}", "%Y-%m-%d %H:%M")
                slot_end = slot_dt + timedelta(minutes=30)

                matched_b = None
                for bk in b_bookings:
                    b_raw = (bk.get("booking_time") or "").strip()
                    parts = b_raw.split()
                    if len(parts) >= 2:
                        try:
                            bk_start = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M")
                            dur = 45
                            s_id = bk.get("service_id")
                            if s_id and s_id in services_map:
                                dur = services_map[s_id].get("duration_min") or 45
                            bk_end = bk_start + timedelta(minutes=dur)

                            if max(slot_dt, bk_start) < min(slot_end, bk_end):
                                matched_b = bk
                                break
                        except Exception:
                            if s_time in b_raw:
                                matched_b = bk
                                break
                    elif s_time in b_raw:
                        matched_b = bk
                        break

                if matched_b:
                    matrix[b_id][s_time] = {
                        "id": matched_b["id"],
                        "client_name": matched_b.get("full_name") or "Клиент",
                        "phone": matched_b.get("phone") or "",
                        "service": matched_b.get("service") or "",
                        "status": matched_b.get("status") or "new",
                        "booking_time": matched_b.get("booking_time") or "",
                        "details": matched_b.get("details") or "",
                    }
                else:
                    matrix[b_id][s_time] = None

        return web.json_response({
            "ok": True,
            "date": date_str,
            "slots": slots,
            "barbers": barbers_data,
            "matrix": matrix
        })
    except Exception as e:
        logger.error(f"Ошибка при получении шахматки мастеров: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_admin_analytics(request: web.Request) -> web.Response:
    """API: Аналитика выручки, загрузки мастеров и метрик салона (для админки и владельца)"""
    if not is_admin_or_super(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        now_dt = get_now_tashkent()
        today_str = now_dt.strftime("%Y-%m-%d")

        start_of_week = now_dt.date() - timedelta(days=now_dt.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        all_bookings = await get_all_bookings()
        services = await get_services()
        service_prices = {}
        for s in services:
            s_name = (s.get("name") or "").strip().lower()
            s_price = int(s.get("price") or 0)
            if s_name:
                service_prices[s_name] = s_price
            if s.get("id"):
                service_prices[f"id_{s['id']}"] = s_price

        def get_booking_price(bk) -> int:
            if bk.get("service_id") and f"id_{bk['service_id']}" in service_prices:
                return service_prices[f"id_{bk['service_id']}"]
            srv_name = (bk.get("service") or "").strip().lower()
            if srv_name in service_prices:
                return service_prices[srv_name]
            for sn, pr in service_prices.items():
                if not sn.startswith("id_") and (sn in srv_name or srv_name in sn):
                    return pr
            return 150000

        today_bookings = []
        week_bookings = []
        valid_bookings = [b for b in all_bookings if b.get("status") != "cancelled"]

        status_counts = {"new": 0, "pending_vip": 0, "confirmed": 0, "completed": 0, "cancelled": 0}
        for b in all_bookings:
            st = b.get("status") or "new"
            status_counts[st] = status_counts.get(st, 0) + 1

            bt = (b.get("booking_time") or "").strip()
            parts = bt.split()
            if parts:
                b_date_str = parts[0]
                try:
                    b_date = datetime.strptime(b_date_str, "%Y-%m-%d").date()
                    if b_date == now_dt.date() and st != "cancelled":
                        today_bookings.append(b)
                    if start_of_week <= b_date <= end_of_week and st != "cancelled":
                        week_bookings.append(b)
                except Exception:
                    if today_str in bt and st != "cancelled":
                        today_bookings.append(b)

        today_revenue = sum(get_booking_price(b) for b in today_bookings)
        week_revenue = sum(get_booking_price(b) for b in week_bookings)

        avg_check = int(week_revenue / len(week_bookings)) if week_bookings else (
            int(today_revenue / len(today_bookings)) if today_bookings else 150000
        )

        barber_stats_map = {}
        for b in valid_bookings:
            b_name = b.get("barber_name") or "Любой мастер"
            if b_name not in barber_stats_map:
                barber_stats_map[b_name] = {"name": b_name, "count": 0, "revenue": 0}
            barber_stats_map[b_name]["count"] += 1
            barber_stats_map[b_name]["revenue"] += get_booking_price(b)

        top_barbers = sorted(barber_stats_map.values(), key=lambda x: x["count"], reverse=True)

        service_stats_map = {}
        for b in valid_bookings:
            s_name = b.get("service") or "Стрижка"
            if s_name not in service_stats_map:
                service_stats_map[s_name] = {"name": s_name, "count": 0, "revenue": 0}
            service_stats_map[s_name]["count"] += 1
            service_stats_map[s_name]["revenue"] += get_booking_price(b)

        top_services = sorted(service_stats_map.values(), key=lambda x: x["count"], reverse=True)[:5]

        unique_clients = set()
        for b in all_bookings:
            cid = b.get("user_id") or b.get("phone") or b.get("full_name")
            if cid:
                unique_clients.add(cid)

        return web.json_response({
            "ok": True,
            "today_revenue": today_revenue,
            "today_revenue_formatted": f"{today_revenue:,} сум".replace(",", " "),
            "week_revenue": week_revenue,
            "week_revenue_formatted": f"{week_revenue:,} сум".replace(",", " "),
            "today_bookings_count": len(today_bookings),
            "week_bookings_count": len(week_bookings),
            "total_bookings_count": len(all_bookings),
            "average_check": avg_check,
            "average_check_formatted": f"{avg_check:,} сум".replace(",", " "),
            "total_clients": len(unique_clients),
            "status_counts": status_counts,
            "top_barbers": top_barbers,
            "top_services": top_services
        })
    except Exception as e:
        logger.error(f"Ошибка при расчете аналитики: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)



# =============================================================================
# ПРИЛОЖЕНИЕ AIOHTTP И РОУТЕР
# =============================================================================

def create_web_app(bot: Bot = None) -> web.Application:
    """Фабрика создания приложения aiohttp для TMA"""
    app = web.Application(middlewares=[subscription_middleware])
    app[BOT_KEY] = bot

    # Статические файлы (аватарки барберов, логотипы, иконки)
    if os.path.exists(STATIC_DIR_PATH):
        app.router.add_static("/static", STATIC_DIR_PATH, name="static")

    # TMA страницы
    app.router.add_get("/", handle_client)
    app.router.add_get("/book", handle_client)
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/webapp", handle_admin)
    app.router.add_get("/barber", handle_barber_page)
    app.router.add_get("/super-admin", handle_super_admin_page)

    # Super-Admin API
    app.router.add_post("/api/super/auth", handle_super_auth)
    app.router.add_get("/api/super/tenants", handle_super_get_tenants)
    app.router.add_post("/api/super/tenants", handle_super_create_tenant)
    app.router.add_post("/api/super/tenants/{id}/extend-30", handle_super_extend_30)
    app.router.add_post("/api/super/tenants/{id}/toggle-active", handle_super_toggle_active)
    app.router.add_post("/api/super/tenants/{id}/sync-subscription", handle_super_sync_subscription)

    # API маршруты
    app.router.add_get("/api/config", handle_get_config)
    app.router.add_get("/api/user-role", handle_get_user_role)
    app.router.add_get("/api/slots", handle_get_slots)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_update_settings)

    # Услуги
    app.router.add_get("/api/services", handle_get_services)
    app.router.add_post("/api/services", handle_create_service)
    app.router.add_post("/api/services/{id}/update", handle_update_service)
    app.router.add_post("/api/services/{id}/delete", handle_delete_service)

    # Барберы
    app.router.add_get("/api/barbers", handle_get_barbers)
    app.router.add_post("/api/barbers", handle_create_barber)
    app.router.add_post("/api/barbers/{id}/update", handle_update_barber)
    app.router.add_post("/api/barbers/{id}/toggle-shift", handle_toggle_barber_shift)
    app.router.add_post("/api/barbers/{id}/toggle-vip", handle_toggle_barber_vip)
    app.router.add_post("/api/barbers/{id}/delete", handle_delete_barber)

    # Кабинет мастера
    app.router.add_get("/api/barber/my-schedule", handle_get_barber_my_schedule)
    app.router.add_post("/api/barber/manual-booking", handle_barber_manual_booking)

    # Филиалы
    app.router.add_get("/api/branches", handle_get_branches)
    app.router.add_post("/api/branches", handle_create_branch)

    # Аутентификация
    app.router.add_post("/api/auth/login", handle_auth_login)

    # Биллинг и квитанции
    app.router.add_get("/api/billing/subscription-info", handle_get_subscription_info)
    app.router.add_post("/api/billing/upload-receipt", handle_upload_receipt)

    # Бронирования
    app.router.add_get("/api/bookings", handle_get_bookings)
    app.router.add_post("/api/bookings", handle_create_booking)
    app.router.add_post("/api/bookings/{id}/status", handle_update_status)
    app.router.add_get("/api/my-booking", handle_get_my_booking)
    app.router.add_post("/api/my-booking/cancel", handle_cancel_my_booking)

    # Шахматка и аналитика
    app.router.add_get("/api/admin/schedule", handle_get_admin_schedule)
    app.router.add_get("/api/admin/analytics", handle_get_admin_analytics)


    return app



async def start_web_server(host: str = None, port: int = None, bot: Bot = None) -> web.AppRunner:
    """Запуск асинхронного веб-сервера aiohttp на общем event loop"""
    h = host or settings.WEBAPP_HOST or "0.0.0.0"
    p = port or settings.WEBAPP_PORT or 8000

    app = create_web_app(bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, h, p)
    await site.start()
    logger.info("🌐 Сервер Telegram Mini App успешно запущен: http://%s:%s", h, p)
    return runner


if __name__ == "__main__":
    from database import init_db
    import asyncio
    asyncio.run(init_db())
    web.run_app(create_web_app(), host="127.0.0.1", port=8000)
