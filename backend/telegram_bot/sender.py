import logging
from pathlib import Path
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _api_url(method: str) -> str:
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN is not configured')
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(chat_id: int | str, text: str, reply_markup: dict | None = None, timeout: int = 10) -> bool:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(_api_url("sendMessage"), json=payload, timeout=timeout)
        if response.status_code >= 400:
            logger.warning("Telegram sendMessage failed: %s", response.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram sendMessage error: %s", exc)
        return False


def send_photo(
    chat_id: int | str,
    photo_path: str | Path,
    caption: str = '',
    reply_markup: dict | None = None,
    timeout: int = 15,
) -> bool:
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "caption": caption,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    try:
        path = Path(photo_path)
        with path.open('rb') as photo_file:
            response = requests.post(
                _api_url("sendPhoto"),
                data=data,
                files={"photo": photo_file},
                timeout=timeout,
            )
        if response.status_code >= 400:
            logger.warning("Telegram sendPhoto failed: %s", response.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram sendPhoto error: %s", exc)
        return False
