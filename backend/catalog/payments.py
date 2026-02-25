import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
from telegram_bot.sender import send_message

try:
    from yookassa import Configuration, Payment
except Exception:  # pragma: no cover - optional dependency
    Configuration = None
    Payment = None

logger = logging.getLogger(__name__)


def yookassa_enabled() -> bool:
    return bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY and Payment)


def configure_yookassa() -> None:
    if not Payment or not Configuration:
        return
    Configuration.account_id = settings.YOOKASSA_SHOP_ID
    Configuration.secret_key = settings.YOOKASSA_SECRET_KEY


def get_return_url(request=None) -> str | None:
    if getattr(settings, 'YOOKASSA_RETURN_URL', ''):
        return settings.YOOKASSA_RETURN_URL
    if getattr(settings, 'SITE_URL', ''):
        return settings.SITE_URL
    if request:
        return request.build_absolute_uri('/')
    return None


def get_manual_payment_url(order) -> str | None:
    template = getattr(settings, 'MANUAL_PAYMENT_URL_TEMPLATE', '').strip()
    if not template:
        site_url = getattr(settings, 'SITE_URL', '').strip()
        if site_url:
            template = f"{site_url}/payment-demo.html?order_id={{order_id}}&amount={{amount}}"

    if not template:
        return None

    raw_amount = getattr(order, 'total_price', 0)
    try:
        amount = f"{Decimal(str(raw_amount)):.2f}"
    except Exception:
        amount = "0.00"

    try:
        return template.format(
            order_id=order.id,
            amount=amount,
            telegram_user_id=order.telegram_user_id,
        )
    except Exception as exc:
        logger.warning(
            "Некорректный MANUAL_PAYMENT_URL_TEMPLATE для заказа %s: %s",
            getattr(order, 'id', 'unknown'),
            exc
        )
        return None


def map_payment_status(status: str | None) -> str:
    if status in {'pending', 'waiting_for_capture'}:
        return 'pending'
    if status == 'succeeded':
        return 'succeeded'
    if status == 'canceled':
        return 'canceled'
    return 'not_paid'


def create_payment_for_order(order, amount, description: str, return_url: str | None):
    if not yookassa_enabled():
        return None
    try:
        configure_yookassa()
        confirmation_url = return_url or get_return_url()
        if not confirmation_url:
            logger.warning("YOOKASSA_RETURN_URL/SITE_URL не задан — платеж для заказа %s не создан", order.id)
            return None
        payload = {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": confirmation_url,
            },
            "capture": True,
            "description": description,
            "metadata": {
                "order_id": str(order.id),
                "telegram_user_id": str(order.telegram_user_id),
            },
        }
        payment = Payment.create(payload, str(uuid4()))
        return payment
    except Exception as exc:
        logger.warning("Не удалось создать платеж YooKassa для заказа %s: %s", order.id, exc)
        return None


def update_order_from_payment(order, payment) -> tuple[str, str | None]:
    """Синхронизировать поля заказа с объектом платежа.

    Возвращает (payment_status, payment_url).
    """
    payment_id = getattr(payment, 'id', '') or ''
    payment_status = map_payment_status(getattr(payment, 'status', None))
    confirmation = getattr(payment, 'confirmation', None)
    payment_url = getattr(confirmation, 'confirmation_url', None) if confirmation else None

    order.payment_id = payment_id
    order.payment_status = payment_status
    if payment_url:
        order.payment_url = payment_url
    if payment_status == 'succeeded' and not order.paid_at:
        order.paid_at = timezone.now()
    order.save(update_fields=['payment_id', 'payment_status', 'payment_url', 'paid_at', 'updated_at'])
    return payment_status, payment_url


def fetch_payment(payment_id: str):
    if not yookassa_enabled() or not payment_id:
        return None
    try:
        configure_yookassa()
        return Payment.find_one(payment_id)
    except Exception as exc:
        logger.warning("Не удалось проверить платеж YooKassa %s: %s", payment_id, exc)
        return None


def notify_payment_status(order, status: str) -> None:
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token or not getattr(order, 'telegram_user_id', None):
        return

    status_labels = {
        'succeeded': 'Оплата получена',
        'pending': 'Ожидаем оплату',
        'canceled': 'Оплата отменена',
        'not_paid': 'Не оплачено',
    }
    text = (
        f"💳 {status_labels.get(status, status)} по заказу #{order.id}.\n"
        f"Сумма: {order.total_price} ₽"
    )
    if not send_message(order.telegram_user_id, text, timeout=5):
        logger.warning("Не удалось отправить сообщение об оплате заказа %s", order.id)
