import json
import logging
from typing import Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from config import settings

logger = logging.getLogger(__name__)


class PaymentReceipt(BaseModel):
    """Структурированные данные банковского чека/квитанции об оплате."""
    transaction_id: str = Field(
        default="",
        description="Уникальный номер транзакции или квитанции из чека (Payme, Click, Uzum, банк)"
    )
    amount: int = Field(
        default=0,
        description="Сумма транзакции в сумах (целое число, например 1000000)"
    )
    timestamp: Optional[str] = Field(
        default=None,
        description="Дата и время совершения транзакции (например, 2026-10-18 14:32:10)"
    )
    recipient_card_last4: Optional[str] = Field(
        default=None,
        description="Последние 4 цифры номера карты или счета получателя"
    )
    sender_name: Optional[str] = Field(
        default=None,
        description="Имя или инициалы отправителя перевода"
    )
    payment_system: Optional[str] = Field(
        default="unknown",
        description="Платежная система: payme, click, uzum, bank, other"
    )
    is_valid_receipt: bool = Field(
        default=False,
        description="True, если изображение является подлинным чеком успешной оплаты. False, если это не чек или статус платежа 'Ошибка' / 'Отменено'"
    )


class ReceiptOCRService:
    """Сервис распознавания платежных квитанций через Google Gemini Vision."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.GEMINI_MODEL or "gemini-2.5-flash"
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            key = self.api_key or "AIzaSy_dummy_api_key_placeholder"
            self._client = genai.Client(api_key=key)
        return self._client

    @client.setter
    def client(self, value: genai.Client) -> None:
        self._client = value

    async def parse_receipt(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PaymentReceipt:
        """
        Анализирует изображение чека и извлекает метаданные платежа.
        """
        prompt = (
            "Ты — строгий финансовый аудитор анти-фрод системы платежей в Узбекистане.\n"
            "Внимательно изучи приложенное изображение квитанции/чека об оплате (Payme, Click, Uzum Bank, Anorbank, TBC, Apelsin и др.).\n"
            "Твоя задача:\n"
            "1. Определить, является ли это чек об УСПЕШНОМ переводе/оплате (is_valid_receipt = True/False).\n"
            "2. Извлечь уникальный идентификатор транзакции/квитанции (transaction_id). Если номера нет, сгенерируй пустую строку.\n"
            "3. Извлечь точную сумму в сумах (amount) без копеек/тийинов.\n"
            "4. Извлечь дату и время транзакции (timestamp).\n"
            "5. Извлечь последние 4 цифры карты получателя (recipient_card_last4) и имя отправителя (sender_name), если указаны.\n"
            "6. Определить платежную систему (payme, click, uzum, bank, other).\n"
            "Если изображение не является чеком или оплата не удалась — верни is_valid_receipt = False."
        )

        try:
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PaymentReceipt,
                temperature=0.0,
            )

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[image_part, prompt],
                config=config,
            )

            if response and response.text:
                data = json.loads(response.text)
                return PaymentReceipt(**data)
            return PaymentReceipt(is_valid_receipt=False)
        except Exception as e:
            logger.error("Ошибка OCR-распознавания чека в Gemini: %s", e)
            return PaymentReceipt(is_valid_receipt=False)


ocr_service = ReceiptOCRService()
