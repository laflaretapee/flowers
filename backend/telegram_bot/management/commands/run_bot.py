"""
Команда для запуска Telegram бота (aiogram 3.x)
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Polling mode is disabled. Use Telegram webhook mode.'

    def handle(self, *args, **options):
        raise CommandError(
            "Polling mode is disabled. Start Django web app and run: "
            "python manage.py telegram_webhook set"
        )
