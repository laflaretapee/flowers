"""
Shared business logic for the Telegram bot.

This module contains helpers that are used across multiple handler modules:
subscription checks, admin checks, file downloads, order posting, etc.
"""
import html
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Q

from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from catalog.models import (
    BotAdmin,
    Order,
    OrderItem,
    TransferPaymentTemplate,
)

from .globals import get_bot, get_channel_id, get_group_id
from .constants import DELIVERY_MANUAL_NOTE, CARD_PAYMENT_MAINTENANCE_NOTE
from .utils import (
    to_decimal, format_money,
    order_status_title, payment_status_label, payment_method_label,
)

logger = logging.getLogger(__name__)

# ── Subscription ─────────────────────────────────────────────────

subscription_check_disabled = False


async def check_user_subscription(user_id: int) -> bool:
    global subscription_check_disabled

    if subscription_check_disabled:
        return True

    channel_id = get_channel_id()
    group_id = get_group_id()
    bot = get_bot()

    if not channel_id and not group_id:
        return True

    try:
        if channel_id:
            member = await bot.get_chat_member(channel_id, user_id)
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return True

        if group_id:
            member = await bot.get_chat_member(group_id, user_id)
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return True

    except TelegramBadRequest as e:
        error_msg = str(e)
        if "member list is inaccessible" in error_msg or "chat not found" in error_msg.lower():
            logger.warning(
                "Проверка подписки отключена! Бот не имеет доступа к каналу/группе. "
                "Channel ID: %s, Group ID: %s", channel_id, group_id,
            )
            subscription_check_disabled = True
            return True
        logger.error("Ошибка проверки подписки: %s", e)
    except Exception as e:
        logger.error("Ошибка проверки подписки: %s", e)

    return False


# ── Admin helpers ────────────────────────────────────────────────

async def is_bot_admin(user_id: int, username: str | None) -> bool:
    username_norm = (username or '').lstrip('@').strip().lower()

    def _check() -> bool:
        qs = BotAdmin.objects.filter(is_active=True)
        if username_norm:
            qs = qs.filter(Q(telegram_user_id=user_id) | Q(username__iexact=username_norm))
        else:
            qs = qs.filter(telegram_user_id=user_id)
        return qs.exists()

    return await sync_to_async(_check)()


# ── File downloads ───────────────────────────────────────────────

