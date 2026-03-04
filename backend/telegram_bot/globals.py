"""
Global bot state management.

Provides getter/setter functions instead of raw global variables
for safer access across modules.
"""
from aiogram import Bot

_bot_instance: Bot | None = None
_channel_id: str | int | None = None
_group_id: str | int | None = None


def get_bot() -> Bot | None:
    return _bot_instance


def set_bot(bot: Bot | None) -> None:
    global _bot_instance
    _bot_instance = bot


def get_channel_id() -> str | int | None:
    return _channel_id


def set_channel_id(value: str | int | None) -> None:
    global _channel_id
    _channel_id = value


def get_group_id() -> str | int | None:
    return _group_id


def set_group_id(value: str | int | None) -> None:
    global _group_id
    _group_id = value
