"""
Admin panel handlers: /admin, order list, status changes, ready photo,
export, transfer payment details.
"""
import csv
import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardButton, InlineKeyboardMarkup,
    FSInputFile, ReplyKeyboardRemove,
)
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from catalog.models import Order

from ..constants import ADMIN_ORDERS_PAGE_SIZE, DELIVERY_MANUAL_NOTE
from ..states import AdminStates
from ..utils import (
    to_decimal, format_money,
    order_status_icon, is_cancel_command,
    payment_status_label, payment_method_label,
)
from ..keyboards import get_admin_keyboard, get_main_keyboard
from ..globals import get_bot
from ..services import (
    is_bot_admin,
    download_telegram_file_bytes,
    calculate_order_breakdown,
    build_transfer_payment_text,
    refresh_order_group_message,
    apply_group_order_action,
    apply_current_transfer_template,
    notify_customer_transfer_details,
)

logger = logging.getLogger(__name__)

router = Router()


# ── Admin check helpers ──────────────────────────────────────────

async def require_admin_message(message: Message) -> bool:
    ok = await is_bot_admin(message.from_user.id, message.from_user.username)
    if not ok:
        await message.answer("⛔️ Нет доступа.")
    return ok


async def require_admin_callback(callback: CallbackQuery) -> bool:
    ok = await is_bot_admin(callback.from_user.id, callback.from_user.username)
    if not ok:
        await callback.answer("Нет доступа", show_alert=True)
    return ok


# ── Entry / exit ─────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_entry(message: Message, state: FSMContext):
    if not await require_admin_message(message):
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(),
    )


@router.message(F.text == "🔙 Выйти")
async def admin_exit(message: Message, state: FSMContext):
    if not await is_bot_admin(message.from_user.id, message.from_user.username):
        return
    await state.clear()
    await message.answer("Ок.", reply_markup=get_main_keyboard())


# ── Orders list ──────────────────────────────────────────────────

