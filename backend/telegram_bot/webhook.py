"""
Django views for Telegram bot webhook.
"""
import hashlib
import json
import logging

import requests
from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .bot import get_webhook_bot

logger = logging.getLogger(__name__)

# Webhook secret token derived from bot token (for verifying requests from Telegram)
_webhook_secret = None


def get_webhook_secret():
    global _webhook_secret
    configured_secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '').strip()
    if configured_secret:
        _webhook_secret = configured_secret
        return _webhook_secret

    if _webhook_secret is None and settings.TELEGRAM_BOT_TOKEN:
        _webhook_secret = hashlib.sha256(
            settings.TELEGRAM_BOT_TOKEN.encode()
        ).hexdigest()[:32]
    return _webhook_secret


def build_webhook_url() -> str:
    host = getattr(settings, 'WEBHOOK_HOST', '').rstrip('/')
    path = getattr(settings, 'TELEGRAM_WEBHOOK_PATH', '/bot/webhook/')
    if not host:
        return ''
    return f"{host}{path}"


def _telegram_api_call(method: str, payload: dict | None = None) -> dict:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def setup_webhook_url(drop_pending_updates: bool | None = None) -> tuple[bool, dict]:
    """Set Telegram webhook URL and return `(ok, response_json)`."""
    webhook_url = build_webhook_url()
    if not webhook_url:
        return False, {'description': 'WEBHOOK_HOST is not configured'}

    payload = {
        "url": webhook_url,
        "secret_token": get_webhook_secret(),
    }
    if drop_pending_updates is None:
        drop_pending_updates = getattr(settings, 'TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES', False)
    payload["drop_pending_updates"] = bool(drop_pending_updates)

    data = _telegram_api_call("setWebhook", payload=payload)
    return bool(data.get("ok")), data


def delete_webhook(drop_pending_updates: bool = False) -> tuple[bool, dict]:
    data = _telegram_api_call("deleteWebhook", payload={"drop_pending_updates": drop_pending_updates})
    return bool(data.get("ok")), data


def get_webhook_info() -> tuple[bool, dict]:
    data = _telegram_api_call("getWebhookInfo")
    return bool(data.get("ok")), data


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Handle incoming Telegram webhook updates."""
    # Verify secret token header
    secret = get_webhook_secret()
    if secret:
        header_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if header_secret != secret:
            return HttpResponse('Forbidden', status=403)

    try:
        update_data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponse('Bad Request', status=400)

    try:
        bot = get_webhook_bot()
        async_to_sync(bot.process_update)(update_data)
    except Exception as e:
        logger.error("Error processing Telegram update: %s", e, exc_info=True)

    return JsonResponse({'ok': True})
