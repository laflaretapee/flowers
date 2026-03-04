"""
Order flow: quantity selection, name, phone, address, comment, order creation.
Also includes custom bouquet and pre-order flows since they share the same
order creation pipeline.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction

from catalog.models import Order, OrderItem, Product, normalize_phone
from catalog.taxi_integration import TaxiDeliveryIntegration
from catalog.payments import (
    update_order_from_payment,
    create_payment_for_order,
    get_return_url,
    get_manual_payment_url,
    yookassa_enabled,
)

from ..constants import DELIVERY_MANUAL_NOTE, CARD_PAYMENT_MAINTENANCE_NOTE
from ..states import OrderStates, CustomBouquetStates, PreOrderStates
from ..utils import to_decimal, format_money, parse_budget_value
from ..keyboards import get_main_keyboard, get_quantity_keyboard, get_address_confirm_keyboard
from ..services import check_user_subscription, post_order_to_group

logger = logging.getLogger(__name__)

router = Router()


# ── Custom bouquet flow entry ────────────────────────────────────

async def start_custom_bouquet_flow(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CustomBouquetStates.waiting_for_style)
    await message.answer(
        "💐 <b>Соберем букет по вашим пожеланиям</b>\n\n"
        "Расскажите, какие цветы, цвета или повод вы хотите учесть.\n\n"
        "<i>Или отправьте /cancel для отмены</i>",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "💐 Собрать свой букет")
async def start_custom_bouquet_from_menu(message: Message, state: FSMContext):
    await start_custom_bouquet_flow(message, state)


# ── Pre-order entry ──────────────────────────────────────────────

@router.message(F.text == "🌷 Предзаказ на 8 марта")
async def start_preorder(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(preorder_mode=True)
    payment_flow = (getattr(settings, 'PAYMENT_FLOW', 'transfer') or 'transfer').strip().lower()
    if payment_flow == 'online':
        payment_line = "Для предзаказа будет сразу создана ссылка на оплату."
    else:
        payment_line = (
            f"{CARD_PAYMENT_MAINTENANCE_NOTE} "
            "После подтверждения заказа менеджер отправит реквизиты перевода."
        )
    await message.answer(
        "🌷 <b>Режим предзаказа включен</b>\n\n"
        "Выберите букет в каталоге. После выбора укажете дату и время вручения.\n"
        f"{payment_line}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    from .catalog import send_catalog_menu
    await send_catalog_menu(message)


# ── Begin order flow (shared by catalog order + confirm_order) ───

async def begin_order_flow(callback: CallbackQuery, state: FSMContext, product_id: int):
    try:
        product = await sync_to_async(Product.objects.get)(id=product_id, is_active=True)
    except Product.DoesNotExist:
        await callback.answer("Товар не найден")
        return

    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)

    promo_enabled = getattr(settings, 'PROMO_ENABLED', True)
    discount_percent = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)

    price = to_decimal(await sync_to_async(lambda: product.price)())
    product_name = await sync_to_async(lambda: product.name)()
    state_data = await state.get_data()
    preorder_mode = bool(state_data.get('preorder_mode'))

    await state.set_state(OrderStates.waiting_for_quantity)
    await state.update_data(
        pending_order_product_id=product_id,
        pending_order_product_name=product_name,
        pending_order_product_price=str(price),
        pending_order_is_subscribed=is_subscribed,
        pending_order_promo_enabled=promo_enabled,
        pending_order_discount_percent=discount_percent,
        pending_order_preorder_mode=preorder_mode,
    )
    await callback.message.answer(
        "🔢 <b>Выберите количество</b>\n\n"
        f"{product_name}\n"
        f"Цена за 1 шт: {format_money(price)} ₽\n\n"
        "Отправьте число (например 7) или нажмите кнопку.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_quantity_keyboard(),
    )


@router.callback_query(F.data.startswith("order_"))
async def start_order(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    await begin_order_flow(callback, state, product_id)


@router.callback_query(F.data.startswith("confirm_order_"))
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await begin_order_flow(callback, state, product_id)


# ── Quantity step ────────────────────────────────────────────────

@router.message(OrderStates.waiting_for_quantity)
async def process_order_quantity(message: Message, state: FSMContext):
    if message.text in {"/cancel", "❌ Отмена"}:
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите количество числом. Пример: 5", reply_markup=get_quantity_keyboard())
        return

    quantity = int(raw)
    if quantity < 1 or quantity > 999:
        await message.answer("Количество должно быть от 1 до 999.", reply_markup=get_quantity_keyboard())
        return

    data = await state.get_data()
    product_id = data.get('pending_order_product_id')
    product_name = data.get('pending_order_product_name') or 'Букет'
    product_price = to_decimal(data.get('pending_order_product_price') or '0')
    is_subscribed = bool(data.get('pending_order_is_subscribed'))
    promo_enabled = bool(data.get('pending_order_promo_enabled', True))
    discount_percent = int(data.get('pending_order_discount_percent') or 10)
    preorder_mode = bool(data.get('pending_order_preorder_mode'))

    if not product_id:
        await state.clear()
        await message.answer(
            "Не удалось найти выбранный букет. Выберите товар еще раз в каталоге.",
            reply_markup=get_main_keyboard(),
        )
        return

    if preorder_mode:
        await state.set_state(PreOrderStates.waiting_for_datetime)
        await state.update_data(
            pending_order_quantity=quantity,
            pending_order_product_id=product_id,
            pending_order_product_name=product_name,
            pending_order_product_price=str(product_price),
            pending_order_is_subscribed=is_subscribed,
            pending_order_promo_enabled=promo_enabled,
            pending_order_discount_percent=discount_percent,
        )
        total_products = (product_price * Decimal(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        await message.answer(
            "🌷 <b>Предзаказ на 8 марта</b>\n\n"
            f"{product_name}\n"
            f"Количество: {quantity}\n"
            f"Сумма товара: {format_money(total_products)} ₽\n\n"
            "Теперь укажите дату и время вручения (например: 8 марта, 12:00).\n"
            "<i>/cancel — отмена</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await start_order_name_step(
        message, state,
        product_id=int(product_id),
        product_name=product_name,
        product_price=product_price,
        quantity=quantity,
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=False,
    )


# ── Pre-order datetime step ──────────────────────────────────────

@router.message(PreOrderStates.waiting_for_datetime)
async def process_preorder_datetime(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Предзаказ отменен.", reply_markup=get_main_keyboard())
        return

    requested_delivery = (message.text or '').strip()
    if not requested_delivery:
        await message.answer("Укажите дату и время текстом. Пример: 8 марта, 12:00")
        return

    data = await state.get_data()
    product_id = data.get('pending_order_product_id')
    product_name = data.get('pending_order_product_name') or 'Букет'
    product_price = to_decimal(data.get('pending_order_product_price') or '0')
    quantity = int(data.get('pending_order_quantity') or 1)
    is_subscribed = bool(data.get('pending_order_is_subscribed'))
    promo_enabled = bool(data.get('pending_order_promo_enabled', True))
    discount_percent = int(data.get('pending_order_discount_percent') or 10)

    if not product_id:
        await state.clear()
        await message.answer(
            "Не удалось найти выбранный букет. Нажмите «🌷 Предзаказ на 8 марта» и выберите заново.",
            reply_markup=get_main_keyboard(),
        )
        return

    await start_order_name_step(
        message, state,
        product_id=int(product_id),
        product_name=product_name,
        product_price=product_price,
        quantity=quantity,
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=False,
        is_preorder=True,
        requested_delivery=requested_delivery,
    )


# ── Custom bouquet steps ─────────────────────────────────────────

@router.message(CustomBouquetStates.waiting_for_style)
async def process_custom_style(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заявка отменена.", reply_markup=get_main_keyboard())
        return

    if not message.text:
        await message.answer("Пожалуйста, опишите пожелания текстом.")
        return

    await state.update_data(custom_style=message.text)
    await state.set_state(CustomBouquetStates.waiting_for_budget)
    await message.answer(
        "💰 Укажите бюджет (можно диапазон), либо нажмите «Пропустить».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⏭ Пропустить")], [KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(CustomBouquetStates.waiting_for_budget)
async def process_custom_budget(message: Message, state: FSMContext):
    if message.text in ["/cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer("❌ Заявка отменена.", reply_markup=get_main_keyboard())
        return

    budget_text = "" if message.text in ["/skip", "⏭ Пропустить"] else (message.text or "")
    await state.update_data(custom_budget=budget_text)
    await state.set_state(CustomBouquetStates.waiting_for_deadline)
    await message.answer(
        "🕒 Когда нужен букет? (дата/время или «прямо сегодня»)",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⏭ Пропустить")], [KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(CustomBouquetStates.waiting_for_deadline)
async def process_custom_deadline(message: Message, state: FSMContext):
    if message.text in ["/cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer("❌ Заявка отменена.", reply_markup=get_main_keyboard())
        return

    deadline_text = "" if message.text in ["/skip", "⏭ Пропустить"] else (message.text or "")
    await state.update_data(custom_deadline=deadline_text)
    await _begin_custom_order_contact(message, state)


async def _begin_custom_order_contact(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_subscribed = await check_user_subscription(user_id)

    promo_enabled = getattr(settings, 'PROMO_ENABLED', True)
    discount_percent = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)

    await message.answer(
        "💐 <b>Индивидуальный букет</b>\n\n"
        "Учтем ваши пожелания и оформим заявку.",
        parse_mode=ParseMode.HTML,
    )
    await start_order_name_step(
        message, state,
        product_id=None,
        product_name="Индивидуальный букет",
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=True,
    )


# ── Cancel command ───────────────────────────────────────────────

@router.message(Command("cancel"))
async def cancel_order(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
    else:
        await message.answer("Нет активного заказа.")


# ── Name step ────────────────────────────────────────────────────

async def start_order_name_step(
    message: Message,
    state: FSMContext,
    *,
    product_id: int | None,
    product_name: str,
    product_price: Decimal | None = None,
    quantity: int = 1,
    is_subscribed: bool,
    promo_enabled: bool,
    discount_percent: int,
    is_custom: bool = False,
    is_preorder: bool = False,
    requested_delivery: str = '',
):
    quantity = max(1, int(quantity or 1))
    text = "🛒 <b>Оформление заказа</b>\n\n"
    text += f"🌸 {product_name}\n"
    if product_price is not None:
        total_products = (product_price * Decimal(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        text += f"💰 Цена за шт: {format_money(product_price)} ₽\n"
        text += f"🔢 Количество: {quantity}\n"
        text += f"💳 Сумма товара: {format_money(total_products)} ₽\n"
    if is_preorder:
        requested_delivery_text = requested_delivery.strip() or "8 марта, удобное время"
        text += f"🌷 Предзаказ: {requested_delivery_text}\n"

    if promo_enabled:
        if is_subscribed:
            text += (
                f"🎁 Скидка {discount_percent}% на первый полученный заказ "
                f"по номеру телефона будет рассчитана после указания номера.\n\n"
            )
        else:
            text += (
                f"🎁 Скидка {discount_percent}% доступна подписчикам.\n"
                f"Подпишитесь на канал и мы применим скидку к первому полученному заказу.\n\n"
            )
    else:
        text += "\n"

    text += "👤 <b>Шаг 1/4:</b> Введите ваше имя\n\n"
    text += "<i>Или отправьте /cancel для отмены</i>"

    await state.set_state(OrderStates.waiting_for_name)
    await state.update_data(
        product_id=product_id,
        product_name=product_name,
        order_quantity=quantity,
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=is_custom,
        is_preorder=is_preorder,
        requested_delivery=(requested_delivery or '').strip(),
        preorder_mode=False,
        pending_order_product_id=None,
        pending_order_product_name=None,
        pending_order_is_subscribed=None,
        pending_order_promo_enabled=None,
        pending_order_discount_percent=None,
        pending_order_quantity=None,
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())


@router.message(OrderStates.waiting_for_name)
async def process_order_name(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.text:
        await message.answer("Пожалуйста, отправьте имя текстом.")
        return

    await state.update_data(customer_name=message.text)

    phone_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await state.set_state(OrderStates.waiting_for_phone)
    await message.answer(
        "📱 <b>Шаг 2/4:</b> Отправьте ваш номер телефона\n\n"
        "Нажмите кнопку ниже или введите вручную:",
        parse_mode=ParseMode.HTML,
        reply_markup=phone_keyboard,
    )


# ── Phone step ───────────────────────────────────────────────────

@router.message(OrderStates.waiting_for_phone)
async def process_order_phone(message: Message, state: FSMContext):
    if message.text == "❌ Отмена" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.contact and not message.text:
        await message.answer("Пожалуйста, отправьте номер телефона текстом или кнопкой.")
        return

    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text

    normalized_phone = normalize_phone(phone)
    data = await state.get_data()
    promo_enabled = data.get('promo_enabled', True)
    discount_percent = data.get('discount_percent', 10)
    is_subscribed = data.get('is_subscribed', False)

    has_completed_orders = False
    if normalized_phone:
        has_completed_orders = await sync_to_async(
            Order.objects.filter(phone_normalized=normalized_phone, status='completed').exists
        )()

    discount = discount_percent if promo_enabled and is_subscribed and not has_completed_orders else 0

    await state.update_data(phone=phone, phone_normalized=normalized_phone, discount=discount)

    location_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await state.set_state(OrderStates.waiting_for_address)
    discount_note = ""
    if promo_enabled:
        if is_subscribed:
            if discount > 0:
                discount_note = f"🎁 Скидка {discount}% будет применена.\n\n"
            else:
                discount_note = "ℹ️ Скидка на первый полученный заказ уже использована.\n\n"
        else:
            discount_note = "ℹ️ Скидка доступна после подписки на канал.\n\n"

    await message.answer(
        f"{discount_note}"
        "📍 <b>Шаг 3/4:</b> Укажите адрес доставки\n\n"
        "Отправьте геолокацию или введите адрес текстом:",
        parse_mode=ParseMode.HTML,
        reply_markup=location_keyboard,
    )


# ── Address step ─────────────────────────────────────────────────

@router.message(OrderStates.waiting_for_address)
async def process_order_address(message: Message, state: FSMContext):
    if message.text == "❌ Отмена" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    awaiting_confirmation = data.get('awaiting_address_confirmation', False)

    if message.location:
        taxi_integration = TaxiDeliveryIntegration()
        address_info = await sync_to_async(taxi_integration.reverse_geocode)(
            message.location.latitude,
            message.location.longitude,
        )

        if address_info:
            address = address_info['formatted_address']
            await state.update_data(address=address, awaiting_address_confirmation=True)
            await message.answer(
                f"📍 <b>Адрес определен:</b>\n\n{address}\n\n"
                "Подтвердите адрес или введите вручную:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_address_confirm_keyboard(),
            )
        else:
            address = f"📍 Координаты: {message.location.latitude:.6f}, {message.location.longitude:.6f}"
            await state.update_data(address=address, awaiting_address_confirmation=True)
            await message.answer(
                "⚠️ Не удалось определить адрес по геолокации.\n"
                "Подтвердите адрес или введите вручную:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_address_confirm_keyboard(),
            )
        return

    if awaiting_confirmation:
        if message.text == "✅ Подтвердить":
            await state.update_data(awaiting_address_confirmation=False)
            await _ask_for_comment(message, state)
            return
        if message.text == "✏️ Ввести вручную":
            await state.update_data(awaiting_address_confirmation=False)
            await message.answer("Введите адрес текстом:", reply_markup=ReplyKeyboardRemove())
            return
        if message.text == "❌ Отмена":
            await state.clear()
            await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
            return

    if not message.text:
        await message.answer("Отправьте адрес текстом или геолокацию.")
        return

    address = message.text
    await state.update_data(address=address, awaiting_address_confirmation=False)
    await _ask_for_comment(message, state)


# ── Comment step ─────────────────────────────────────────────────

async def _ask_for_comment(message: Message, state: FSMContext):
    await state.set_state(OrderStates.waiting_for_comment)
    await message.answer(
        "💬 <b>Шаг 4/4:</b> Добавьте комментарий к заказу\n\n"
        "(пожелания, время доставки и т.д.)\n\n"
        "<i>Или отправьте /skip чтобы пропустить</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⏭ Пропустить")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(OrderStates.waiting_for_comment)
async def process_order_comment(message: Message, state: FSMContext):
    if message.text == "/cancel" or message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.text:
        await message.answer("Пожалуйста, отправьте комментарий текстом или /skip.")
        return

    comment = "" if message.text in ["/skip", "⏭ Пропустить"] else message.text
    await state.update_data(comment=comment)

    await _create_order(message, state)


# ── Order creation ───────────────────────────────────────────────

async def _create_order(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        user = message.from_user

        product_id = data.get('product_id')
        discount = data.get('discount', 0)
        order_quantity = max(1, int(data.get('order_quantity') or 1))
        is_custom = data.get('is_custom', False)
        is_preorder = bool(data.get('is_preorder', False))
        requested_delivery = (data.get('requested_delivery') or '').strip()
        custom_style = data.get('custom_style', '')
        custom_budget = data.get('custom_budget', '')
        custom_deadline = data.get('custom_deadline', '')

        name = data.get('customer_name', user.first_name)
        phone = data.get('phone', 'Не указан')
        address = data.get('address', 'Не указан')
        comment = data.get('comment', '')
        payment_flow = (getattr(settings, 'PAYMENT_FLOW', 'transfer') or 'transfer').strip().lower()
        use_transfer_payment = payment_flow != 'online'
        if not requested_delivery and custom_deadline:
            requested_delivery = custom_deadline

        product = None
        product_name = data.get('product_name', 'Букет')
        if not is_custom:
            product = await sync_to_async(Product.objects.get)(id=product_id)
            product_name = await sync_to_async(lambda: product.name)()

        is_subscribed = await check_user_subscription(user.id)

        shop_address = getattr(
            settings, 'SHOP_ADDRESS',
            "Трактовая улица, 78А, село Раевский, Альшеевский район, Республика Башкортостан, 452120",
        )
        taxi_integration = TaxiDeliveryIntegration()
        delivery_info = await sync_to_async(taxi_integration.calculate_delivery_cost)(
            from_address=shop_address,
            to_address=address,
            order_weight=1,
        )

        delivery_manual_required = bool(delivery_info.get('requires_manual_price'))
        delivery_cost = to_decimal(delivery_info['cost'])
        if delivery_manual_required:
            delivery_cost = Decimal('0')
        product_price_raw = Decimal('0')
        products_subtotal_raw = Decimal('0')
        product_price = Decimal('0')

        if is_custom:
            budget_value = parse_budget_value(custom_budget)
            if budget_value is not None:
                product_price_raw = budget_value
                products_subtotal_raw = budget_value
                discount_ratio = (Decimal('100') - Decimal(discount)) / Decimal('100')
                product_price = (products_subtotal_raw * discount_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                discount = 0
        else:
            product_price_raw = to_decimal(await sync_to_async(lambda: product.price)())
            products_subtotal_raw = (product_price_raw * Decimal(order_quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            discount_ratio = (Decimal('100') - Decimal(discount)) / Decimal('100')
            product_price = (products_subtotal_raw * discount_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        final_price = (product_price + delivery_cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        comment_parts = []
        if comment:
            comment_parts.append(comment)
        if is_custom:
            custom_lines = []
            if custom_style:
                custom_lines.append(f"Пожелания: {custom_style}")
            if custom_budget:
                custom_lines.append(f"Бюджет: {custom_budget}")
            if custom_deadline:
                custom_lines.append(f"Когда нужен: {custom_deadline}")
            if custom_lines:
                comment_parts.append("Запрос на индивидуальный букет:\n" + "\n".join(custom_lines))

        if delivery_manual_required:
            comment_parts.append(DELIVERY_MANUAL_NOTE)
        else:
            comment_parts.append(
                f"Доставка через {delivery_info.get('service', 'такси')}. Примерное время: {delivery_info['duration']} мин."
            )
        order_comment = "\n\n".join(comment_parts).strip()

        @sync_to_async
        def create_order_in_db():
            with transaction.atomic():
                order = Order.objects.create(
                    telegram_user_id=user.id,
                    telegram_username=user.username or '',
                    customer_name=name,
                    phone=phone,
                    address=address,
                    comment=order_comment,
                    is_preorder=is_preorder,
                    requested_delivery=requested_delivery,
                    items_subtotal=product_price,
                    delivery_price=delivery_cost,
                    total_price=final_price,
                    discount_percent=discount,
                    has_subscription=is_subscribed,
                    payment_method='transfer' if use_transfer_payment else 'online',
                )
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product_name,
                    price=product_price_raw,
                    quantity=1 if is_custom else order_quantity,
                )
                return order

        order = await create_order_in_db()

        payment_url = ''
        has_yookassa = yookassa_enabled()
        if not use_transfer_payment and is_preorder and final_price > 0 and not delivery_manual_required:
            @sync_to_async
            def _prepare_payment() -> tuple[str, str]:
                db_order = Order.objects.get(pk=order.id)
                current_payment_url = db_order.payment_url or ''

                if not current_payment_url and has_yookassa:
                    payment = create_payment_for_order(
                        order=db_order,
                        amount=db_order.total_price,
                        description=f"Предзаказ #{db_order.id}",
                        return_url=get_return_url(),
                    )
                    if payment:
                        _, current_payment_url = update_order_from_payment(db_order, payment)

                if not current_payment_url:
                    current_payment_url = get_manual_payment_url(db_order) or ''
                    if current_payment_url:
                        db_order.payment_url = current_payment_url
                        if db_order.payment_status == 'not_paid':
                            db_order.payment_status = 'pending'
                        db_order.save(update_fields=['payment_url', 'payment_status', 'updated_at'])

                return db_order.payment_status, current_payment_url

            _, payment_url = await _prepare_payment()
            if payment_url:
                order = await sync_to_async(Order.objects.get)(pk=order.id)

        if is_custom:
            response_text = "✅ <b>Заявка на индивидуальный букет принята!</b>\n\n"
            response_text += f"📦 Номер заявки: #{order.id}\n"
            if custom_budget:
                response_text += f"💰 Бюджет: {custom_budget}\n"
            if custom_deadline:
                response_text += f"🕒 Когда нужен: {custom_deadline}\n"
            if discount > 0 and product_price_raw > 0:
                response_text += f"🎁 Скидка: {discount}%\n"
            response_text += f"🚗 Доставка: {format_money(delivery_cost)} ₽\n"
            response_text += "💬 Стоимость букета уточним перед сборкой.\n\n"
            response_text += "📞 Мы свяжемся с вами в ближайшее время для подтверждения деталей."
        else:
            response_text = f"✅ <b>Заказ оформлен!</b>\n\n"
            response_text += f"📦 Номер заказа: #{order.id}\n"
            response_text += f"🌸 Товар: {product_name}\n"
            response_text += f"🔢 Количество: {order_quantity}\n"
            if requested_delivery:
                response_text += f"📅 Дата/время: {requested_delivery}\n"
            response_text += f"💰 Цена за 1 шт: {format_money(product_price_raw)} ₽\n"
            response_text += f"💳 Сумма товара: {format_money(products_subtotal_raw)} ₽\n"
            if discount > 0:
                response_text += f"🎁 Скидка: {discount}%\n"
            if delivery_manual_required:
                response_text += "🚗 Доставка: стоимость уточним вручную менеджером\n"
            else:
                response_text += f"🚗 Доставка: {format_money(delivery_cost)} ₽\n"
            response_text += f"💳 <b>Итого: {format_money(final_price)} ₽</b>\n\n"
            if is_preorder:
                if delivery_manual_required:
                    response_text += (
                        "🌷 Это предзаказ. Стоимость доставки пока не определена, "
                        "менеджер уточнит и пришлет сумму к оплате.\n\n"
                    )
                elif use_transfer_payment:
                    response_text += (
                        f"🌷 Это предзаказ. {CARD_PAYMENT_MAINTENANCE_NOTE}\n"
                        "Оплата принимается переводом после подтверждения заказа.\n"
                        "Менеджер пришлет реквизиты в этом чате.\n\n"
                    )
                else:
                    response_text += "🌷 Это предзаказ. Для фиксации слота нужна оплата.\n\n"
            else:
                response_text += f"⏱ Примерное время доставки: {delivery_info['duration']} минут\n\n"
            response_text += "📞 Мы свяжемся с вами в ближайшее время для подтверждения заказа."

        if use_transfer_payment and not is_preorder:
            response_text += (
                f"\n\n💳 {CARD_PAYMENT_MAINTENANCE_NOTE} "
                "Оплата: переводом по реквизитам магазина. "
                "Реквизиты и подтверждение оплаты отправит менеджер в этом чате."
            )

        await state.clear()
        await message.answer(response_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

        if is_preorder and payment_url:
            keyboard_rows = [[InlineKeyboardButton(text="💳 Оплатить предзаказ", url=payment_url)]]
            if has_yookassa and order.payment_id:
                keyboard_rows.append([
                    InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_payment_{order.id}")
                ])
            await message.answer(
                "Оплатите заказ сейчас, чтобы закрепить за собой букет и время выдачи.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
            )
        elif is_preorder and use_transfer_payment:
            await message.answer(
                f"{CARD_PAYMENT_MAINTENANCE_NOTE} "
                "Менеджер отправит реквизиты перевода после проверки заказа. "
                "Это безопасно: платеж идет напрямую магазину, а подтверждение оплаты вы получите в чате."
            )

        await post_order_to_group(order.id)

    except Exception as e:
        logger.error("Ошибка создания заказа: %s", e)
        await state.clear()
        await message.answer(
            "❌ Произошла ошибка при оформлении заказа. Попробуйте еще раз.",
            reply_markup=get_main_keyboard(),
        )
