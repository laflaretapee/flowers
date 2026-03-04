import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def format_money(value: Decimal) -> str:
    quantized = to_decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f"{quantized:.0f}"


def parse_budget_value(text: str) -> Decimal | None:
    if not text:
        return None
    matches = re.findall(r'\d+(?:[.,]\d+)?', text)
    if not matches:
        return None
    raw = matches[0].replace(',', '.')
    try:
        return Decimal(raw)
    except Exception:
        return None


def is_cancel_command(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().lower()
    if normalized == "❌ отмена":
        return True
    return bool(re.match(r"^/cancel(?:@\w+)?$", normalized))


def order_status_icon(status: str) -> str:
    return {
        'new': '🆕',
        'processing': '🟡',
        'ready': '📦',
        'completed': '✅',
        'cancelled': '❌',
        'expired': '⌛',
        'confirmed': '✅',
        'in_progress': '🛠️',
        'delivering': '🚚',
    }.get(status, 'ℹ️')


def order_status_title(status: str) -> str:
    return {
        'new': '🆕 Новый',
        'processing': '🟡 В работе',
        'ready': '🟢 Готов',
        'completed': '✅ Завершен',
        'cancelled': '❌ Отменен',
        'expired': '⌛ Просрочен',
        'confirmed': '✅ Подтвержден',
        'in_progress': '🛠️ В работе',
        'delivering': '🚚 Доставляется',
    }.get(status, 'ℹ️ Статус')


def payment_status_label(status: str) -> str:
    return {
        'not_paid': 'Не оплачен',
        'pending': 'Ожидает оплаты',
        'succeeded': 'Оплачен',
        'canceled': 'Платеж отменен',
    }.get(status, status or '—')


def payment_method_label(method: str) -> str:
    return {
        'transfer': 'Перевод',
        'online': 'Онлайн',
    }.get(method or '', method or '—')
