"""
Django views for Telegram bot webhook.
"""
import asyncio
import hashlib
import json
import logging

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
    if _webhook_secret is None and settings.TELEGRAM_BOT_TOKEN:
        _webhook_secret = hashlib.sha256(
            settings.TELEGRAM_BOT_TOKEN.encode()
        ).hexdigest()[:32]
    return _webhook_secret


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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.process_update(update_data))
        finally:
            loop.close()
    except Exception as e:
        logger.error("Error processing Telegram update: %s", e, exc_info=True)

    return JsonResponse({'ok': True})


def setup_webhook_url():
    """Set the Telegram webhook URL. Call this once during deployment."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping webhook setup")
        return

    render_host = settings.RENDER_EXTERNAL_HOSTNAME
    webhook_host = settings.WEBHOOK_HOST if hasattr(settings, 'WEBHOOK_HOST') else None

    if not webhook_host and render_host:
        webhook_host = f"https://{render_host}"

    if not webhook_host:
        logger.warning("No WEBHOOK_HOST or RENDER_EXTERNAL_HOSTNAME, skipping webhook setup")
        return

    secret = get_webhook_secret()
    webhook_url = f"{webhook_host}/bot/webhook/"

    import requests as req
    resp = req.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": webhook_url,
            "secret_token": secret,
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        logger.info("Telegram webhook set: %s", webhook_url)
    else:
        logger.error("Failed to set webhook: %s", data)
