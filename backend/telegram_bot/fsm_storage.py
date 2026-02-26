from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DEFAULT_DESTINY, StateType, StorageKey
from asgiref.sync import sync_to_async

from .models import TelegramFSMState


def _to_json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DjangoFSMStorage(BaseStorage):
    """aiogram FSM storage backed by Django ORM."""

    @staticmethod
    def _key_filter(key: StorageKey) -> Dict[str, Any]:
        return {
            "bot_id": int(key.bot_id),
            "chat_id": int(key.chat_id),
            "user_id": int(key.user_id),
            "thread_id": int(key.thread_id) if key.thread_id is not None else None,
            "destiny": key.destiny or DEFAULT_DESTINY,
        }

    @staticmethod
    def _state_value(state: StateType = None) -> Optional[str]:
        if isinstance(state, State):
            return state.state
        return state

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await sync_to_async(self._set_state_sync, thread_sensitive=True)(key, state)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await sync_to_async(self._get_state_sync, thread_sensitive=True)(key)

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await sync_to_async(self._set_data_sync, thread_sensitive=True)(key, data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return await sync_to_async(self._get_data_sync, thread_sensitive=True)(key)

    async def close(self) -> None:
        return None

    def _set_state_sync(self, key: StorageKey, state: StateType = None) -> None:
        filters = self._key_filter(key)
        state_value = self._state_value(state)
        record = TelegramFSMState.objects.filter(**filters).first()

        if state_value is None:
            if not record:
                return
            record.state = None
            if not record.data:
                record.delete()
                return
            record.save(update_fields=["state", "updated_at"])
            return

        if not record:
            TelegramFSMState.objects.create(**filters, state=state_value, data={})
            return

        if record.state != state_value:
            record.state = state_value
            record.save(update_fields=["state", "updated_at"])

    def _get_state_sync(self, key: StorageKey) -> Optional[str]:
        filters = self._key_filter(key)
        state = TelegramFSMState.objects.filter(**filters).values_list("state", flat=True).first()
        return state or None

    def _set_data_sync(self, key: StorageKey, data: Dict[str, Any]) -> None:
        filters = self._key_filter(key)
        payload = _to_json_compatible(data or {})
        record = TelegramFSMState.objects.filter(**filters).first()

        if not payload:
            if not record:
                return
            record.data = {}
            if not record.state:
                record.delete()
                return
            record.save(update_fields=["data", "updated_at"])
            return

        if not record:
            TelegramFSMState.objects.create(**filters, state=None, data=payload)
            return

        record.data = payload
        record.save(update_fields=["data", "updated_at"])

    def _get_data_sync(self, key: StorageKey) -> Dict[str, Any]:
        filters = self._key_filter(key)
        data = TelegramFSMState.objects.filter(**filters).values_list("data", flat=True).first()
        if not isinstance(data, dict):
            return {}
        return data.copy()
