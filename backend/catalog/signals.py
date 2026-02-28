import logging

from django.conf import settings
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from telegram_bot.sender import send_message, send_photo
from .models import Order, normalize_phone
from .payments import (
    yookassa_enabled,
    create_payment_for_order,
    update_order_from_payment,
    get_return_url,
    get_manual_payment_url,
)

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
        'processing': '🛠️',
        'ready': '📦',
        'completed': '🏁',
        'cancelled': '❌',
        'expired': '⌛',
        # legacy statuses (safety for old rows)
        'confirmed': '✅',
        'in_progress': '🛠️',
        'delivering': '🚚',
    }
    old_label = status_labels.get(previous_status, previous_status)
    new_label = status_labels.get(instance.status, instance.status)
    new_icon = status_icons.get(instance.status, 'ℹ️')

    text = (
        f"{new_icon} Статус вашего заказа #{instance.id} изменен:\n"
        f"{old_label} -> {new_label}"
    )

    delivered = False
    if instance.status == 'ready' and getattr(instance, 'ready_photo', None):
        try:
            photo_path = instance.ready_photo.path
        except Exception:
            photo_path = None

        if photo_path:
            delivered = send_photo(instance.telegram_user_id, photo_path, caption=text, timeout=10)

    if not delivered:
        delivered = send_message(instance.telegram_user_id, text, timeout=5)

    if not delivered:
        logger.warning("Не удалось отправить уведомление о статусе заказа %s", instance.id)

    # После статуса "Готов" — запрос оплаты (YooKassa или временная ссылка)
    if instance.status == 'ready':
        if not instance.total_price or instance.total_price <= 0:
            return
        if instance.payment_status != 'succeeded':
            payment_url = getattr(instance, 'payment_url', '')
            has_yookassa = yookassa_enabled()

            if not payment_url and has_yookassa:
                payment = create_payment_for_order(
                    order=instance,
                    amount=instance.total_price,
                    description=f"Оплата заказа #{instance.id}",
                    return_url=get_return_url()
                )
                if payment:
                    _, payment_url = update_order_from_payment(instance, payment)

            if not payment_url:
                payment_url = get_manual_payment_url(instance)
                if payment_url:
                    instance.payment_url = payment_url
                    if instance.payment_status == 'not_paid':
                        instance.payment_status = 'pending'
                    instance.save(update_fields=['payment_url', 'payment_status', 'updated_at'])

            if payment_url:
                if has_yookassa:
                    pay_text = (
                        f"💳 Ваш букет готов! Пожалуйста, оплатите заказ #{instance.id}.\n"
                        "Нажмите кнопку ниже для оплаты через YooKassa."
                    )
                else:
                    pay_text = (
                        f"💳 Ваш букет готов! Пожалуйста, оплатите заказ #{instance.id}.\n"
                        "Нажмите кнопку ниже для перехода к временной оплате."
                    )

                inline_keyboard = [[{"text": "💳 Оплатить онлайн", "url": payment_url}]]
                if has_yookassa and instance.payment_id:
                    inline_keyboard.append(
                        [{"text": "✅ Проверить оплату", "callback_data": f"check_payment_{instance.id}"}]
                    )

                reply_markup = {
                    "inline_keyboard": inline_keyboard
                }
                if not send_message(instance.telegram_user_id, pay_text, reply_markup=reply_markup, timeout=10):
                    logger.warning("Не удалось отправить ссылку на оплату заказа %s", instance.id)

    # После завершения — сразу запросить отзыв со звездами.
    if instance.status == 'completed':
        review_text = (
            f"🙏 Спасибо! Заказ #{instance.id} завершен.\n\n"
            "Оцените, пожалуйста, наш сервис:"
        )
        review_markup = {
            "inline_keyboard": [[
                {"text": "⭐️", "callback_data": "rate_1"},
                {"text": "⭐️", "callback_data": "rate_2"},
                {"text": "⭐️", "callback_data": "rate_3"},
                {"text": "⭐️", "callback_data": "rate_4"},
                {"text": "⭐️", "callback_data": "rate_5"},
            ]]
        }
        if not send_message(instance.telegram_user_id, review_text, reply_markup=review_markup, timeout=10):
            logger.warning("Не удалось отправить запрос отзыва по заказу %s", instance.id)
