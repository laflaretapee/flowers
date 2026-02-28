"""
Интеграция с API такси для доставки
"""
import requests
from django.conf import settings
import logging
from decimal import Decimal

from .delivery_tariffs import load_delivery_tariffs, normalize_address_text

logger = logging.getLogger(__name__)


class TaxiDeliveryIntegration:
    """Класс для работы с доставкой через такси"""
    
    def __init__(self):
        # Настройки для разных сервисов такси
        self.yandex_taxi_api_key = getattr(settings, 'YANDEX_TAXI_API_KEY', '')
        self.yandex_taxi_clid = getattr(settings, 'YANDEX_TAXI_CLID', '')
        self.uber_api_key = getattr(settings, 'UBER_API_KEY', '')
        self.delivery_service = getattr(settings, 'TAXI_DELIVERY_SERVICE', 'yandex')  # yandex, uber, custom
        # Фиксированные тарифы по населенным пунктам (подгружаются из CSV).
        self.fixed_location_tariffs = load_delivery_tariffs()
    
    def calculate_delivery_cost(self, from_address, to_address, order_weight=1):
        """
        Расчет стоимости доставки
        
        Args:
            from_address: Адрес магазина
            to_address: Адрес доставки
            order_weight: Вес заказа в кг (по умолчанию 1 кг для букета)
        
        Returns:
            dict с информацией о доставке:
            {
                'cost': стоимость в рублях,
                'duration': время в минутах,
                'available': доступна ли доставка
            }
        """
        fixed_tariff = self._get_fixed_tariff_by_address(to_address)
        if fixed_tariff:
            return fixed_tariff

        # Если адрес написан свободным текстом, уточняем локацию через геокодер
        # и пытаемся сопоставить по населенному пункту/району.
        for geocoded_text in self._geocode_address_candidates(to_address):
            fixed_tariff = self._get_fixed_tariff_by_address(geocoded_text)
            if fixed_tariff:
                fixed_tariff['note'] = (
                    f"{fixed_tariff.get('note', 'Применен фиксированный тариф')} "
                    f"(по данным геокодера: {geocoded_text})"
                ).strip()
                return fixed_tariff

        # Если справочник тарифов загружен, он считается источником истины.
        # Неугаданный адрес уходит в ручной расчет.
        if self.fixed_location_tariffs:
            return self._estimate_delivery(from_address, to_address)

        if self.delivery_service == 'yandex':
            return self._calculate_yandex_taxi(from_address, to_address, order_weight)
        elif self.delivery_service == 'uber':
            return self._calculate_uber(from_address, to_address, order_weight)
        else:
            # Базовая оценка для других сервисов
            return self._estimate_delivery(from_address, to_address)

    def _get_fixed_tariff_by_address(self, to_address: str):
        normalized_address = normalize_address_text(to_address)
        if not normalized_address:
            return None

        for aliases, cost, label in self.fixed_location_tariffs:
            for alias in aliases:
                normalized_alias = normalize_address_text(alias)
                if normalized_alias and normalized_alias in normalized_address:
                    if cost is None:
                        return {
                            'cost': Decimal('0'),
                            'duration': 0,
                            'available': False,
                            'service': 'manual',
                            'requires_manual_price': True,
                            'tariff_label': label,
                            'note': f'Не получилось рассчитать стоимость доставки автоматически ({label})',
                        }
                    return {
                        'cost': cost,
                        'duration': 30,
                        'available': True,
                        'service': 'прайс по адресу',
                        'tariff_label': label,
                        'note': f'Применен фиксированный тариф: {label}'
                    }
        return None
    
    def _calculate_yandex_taxi(self, from_address, to_address, order_weight):
        """Расчет через Yandex Taxi API"""
        if not self.yandex_taxi_api_key:
            logger.warning("Yandex Taxi API ключ не настроен")
            return self._estimate_delivery(from_address, to_address)
        
        try:
            # Yandex Taxi API для расчета стоимости
            # Требуется регистрация в партнерской программе
            url = "https://taxi-api.yandex.net/v1/estimate"
            headers = {
                'Authorization': f'Bearer {self.yandex_taxi_api_key}',
                'Content-Type': 'application/json'
            }
            
            # Нужно получить координаты адресов
            from_coords = self._geocode_address(from_address)
            to_coords = self._geocode_address(to_address)
            
            if not from_coords or not to_coords:
                return self._estimate_delivery(from_address, to_address)
            
            data = {
                'route': [
                    {'lat': from_coords['lat'], 'lon': from_coords['lon']},
                    {'lat': to_coords['lat'], 'lon': to_coords['lon']}
                ],
                'requirements': {
                    'cargo_options': {
                        'cargo_type': 'flowers',
                        'weight': order_weight
                    }
                }
            }
            
            response = requests.post(url, json=data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                options = result.get('options', [])
                if options:
                    cheapest = min(options, key=lambda x: x.get('price', {}).get('total', 0))
                    return {
                        'cost': Decimal(str(cheapest['price']['total'])),
                        'duration': cheapest.get('time', {}).get('minutes', 30),
                        'available': True,
                        'service': 'yandex_taxi'
                    }
        except Exception as e:
            logger.error(f"Ошибка расчета доставки через Yandex Taxi: {e}")
        
        return self._estimate_delivery(from_address, to_address)

    def _geocode_address_candidates(self, address: str) -> list[str]:
        """Получить кандидаты адреса/локации из геокодера для сопоставления тарифа."""
        api_key = getattr(settings, 'YANDEX_GEOCODER_API_KEY', '')
        if not api_key:
            return []
        try:
            url = "https://geocode-maps.yandex.ru/1.x/"
            params = {
                'geocode': address,
                'format': 'json',
                'apikey': api_key,
                'results': 1,
            }
            response = requests.get(url, params=params, timeout=5)
            if response.status_code >= 400:
                return []
            data = response.json()
            features = (
                data.get('response', {})
                .get('GeoObjectCollection', {})
                .get('featureMember', [])
            )
            if not features:
                return []

            geo_object = features[0].get('GeoObject', {})
            meta = geo_object.get('metaDataProperty', {}).get('GeocoderMetaData', {})
            address_meta = meta.get('Address', {}) or {}
            components = address_meta.get('Components', []) or []

            candidates: list[str] = []
            full_text = (meta.get('text') or '').strip()
            if full_text:
                candidates.append(full_text)

            formatted = (address_meta.get('formatted') or '').strip()
            if formatted:
                candidates.append(formatted)

            for component in components:
                name = (component.get('name') or '').strip()
                kind = (component.get('kind') or '').strip().lower()
                if not name:
                    continue
                # Для тарифа важны в первую очередь населенный пункт/район.
                if kind in {'locality', 'district', 'area', 'province'}:
                    candidates.append(name)

            # Удаляем дубли и слишком общие значения.
            stop_values = {
                normalize_address_text('россия'),
                normalize_address_text('российская федерация'),
                normalize_address_text('республика башкортостан'),
                normalize_address_text('башкортостан'),
            }
            unique: list[str] = []
            seen: set[str] = set()
            for item in candidates:
                normalized_item = normalize_address_text(item)
                if not normalized_item or normalized_item in stop_values or normalized_item in seen:
                    continue
                seen.add(normalized_item)
                unique.append(item)
            return unique
        except Exception as exc:
            logger.warning("Ошибка геокодирования текстового адреса %s: %s", address, exc)
            return []
    
    def _calculate_uber(self, from_address, to_address, order_weight):
        """Расчет через Uber API"""
        if not self.uber_api_key:
            logger.warning("Uber API ключ не настроен")
            return self._estimate_delivery(from_address, to_address)
        
        # Реализация для Uber API
        # Требуется регистрация в Uber для бизнеса
        return self._estimate_delivery(from_address, to_address)
    
    def _geocode_address(self, address):
        """Геокодирование адреса (получение координат)"""
        try:
            # Используем Yandex Geocoder API
            url = "https://geocode-maps.yandex.ru/1.x/"
            params = {
                'geocode': address,
                'format': 'json',
                'apikey': getattr(settings, 'YANDEX_GEOCODER_API_KEY', '')
            }
            
            response = requests.get(url, params=params, timeout=5)
            data = response.json()
            
            if 'response' in data and 'GeoObjectCollection' in data['response']:
                features = data['response']['GeoObjectCollection'].get('featureMember', [])
                if features:
                    pos = features[0]['GeoObject']['Point']['pos']
                    lon, lat = map(float, pos.split())
                    return {'lat': lat, 'lon': lon}
        except Exception as e:
            logger.error(f"Ошибка геокодирования адреса {address}: {e}")
        
        return None
    
    def reverse_geocode(self, lat, lon):
        """Обратное геокодирование: координаты → адрес"""
        try:
            # Используем Yandex Geocoder API для обратного геокодирования
            url = "https://geocode-maps.yandex.ru/1.x/"
            params = {
                'geocode': f"{lon},{lat}",  # Важно: сначала долгота, потом широта
                'format': 'json',
                'apikey': getattr(settings, 'YANDEX_GEOCODER_API_KEY', ''),
                'results': 1,
                'kind': 'house'  # Ищем дома
            }
            
            response = requests.get(url, params=params, timeout=5)
            data = response.json()
            
            if 'response' in data and 'GeoObjectCollection' in data['response']:
                features = data['response']['GeoObjectCollection'].get('featureMember', [])
                if features:
                    # Получаем полный адрес
                    address_components = features[0]['GeoObject']['metaDataProperty']['GeocoderMetaData']['Address']['Components']
                    address_parts = [comp['name'] for comp in address_components]
                    full_address = ', '.join(address_parts)
                    
                    # Также можно получить красивый форматированный адрес
                    formatted_address = features[0]['GeoObject']['metaDataProperty']['GeocoderMetaData']['text']
                    
                    return {
                        'formatted_address': formatted_address,
                        'full_address': full_address,
                        'components': address_components
                    }
        except Exception as e:
            logger.error(f"Ошибка обратного геокодирования координат {lat}, {lon}: {e}")
        
        return None
    
    def _estimate_delivery(self, from_address, to_address):
        """Fallback, когда не удалось определить точный тариф/маршрут."""
        return {
            'cost': Decimal('0'),
            'duration': 0,
            'available': False,
            'service': 'manual',
            'requires_manual_price': True,
            'note': 'Не получилось рассчитать стоимость доставки - введите стоимость сами'
        }
    
    def create_delivery_order(self, order_id, from_address, to_address, order_weight=1):
        """
        Создание заказа на доставку через такси
        
        Args:
            order_id: ID заказа в системе
            from_address: Адрес магазина
            to_address: Адрес доставки
            order_weight: Вес заказа
        
        Returns:
            dict с информацией о заказе доставки
        """
        delivery_info = self.calculate_delivery_cost(from_address, to_address, order_weight)
        
        # Здесь можно создать заказ в системе такси
        # Пока возвращаем информацию о доставке
        
        return {
            'order_id': order_id,
            'delivery_cost': float(delivery_info['cost']),
            'estimated_duration': delivery_info['duration'],
            'status': 'pending',
            'service': delivery_info.get('service', 'estimated')
        }
