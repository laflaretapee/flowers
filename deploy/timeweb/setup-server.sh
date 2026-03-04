#!/usr/bin/env bash
#
# Скрипт первоначальной настройки сервера Timeweb Cloud (Ubuntu 22.04/24.04)
# Запускать от root: bash setup-server.sh
#
set -euo pipefail

DOMAIN="${1:?Укажите домен первым аргументом: bash setup-server.sh flowers.ru}"
EMAIL="${2:?Укажите email вторым аргументом: bash setup-server.sh flowers.ru admin@flowers.ru}"
PROJECT_DIR="/opt/flowers"

echo "=== 1. Обновление системы ==="
apt-get update && apt-get upgrade -y

echo "=== 2. Установка Docker ==="
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

echo "=== 3. Установка Docker Compose plugin ==="
if ! docker compose version &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi

echo "=== 4. Установка nginx и certbot ==="
apt-get install -y nginx certbot python3-certbot-nginx ufw

echo "=== 5. Настройка фаервола ==="
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
ufw status

echo "=== 6. Создание директории проекта ==="
mkdir -p "$PROJECT_DIR"

echo "=== 7. Настройка nginx ==="
# Временный конфиг для получения сертификата (только HTTP)
cat > /etc/nginx/sites-available/flowers <<NGINX_CONF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX_CONF

ln -sf /etc/nginx/sites-available/flowers /etc/nginx/sites-enabled/flowers
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== 8. Получение SSL-сертификата ==="
mkdir -p /var/www/certbot
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

echo "=== 9. Автообновление сертификатов ==="
systemctl enable certbot.timer

echo "=== 10. Настройка nginx с SSL ==="
# certbot уже обновил конфиг, но добавим security headers
cat > /etc/nginx/sites-available/flowers <<NGINX_CONF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN} www.${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 20m;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
NGINX_CONF

nginx -t && systemctl reload nginx

echo ""
echo "========================================="
echo " Сервер настроен!"
echo " Домен: https://${DOMAIN}"
echo " Проект: ${PROJECT_DIR}"
echo ""
echo " Следующие шаги:"
echo " 1. Скопируйте проект: rsync -avz --exclude=venv --exclude=.git --exclude='*.pyc' --exclude=__pycache__ --exclude=staticfiles /path/to/flowers/ root@IP:${PROJECT_DIR}/"
echo " 2. Настройте .env: nano ${PROJECT_DIR}/.env"
echo " 3. Запустите: cd ${PROJECT_DIR} && docker compose up -d --build"
echo "========================================="
