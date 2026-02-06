import os
import threading
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TelegramBotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'telegram_bot'

    def ready(self):
        # Only set up webhook in production (on Render) and not during manage.py commands
        is_render = os.getenv('RENDER_EXTERNAL_HOSTNAME')
        is_main_process = os.environ.get('RUN_MAIN') != 'true'  # avoid double run in dev
        if is_render and is_main_process:
            # Run in a background thread to not block startup
            threading.Thread(target=self._setup_webhook, daemon=True).start()

    def _setup_webhook(self):
        try:
            from .webhook import setup_webhook_url
            setup_webhook_url()
        except Exception as e:
            logger.error("Failed to set up Telegram webhook: %s", e)
