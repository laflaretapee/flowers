import logging

import requests
from django.conf import settings
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Order, normalize_phone

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Order)
def order_pre_save(sender, instance: Order, **kwargs):
    instance.phone_normalized = normalize_phone(instance.phone)
    if instance.pk:
        instance._previous_status = (
            Order.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
        )
    else:
        instance._previous_status = None


@receiver(post_save, sender=Order)
def order_post_save(sender, instance: Order, created: bool, **kwargs):
    if created:
        return

    previous_status = getattr(instance, '_previous_status', None)
    if not previous_status or previous_status == instance.status:
        return

    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not instance.telegram_user_id:
        return

    status_labels = dict(Order.STATUS_CHOICES)
    status_icons = {
        'new': '🆕',
        'confirmed': '✅',
        'in_progress': '🛠️',
        'ready': '📦',
        'delivering': '🚚',
        'completed': '🏁',
        'cancelled': '❌',
    }
    old_label = status_labels.get(previous_status, previous_status)
    new_label = status_labels.get(instance.status, instance.status)
    new_icon = status_icons.get(instance.status, 'ℹ️')

    text = (
        f"{new_icon} Статус вашего заказа #{instance.id} изменен:\n"
        f"{old_label} -> {new_label}"
    )

    try:
        if instance.status == 'ready' and getattr(instance, 'ready_photo', None):
            try:
                photo_path = instance.ready_photo.path
            except Exception:
                photo_path = None

            if photo_path:
                with open(photo_path, 'rb') as handle:
                    response = requests.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        data={
                            "chat_id": instance.telegram_user_id,
                            "caption": text
                        },
                        files={"photo": handle},
                        timeout=10
                    )
            else:
                response = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": instance.telegram_user_id, "text": text},
                    timeout=5
                )
        else:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": instance.telegram_user_id, "text": text},
                timeout=5
            )
        if response.status_code >= 400:
            logger.warning(
                "Не удалось отправить уведомление о статусе заказа %s: %s",
                instance.id,
                response.text
            )
    except Exception as exc:
        logger.warning("Ошибка отправки уведомления о статусе заказа %s: %s", instance.id, exc)