async def fetch_user_avatar_bytes(user_id: int) -> bytes | None:
    bot = get_bot()
    if not bot:
        return None

    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos or photos.total_count < 1:
            return None

        file_id = photos.photos[0][-1].file_id
        file = await bot.get_file(file_id)
        if not file or not file.file_path:
            return None

        url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file.file_path}"

        import requests as _requests
        resp = await sync_to_async(_requests.get)(url, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.content
    except Exception as exc:
        logger.info("Не удалось получить аватар пользователя %s: %s", user_id, exc)
        return None


async def download_telegram_file_bytes(file_id: str) -> tuple[bytes | None, str | None]:
    bot = get_bot()
    if not bot:
        return None, None
    try:
        tg_file = await bot.get_file(file_id)
        if not tg_file or not tg_file.file_path:
            return None, None
        file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{tg_file.file_path}"

        import requests as _requests
        resp = await sync_to_async(_requests.get)(file_url, timeout=15)
        if resp.status_code >= 400:
            return None, None
        basename = os.path.basename(tg_file.file_path)
        return resp.content, basename
    except Exception as exc:
        logger.warning("Не удалось скачать файл %s: %s", file_id, exc)
        return None, None


# ── Orders chat helpers ──────────────────────────────────────────

def get_orders_chat_id() -> str:
    explicit = (getattr(settings, 'TELEGRAM_ORDERS_CHAT_ID', '') or '').strip()
    fallback = (getattr(settings, 'TELEGRAM_GROUP_ID', '') or '').strip()
    return explicit or fallback


# ── Order breakdown / formatting ─────────────────────────────────

def calculate_order_breakdown(order: Order, items: list[OrderItem]) -> tuple[Decimal, Decimal, Decimal]:
    raw_items_total = sum((to_decimal(it.price) * Decimal(it.quantity) for it in items), Decimal('0'))
    discount_multiplier = (Decimal('100') - Decimal(order.discount_percent or 0)) / Decimal('100')
    discounted_items_total = (raw_items_total * discount_multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    items_total = to_decimal(getattr(order, 'items_subtotal', 0) or 0)
    if items_total <= 0:
        items_total = discounted_items_total

    delivery_price = to_decimal(getattr(order, 'delivery_price', 0) or 0)
    if delivery_price <= 0:
        delivery_price = (to_decimal(order.total_price) - items_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if delivery_price < 0:
            delivery_price = Decimal('0')

    total = to_decimal(order.total_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return items_total, delivery_price, total


def format_items_for_group(items: list[OrderItem]) -> str:
    if not items:
        return 'Индивидуальный букет'
    parts = [f"{it.product_name} x{it.quantity}" for it in items[:4]]
    if len(items) > 4:
        parts.append(f"+{len(items) - 4} поз.")
    return ", ".join(parts)


def build_transfer_payment_text(order: Order) -> str:
    details = (order.transfer_details or '').strip()
    text = (
        f"💳 Оплата заказа #{order.id}\n\n"
        f"{CARD_PAYMENT_MAINTENANCE_NOTE}\n"
        "Сейчас принимаем перевод напрямую магазину.\n"
        "После перевода отправьте, пожалуйста, чек/скрин в этот чат.\n\n"
    )
    if details:
        text += f"<b>Реквизиты для перевода:</b>\n<code>{html.escape(details)}</code>\n\n"
    text += (
        f"Сумма к оплате: <b>{format_money(to_decimal(order.total_price))} ₽</b>\n"
        "Если есть вопросы, менеджер подскажет по оплате."
    )
    return text


# ── Order group keyboard ─────────────────────────────────────────

def build_order_group_keyboard(order: Order) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    is_terminal = order.status in {'completed', 'cancelled', 'expired'}
    if order.status == 'new':
        rows.append([InlineKeyboardButton(text="🟡 Взять в работу", callback_data=f"svc_take_{order.id}")])
    elif order.status == 'processing':
        rows.append([InlineKeyboardButton(text="📦 Готово (с фото)", callback_data=f"svc_ready_{order.id}")])
        rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"svc_cancel_{order.id}")])
    elif order.status == 'ready':
        rows.append([InlineKeyboardButton(text="✅ Завершить", callback_data=f"svc_complete_{order.id}")])
        rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"svc_cancel_{order.id}")])
        if order.payment_status != 'succeeded':
            rows.append([InlineKeyboardButton(text="⌛ Не оплатил (expired)", callback_data=f"svc_expire_{order.id}")])

    if order.payment_method == 'transfer' and not is_terminal:
        rows.append([
            InlineKeyboardButton(
                text="✅ Подтвердить актуальные реквизиты",
                callback_data=f"svc_paycurrent_{order.id}",
            )
        ])
        details_button = "✏️ Обновить реквизиты вручную" if order.transfer_details else "💳 Ввести реквизиты вручную"
        rows.append([InlineKeyboardButton(text=details_button, callback_data=f"svc_payreq_{order.id}")])
        if order.payment_status != 'succeeded':
            rows.append([InlineKeyboardButton(text="✅ Отметить оплаченным", callback_data=f"svc_paid_{order.id}")])
        else:
            rows.append([InlineKeyboardButton(text="↩️ Вернуть в «не оплачен»", callback_data=f"svc_unpaid_{order.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


# ── Build / post / refresh order in group chat ───────────────────

async def build_order_group_message(order_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    @sync_to_async
    def _fetch():
        order = Order.objects.prefetch_related('items').get(pk=order_id)
        items = list(order.items.all())
        return order, items

    order, items = await _fetch()
    customer_name = html.escape((order.customer_name or '').strip() or 'Без имени')
    telegram_username = (order.telegram_username or '').strip().lstrip('@')
    if telegram_username:
        profile_link = f'<a href="https://t.me/{html.escape(telegram_username)}">@{html.escape(telegram_username)}</a>'
    else:
        profile_link = f'<a href="tg://user?id={order.telegram_user_id}">профиль</a>'

    composition = html.escape(format_items_for_group(items))
    requested_delivery = html.escape((order.requested_delivery or '').strip() or 'Как можно скорее')
    title = order_status_title(order.status)
    payment_label = html.escape(payment_status_label(order.payment_status))
    payment_method = html.escape(payment_method_label(order.payment_method))
    items_total, delivery_price, total = calculate_order_breakdown(order, items)
    manual_delivery = DELIVERY_MANUAL_NOTE.lower() in (order.comment or "").lower()
    delivery_display = "уточняется вручную" if manual_delivery and delivery_price <= 0 else f"{format_money(delivery_price)} ₽"

    text = (
        f"{title} | Заказ #{order.id}\n"
        f"👤 {customer_name}\n"
        f"🔗 {profile_link}\n"
        f"📞 {html.escape(order.phone or 'Не указан')}\n"
        f"📍 {html.escape(order.address or 'Не указан')}\n"
        f"💐 {composition}\n"
        f"📅 {requested_delivery}\n"
        f"🧾 Товары: {format_money(items_total)} ₽\n"
        f"🚚 Доставка: {delivery_display}\n"
        f"💰 Итого: {format_money(total)} ₽\n"
        f"💳 Оплата: {payment_label} · {payment_method}\n"
    )

    if order.is_preorder:
        text += "🌷 Предзаказ: да\n"

    if order.processing_by_user_id or order.processing_by_username:
        processor_username = (order.processing_by_username or '').strip().lstrip('@')
        if processor_username:
            text += f"👨‍🔧 Обрабатывает: @{html.escape(processor_username)}\n"
        else:
            text += f"👨‍🔧 Обрабатывает: <a href=\"tg://user?id={order.processing_by_user_id}\">мастер</a>\n"

    if manual_delivery:
        text += f"⚠️ {html.escape(DELIVERY_MANUAL_NOTE)}.\n"

    if order.payment_method == 'transfer':
        if order.transfer_details:
            text += f"💳 Реквизиты: <code>{html.escape(order.transfer_details)}</code>\n"
        else:
            text += "💳 Реквизиты: не указаны\n"

    comment = (order.comment or '').strip()
    if comment:
        short_comment = comment if len(comment) <= 500 else (comment[:500] + "…")
        text += f"💬 Комментарий: {html.escape(short_comment)}\n"

    return text.strip(), build_order_group_keyboard(order)


async def post_order_to_group(order_id: int) -> None:
    orders_chat_id = get_orders_chat_id()
    bot = get_bot()
    if not orders_chat_id or not bot:
        return

    try:
        text, keyboard = await build_order_group_message(order_id)
        sent = await bot.send_message(
            chat_id=orders_chat_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

        @sync_to_async
        def _bind():
            order = Order.objects.get(pk=order_id)
            order.service_chat_id = str(orders_chat_id)
            order.service_message_id = int(sent.message_id)
            order.save(update_fields=['service_chat_id', 'service_message_id', 'updated_at', 'phone_normalized'])

        await _bind()
    except Exception as exc:
        logger.warning("Не удалось отправить заказ #%s в служебный чат: %s", order_id, exc)


async def refresh_order_group_message(order_id: int) -> None:
    bot = get_bot()
    if not bot:
        return

    @sync_to_async
    def _meta():
        order = Order.objects.filter(pk=order_id).first()
        if not order:
            return None, None
        return order.service_chat_id, order.service_message_id

    service_chat_id, service_message_id = await _meta()
    if not service_chat_id or not service_message_id:
        return

    text, keyboard = await build_order_group_message(order_id)
    try:
        await bot.edit_message_text(
            chat_id=service_chat_id,
            message_id=service_message_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        msg = str(exc).lower()
        if "message is not modified" in msg:
            return
        if "message to edit not found" in msg or "can't be edited" in msg:
            await post_order_to_group(order_id)
            return
        logger.warning("Не удалось отредактировать служебное сообщение заказа #%s: %s", order_id, exc)
    except Exception as exc:
        logger.warning("Не удалось обновить сообщение заказа #%s: %s", order_id, exc)


# ── Transfer template ────────────────────────────────────────────

@sync_to_async
def apply_current_transfer_template(order_id: int) -> tuple[bool, str]:
    order = Order.objects.filter(pk=order_id).first()
    if not order:
        return False, "Заказ не найден"

    template = TransferPaymentTemplate.get_current_template()
    if not template:
        return False, "Нет активного шаблона реквизитов. Добавьте его в админке."

    order.transfer_details = (template.details or '').strip()
    order.payment_method = 'transfer'
    update_fields = ['transfer_details', 'payment_method', 'updated_at', 'phone_normalized']
    if order.payment_status == 'not_paid':
        order.payment_status = 'pending'
        update_fields.append('payment_status')
    order.save(update_fields=update_fields)
    return True, template.name


async def notify_customer_transfer_details(order_id: int) -> None:
    bot = get_bot()

    @sync_to_async
    def _fetch() -> Order | None:
        return Order.objects.filter(pk=order_id).first()

    order = await _fetch()
    if not order:
        return

    try:
        await bot.send_message(
            chat_id=order.telegram_user_id,
            text=build_transfer_payment_text(order),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Не удалось отправить реквизиты клиенту по заказу %s: %s", order.id, exc)


# ── Order group actions ──────────────────────────────────────────

async def apply_group_order_action(
    order_id: int,
    action: str,
    actor_id: int,
    actor_username: str | None,
) -> tuple[bool, str]:
    from django.utils import timezone

    terminal = {'completed', 'cancelled', 'expired'}

    @sync_to_async
    def _apply() -> tuple[bool, str]:
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            return False, "Заказ не найден"

        if order.status in terminal:
            return False, "Заказ уже закрыт"

        update_fields = ['updated_at', 'phone_normalized']
        actor_username_clean = (actor_username or '').strip().lstrip('@')

        if action == 'take':
            if order.status != 'new':
                return False, "Заказ уже взят в работу"
            order.status = 'processing'
            order.processing_by_user_id = actor_id
            order.processing_by_username = actor_username_clean
            update_fields.extend(['status', 'processing_by_user_id', 'processing_by_username'])
            order.save(update_fields=update_fields)
            return True, "Взято в работу"

        if action == 'ready':
            return False, "Для статуса «Готов» нужно загрузить фото букета"

        if action == 'complete':
            if order.status not in {'processing', 'ready'}:
                return False, "Нельзя завершить заказ в текущем статусе"
            order.status = 'completed'
            update_fields.append('status')
            order.save(update_fields=update_fields)
            return True, "Заказ завершен"

        if action == 'cancel':
            order.status = 'cancelled'
            update_fields.append('status')
            order.save(update_fields=update_fields)
            return True, "Заказ отменен"

        if action == 'expire':
            if order.payment_status == 'succeeded':
                return False, "Заказ уже оплачен"
            order.status = 'expired'
            update_fields.append('status')
            order.save(update_fields=update_fields)
            return True, "Заказ помечен как expired"

        if action == 'paid':
            if order.payment_status == 'succeeded':
                return False, "Оплата уже отмечена"
            order.payment_status = 'succeeded'
            order.payment_method = order.payment_method or 'transfer'
            order.paid_at = timezone.now()
            update_fields.extend(['payment_status', 'payment_method', 'paid_at'])
            order.save(update_fields=update_fields)
            return True, "Оплата отмечена"

        if action == 'unpaid':
            if order.payment_status == 'not_paid':
                return False, "Заказ уже в статусе «не оплачен»"
            order.payment_status = 'not_paid'
            order.paid_at = None
            update_fields.extend(['payment_status', 'paid_at'])
            order.save(update_fields=update_fields)
            return True, "Оплата сброшена"

        return False, "Неизвестное действие"

    return await _apply()
