"""
Команда для запуска Telegram бота
"""
from django.core.management.base import BaseCommand
from telegram_bot.bot import FlowerShopBot


class Command(BaseCommand):
    help = 'Запускает Telegram бота'

    def handle(self, *args, **options):
        bot = FlowerShopBot()
        bot.run()
