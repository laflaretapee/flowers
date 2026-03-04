import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from catalog.models import Order
from catalog.payments import update_order_from_payment, fetch_payment

from ..constants import CARD_PAYMENT_MAINTENANCE_NOTE
from ..services import refresh_order_group_message

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data.startswith("check_payment_"))
async def check_payment_status(callback: CallbackQuery):
    await callback.answer()
    try:
        order_id = int(callback.data.split("_")[2])
    except Exception:
        await callback.message.answer("Не удалось определить заказ.")
        return

    @sync_to_async
    def _get_order():
        return Order.objects.filter(pk=order_id, telegram_user_id=callback.from_user.id).first()

    order = await _get_order()
    if not order:
        await callback.message.answer("Заказ не найден.")
        return

    if getattr(order, 'payment_method', '') == 'transfer':
        await callback.message.answer(
            f"{CARD_PAYMENT_MAINTENANCE_NOTE} "
            "По этому заказу оплата принимается переводом. "
            "Менеджер пришлет реквизиты и подтвердит оплату вручную."
        )
        return

    if not order.payment_id:
        if order.payment_url:
            await callback.message.answer(
                "Для этого заказа используется временная ссылка оплаты. "
                "Подтверждение оплаты делается вручную менеджером."
            )
        else:
            await callback.message.answer("Оплата по этому заказу не создавалась.")
        return

    payment = await sync_to_async(fetch_payment)(order.payment_id)
    if not payment:
        await callback.message.answer("Не удалось проверить оплату. Попробуйте позже.")
        return

    new_status, _ = await sync_to_async(update_order_from_payment)(order, payment)
    await refresh_order_group_message(order.id)

    status_labels = {
        'not_paid': 'Не оплачено',
        'pending': 'Ожидает оплаты',
        'succeeded': 'Оплачено',
        'canceled': 'Отменено',
    }
    await callback.message.answer(
        f"Статус оплаты заказа #{order.id}: {status_labels.get(new_status, new_status)}."
    )
