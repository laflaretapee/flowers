#!/usr/bin/env bash
# Build script for PaaS deployment
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Django build steps
cd backend
python manage.py collectstatic --no-input
python manage.py migrate

# Create superuser automatically from env vars (only if doesn't exist)
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
  python manage.py createsuperuser --no-input || true
fi
