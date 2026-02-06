# Деплой на Render.com

## Что нужно сделать (пошагово)

### 1. Залить проект на GitHub

```bash
cd /home/dinar/sites/flowers
git init   # если ещё не инициализирован
git add .
git commit -m "Prepare for Render deployment"
git remote add origin https://github.com/ВАШ_ЮЗЕРНЕЙМ/flowers.git
git push -u origin main
```

> Если репозиторий приватный — это ОК, Render умеет с ними работать.

### 2. Зарегистрироваться на Render.com

1. Перейти на https://render.com
2. Нажать **Get Started for Free**
3. Войти через **GitHub** (самый удобный вариант)

### 3. Создать базу данных PostgreSQL

1. В Dashboard → **New** → **PostgreSQL**
2. Заполнить:
   - **Name**: `flowers-db`
   - **Region**: `Frankfurt (EU Central)` (ближайший к РФ)
   - **Plan**: **Free**
3. Нажать **Create Database**
4. Скопировать **Internal Database URL** (понадобится на следующем шаге)

### 4. Создать Web Service

1. В Dashboard → **New** → **Web Service**
2. Подключить GitHub репозиторий `flowers`
3. Заполнить настройки:
   - **Name**: `flowers-shop` (или любое другое)
   - **Region**: `Frankfurt (EU Central)`
   - **Branch**: `main`
   - **Runtime**: `Python`
   - **Build Command**: `./build.sh`
   - **Start Command**: `cd backend && gunicorn flowers_shop.wsgi:application --bind 0.0.0.0:$PORT`
   - **Plan**: **Free**

4. Добавить **Environment Variables** (кнопка Advanced → Add Environment Variable):

| Ключ | Значение |
|------|----------|
| `DATABASE_URL` | *Internal Database URL из шага 3* |
| `SECRET_KEY` | *любая длинная случайная строка (можно сгенерировать)* |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `.onrender.com` |
| `PYTHON_VERSION` | `3.11.6` |
| `TELEGRAM_BOT_TOKEN` | *ваш токен бота* |
| `TELEGRAM_GROUP_ID` | *ID вашей группы* |
| `TELEGRAM_CHANNEL_ID` | *ID вашего канала* |

5. Нажать **Create Web Service**

### 5. Создать суперпользователя

Суперпользователь создаётся **автоматически** при деплое, если заданы переменные окружения:

| Ключ | Значение |
|------|----------|
| `DJANGO_SUPERUSER_USERNAME` | `admin` |
| `DJANGO_SUPERUSER_EMAIL` | `your@email.com` |
| `DJANGO_SUPERUSER_PASSWORD` | *надёжный пароль* |

Добавьте их в Environment Variables сервиса и сделайте **Manual Deploy**.

> Shell недоступен на Free плане Render, поэтому используется автоматическое создание через `build.sh`.

### 6. Готово!

Ваш сайт будет доступен по адресу:
```
https://flowers-shop.onrender.com
```

- Главная: `https://flowers-shop.onrender.com/`
- Каталог: `https://flowers-shop.onrender.com/catalog.html`
- Админка: `https://flowers-shop.onrender.com/admin/`
- API: `https://flowers-shop.onrender.com/api/`

---

## Альтернатива: деплой через render.yaml (Blueprint)

Вместо ручной настройки можно использовать автоматический деплой:

1. В Dashboard → **New** → **Blueprint**
2. Подключить репозиторий
3. Render автоматически прочитает `render.yaml` и создаст БД + сервис
4. Останется только добавить переменные `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_ID`, `TELEGRAM_CHANNEL_ID`

---

## Важные заметки

- **Free план засыпает через 15 минут** без трафика. Первый запрос после сна ~30 секунд.
- **Бесплатная БД PostgreSQL** действует 90 дней, потом нужно пересоздать.
- **Media файлы** (загруженные картинки) на Free плане не сохраняются между деплоями. Для постоянного хранения нужен внешний сервис (Cloudinary, AWS S3) или Render Disk (платный).
- **Telegram бот** в режиме polling нужно запускать отдельно. Для production лучше настроить webhook.

## Запуск Telegram бота на Render

Создать отдельный **Background Worker**:
1. **New** → **Background Worker**
2. Тот же репозиторий
3. **Build Command**: `./build.sh`
4. **Start Command**: `cd backend && python manage.py run_bot`
5. Те же переменные окружения
6. **Plan**: Free (Render может не давать бесплатные workers — в таком случае бота можно запускать локально)
