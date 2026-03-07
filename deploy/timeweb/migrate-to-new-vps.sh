#!/usr/bin/env bash
#
# Миграция проекта flowers с VPS Германии на VPS Россия (Timeweb Cloud)
#
# ЧТО ДЕЛАЕТ:
#   1. Экспортирует базу данных PostgreSQL из Docker
#   2. Переносит проект + дамп базы + медиа на новый сервер
#   3. На новом сервере: устанавливает Docker, запускает проект, импортирует базу
#
# КАК ИСПОЛЬЗОВАТЬ:
#   1. Создай новый VPS в Timeweb Cloud (Россия, Ubuntu 22.04/24.04)
#   2. Зайди по SSH на СТАРЫЙ сервер (Германия)
#   3. Запусти: bash migrate-to-new-vps.sh <IP_нового_сервера> <домен>
#
#   Пример: bash migrate-to-new-vps.sh 195.2.xx.xx flowers.example.ru
#
set -euo pipefail

NEW_IP="${1:?Ошибка: укажи IP нового сервера. Пример: bash migrate-to-new-vps.sh 195.2.xx.xx flowers.ru}"
DOMAIN="${2:?Ошибка: укажи домен. Пример: bash migrate-to-new-vps.sh 195.2.xx.xx flowers.ru}"

PROJECT_DIR="/home/dinar/sites/flowers"
REMOTE_DIR="/opt/flowers"
REMOTE_USER="root"
DUMP_FILE="/tmp/flowers_db_dump.sql"

echo ""
echo "============================================"
echo " Миграция Flowers Shop"
echo " Старый сервер -> ${NEW_IP}"
echo " Домен: ${DOMAIN}"
echo "============================================"
echo ""

# ─── Шаг 1: Экспорт базы данных ─────────────────────────────────
echo "=== [1/6] Экспорт базы данных ==="
cd "$PROJECT_DIR"

docker compose exec -T db pg_dump -U flowers_user -d flowers_db --clean --if-exists > "$DUMP_FILE"

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "    Дамп создан: ${DUMP_FILE} (${DUMP_SIZE})"

# ─── Шаг 2: Перенос файлов проекта ──────────────────────────────
echo ""
echo "=== [2/6] Перенос файлов на новый сервер ==="

ssh "${REMOTE_USER}@${NEW_IP}" "mkdir -p ${REMOTE_DIR}"

rsync -avz --progress \
    --exclude=venv \
    --exclude=.venv \
    --exclude=.git \
    --exclude='*.pyc' \
    --exclude=__pycache__ \
    --exclude=backend/staticfiles \
    --exclude=backend/db.sqlite3 \
    -e ssh \
    "${PROJECT_DIR}/" "${REMOTE_USER}@${NEW_IP}:${REMOTE_DIR}/"

echo "    Файлы перенесены"

# ─── Шаг 3: Перенос дампа базы ───────────────────────────────────
echo ""
echo "=== [3/6] Перенос дампа базы ==="
scp "$DUMP_FILE" "${REMOTE_USER}@${NEW_IP}:/tmp/flowers_db_dump.sql"
echo "    Дамп перенесён"

# ─── Шаг 4: Установка Docker на новом сервере ────────────────────
echo ""
echo "=== [4/6] Настройка нового сервера ==="

ssh "${REMOTE_USER}@${NEW_IP}" bash -s "$DOMAIN" "$REMOTE_DIR" <<'REMOTE_SETUP'
set -euo pipefail

DOMAIN="$1"
REMOTE_DIR="$2"

echo "--- Обновление системы ---"
apt-get update -qq && apt-get upgrade -y -qq

echo "--- Установка Docker ---"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

echo "--- Установка nginx + certbot + ufw ---"
apt-get install -y -qq nginx certbot python3-certbot-nginx ufw

echo "--- Фаервол ---"
ufw allow OpenSSH
ufw allow 'Nginx Full'
echo "y" | ufw enable

echo "--- Конфиг nginx (HTTP пока) ---"
cat > /etc/nginx/sites-available/flowers <<EOF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    client_max_body_size 64m;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_read_timeout 120s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/flowers /etc/nginx/sites-enabled/flowers
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "--- Сервер готов ---"
REMOTE_SETUP

echo "    Новый сервер настроен"

# ─── Шаг 5: Запуск проекта на новом сервере ──────────────────────
echo ""
echo "=== [5/6] Запуск Docker Compose на новом сервере ==="

ssh "${REMOTE_USER}@${NEW_IP}" bash -s "$REMOTE_DIR" <<'REMOTE_START'
set -euo pipefail
REMOTE_DIR="$1"
cd "$REMOTE_DIR"

echo "--- Сборка и запуск контейнеров ---"
docker compose up -d --build

echo "--- Ожидание готовности БД (30 сек) ---"
sleep 30

echo "--- Импорт дампа базы ---"
docker compose exec -T db psql -U flowers_user -d flowers_db < /tmp/flowers_db_dump.sql

echo "--- Миграции (на случай новых) ---"
docker compose exec -T web python manage.py migrate --noinput

echo "--- Collectstatic ---"
docker compose exec -T web python manage.py collectstatic --noinput

echo "--- Готово ---"
docker compose ps
REMOTE_START

echo "    Проект запущен"

# ─── Шаг 6: Итоговая инструкция ──────────────────────────────────
echo ""
echo "============================================"
echo " Перенос завершён!"
echo "============================================"
echo ""
echo " Что осталось сделать ВРУЧНУЮ:"
echo ""
echo " 1. ДОМЕН — в панели Timeweb Cloud измени"
echo "    A-запись домена ${DOMAIN} на ${NEW_IP}"
echo ""
echo " 2. SSL — после обновления DNS (5-30 мин),"
echo "    зайди на новый сервер и выполни:"
echo "    ssh root@${NEW_IP}"
echo "    certbot --nginx -d ${DOMAIN} -d www.${DOMAIN}"
echo ""
echo " 3. .env — проверь настройки на новом сервере:"
echo "    ssh root@${NEW_IP}"
echo "    nano ${REMOTE_DIR}/.env"
echo "    # Убедись что:"
echo "    #   WEBHOOK_HOST=https://${DOMAIN}"
echo "    #   ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN}"
echo "    #   SITE_URL=https://${DOMAIN}"
echo "    #   DEBUG=False"
echo "    #   SECRET_KEY=<сгенерированный ключ>"
echo ""
echo " 4. WEBHOOK — после обновления .env:"
echo "    cd ${REMOTE_DIR}"
echo "    docker compose restart web"
echo "    # Webhook обновится автоматически при старте"
echo ""
echo " 5. СТАРЫЙ СЕРВЕР — после проверки нового:"
echo "    # Останови проект на старом:"
echo "    docker compose down"
echo ""
echo " 6. Пароль БД — смени в docker-compose.yml:"
echo "    POSTGRES_PASSWORD=<новый_пароль>"
echo "    И в DATABASE_URL тоже"
echo "============================================"