async def build_admin_orders_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    page = max(0, int(page))

    @sync_to_async
    def _fetch():
        qs = Order.objects.order_by('-created_at')
        total = qs.count()
        offset = page * ADMIN_ORDERS_PAGE_SIZE
        orders = list(qs[offset: offset + ADMIN_ORDERS_PAGE_SIZE])
        return total, orders

    total, orders = await _fetch()

    if total == 0:
        return "📦 <b>Заказы</b>\n\nПока заказов нет.", InlineKeyboardMarkup(inline_keyboard=[])

    pages = max(1, (total + ADMIN_ORDERS_PAGE_SIZE - 1) // ADMIN_ORDERS_PAGE_SIZE)
    page = min(page, pages - 1)

    text = f"📦 <b>Заказы</b> (страница {page+1}/{pages})\n\n"

    buttons: list[list[InlineKeyboardButton]] = []
    for o in orders:
        icon = order_status_icon(o.status)
        customer = (o.customer_name or '').strip() or 'Без имени'
        text += f"#{o.id} {icon} {customer} — {o.get_status_display()}\n"
        buttons.append([InlineKeyboardButton(text=f"#{o.id} {icon} {customer}", callback_data=f"admin_order_{o.id}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_orders_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_orders_{page+1}"))
    buttons.append(nav)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return text, keyboard


@router.message(F.text == "📦 Заказы")
async def admin_orders_list(message: Message):
    if not await require_admin_message(message):
        return
    text, keyboard = await build_admin_orders_page(0)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data.startswith("admin_orders_"))
async def admin_orders_list_page(callback: CallbackQuery):
    if not await require_admin_callback(callback):
        return
    try:
        page = int(callback.data.split("_")[2])
    except Exception:
        page = 0
    text, keyboard = await build_admin_orders_page(page)
    await callback.answer()
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Order detail ─────────────────────────────────────────────────

async def build_admin_order_detail(order_id: int) -> tuple[str, InlineKeyboardMarkup]:
    @sync_to_async
    def _fetch():
        order = Order.objects.prefetch_related('items').get(pk=order_id)
        items = list(order.items.all())
        return order, items

    order, items = await _fetch()
    items_total, delivery_price, total = calculate_order_breakdown(order, items)
    manual_delivery = DELIVERY_MANUAL_NOTE.lower() in (order.comment or "").lower()
    delivery_display = "уточняется вручную" if manual_delivery and delivery_price <= 0 else f"{format_money(delivery_price)} ₽"

    icon = order_status_icon(order.status)
    created = timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M')
    text = f"{icon} <b>Заказ #{order.id}</b>\n"
    text += f"Статус: <b>{order.get_status_display()}</b>\n"
    text += f"Создан: {created}\n\n"
    text += f"👤 {order.customer_name}\n"
    text += f"📞 {order.phone}\n"
    text += f"📍 {order.address}\n"
    if order.requested_delivery:
        text += f"📅 {order.requested_delivery}\n"
    if order.is_preorder:
        text += "🌷 Предзаказ: да\n"
    text += f"🧾 Товары: {format_money(items_total)} ₽\n"
    text += f"🚚 Доставка: {delivery_display}\n"
    text += f"💰 Итог: {format_money(total)} ₽\n"
    text += f"💳 Оплата: {payment_status_label(order.payment_status)} · {payment_method_label(order.payment_method)}\n"
    if order.payment_method == 'transfer':
        if order.transfer_details:
            text += f"💳 Реквизиты: <code>{html.escape(order.transfer_details)}</code>\n"
        else:
            text += "💳 Реквизиты: не указаны\n"
    if order.processing_by_user_id or order.processing_by_username:
        assignee = f"@{order.processing_by_username}" if order.processing_by_username else f"id={order.processing_by_user_id}"
        text += f"👨‍🔧 Обрабатывает: {assignee}\n"
    if order.discount_percent:
        text += f"🎁 Скидка: {order.discount_percent}%\n"
    if order.comment:
        text += f"\n💬 Комментарий:\n{order.comment}\n"

    if items:
        text += "\n🌸 Позиции:\n"
        for it in items[:10]:
            text += f"- {it.product_name} x{it.quantity}\n"
        if len(items) > 10:
            text += f"... и еще {len(items)-10}\n"

    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([
        InlineKeyboardButton(text="🟡 В работу", callback_data=f"admin_status_{order.id}_processing"),
        InlineKeyboardButton(text="📦 Готов (фото)", callback_data=f"admin_ready_{order.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ Завершить", callback_data=f"admin_status_{order.id}_completed"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_status_{order.id}_cancelled"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⌛ Просрочить", callback_data=f"admin_status_{order.id}_expired"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ Актуальные реквизиты", callback_data=f"svc_paycurrent_{order.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="💳 Реквизиты перевода", callback_data=f"admin_payreq_{order.id}"),
    ])

    if order.ready_photo:
        buttons.append([InlineKeyboardButton(text="📷 Обновить фото готовности", callback_data=f"admin_ready_{order.id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_orders_0")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("admin_order_"))
async def admin_order_open(callback: CallbackQuery):
    if not await require_admin_callback(callback):
        return
    await callback.answer()
    order_id = int(callback.data.split("_")[2])
    text, keyboard = await build_admin_order_detail(order_id)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Status changes ───────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_status_"))
async def admin_order_set_status(callback: CallbackQuery):
    if not await require_admin_callback(callback):
        return
    parts = callback.data.split("_", 3)
    order_id = int(parts[2])
    new_status = parts[3]
    actor_id = callback.from_user.id
    actor_username = (callback.from_user.username or '').strip().lstrip('@')
    allowed_statuses = {choice[0] for choice in Order.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        await callback.answer("Недопустимый статус", show_alert=True)
        return

    if new_status == 'ready':
        await callback.answer("Для статуса «Готов» нужен снимок.", show_alert=True)
        return

    @sync_to_async
    def _update():
        order = Order.objects.get(pk=order_id)
        order.status = new_status
        if new_status == 'processing':
            order.processing_by_user_id = actor_id
            order.processing_by_username = actor_username
            order.save(update_fields=['status', 'processing_by_user_id', 'processing_by_username', 'updated_at', 'phone_normalized'])
            return
        order.save(update_fields=['status', 'updated_at', 'phone_normalized'])

    try:
        await _update()
    except Exception as exc:
        await callback.answer("Не удалось обновить статус", show_alert=True)
        logger.warning("Admin status update failed: %s", exc)
        return

    await refresh_order_group_message(order_id)
    text, keyboard = await build_admin_order_detail(order_id)
    await callback.answer("Статус обновлен")
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Ready photo ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ready_"))
async def admin_order_ready_photo_request(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return
    await callback.answer()
    order_id = int(callback.data.split("_")[2])
    await state.set_state(AdminStates.waiting_for_ready_photo)
    await state.update_data(admin_ready_order_id=order_id)
    await callback.message.answer(
        f"📷 Отправьте фото готового букета для заказа #{order_id}.\n\n"
        "После этого статус будет изменён на «Готов» и клиент получит фото.\n"
        "<i>/cancel — отмена</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdminStates.waiting_for_ready_photo)
async def admin_order_ready_photo_receive(message: Message, state: FSMContext):
    if not await require_admin_message(message):
        return
    is_private_chat = bool(getattr(message.chat, 'type', '') == 'private')
    done_markup = get_admin_keyboard() if is_private_chat else ReplyKeyboardRemove()

    if is_cancel_command(message.text):
        await state.clear()
        await message.answer("Ок, отменено.", reply_markup=done_markup)
        return

    if not message.photo and not message.document:
        await message.answer("Пришлите фото (как изображение) или файл-картинку.")
        return

    data = await state.get_data()
    order_id = int(data.get('admin_ready_order_id') or 0)
    if not order_id:
        await state.clear()
        await message.answer("Не найден заказ. Повторите.", reply_markup=get_admin_keyboard())
        return

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        await message.answer("Не удалось прочитать файл. Попробуйте еще раз.")
        return

    content, basename = await download_telegram_file_bytes(file_id)
    if not content:
        await message.answer("Не удалось скачать фото. Попробуйте еще раз.")
        return

    filename = basename or f"order_{order_id}_ready.jpg"

    @sync_to_async
    def _save() -> tuple[str, int]:
        order = Order.objects.get(pk=order_id)
        prev_status = order.status
        order.ready_photo.save(filename, ContentFile(content), save=False)
        order.status = 'ready'
        order.save()
        return prev_status, int(order.telegram_user_id)

    try:
        prev_status, customer_chat_id = await _save()
    except Exception as exc:
        logger.warning("Ready photo save failed: %s", exc)
        await message.answer("❌ Не удалось сохранить фото.", reply_markup=done_markup)
        await state.clear()
        return

    if prev_status == 'ready':
        bot = get_bot()
        try:
            order = await sync_to_async(Order.objects.get)(pk=order_id)
            caption = f"📦 Ваш заказ #{order_id} готов."
            photo_path = await sync_to_async(lambda: order.ready_photo.path)()
            await bot.send_photo(chat_id=customer_chat_id, photo=FSInputFile(photo_path), caption=caption)
        except Exception as exc:
            logger.warning("Manual ready photo notify failed: %s", exc)

    await state.clear()
    await refresh_order_group_message(order_id)
    await message.answer(f"✅ Фото сохранено, заказ #{order_id} помечен как «Готов».", reply_markup=done_markup)


# ── Transfer details ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_payreq_"))
async def admin_order_payment_details_request(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return
    order_id = int(callback.data.split("_")[2])
    current_state = await state.get_state()
    current_data = await state.get_data()
    current_order_id = int(current_data.get('admin_transfer_order_id') or 0)
    if current_state == AdminStates.waiting_for_transfer_details.state and current_order_id == order_id:
        await callback.answer("Режим уже активен")
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_transfer_details)
    await state.update_data(admin_transfer_order_id=order_id)
    await callback.message.answer(
        f"💳 Введите реквизиты для заказа #{order_id} одним сообщением.\n\n"
        "Пример: +7 900 000-00-00 (СБП, Иван И.)\n"
        "/cancel — отмена",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdminStates.waiting_for_transfer_details)
async def admin_receive_transfer_details(message: Message, state: FSMContext):
    if not await require_admin_message(message):
        return

    text = (message.text or '').strip()
    if is_cancel_command(text):
        await state.clear()
        await message.answer("Ок, отменено.", reply_markup=get_admin_keyboard())
        return

    if not text:
        await message.answer("Отправьте реквизиты текстом одним сообщением.")
        return

    data = await state.get_data()
    order_id = int(data.get('admin_transfer_order_id') or 0)
    if not order_id:
        await state.clear()
        await message.answer("Не найден номер заказа. Повторите действие.", reply_markup=get_admin_keyboard())
        return

    @sync_to_async
    def _save_details() -> Order | None:
        order = Order.objects.filter(pk=order_id).first()
        if not order:
            return None
        order.transfer_details = text
        order.payment_method = 'transfer'
        if order.payment_status == 'not_paid':
            order.payment_status = 'pending'
            order.save(update_fields=['transfer_details', 'payment_method', 'payment_status', 'updated_at', 'phone_normalized'])
        else:
            order.save(update_fields=['transfer_details', 'payment_method', 'updated_at', 'phone_normalized'])
        return order

    order = await _save_details()
    if not order:
        await state.clear()
        await message.answer("Заказ не найден.", reply_markup=get_admin_keyboard())
        return

    await state.clear()
    await refresh_order_group_message(order.id)

    bot = get_bot()
    try:
        await bot.send_message(
            chat_id=order.telegram_user_id,
            text=build_transfer_payment_text(order),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("Не удалось отправить реквизиты клиенту по заказу %s: %s", order.id, exc)

    await message.answer(
        f"✅ Реквизиты сохранены для заказа #{order.id} и отправлены клиенту.",
        reply_markup=get_admin_keyboard(),
    )


# ── Service group callbacks (svc_*) ─────────────────────────────

@router.callback_query(F.data.startswith("svc_"))
async def service_group_order_actions(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return

    parts = (callback.data or "").split("_", 2)
    if len(parts) != 3:
        await callback.answer("Некорректная команда", show_alert=True)
        return

    action = parts[1]
    try:
        order_id = int(parts[2])
    except Exception:
        await callback.answer("Некорректный номер заказа", show_alert=True)
        return

    if action == 'payreq':
        current_state = await state.get_state()
        current_data = await state.get_data()
        current_order_id = int(current_data.get('admin_transfer_order_id') or 0)
        if current_state == AdminStates.waiting_for_transfer_details.state and current_order_id == order_id:
            await callback.answer("Режим ввода реквизитов для этого заказа уже активен")
            return
        await state.set_state(AdminStates.waiting_for_transfer_details)
        await state.update_data(admin_transfer_order_id=order_id)
        await callback.answer("Введите реквизиты переводом следующим сообщением")
        await callback.message.answer(
            f"💳 Введите реквизиты для заказа #{order_id} одним сообщением.\n\n"
            "Пример: +7 900 000-00-00 (СБП, Иван И.)\n"
            "/cancel — отмена"
        )
        return

    if action == 'ready':
        await state.set_state(AdminStates.waiting_for_ready_photo)
        await state.update_data(admin_ready_order_id=order_id)
        await callback.answer("Пришлите фото готового букета")
        await callback.message.answer(
            f"📷 Отправьте фото готового букета для заказа #{order_id}.\n\n"
            "После фото заказ получит статус «Готов», а клиент получит уведомление.\n"
            "/cancel — отмена",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if action == 'paycurrent':
        changed, info = await apply_current_transfer_template(order_id)
        if changed:
            await refresh_order_group_message(order_id)
            await notify_customer_transfer_details(order_id)
            await callback.answer(f"Реквизиты подтверждены по шаблону: {info}")
        else:
            await callback.answer(info, show_alert=True)
        return

    changed, result_text = await apply_group_order_action(
        order_id=order_id,
        action=action,
        actor_id=callback.from_user.id,
        actor_username=callback.from_user.username,
    )
    if changed:
        await refresh_order_group_message(order_id)
        if action in {'paid', 'unpaid'}:
            @sync_to_async
            def _get_payment_meta():
                order = Order.objects.filter(pk=order_id).first()
                if not order:
                    return None, None
                return int(order.telegram_user_id), to_decimal(order.total_price)

            chat_id, amount = await _get_payment_meta()
            if chat_id:
                bot = get_bot()
                try:
                    if action == 'paid':
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"✅ Оплата по заказу #{order_id} подтверждена.\n"
                                f"Сумма: {format_money(amount)} ₽."
                            ),
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"ℹ️ Оплата по заказу #{order_id} переведена в статус «не оплачено».\n"
                                "Если вы уже переводили деньги, отправьте чек в этот чат."
                            ),
                        )
                except Exception as exc:
                    logger.warning("Не удалось отправить клиенту статус оплаты по заказу %s: %s", order_id, exc)
    await callback.answer(result_text, show_alert=not changed)


# ── Export orders ────────────────────────────────────────────────

@router.message(F.text == "📤 Экспорт заказов")
async def admin_export_orders(message: Message):
    if not await require_admin_message(message):
        return

    @sync_to_async
    def _export() -> str:
        export_dir = Path(settings.MEDIA_ROOT) / 'exports'
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / 'orders_latest.csv'

        orders = Order.objects.prefetch_related('items').order_by('-created_at')
        with path.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow([
                'id', 'created_at', 'status', 'customer_name', 'phone', 'address',
                'total_price', 'discount_percent', 'has_subscription', 'items'
            ])
            for o in orders:
                created = timezone.localtime(o.created_at).strftime('%Y-%m-%d %H:%M:%S')
                items = '; '.join([f"{it.product_name} x{it.quantity}" for it in o.items.all()][:50])
                w.writerow([
                    o.id, created, o.status, o.customer_name, o.phone, o.address,
                    str(o.total_price), o.discount_percent, int(o.has_subscription), items
                ])
        return str(path)

    file_path = await _export()
    await message.answer_document(FSInputFile(file_path), caption="📤 Экспорт заказов (CSV). Файл обновляется при каждом экспорте.")
