import os
import sys
import threading
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TelegramBotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'telegram_bot'

    def ready(self):
        auto_configure = os.getenv('TELEGRAM_WEBHOOK_AUTOCONFIGURE', '').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        if not auto_configure:
            return

        # Avoid duplicate startup in Django runserver autoreload mode.
        if os.environ.get('RUN_MAIN') == 'true':
            return

        # Do not auto-configure during management commands where network side effects are unwanted.
        if len(sys.argv) > 1 and sys.argv[1] not in {'runserver'}:
            return

        threading.Thread(target=self._setup_webhook, daemon=True).start()

    def _setup_webhook(self):
        try:
            from .webhook import setup_webhook_url
            ok, data = setup_webhook_url()
            if ok:
                logger.info("Telegram webhook configured successfully")
            else:
                logger.warning("Telegram webhook setup skipped/failed: %s", data)
        except Exception as e:
            logger.error("Failed to set up Telegram webhook: %s", e)
