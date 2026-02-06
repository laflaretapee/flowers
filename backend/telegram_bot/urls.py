from django.urls import path
from .webhook import telegram_webhook

urlpatterns = [
    path('', telegram_webhook, name='telegram-webhook'),
]
