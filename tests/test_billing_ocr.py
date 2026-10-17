import os
import io
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp.test_utils import TestClient, TestServer
from aiogram.types import CallbackQuery, User, Message
from config import settings
from database import (
    init_db,
    get_receipt_log_by_id,
    get_tenant_subscription,
    update_receipt_log_status,
)
from services.ocr_service import PaymentReceipt
from web_app import create_web_app
from bot.handlers import handle_approve_receipt, handle_reject_receipt

TEST_DB = "test_billing_ocr.db"


@pytest.mark.asyncio
async def test_billing_subscription_info_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "super_pwd_123")
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    app = create_web_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Без авторизации -> 401
        res_unauth = await client.get("/api/billing/subscription-info")
        assert res_unauth.status == 401

        # С суперадмин токеном -> 200
        headers = {"X-Super-Admin-Token": "super_pwd_123"}
        res = await client.get("/api/billing/subscription-info", headers=headers)
        assert res.status == 200
        data = await res.json()
        assert data["ok"] is True
        assert "pricing" in data
        assert "payme_link" in data["pricing"]
        assert "click_link" in data["pricing"]
        assert "card_display" in data["pricing"]
        assert data["pricing"]["amount"] == 500000

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


@pytest.mark.asyncio
async def test_receipt_upload_and_replay_protection(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "SUPER_ADMIN_PASSWORD", "super_pwd_123")
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    mock_bot = AsyncMock()
    app = create_web_app(bot=mock_bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        headers = {"X-Super-Admin-Token": "super_pwd_123"}

        # 1. Загрузка фальшивого/невалидного чека
        with patch("services.ocr_service.ocr_service.parse_receipt", new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = PaymentReceipt(is_valid_receipt=False)
            
            data = {"receipt_file": io.BytesIO(b"dummy fake image bytes")}
            res_invalid = await client.post("/api/billing/upload-receipt", headers=headers, data=data)
            assert res_invalid.status == 400
            res_json = await res_invalid.json()
            assert res_json["error"] == "invalid_receipt"

        # 2. Успешная загрузка валидного чека Payme
        valid_receipt = PaymentReceipt(
            transaction_id="TX_987654321",
            amount=1000000,
            timestamp="2026-10-18 14:00:00",
            recipient_card_last4="1234",
            sender_name="Akmal",
            payment_system="payme",
            is_valid_receipt=True
        )
        with patch("services.ocr_service.ocr_service.parse_receipt", new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = valid_receipt

            data = {"receipt_file": io.BytesIO(b"genuine receipt bytes")}
            res_valid = await client.post("/api/billing/upload-receipt", headers=headers, data=data)
            assert res_valid.status == 200
            res_data = await res_valid.json()
            assert res_data["ok"] is True
            receipt_id = res_data["receipt_id"]
            assert receipt_id > 0

            # Проверяем запись в БД
            rec_db = await get_receipt_log_by_id(receipt_id, db_path=TEST_DB)
            assert rec_db is not None
            assert rec_db["transaction_id"] == "TX_987654321"
            assert rec_db["status"] == "pending_verification"

            # Проверяем, что бот отправил уведомление суперадмину с кнопками
            assert mock_bot.send_message.called

        # 3. Попытка повторной загрузки ТОГО ЖЕ САМОГО ЧЕКА (Replay Attack)
        with patch("services.ocr_service.ocr_service.parse_receipt", new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = valid_receipt

            data_dup = {"receipt_file": io.BytesIO(b"genuine receipt bytes again")}
            res_dup = await client.post("/api/billing/upload-receipt", headers=headers, data=data_dup)
            assert res_dup.status == 400
            dup_data = await res_dup.json()
            assert dup_data["error"] == "duplicate_receipt"
            assert "уже был использован" in dup_data["message"]

    finally:
        await client.close()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


@pytest.mark.asyncio
async def test_telegram_receipt_approval_callback(monkeypatch):
    monkeypatch.setattr(settings, "DB_NAME", TEST_DB)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 999888)
    monkeypatch.setattr(settings, "SUPER_ADMIN_CHAT_ID", 1243374131)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)

    try:
        from database import save_receipt_log
        receipt_id = await save_receipt_log(
            tenant_id=1,
            transaction_id="TX_CALLBACK_TEST",
            amount=1000000,
            payment_method="click",
            db_path=TEST_DB
        )

        initial_sub = await get_tenant_subscription("brohouse", db_path=TEST_DB)
        initial_exp = initial_sub["expires_at"]

        # 1. Посторонний юзер пытается подтвердить -> отказ
        stranger_cb = MagicMock(spec=CallbackQuery)
        stranger_cb.from_user = User(id=55555, is_bot=False, first_name="Stranger")
        stranger_cb.data = f"approve_receipt:{receipt_id}"
        stranger_cb.answer = AsyncMock()

        await handle_approve_receipt(stranger_cb)
        assert "нет прав" in stranger_cb.answer.call_args[0][0]

        # 2. Суперадмин подтверждает -> статус approved и подписка продлена
        super_cb = MagicMock(spec=CallbackQuery)
        super_cb.from_user = User(id=1243374131, is_bot=False, first_name="SuperAdmin")
        super_cb.data = f"approve_receipt:{receipt_id}"
        super_cb.answer = AsyncMock()
        super_cb.message = MagicMock(spec=Message)
        super_cb.message.text = "Чек на оплату"
        super_cb.message.edit_text = AsyncMock()

        await handle_approve_receipt(super_cb)
        super_cb.answer.assert_called_once()
        assert "продлена" in super_cb.answer.call_args[0][0]

        # Проверяем БД
        rec = await get_receipt_log_by_id(receipt_id, db_path=TEST_DB)
        assert rec["status"] == "approved"

        updated_sub = await get_tenant_subscription("brohouse", db_path=TEST_DB)
        assert updated_sub["expires_at"] > initial_exp

        # 3. Тест отклонения другого чека
        rej_id = await save_receipt_log(
            tenant_id=1,
            transaction_id="TX_REJECT_TEST",
            amount=500000,
            payment_method="payme",
            db_path=TEST_DB
        )
        rej_cb = MagicMock(spec=CallbackQuery)
        rej_cb.from_user = User(id=1243374131, is_bot=False, first_name="SuperAdmin")
        rej_cb.data = f"reject_receipt:{rej_id}"
        rej_cb.answer = AsyncMock()
        rej_cb.message = MagicMock(spec=Message)
        rej_cb.message.text = "Чек на оплату"
        rej_cb.message.edit_text = AsyncMock()

        await handle_reject_receipt(rej_cb)
        rej_cb.answer.assert_called_once()
        assert "отклонён" in rej_cb.answer.call_args[0][0].lower()

        rec_rej = await get_receipt_log_by_id(rej_id, db_path=TEST_DB)
        assert rec_rej["status"] == "rejected"

    finally:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
