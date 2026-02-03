"""
Команда для запуска Telegram бота (aiogram 3.x)
"""
from django.core.management.base import BaseCommand
from telegram_bot.bot import FlowerShopBot


class Command(BaseCommand):
    help = 'Запускает Telegram бота на aiogram 3.x'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🌸 Запуск бота Цветочная Лавка...'))
        bot = FlowerShopBot()
        bot.run()
