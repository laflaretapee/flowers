# Деплой на Timeweb Cloud (VPS)

## Архитектура

- `web` контейнер: Django + Telegram bot в webhook-режиме (без polling).
- `db` контейнер: PostgreSQL 15.
- Nginx на VPS: reverse proxy + HTTPS.

Webhook Telegram в проекте: `https://<ваш-домен>/bot/webhook/`.

## 1. Подготовка VPS

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx git
sudo systemctl enable --now docker
```

## 2. Клонирование проекта

```bash
cd /opt
sudo git clone <YOUR_REPO_URL> flowers
sudo chown -R $USER:$USER /opt/flowers
cd /opt/flowers
```

## 3. Настройка `.env`

```bash
cp .env.example .env
```

Минимум для production:

- `DEBUG=False`
- `SECRET_KEY=<случайная длинная строка>`
- `ALLOWED_HOSTS=<домен>,www.<домен>,localhost,127.0.0.1`
- `CSRF_TRUSTED_ORIGINS=https://<домен>,https://www.<домен>`
- `SITE_URL=https://<домен>`
- `DATABASE_URL=postgres://flowers_user:flowers_password@db:5432/flowers_db`
- `TELEGRAM_BOT_TOKEN=<token>`
- `TELEGRAM_GROUP_ID=<id>`
- `WEBHOOK_HOST=https://<домен>`

Опционально:

- `TELEGRAM_WEBHOOK_SECRET=<случайная строка>`
- `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` / `YOOKASSA_RETURN_URL`

## 4. Запуск контейнеров

```bash
docker compose up -d --build
docker compose ps
```

Контейнер `web` поднимает миграции, статику, регистрирует Telegram webhook и запускает Gunicorn.

## 5. Nginx

1. Отредактируйте домен в шаблоне [`deploy/timeweb/nginx.flowers.conf`](deploy/timeweb/nginx.flowers.conf).
2. Подключите конфиг:

```bash
sudo cp deploy/timeweb/nginx.flowers.conf /etc/nginx/sites-available/flowers
sudo ln -sf /etc/nginx/sites-available/flowers /etc/nginx/sites-enabled/flowers
sudo nginx -t
sudo systemctl reload nginx
```

## 6. HTTPS (Let's Encrypt)

```bash
sudo certbot --nginx -d <домен> -d www.<домен>
```

Проверьте автообновление сертификата:

```bash
sudo certbot renew --dry-run
```

## 7. Проверка Telegram webhook

```bash
docker compose exec web python manage.py telegram_webhook info
docker compose exec web python manage.py telegram_webhook set --drop-pending-updates
docker compose exec web python manage.py telegram_webhook info
```

## 8. Проверка приложения

- Главная: `https://<домен>/`
- Админка: `https://<домен>/admin/`
- API: `https://<домен>/api/`
- Telegram webhook endpoint: `https://<домен>/bot/webhook/` (должен отвечать только Telegram с секретом)

## 9. YooKassa (когда будут доступы)

Проект уже готов к webhook-обновлению оплаты:

- Endpoint: `https://<домен>/api/payments/yookassa/`
- После заполнения `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` статус оплаты обновляется автоматически.

После добавления ключей:

```bash
docker compose up -d
```

И настройте webhook в кабинете YooKassa на URL выше.

## 10. Полезные команды

```bash
docker compose logs -f web
docker compose logs -f db
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose restart web
```
