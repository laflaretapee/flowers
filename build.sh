#!/usr/bin/env bash
# Build script for Render.com deployment
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Django build steps
cd backend
python manage.py collectstatic --no-input
python manage.py migrate
