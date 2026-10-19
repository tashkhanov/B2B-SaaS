"""
Модульные роутеры Telegram-бота Brohouse Barbershop.
"""

from aiogram import Router

common_router = Router(name="common_router")
barber_router = Router(name="barber_router")
admin_router = Router(name="admin_router")
client_router = Router(name="client_router")

__all__ = [
    "common_router",
    "barber_router",
    "admin_router",
    "client_router",
]
