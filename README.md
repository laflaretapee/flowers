# Bloom&Love - Цветочный магазин

Комплексное решение для цветочного магазина: лендинг, Django бэкенд с админкой, Telegram-бот.

## Структура проекта

```
flowers/
├── backend/              # Django проект
│   ├── flowers_shop/     # Основные настройки Django
│   ├── catalog/          # Приложение каталога
│   └── telegram_bot/     # Telegram бот
├── css/                  # Стили лендинга
├── js/                   # JavaScript лендинга
├── assets/               # Изображения
├── index.html           # Главная страница
├── catalog.html         # Страница каталога
└── requirements.txt     # Python зависимости
```

## Установка и запуск

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

Обязательно укажите:
- `TELEGRAM_BOT_TOKEN` - токен вашего Telegram бота (получить у @BotFather)
- `TELEGRAM_GROUP_ID` - ID группы для проверки подписки
- `SECRET_KEY` - секретный ключ Django

### 3. Настройка базы данных

```bash
cd backend
python manage.py migrate
python manage.py createsuperuser
```

### 4. Запуск Django сервера

```bash
cd backend
python manage.py runserver
```

Админка будет доступна по адресу: http://localhost:8000/admin/

### 5. Запуск Telegram бота

В отдельном терминале:

```bash
cd backend
python manage.py run_bot
```

## Функционал

### Лендинг
- Адаптивный дизайн
- Промо-баннер с акцией 10% за подписку
- Все кнопки "Заказать" ведут на Telegram-бота
- Плавная прокрутка и мобильное меню

### Django админка
- Управление категориями и товарами
- Загрузка изображений
- Управление заказами
- Модерация отзывов
- Настройка цен и популярных товаров

### REST API
- `/api/products/` - список товаров
- `/api/categories/` - список категорий
- `/api/reviews/` - отзывы
- `/api/products/popular/` - популярные товары

### Telegram бот
- Каталог товаров с категориями
- Проверка подписки на группу
- Скидка 10% для подписчиков
- Оформление заказов
- Прием отзывов

## Особенности

1. **Акция за подписку**: Пользователи получают скидку 10% при подписке на группу через бота
2. **Единый каталог**: Товары управляются через Django админку и отображаются на сайте и в боте
3. **Система заказов**: Все заказы сохраняются в БД и доступны в админке
4. **Отзывы**: Клиенты могут оставлять отзывы через бота

## Настройка интеграций

### Google Maps / Yandex Maps для отзывов

1. Получите API ключ:
   - Google Maps: https://console.cloud.google.com/
   - Yandex Maps: https://developer.tech.yandex.ru/

2. Добавьте в `.env`:
   ```
   GOOGLE_MAPS_API_KEY=your-key
   MAPS_PLACE_ID=your-place-id
   ```

3. Синхронизация отзывов:
   ```bash
   python manage.py sync_maps_reviews
   ```

### Интеграция с такси для доставки

1. **Yandex Taxi**:
   - Зарегистрируйтесь в партнерской программе Yandex Taxi
   - Получите API ключ и CLID
   - Добавьте в `.env`:
     ```
     YANDEX_TAXI_API_KEY=your-key
     YANDEX_TAXI_CLID=your-clid
     YANDEX_GEOCODER_API_KEY=your-geocoder-key
     TAXI_DELIVERY_SERVICE=yandex
     ```

2. **Uber** (альтернатива):
   - Зарегистрируйтесь в Uber для бизнеса
   - Добавьте в `.env`:
     ```
     UBER_API_KEY=your-key
     TAXI_DELIVERY_SERVICE=uber
     ```

## Следующие шаги

1. ✅ Интеграция с API карт (Google Maps/Yandex Maps) для отзывов - реализовано
2. ✅ Интеграция с API такси для доставки - реализовано
3. Настройка вебхуков для Telegram бота (вместо polling)
4. Добавление реальных изображений товаров
5. Настройка домена и деплой
6. Настройка автоматической синхронизации отзывов (cron)
7. Добавление уведомлений администратору о новых заказах

## Контакты

Для вопросов и поддержки обращайтесь к разработчику.
