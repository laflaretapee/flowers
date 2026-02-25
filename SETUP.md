# Инструкция по настройке проекта

## Быстрый старт

### 1. Установка зависимостей

```bash
# Создайте виртуальное окружение (рекомендуется)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

# Установите зависимости
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

```bash
cp .env.example .env
```

Отредактируйте `.env` и укажите:

**Обязательные:**
- `SECRET_KEY` - сгенерируйте случайную строку
- `TELEGRAM_BOT_TOKEN` - токен бота от @BotFather
- `TELEGRAM_GROUP_ID` - ID группы для проверки подписки
- `SITE_URL` - базовый URL сайта (для SEO и оплаты)
- `DATABASE_URL` - строка подключения PostgreSQL
- `WEBHOOK_HOST` - публичный HTTPS URL вашего домена (например `https://flowers.example.ru`)

**Опциональные (для полного функционала):**
- `GOOGLE_MAPS_API_KEY` - для синхронизации отзывов из Google Maps
- `YANDEX_TAXI_API_KEY` - для интеграции доставки через Yandex Taxi
- `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` - для онлайн-оплаты

### 3. Настройка базы данных

```bash
cd backend
python manage.py migrate
python manage.py createsuperuser
```

### 4. Запуск

**Django сервер:**
```bash
cd backend
python manage.py runserver
```

**Telegram webhook:**
```bash
cd backend
python manage.py telegram_webhook set
python manage.py telegram_webhook info
```

## Настройка Telegram бота

### Создание бота

1. Найдите @BotFather в Telegram
2. Отправьте `/newbot`
3. Следуйте инструкциям и получите токен
4. Добавьте токен в `.env` как `TELEGRAM_BOT_TOKEN`

### Настройка группы для подписки

1. Создайте группу в Telegram
2. Добавьте бота в группу как администратора
3. Получите ID группы:
   - Добавьте бота @userinfobot в группу
   - Он покажет ID группы
   - Или используйте @RawDataBot
4. Добавьте ID в `.env` как `TELEGRAM_GROUP_ID`

**Важно:** ID группы обычно отрицательное число (например, `-1001234567890`)

## Настройка админки Django

1. Откройте http://localhost:8000/admin/
2. Войдите с учетными данными суперпользователя
3. Создайте категории товаров
4. Добавьте товары с изображениями
5. Настройте популярные товары (чекбокс "Популярный")

## Запрос фото готового букета через админку

1. Откройте нужный заказ в админке.
2. В блоке "Заказ" нажмите кнопку **"Запросить фото в боте"**.
3. Администратор(ы) бота получат сообщение с кнопкой и смогут отправить фото в Telegram.

> Для работы функции в разделе "Админы бота" должны быть заполнены `telegram_user_id`.

## Онлайн-оплата YooKassa

1. Зарегистрируйте магазин в YooKassa и получите:
   - `YOOKASSA_SHOP_ID`
   - `YOOKASSA_SECRET_KEY`
2. Заполните их в `.env` (или переменных окружения).
3. Укажите `YOOKASSA_RETURN_URL` (обычно это ваш `SITE_URL`).
4. В Telegram-боте после оформления заказа появится кнопка оплаты.
5. После оплаты нажмите кнопку **"Проверить оплату"** в боте.

### Webhook (по желанию)

Можно подключить webhook, чтобы оплата отмечалась автоматически:

- URL: `https://ваш-домен/api/payments/yookassa/`

После успешного платежа статус оплаты в заказе обновится автоматически.

## Добавление товаров

1. Войдите в админку Django
2. Перейдите в "Каталог" → "Товары"
3. Нажмите "Добавить товар"
4. Заполните:
   - Название
   - Описание
   - Цену
   - Загрузите изображение
   - Выберите категорию
   - Отметьте "Популярный" если нужно показать на главной
4. Сохраните

## Настройка промо-акции

В `.env` можно настроить:
- `PROMO_DISCOUNT_PERCENT=10` - процент скидки
- `PROMO_ENABLED=True` - включить/выключить акцию

## Интеграция с картами для отзывов

### Google Maps

1. Перейдите в https://console.cloud.google.com/
2. Создайте проект
3. Включите "Places API"
4. Создайте API ключ
5. Найдите Place ID вашего магазина:
   - Используйте https://developers.google.com/maps/documentation/places/web-service/place-id
6. Добавьте в `.env`:
   ```
   GOOGLE_MAPS_API_KEY=your-api-key
   MAPS_PLACE_ID=your-place-id
   ```

### Синхронизация отзывов

```bash
python manage.py sync_maps_reviews
```

Рекомендуется настроить cron для автоматической синхронизации:
```bash
# Каждый день в 9:00
0 9 * * * cd /path/to/project/backend && python manage.py sync_maps_reviews
```

## Интеграция с такси для доставки

### Yandex Taxi

1. Зарегистрируйтесь в партнерской программе: https://taxi.yandex.ru/partners/
2. Получите API ключ и CLID
3. Получите ключ для Geocoder API: https://developer.tech.yandex.ru/
4. Добавьте в `.env`:
   ```
   YANDEX_TAXI_API_KEY=your-taxi-key
   YANDEX_TAXI_CLID=your-clid
   YANDEX_GEOCODER_API_KEY=your-geocoder-key
   TAXI_DELIVERY_SERVICE=yandex
   ```

**Примечание:** Если API такси не настроено, система будет использовать базовую оценку стоимости доставки (200 ₽).

## Обновление каталога на сайте

Каталог на сайте можно обновить через API:

```javascript
// В js/main.js уже есть функция loadCatalog()
// Она будет автоматически загружать товары с /api/products/
```

Или вручную обновите `index.html` и `catalog.html` с реальными данными.

## Деплой

### Подготовка к продакшену

1. Измените `DEBUG=False` в `.env`
2. Установите `ALLOWED_HOSTS` с вашим доменом
3. Настройте статические файлы:
   ```bash
   python manage.py collectstatic
   ```
4. Используйте PostgreSQL вместо SQLite для продакшена
5. Настройте вебхуки для Telegram бота вместо polling

### Настройка вебхуков для бота

`run_bot` (polling) отключен. Для работы бота в production:

```bash
cd backend
python manage.py telegram_webhook set
python manage.py telegram_webhook info
```

Путь webhook в проекте: `/bot/webhook/`.

## Поддержка

При возникновении проблем проверьте:
1. Все переменные в `.env` заполнены корректно
2. База данных мигрирована (`python manage.py migrate`)
3. Бот добавлен в группу как администратор
4. API ключи действительны и имеют нужные права
