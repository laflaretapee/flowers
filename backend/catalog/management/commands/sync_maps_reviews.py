"""
Команда для синхронизации отзывов из карт
"""
from django.core.management.base import BaseCommand
from catalog.maps_integration import MapsReviewIntegration


class Command(BaseCommand):
    help = 'Синхронизирует отзывы из Google Maps / Yandex Maps в базу данных'

    def handle(self, *args, **options):
        integration = MapsReviewIntegration()
        synced_count = integration.sync_reviews_to_db()
        
        if synced_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'Успешно синхронизировано {synced_count} отзывов')
            )
        else:
            self.stdout.write(
                self.style.WARNING('Новых отзывов для синхронизации не найдено')
            )
