"""
Интеграция с картами для отзывов (Google Maps / Yandex Maps)
"""
import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class MapsReviewIntegration:
    """Класс для работы с отзывами через API карт"""
    
    def __init__(self):
        self.google_api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        self.yandex_api_key = getattr(settings, 'YANDEX_MAPS_API_KEY', '')
        self.place_id = getattr(settings, 'MAPS_PLACE_ID', '')
    
    def get_reviews_from_google(self):
        """Получение отзывов из Google Maps"""
        if not self.google_api_key or not self.place_id:
            logger.warning("Google Maps API ключ или Place ID не настроены")
            return []
        
        try:
            url = f"https://maps.googleapis.com/maps/api/place/details/json"
            params = {
                'place_id': self.place_id,
                'fields': 'reviews,rating',
                'key': self.google_api_key,
                'language': 'ru'
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if data.get('status') == 'OK' and 'result' in data:
                reviews_data = data['result'].get('reviews', [])
                return [
                    {
                        'name': review.get('author_name', 'Аноним'),
                        'text': review.get('text', ''),
                        'rating': review.get('rating', 5),
                        'date': review.get('time', 0),
                        'source': 'google'
                    }
                    for review in reviews_data[:10]  # Берем последние 10
                ]
        except Exception as e:
            logger.error(f"Ошибка получения отзывов из Google Maps: {e}")
        
        return []
    
    def get_reviews_from_yandex(self):
        """Получение отзывов из Yandex Maps"""
        if not self.yandex_api_key or not self.place_id:
            logger.warning("Yandex Maps API ключ или Place ID не настроены")
            return []
        
        try:
            # Yandex Maps API для получения отзывов
            url = f"https://search-maps.yandex.ru/v1/"
            params = {
                'text': self.place_id,
                'apikey': self.yandex_api_key,
                'lang': 'ru_RU',
                'results': 1
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            # Yandex API может требовать другой формат запроса
            # Это базовая реализация, может потребоваться доработка
            if 'features' in data and len(data['features']) > 0:
                # Здесь нужно получить отзывы из свойств места
                # Точная реализация зависит от формата ответа Yandex API
                pass
        except Exception as e:
            logger.error(f"Ошибка получения отзывов из Yandex Maps: {e}")
        
        return []
    
    def sync_reviews_to_db(self):
        """Синхронизация отзывов из карт в БД"""
        from .models import Review
        
        google_reviews = self.get_reviews_from_google()
        yandex_reviews = self.get_reviews_from_yandex()
        
        all_reviews = google_reviews + yandex_reviews
        
        synced_count = 0
        for review_data in all_reviews:
            # Проверяем, не существует ли уже такой отзыв
            existing = Review.objects.filter(
                name=review_data['name'],
                text=review_data['text'][:500]  # Ограничиваем длину для сравнения
            ).first()
            
            if not existing:
                Review.objects.create(
                    name=review_data['name'],
                    text=review_data['text'],
                    rating=review_data['rating'],
                    is_published=True
                )
                synced_count += 1
        
        return synced_count
    
    def get_average_rating(self):
        """Получение средней оценки из карт"""
        google_reviews = self.get_reviews_from_google()
        if google_reviews:
            ratings = [r['rating'] for r in google_reviews]
            return sum(ratings) / len(ratings) if ratings else None
        return None
