"""
Menu handlers: promotions, my orders, contacts, reviews display,
and the catch-all unknown message handler.
"""
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from catalog.models import Order, Review

from ..utils import format_money
from ..keyboards import get_main_keyboard
from ..services import check_user_subscription

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text == "🎁 Акции")
async def show_promotions(message: Message):
    user_id = message.from_user.id
    is_subscribed = await check_user_subscription(user_id)
    discount = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)

    last_order = await sync_to_async(
        Order.objects.filter(telegram_user_id=user_id).order_by('-created_at').first
    )()
    phone_normalized = last_order.phone_normalized if last_order else ''
    has_completed_orders = False
    if phone_normalized:
        has_completed_orders = await sync_to_async(
            Order.objects.filter(phone_normalized=phone_normalized, status='completed').exists
        )()

    if is_subscribed and not has_completed_orders:
        text = (
            f"🎁 <b>Ваши акции</b>\n\n"
            f"✅ Скидка <b>{discount}%</b> на первый полученный заказ по номеру телефона.\n\n"
            f"Скидка применяется автоматически при оформлении заказа."
        )
    elif is_subscribed and has_completed_orders:
        text = (
            f"🎁 <b>Акции</b>\n\n"
            f"Вы уже использовали скидку на первый полученный заказ.\n\n"
            f"Следите за нашими новыми акциями! 🌸"
        )
    else:
        text = (
            f"🎁 <b>Акции</b>\n\n"
            f"📢 Подпишитесь на наш канал и получите скидку <b>{discount}%</b> на первый полученный заказ!"
        )

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "🧾 Мои заказы")
async def show_my_orders(message: Message):
    user_id = message.from_user.id
    orders = await sync_to_async(list)(
        Order.objects.filter(telegram_user_id=user_id).order_by('-created_at')[:10]
    )

    if not orders:
        await message.answer("У вас пока нет заказов.", reply_markup=get_main_keyboard())
        return

    status_labels = dict(Order.STATUS_CHOICES)
    status_icons = {
        'new': '🆕',
        'processing': '🟡',
        'ready': '📦',
        'completed': '✅',
        'cancelled': '❌',
        'expired': '⌛',
        'confirmed': '✅',
        'in_progress': '🛠️',
        'delivering': '🚚',
    }
    lines = []
    for order in orders:
        created_at = timezone.localtime(order.created_at).strftime('%d.%m.%Y %H:%M')
        status_label = status_labels.get(order.status, order.status)
        status_icon = status_icons.get(order.status, 'ℹ️')
        total = format_money(order.total_price)
        lines.append(f"{status_icon} #{order.id} · {status_label} · {total} ₽ · {created_at}")

    text = "🧾 <b>Ваши заказы</b>\n\n" + "\n".join(lines)
    if len(orders) == 10:
        text += "\n\nПоказаны последние 10 заказов."

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "⭐️ Отзывы")
async def show_reviews(message: Message):
    reviews = await sync_to_async(list)(
        Review.objects.filter(is_published=True).order_by('-created_at')[:5]
    )

    if not reviews:
        await message.answer("Пока нет отзывов. Будьте первым!", reply_markup=get_main_keyboard())
        return

    lines = []
    for review in reviews:
        stars = "🌟" * review.rating + "⭐️" * (5 - review.rating)
        lines.append(f"{stars} {review.name}: {review.text}")

    text = "⭐️ <b>Отзывы клиентов</b>\n\n" + "\n\n".join(lines)
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "📞 Контакты")
async def show_contacts(message: Message):
    text = (
        "📞 <b>Контакты</b>\n\n"
        "📱 Телефон: +7 (999) 123-45-67\n"
        "📍 Адрес: Трактовая улица, 78А, село Раевский,\n"
        "Альшеевский район, Республика Башкортостан, 452120\n\n"
        "🕐 Мы работаем: 9:00 - 21:00\n"
        "🚗 Доставка по городу и району"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message()
async def handle_unknown(message: Message):
    await message.answer(
        "🤔 Используйте кнопки меню или команды:\n\n"
        "/start - Главное меню\n"
        "/catalog - Каталог"
    )
