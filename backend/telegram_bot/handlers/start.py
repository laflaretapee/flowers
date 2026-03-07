import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from catalog.models import Product

from ..keyboards import get_main_keyboard, get_subscribe_keyboard
from ..services import check_user_subscription, get_promo_config

logger = logging.getLogger(__name__)

router = Router()


def extract_start_payload(text: str) -> str:
    if not text:
        return ''
    if text.startswith('/start '):
        return text.split(' ', 1)[1].strip()
    return ''


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user

    payload = extract_start_payload(message.text)
    product_id = None
    pending_custom = payload == 'custom'
    if payload.startswith('product_'):
        try:
            product_id = int(payload.split('_', 1)[1])
        except ValueError:
            product_id = None

    is_subscribed = await check_user_subscription(user.id)

    if not is_subscribed:
        if product_id:
            await state.update_data(pending_product_id=product_id)
        if pending_custom:
            await state.update_data(pending_custom_bouquet=True)
        keyboard = get_subscribe_keyboard()
        text = (
            f"🌸 Добро пожаловать в <b>Цветочная Лавка</b>, {user.first_name}!\n\n"
            "Мы создаем авторские букеты из свежих цветов с доставкой по городу.\n\n"
            "⚠️ <b>Для использования бота подпишитесь на наш канал!</b>\n\n"
            "После подписки нажмите кнопку «✅ Я подписался»."
        )
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    promo_enabled, discount = await get_promo_config()

    text = f"🌸 Добро пожаловать в <b>Цветочная Лавка</b>, {user.first_name}!\n\n"
    text += "Мы создаем авторские букеты из свежих цветов с доставкой по городу.\n\n"

    if promo_enabled:
        text += (
            f"🎁 У вас есть скидка <b>{discount}%</b> на первый полученный заказ "
            f"по номеру телефона за подписку на канал!\n\n"
        )

    text += "Выберите действие в меню ниже 👇"

    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

    if pending_custom:
        from .order import start_custom_bouquet_flow
        await start_custom_bouquet_flow(message, state)
        return

    if product_id:
        try:
            product = await sync_to_async(Product.objects.get)(id=product_id, is_active=True)
            from .catalog import send_product_confirmation
            await send_product_confirmation(message, product)
        except Product.DoesNotExist:
            await message.answer("Товар не найден. Откройте каталог, чтобы выбрать другой букет.")


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)

    if is_subscribed:
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)

        promo_enabled, discount = await get_promo_config()
        if promo_enabled:
            text = (
                f"🎉 <b>Отлично!</b> Вы подписаны на наш канал!\n\n"
                f"🎁 Вам доступна скидка <b>{discount}%</b> на первый полученный заказ по номеру телефона!\n\n"
                "Выберите действие в меню ниже 👇"
            )
        else:
            text = (
                "🎉 <b>Отлично!</b> Вы подписаны на наш канал!\n\n"
                "Выберите действие в меню ниже 👇"
            )
        await callback.message.answer(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
        await callback.message.delete()

        data = await state.get_data()
        pending_product_id = data.get('pending_product_id')
        pending_custom_bouquet = data.get('pending_custom_bouquet')
        if pending_product_id:
            await state.update_data(pending_product_id=None)
            try:
                product = await sync_to_async(Product.objects.get)(id=pending_product_id, is_active=True)
                from .catalog import send_product_confirmation
                await send_product_confirmation(callback.message, product)
            except Product.DoesNotExist:
                await callback.message.answer("Товар не найден. Откройте каталог, чтобы выбрать другой букет.")
        if pending_custom_bouquet:
            await state.update_data(pending_custom_bouquet=None)
            from .order import start_custom_bouquet_flow
            await start_custom_bouquet_flow(callback.message, state)
    else:
        await callback.answer("❌ Вы ещё не подписаны! Подпишитесь и попробуйте снова.", show_alert=True)
