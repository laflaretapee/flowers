"""
Telegram бот для цветочного магазина (aiogram 3.x)
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
import os
import re
import csv
import html
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict
import requests

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, TelegramObject,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties

from django.conf import settings
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db.models import Q
from django.db import transaction
from asgiref.sync import sync_to_async

from catalog.models import Product, Category, HeroSection, BotAdmin, Order, OrderItem, Review, normalize_phone
from catalog.taxi_integration import TaxiDeliveryIntegration
from catalog.payments import (
    update_order_from_payment,
    fetch_payment,
    create_payment_for_order,
    get_return_url,
    get_manual_payment_url,
    yookassa_enabled,
)
from .fsm_storage import DjangoFSMStorage

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DELIVERY_MANUAL_NOTE = "Не получилось рассчитать стоимость доставки - введите стоимость сами"
CARD_PAYMENT_MAINTENANCE_NOTE = "Оплата по карте временно на техническом обслуживании."


# FSM States
class OrderStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_address = State()
    waiting_for_comment = State()


class CustomBouquetStates(StatesGroup):
    waiting_for_style = State()
    waiting_for_budget = State()
    waiting_for_deadline = State()


class PreOrderStates(StatesGroup):
    waiting_for_datetime = State()


class AdminStates(StatesGroup):
    waiting_for_ready_photo = State()
    waiting_for_transfer_details = State()


class ReviewStates(StatesGroup):
    waiting_for_review = State()
    waiting_for_review_text = State()


# Pagination settings
PRODUCTS_PER_PAGE = 3


# Global bot instance (will be set in FlowerShopBot)
bot_instance: Bot = None
channel_id = None
group_id = None


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


async def fetch_user_avatar_bytes(user_id: int) -> bytes | None:
    """Скачать аватар пользователя из Telegram (если доступен)."""
    global bot_instance
    if not bot_instance:
        return None

    try:
        photos = await bot_instance.get_user_profile_photos(user_id, limit=1)
        if not photos or photos.total_count < 1:
            return None

        # Берем самое большое фото
        file_id = photos.photos[0][-1].file_id
        file = await bot_instance.get_file(file_id)
        if not file or not file.file_path:
            return None

        url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file.file_path}"
        import requests

        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.content
    except Exception as exc:
        logger.info("Не удалось получить аватар пользователя %s: %s", user_id, exc)
        return None


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


async def download_telegram_file_bytes(file_id: str) -> tuple[bytes | None, str | None]:
    """Скачать файл из Telegram по file_id, вернуть (bytes, basename)."""
    global bot_instance
    if not bot_instance:
        return None, None
    try:
        tg_file = await bot_instance.get_file(file_id)
        if not tg_file or not tg_file.file_path:
            return None, None
        file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{tg_file.file_path}"
        resp = requests.get(file_url, timeout=15)
        if resp.status_code >= 400:
            return None, None
        basename = os.path.basename(tg_file.file_path)
        return resp.content, basename
    except Exception as exc:
        logger.warning("Не удалось скачать файл %s: %s", file_id, exc)
        return None, None


# Subscription Check Middleware
class SubscriptionMiddleware(BaseMiddleware):
    """Middleware для проверки подписки перед обработкой сообщений"""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        global bot_instance, channel_id, group_id
        
        # Получаем user_id из события
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
            # Пропускаем команду /start - она сама проверит подписку
            if event.text and event.text.startswith('/start'):
                return await handler(event, data)
            # /admin должен работать даже без подписки (для админов)
            if event.text and event.text.startswith('/admin'):
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            # Пропускаем проверку подписки callback
            if event.data == "check_subscription":
                return await handler(event, data)
        
        if user_id is None:
            return await handler(event, data)

        # Админы бота не должны упираться в подписку
        try:
            username = None
            if isinstance(event, (Message, CallbackQuery)) and event.from_user:
                username = event.from_user.username
            if await is_bot_admin(user_id, username):
                return await handler(event, data)
        except Exception:
            pass
        
        # Проверяем подписку
        is_subscribed = await check_user_subscription(user_id)
        
        if not is_subscribed:
            # Пользователь не подписан - показываем сообщение
            keyboard = get_subscribe_keyboard()
            
            text = (
                "⚠️ <b>Для использования бота необходимо подписаться на наш канал!</b>\n\n"
                "После подписки нажмите кнопку «✅ Я подписался» для проверки."
            )
            
            if isinstance(event, Message):
                await event.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            elif isinstance(event, CallbackQuery):
                await event.answer("Сначала подпишитесь на канал!", show_alert=True)
            
            return  # Прерываем обработку
        
        return await handler(event, data)


# Флаг для отключения проверки при ошибке конфигурации
subscription_check_disabled = False


async def check_user_subscription(user_id: int) -> bool:
    """Проверка подписки пользователя"""
    global bot_instance, channel_id, group_id, subscription_check_disabled
    
    # Если проверка отключена из-за ошибки конфигурации - пропускаем
    if subscription_check_disabled:
        return True
    
    if not channel_id and not group_id:
        return True  # Если каналы не настроены, пропускаем проверку
    
    try:
        if channel_id:
            member = await bot_instance.get_chat_member(channel_id, user_id)
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return True
        
        if group_id:
            member = await bot_instance.get_chat_member(group_id, user_id)
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return True
                
    except TelegramBadRequest as e:
        error_msg = str(e)
        # Если бот не имеет доступа к списку участников - отключаем проверку
        if "member list is inaccessible" in error_msg or "chat not found" in error_msg.lower():
            logger.warning(
                f"⚠️ Проверка подписки отключена! Бот не имеет доступа к каналу/группе.\n"
                f"Добавьте бота администратором в канал/группу с правом 'Читать сообщения'.\n"
                f"Channel ID: {channel_id}, Group ID: {group_id}"
            )
            subscription_check_disabled = True
            return True
        logger.error(f"Ошибка проверки подписки: {e}")
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
    
    return False


def get_subscribe_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для подписки"""
    global channel_id, group_id
    
    buttons = []
    if channel_id and not str(channel_id).startswith('-'):
        link = f"https://t.me/{str(channel_id).replace('@', '')}"
        buttons.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=link)])
    elif group_id and not str(group_id).startswith('-'):
        link = f"https://t.me/{str(group_id).replace('@', '')}"
        buttons.append([InlineKeyboardButton(text="👥 Вступить в группу", url=link)])
    
    buttons.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [KeyboardButton(text="📋 Каталог"), KeyboardButton(text="💐 Собрать свой букет")],
        [KeyboardButton(text="🌷 Предзаказ на 8 марта")],
        [KeyboardButton(text="🎁 Акции"), KeyboardButton(text="📞 Контакты")],
        [KeyboardButton(text="🧾 Мои заказы"), KeyboardButton(text="⭐️ Отзывы")],
        [KeyboardButton(text="📝 Оставить отзыв")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_admin_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="📦 Заказы"), KeyboardButton(text="📤 Экспорт заказов")],
        [KeyboardButton(text="🔙 Выйти")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_address_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения адреса"""
    keyboard = [
        [KeyboardButton(text="✅ Подтвердить")],
        [KeyboardButton(text="✏️ Ввести вручную"), KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_quantity_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="1"), KeyboardButton(text="3"), KeyboardButton(text="5")],
        [KeyboardButton(text="7"), KeyboardButton(text="9"), KeyboardButton(text="15")],
        [KeyboardButton(text="❌ Отмена")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)


# Router
router = Router()
_router_initialized = False


# Handlers

def extract_start_payload(text: str) -> str:
    if not text:
        return ''
    if text.startswith('/start '):
        return text.split(' ', 1)[1].strip()
    return ''


async def build_catalog_keyboard() -> InlineKeyboardMarkup | None:
    categories = await sync_to_async(list)(
        Category.objects.filter(is_active=True).order_by('order', 'name')[:8]
    )
    if not categories:
        return None

    keyboard = []
    for category in categories:
        product_count = await sync_to_async(
            Product.objects.filter(category=category, is_active=True).count
        )()
        keyboard.append([InlineKeyboardButton(
            text=f"{category.name} ({product_count})",
            callback_data=f"cat_{category.id}_0"
        )])

    keyboard.append([InlineKeyboardButton(text="📋 Все товары", callback_data="all_products_0")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def get_catalog_cover_image():
    hero = await sync_to_async(HeroSection.get_hero)()
    image = await sync_to_async(lambda: hero.image if hero and hero.image else None)()
    if image:
        return image

    product = await sync_to_async(
        lambda: Product.objects.filter(is_active=True, image__isnull=False)
        .exclude(image='')
        .first()
    )()
    if product:
        return await sync_to_async(lambda: product.image)()

    category = await sync_to_async(
        lambda: Category.objects.filter(is_active=True, image__isnull=False)
        .exclude(image='')
        .first()
    )()
    if category:
        return await sync_to_async(lambda: category.image)()

    return None


async def send_catalog_menu(message: Message):
    """Отправить меню каталога"""
    keyboard = await build_catalog_keyboard()
    if not keyboard:
        await message.answer("Каталог пока пуст. Загляните позже!")
        return

    caption = "📋 <b>Каталог</b>\n\nВыберите категорию цветов:"
    image = await get_catalog_cover_image()

    if image:
        try:
            image_path = await sync_to_async(lambda: image.path)()
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        except Exception as e:
            logger.error(f"Ошибка отправки обложки каталога: {e}")

    await message.answer(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def edit_catalog_menu(message: Message):
    """Отредактировать текущее сообщение в меню каталога"""
    keyboard = await build_catalog_keyboard()
    caption = "📋 <b>Каталог</b>\n\nВыберите категорию цветов:"

    if not keyboard:
        try:
            if message.photo:
                await message.edit_caption("Каталог пока пуст. Загляните позже!", reply_markup=None)
            else:
                await message.edit_text("Каталог пока пуст. Загляните позже!")
        except TelegramBadRequest:
            pass
        return

    image = await get_catalog_cover_image()
    try:
        if image:
            image_path = await sync_to_async(lambda: image.path)()
            media = InputMediaPhoto(media=FSInputFile(image_path), caption=caption, parse_mode=ParseMode.HTML)
            if message.photo:
                await message.edit_media(media=media, reply_markup=keyboard)
            else:
                await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            if message.photo:
                await message.edit_caption(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramBadRequest as e:
        logger.warning(f"Не удалось отредактировать каталог: {e}")
        try:
            await message.delete()
        except Exception:
            pass
        if image:
            try:
                image_path = await sync_to_async(lambda: image.path)()
                await message.answer_photo(
                    photo=FSInputFile(image_path),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
                return
            except Exception as ex:
                logger.error(f"Ошибка отправки обложки каталога: {ex}")
        await message.answer(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def send_product_confirmation(message: Message, product: Product):
    """Подтверждение выбранного товара перед оформлением заказа"""
    product_name = await sync_to_async(lambda: product.name)()
    description = await sync_to_async(lambda: product.short_description)()
    category = await sync_to_async(lambda: product.category)()
    hide_price = await sync_to_async(lambda: getattr(product, 'hide_price', False))()
    price = to_decimal(await sync_to_async(lambda: product.price)())
    image = await sync_to_async(lambda: product.image if product.image else None)()

    text = f"🌸 <b>{product_name}</b>\n\n"
    if description:
        text += f"{description}\n\n"
    if category:
        category_name = await sync_to_async(lambda: category.name)()
        text += f"📁 {category_name}\n\n"
    if not hide_price:
        text += f"💰 Цена: <b>{format_money(price)} ₽</b>\n\n"

    text += "Хотите оформить заказ на этот букет?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_order_{product.id}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="decline_order")]
    ])

    if image:
        try:
            image_path = await sync_to_async(lambda: image.path)()
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def start_custom_bouquet_flow(message: Message, state: FSMContext):
    """Начать сбор индивидуального букета"""
    await state.clear()
    await state.set_state(CustomBouquetStates.waiting_for_style)
    await message.answer(
        "💐 <b>Соберем букет по вашим пожеланиям</b>\n\n"
        "Расскажите, какие цветы, цвета или повод вы хотите учесть.\n\n"
        "<i>Или отправьте /cancel для отмены</i>",
        parse_mode=ParseMode.HTML
    )


@router.message(F.text == "💐 Собрать свой букет")
async def start_custom_bouquet_from_menu(message: Message, state: FSMContext):
    await start_custom_bouquet_flow(message, state)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
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

    # Проверяем подписку
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
    
    # Пользователь подписан
    discount = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)
    promo_enabled = getattr(settings, 'PROMO_ENABLED', True)
    
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
        await start_custom_bouquet_flow(message, state)
        return

    if product_id:
        try:
            product = await sync_to_async(Product.objects.get)(id=product_id, is_active=True)
            await send_product_confirmation(message, product)
        except Product.DoesNotExist:
            await message.answer("Товар не найден. Откройте каталог, чтобы выбрать другой букет.")


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext):
    """Проверка подписки по нажатию кнопки"""
    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)
    
    if is_subscribed:
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)
        
        discount = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)
        text = (
            f"🎉 <b>Отлично!</b> Вы подписаны на наш канал!\n\n"
            f"🎁 Вам доступна скидка <b>{discount}%</b> на первый полученный заказ по номеру телефона!\n\n"
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
                await send_product_confirmation(callback.message, product)
            except Product.DoesNotExist:
                await callback.message.answer("Товар не найден. Откройте каталог, чтобы выбрать другой букет.")
        if pending_custom_bouquet:
            await state.update_data(pending_custom_bouquet=None)
            await start_custom_bouquet_flow(callback.message, state)
    else:
        await callback.answer("❌ Вы ещё не подписаны! Подпишитесь и попробуйте снова.", show_alert=True)


@router.message(Command("catalog"))
@router.message(F.text == "📋 Каталог")
async def show_catalog(message: Message):
    """Показать каталог с категориями"""
    await send_catalog_menu(message)


# --- Admin panel (bot) ---

ADMIN_ORDERS_PAGE_SIZE = 10


def order_status_icon(status: str) -> str:
    return {
        'new': '🆕',
        'processing': '🟡',
        'ready': '📦',
        'completed': '✅',
        'cancelled': '❌',
        'expired': '⌛',
        # legacy statuses (for old rows)
        'confirmed': '✅',
        'in_progress': '🛠️',
        'delivering': '🚚',
    }.get(status, 'ℹ️')


def get_orders_chat_id() -> str:
    explicit = (getattr(settings, 'TELEGRAM_ORDERS_CHAT_ID', '') or '').strip()
    fallback = (getattr(settings, 'TELEGRAM_GROUP_ID', '') or '').strip()
    return explicit or fallback


def order_status_title(status: str) -> str:
    return {
        'new': '🆕 Новый',
        'processing': '🟡 В работе',
        'ready': '🟢 Готов',
        'completed': '✅ Завершен',
        'cancelled': '❌ Отменен',
        'expired': '⌛ Просрочен',
        # legacy statuses
        'confirmed': '✅ Подтвержден',
        'in_progress': '🛠️ В работе',
        'delivering': '🚚 Доставляется',
    }.get(status, 'ℹ️ Статус')


def build_order_group_keyboard(order: Order) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    is_terminal = order.status in {'completed', 'cancelled', 'expired'}
    if order.status == 'new':
        rows.append([InlineKeyboardButton(text="🟡 Взять в работу", callback_data=f"svc_take_{order.id}")])
    elif order.status == 'processing':
        rows.append([InlineKeyboardButton(text="🟢 Готово", callback_data=f"svc_ready_{order.id}")])
        rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"svc_cancel_{order.id}")])
    elif order.status == 'ready':
        rows.append([InlineKeyboardButton(text="✅ Завершить", callback_data=f"svc_complete_{order.id}")])
        rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"svc_cancel_{order.id}")])
        if order.payment_status != 'succeeded':
            rows.append([InlineKeyboardButton(text="⌛ Не оплатил (expired)", callback_data=f"svc_expire_{order.id}")])

    if order.payment_method == 'transfer' and not is_terminal:
        details_button = "✏️ Обновить реквизиты" if order.transfer_details else "💳 Указать реквизиты"
        rows.append([InlineKeyboardButton(text=details_button, callback_data=f"svc_payreq_{order.id}")])
        if order.payment_status != 'succeeded':
            rows.append([InlineKeyboardButton(text="✅ Отметить оплаченным", callback_data=f"svc_paid_{order.id}")])
        else:
            rows.append([InlineKeyboardButton(text="↩️ Вернуть в «не оплачен»", callback_data=f"svc_unpaid_{order.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


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
    if not orders_chat_id or not bot_instance:
        return

    try:
        text, keyboard = await build_order_group_message(order_id)
        sent = await bot_instance.send_message(
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
    if not bot_instance:
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
        await bot_instance.edit_message_text(
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


async def apply_group_order_action(
    order_id: int,
    action: str,
    actor_id: int,
    actor_username: str | None,
) -> tuple[bool, str]:
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
            if order.status not in {'new', 'processing'}:
                return False, "Нельзя перевести в «Готов»"
            order.status = 'ready'
            if not order.processing_by_user_id:
                order.processing_by_user_id = actor_id
                order.processing_by_username = actor_username_clean
                update_fields.extend(['processing_by_user_id', 'processing_by_username'])
            update_fields.append('status')
            order.save(update_fields=update_fields)
            return True, "Заказ отмечен как готовый"

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


@router.callback_query(F.data.startswith("svc_"))
async def service_group_order_actions(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return

    parts = (callback.data or "").split("_", 2)
    # svc_<action>_<order_id>
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
                try:
                    if action == 'paid':
                        await bot_instance.send_message(
                            chat_id=chat_id,
                            text=(
                                f"✅ Оплата по заказу #{order_id} подтверждена.\n"
                                f"Сумма: {format_money(amount)} ₽."
                            ),
                        )
                    else:
                        await bot_instance.send_message(
                            chat_id=chat_id,
                            text=(
                                f"ℹ️ Оплата по заказу #{order_id} переведена в статус «не оплачено».\n"
                                "Если вы уже переводили деньги, отправьте чек в этот чат."
                            ),
                        )
                except Exception as exc:
                    logger.warning("Не удалось отправить клиенту статус оплаты по заказу %s: %s", order_id, exc)
    await callback.answer(result_text, show_alert=not changed)


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

    try:
        await bot_instance.send_message(
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


@router.message(Command("admin"))
async def admin_entry(message: Message, state: FSMContext):
    if not await require_admin_message(message):
        return
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>\n\nВыберите действие:", parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())


@router.message(F.text == "🔙 Выйти")
async def admin_exit(message: Message, state: FSMContext):
    if not await is_bot_admin(message.from_user.id, message.from_user.username):
        return
    await state.clear()
    await message.answer("Ок.", reply_markup=get_main_keyboard())


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
    # Status actions
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


@router.callback_query(F.data.startswith("admin_status_"))
async def admin_order_set_status(callback: CallbackQuery):
    if not await require_admin_callback(callback):
        return
    parts = callback.data.split("_", 3)
    # admin_status_<id>_<status>
    order_id = int(parts[2])
    new_status = parts[3]
    actor_id = callback.from_user.id
    actor_username = (callback.from_user.username or '').strip().lstrip('@')
    allowed_statuses = {choice[0] for choice in Order.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        await callback.answer("Недопустимый статус", show_alert=True)
        return

    if new_status == 'ready':
        # Ready requires a photo; use admin_ready_<id>
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
        reply_markup=ReplyKeyboardRemove()
    )


@router.callback_query(F.data.startswith("admin_payreq_"))
async def admin_order_payment_details_request(callback: CallbackQuery, state: FSMContext):
    if not await require_admin_callback(callback):
        return
    await callback.answer()
    order_id = int(callback.data.split("_")[2])
    current_state = await state.get_state()
    current_data = await state.get_data()
    current_order_id = int(current_data.get('admin_transfer_order_id') or 0)
    if current_state == AdminStates.waiting_for_transfer_details.state and current_order_id == order_id:
        await callback.answer("Режим ввода реквизитов для этого заказа уже активен")
        return
    await state.set_state(AdminStates.waiting_for_transfer_details)
    await state.update_data(admin_transfer_order_id=order_id)
    await callback.message.answer(
        f"💳 Введите реквизиты для заказа #{order_id} одним сообщением.\n\n"
        "Пример: +7 900 000-00-00 (СБП, Иван И.)\n"
        "/cancel — отмена",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdminStates.waiting_for_ready_photo)
async def admin_order_ready_photo_receive(message: Message, state: FSMContext):
    if not await require_admin_message(message):
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("Ок, отменено.", reply_markup=get_admin_keyboard())
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
        await message.answer("❌ Не удалось сохранить фото.", reply_markup=get_admin_keyboard())
        await state.clear()
        return

    # If status didn't change (already ready), signals won't notify; send manually.
    if prev_status == 'ready':
        try:
            order = await sync_to_async(Order.objects.get)(pk=order_id)
            caption = f"📦 Ваш заказ #{order_id} готов."
            photo_path = await sync_to_async(lambda: order.ready_photo.path)()
            await bot_instance.send_photo(chat_id=customer_chat_id, photo=FSInputFile(photo_path), caption=caption)
        except Exception as exc:
            logger.warning("Manual ready photo notify failed: %s", exc)

    await state.clear()
    await refresh_order_group_message(order_id)
    await message.answer(f"✅ Фото сохранено, заказ #{order_id} помечен как «Готов».", reply_markup=get_admin_keyboard())


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


@router.callback_query(F.data.startswith("cat_"))
async def show_category_products(callback: CallbackQuery):
    """Показать товары категории - один товар с навигацией"""
    parts = callback.data.split("_")
    category_id = int(parts[1])
    index = int(parts[2]) if len(parts) > 2 else 0
    
    try:
        category = await sync_to_async(Category.objects.get)(id=category_id, is_active=True)
        
        # Получаем все товары категории
        products = await sync_to_async(list)(
            Product.objects.filter(category=category, is_active=True)
            .select_related('category')
            .order_by('order', 'name')
        )
    except Category.DoesNotExist:
        await callback.answer("Категория не найдена")
        return
    
    if not products:
        await callback.answer("В этой категории пока нет товаров")
        return
    
    await callback.answer()
    
    total = len(products)
    index = max(0, min(index, total - 1))  # Ограничиваем индекс
    product = products[index]
    
    # Отправляем/редактируем карточку с навигацией
    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix=f"cat_{category_id}",
        back_callback="back_to_catalog",
        is_first=False
    )


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой callback для кнопки с номером страницы"""
    await callback.answer()


async def send_product_with_nav(
    callback: CallbackQuery,
    product: Product,
    index: int,
    total: int,
    nav_prefix: str,
    back_callback: str,
    is_first: bool = False
):
    """Отправить/редактировать карточку товара с навигацией"""
    product_id = await sync_to_async(lambda: product.id)()
    product_name = await sync_to_async(lambda: product.name)()
    description = await sync_to_async(lambda: product.short_description)()
    category = await sync_to_async(lambda: product.category)()
    hide_price = await sync_to_async(lambda: getattr(product, 'hide_price', False))()
    price = to_decimal(await sync_to_async(lambda: product.price)())
    image = await sync_to_async(lambda: product.image if product.image else None)()
    
    text = f"🌸 <b>{product_name}</b>\n\n"
    if description:
        text += f"{description}\n\n"
    if category:
        category_name = await sync_to_async(lambda: category.name)()
        text += f"📁 {category_name}\n\n"
    if not hide_price:
        text += f"💰 Цена: <b>{format_money(price)} ₽</b>"
    
    # Кнопки навигации
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"{nav_prefix}_{index-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="noop"))
    if index < total - 1:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"{nav_prefix}_{index+1}"))
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Заказать", callback_data=f"order_{product_id}")],
        nav_buttons,
        [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]
    ])
    
    # Если первый показ - отправляем новое сообщение
    if is_first:
        if image:
            try:
                image_path = await sync_to_async(lambda: image.path)()
                await callback.message.answer_photo(
                    photo=FSInputFile(image_path),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    
    # Иначе редактируем существующее сообщение
    try:
        if image:
            image_path = await sync_to_async(lambda: image.path)()
            media = InputMediaPhoto(media=FSInputFile(image_path), caption=text, parse_mode=ParseMode.HTML)
            await callback.message.edit_media(media=media, reply_markup=keyboard)
        else:
            # Если текущее сообщение - фото, а новый товар без фото
            if callback.message.photo:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                return
            else:
                await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramBadRequest as e:
        # Если не удалось отредактировать - отправляем новое
        logger.warning(f"Не удалось отредактировать: {e}")
        try:
            await callback.message.delete()
        except:
            pass
        if image:
            try:
                image_path = await sync_to_async(lambda: image.path)()
                await callback.message.answer_photo(
                    photo=FSInputFile(image_path),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
            except Exception as ex:
                logger.error(f"Ошибка отправки фото: {ex}")
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data == "back_to_catalog")
async def back_to_catalog(callback: CallbackQuery):
    """Вернуться к каталогу"""
    await callback.answer()
    await edit_catalog_menu(callback.message)


@router.callback_query(F.data.startswith("all_products"))
async def show_all_products(callback: CallbackQuery):
    """Показать все товары - один товар с навигацией"""
    parts = callback.data.split("_")
    index = int(parts[2]) if len(parts) > 2 else 0
    
    # Получаем все товары
    products = await sync_to_async(list)(
        Product.objects.filter(is_active=True)
        .select_related('category')
        .order_by('order', 'name')
    )
    
    if not products:
        await callback.answer("Каталог пуст")
        return
    
    await callback.answer()
    
    total = len(products)
    index = max(0, min(index, total - 1))
    product = products[index]
    
    # Отправляем/редактируем карточку с навигацией
    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix="all_products",
        back_callback="back_to_catalog",
        is_first=False
    )


async def send_product_card(message: Message, product: Product):
    """Отправить карточку товара"""
    text = f"🌸 <b>{product.name}</b>\n\n"
    
    description = await sync_to_async(lambda: product.short_description)()
    if description:
        text += f"{description}\n\n"
    
    category = await sync_to_async(lambda: product.category)()
    if category:
        text += f"📁 {category.name}\n\n"
    
    hide_price = await sync_to_async(lambda: getattr(product, 'hide_price', False))()
    if not hide_price:
        price = to_decimal(await sync_to_async(lambda: product.price)())
        text += f"💰 Цена: <b>{format_money(price)} ₽</b>"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Заказать", callback_data=f"order_{product.id}")]
    ])
    
    image = await sync_to_async(lambda: product.image if product.image else None)()
    
    if image:
        try:
            image_url = await sync_to_async(lambda: image.url)()
            # Для локальных файлов используем FSInputFile
            if image_url.startswith('/'):
                image_path = await sync_to_async(lambda: image.path)()
                await message.answer_photo(
                    photo=FSInputFile(image_path),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
            else:
                await message.answer_photo(
                    photo=image_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


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


async def begin_order_flow(callback: CallbackQuery, state: FSMContext, product_id: int):
    """Общее начало оформления заказа"""
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
    """Начать оформление заказа"""
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    await begin_order_flow(callback, state, product_id)


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
    await send_catalog_menu(message)


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
        message,
        state,
        product_id=int(product_id),
        product_name=product_name,
        product_price=product_price,
        quantity=quantity,
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=False,
    )


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
        message,
        state,
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


@router.callback_query(F.data.startswith("confirm_order_"))
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    """Подтверждение заказа из deep link"""
    await callback.answer()
    product_id = int(callback.data.split("_")[2])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await begin_order_flow(callback, state, product_id)


@router.callback_query(F.data == "decline_order")
async def decline_order(callback: CallbackQuery):
    """Отказ от заказа — показать каталог"""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_catalog_menu(callback.message)


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
            one_time_keyboard=True
        )
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
            one_time_keyboard=True
        )
    )


@router.message(CustomBouquetStates.waiting_for_deadline)
async def process_custom_deadline(message: Message, state: FSMContext):
    if message.text in ["/cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer("❌ Заявка отменена.", reply_markup=get_main_keyboard())
        return

    deadline_text = "" if message.text in ["/skip", "⏭ Пропустить"] else (message.text or "")
    await state.update_data(custom_deadline=deadline_text)
    await begin_custom_order_contact(message, state)


async def begin_custom_order_contact(message: Message, state: FSMContext):
    """Переход к сбору контактов для индивидуального букета"""
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
        message,
        state,
        product_id=None,
        product_name="Индивидуальный букет",
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent,
        is_custom=True,
    )


@router.message(Command("cancel"))
async def cancel_order(message: Message, state: FSMContext):
    """Отмена заказа"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
    else:
        await message.answer("Нет активного заказа.")


@router.message(OrderStates.waiting_for_name)
async def process_order_name(message: Message, state: FSMContext):
    """Шаг 1: Обработка имени"""
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.text:
        await message.answer("Пожалуйста, отправьте имя текстом.")
        return
    
    await state.update_data(customer_name=message.text)
    
    # Шаг 2: Запрашиваем телефон с кнопкой
    phone_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await state.set_state(OrderStates.waiting_for_phone)
    await message.answer(
        "📱 <b>Шаг 2/4:</b> Отправьте ваш номер телефона\n\n"
        "Нажмите кнопку ниже или введите вручную:",
        parse_mode=ParseMode.HTML,
        reply_markup=phone_keyboard
    )


@router.message(OrderStates.waiting_for_phone)
async def process_order_phone(message: Message, state: FSMContext):
    """Шаг 2: Обработка телефона"""
    if message.text == "❌ Отмена" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.contact and not message.text:
        await message.answer("Пожалуйста, отправьте номер телефона текстом или кнопкой.")
        return
    
    # Получаем телефон из контакта или текста
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
    
    # Шаг 3: Запрашиваем адрес с кнопкой геолокации
    location_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
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
        reply_markup=location_keyboard
    )


@router.message(OrderStates.waiting_for_address)
async def process_order_address(message: Message, state: FSMContext):
    """Шаг 3: Обработка адреса"""
    if message.text == "❌ Отмена" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    awaiting_confirmation = data.get('awaiting_address_confirmation', False)

    # Получаем адрес из геолокации
    if message.location:
        taxi_integration = TaxiDeliveryIntegration()
        address_info = await sync_to_async(taxi_integration.reverse_geocode)(
            message.location.latitude,
            message.location.longitude
        )

        if address_info:
            address = address_info['formatted_address']
            await state.update_data(address=address, awaiting_address_confirmation=True)
            await message.answer(
                f"📍 <b>Адрес определен:</b>\n\n{address}\n\n"
                "Подтвердите адрес или введите вручную:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_address_confirm_keyboard()
            )
        else:
            address = f"📍 Координаты: {message.location.latitude:.6f}, {message.location.longitude:.6f}"
            await state.update_data(address=address, awaiting_address_confirmation=True)
            await message.answer(
                "⚠️ Не удалось определить адрес по геолокации.\n"
                "Подтвердите адрес или введите вручную:",
                parse_mode=ParseMode.HTML,
                reply_markup=get_address_confirm_keyboard()
            )
        return

    if awaiting_confirmation:
        if message.text == "✅ Подтвердить":
            await state.update_data(awaiting_address_confirmation=False)
            await ask_for_comment(message, state)
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
    await ask_for_comment(message, state)


async def ask_for_comment(message: Message, state: FSMContext):
    await state.set_state(OrderStates.waiting_for_comment)
    await message.answer(
        "💬 <b>Шаг 4/4:</b> Добавьте комментарий к заказу\n\n"
        "(пожелания, время доставки и т.д.)\n\n"
        "<i>Или отправьте /skip чтобы пропустить</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⏭ Пропустить")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


@router.message(OrderStates.waiting_for_comment)
async def process_order_comment(message: Message, state: FSMContext):
    """Шаг 4: Обработка комментария к заказу"""
    if message.text == "/cancel" or message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Заказ отменен.", reply_markup=get_main_keyboard())
        return

    if not message.text:
        await message.answer("Пожалуйста, отправьте комментарий текстом или /skip.")
        return

    comment = "" if message.text in ["/skip", "⏭ Пропустить"] else message.text
    await state.update_data(comment=comment)
    
    # Создаем заказ
    await create_order(message, state)


async def create_order(message: Message, state: FSMContext):
    """Создание заказа в БД"""
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
        
        # Рассчитываем стоимость доставки
        shop_address = "Трактовая улица, 78А, село Раевский, Альшеевский район, Республика Башкортостан, 452120"
        taxi_integration = TaxiDeliveryIntegration()
        delivery_info = await sync_to_async(taxi_integration.calculate_delivery_cost)(
            from_address=shop_address,
            to_address=address,
            order_weight=1
        )

        delivery_manual_required = bool(delivery_info.get('requires_manual_price'))
        delivery_cost = to_decimal(delivery_info['cost'])
        if delivery_manual_required:
            delivery_cost = Decimal('0')
        product_price_raw = Decimal('0')  # price per item
        products_subtotal_raw = Decimal('0')
        product_price = Decimal('0')  # discounted subtotal for all items

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
        
        # Создаем заказ в БД
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
                    quantity=1 if is_custom else order_quantity
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
        logger.error(f"Ошибка создания заказа: {e}")
        await state.clear()
        await message.answer(
            "❌ Произошла ошибка при оформлении заказа. Попробуйте еще раз.",
            reply_markup=get_main_keyboard()
        )


@router.callback_query(F.data.startswith("check_payment_"))
async def check_payment_status(callback: CallbackQuery):
    """Проверка статуса оплаты по кнопке"""
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

@router.message(F.text == "🎁 Акции")
async def show_promotions(message: Message):
    """Показать акции"""
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
    """Показать заказы пользователя"""
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
        # legacy
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
    """Показать отзывы"""
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
    """Показать контакты"""
    text = (
        "📞 <b>Контакты</b>\n\n"
        "📱 Телефон: +7 (999) 123-45-67\n"
        "📍 Адрес: Трактовая улица, 78А, село Раевский,\n"
        "Альшеевский район, Республика Башкортостан, 452120\n\n"
        "🕐 Мы работаем: 9:00 - 21:00\n"
        "🚗 Доставка по городу и району"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "📝 Оставить отзыв")
async def start_review(message: Message, state: FSMContext):
    """Начать оставление отзыва"""
    text = (
        "📝 <b>Оставьте отзыв о нашем сервисе!</b>\n\n"
        "Выберите оценку, затем напишите отзыв."
    )
    await state.set_state(ReviewStates.waiting_for_review)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⭐️", callback_data="rate_1"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_2"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_3"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_4"),
                 InlineKeyboardButton(text="⭐️", callback_data="rate_5")]
            ]
        )
    )


@router.callback_query(F.data.startswith("rate_"))
async def rate_review(callback: CallbackQuery, state: FSMContext):
    """Выбор оценки"""
    rating = int(callback.data.split("_")[1])
    rating = max(1, min(5, rating))
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.waiting_for_review_text)

    filled = "🌟" * rating
    empty = "⭐️" * (5 - rating)
    stars = filled + empty

    await callback.message.edit_text(
        f"📝 <b>Оставьте отзыв о нашем сервисе!</b>\n\n"
        f"Оценка: {stars}\n\n"
        "Теперь напишите отзыв текстом.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌟" if i < rating else "⭐️",
                        callback_data=f"rate_{i+1}"
                    )
                    for i in range(5)
                ]
            ]
        )
    )
    await callback.answer()


@router.message(ReviewStates.waiting_for_review)
async def review_waiting_for_rating(message: Message):
    await message.answer("Сначала выберите оценку кнопками ⭐️.")


@router.message(ReviewStates.waiting_for_review_text)
async def process_review_text(message: Message, state: FSMContext):
    """Обработка текста отзыва"""
    text = message.text
    if not text:
        await message.answer("Пожалуйста, отправьте отзыв текстом.")
        return

    try:
        data = await state.get_data()
        rating = int(data.get('rating', 5))
        rating = max(1, min(5, rating))
        review_text = text

        user = message.from_user

        avatar_bytes = await fetch_user_avatar_bytes(user.id)

        @sync_to_async
        def create_review():
            review = Review(
                name=user.first_name or "Аноним",
                telegram_user_id=user.id,
                text=review_text,
                rating=rating,
                is_published=True
            )
            if avatar_bytes:
                # Сохраняем локально в MEDIA, чтобы не светить токен в URL Telegram
                ext = "jpg"
                filename = f"tg_{user.id}_{int(timezone.now().timestamp())}.{ext}"
                review.avatar.save(filename, ContentFile(avatar_bytes), save=False)
            review.save()
            return review

        await create_review()

        stars = "🌟" * rating + "⭐️" * (5 - rating)
        await state.clear()
        await message.answer(
            f"✅ <b>Спасибо за ваш отзыв!</b>\n\n"
            f"Оценка: {stars}\n"
            f"Отзыв опубликован.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка обработки отзыва: {e}")
        await message.answer(
            "❌ Произошла ошибка. Попробуйте еще раз.",
            parse_mode=ParseMode.HTML
        )


@router.message()
async def handle_unknown(message: Message):
    """Обработка неизвестных сообщений"""
    await message.answer(
        "🤔 Используйте кнопки меню или команды:\n\n"
        "/start - Главное меню\n"
        "/catalog - Каталог"
    )


class FlowerShopBot:
    """Основной класс бота"""
    
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.bot = None
        self.dp = None
    
    def _setup(self):
        """Общая инициализация бота и диспетчера"""
        global bot_instance, channel_id, group_id, _router_initialized
        
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
            return False
        
        # Устанавливаем глобальные переменные
        channel_id = getattr(settings, 'TELEGRAM_CHANNEL_ID', None)
        group_id = getattr(settings, 'TELEGRAM_GROUP_ID', None)
        
        # Создаем бота с настройками по умолчанию
        self.bot = Bot(
            token=self.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        bot_instance = self.bot
        
        # В webhook-режиме состояние должно переживать несколько воркеров.
        self.dp = Dispatcher(
            storage=DjangoFSMStorage(),
            events_isolation=SimpleEventIsolation(),
        )

        # Middleware должен регистрироваться на router только один раз за процесс.
        if not _router_initialized:
            router.message.middleware(SubscriptionMiddleware())
            router.callback_query.middleware(SubscriptionMiddleware())
            _router_initialized = True

        # Router instance is module-level; in webhook mode we can re-create Dispatcher
        # per request, so detach router from previous parent dispatcher first.
        previous_parent = getattr(router, "_parent_router", None)
        if previous_parent is not None and previous_parent is not self.dp:
            try:
                if router in previous_parent.sub_routers:
                    previous_parent.sub_routers.remove(router)
            except Exception:
                pass
            router._parent_router = None

        # Регистрируем роутер в Dispatcher
        self.dp.include_router(router)
        
        return True

    async def setup_webhook(self, webhook_url: str):
        """Настройка webhook для production"""
        if not self._setup():
            return
        
        await self.bot.set_webhook(webhook_url)
        logger.info("🌸 Бот Цветочная Лавка: webhook установлен → %s", webhook_url)

    async def process_update(self, update_data: dict):
        """Обработка входящего обновления от Telegram"""
        from aiogram.types import Update
        update = Update.model_validate(update_data, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    async def close(self):
        """Корректно закрыть HTTP-сессию Telegram клиента."""
        if not self.bot:
            return
        try:
            await self.bot.session.close()
        except Exception:
            pass


# Singleton для webhook-режима
_webhook_bot: FlowerShopBot | None = None


def get_webhook_bot() -> FlowerShopBot:
    """Получить или создать экземпляр бота для webhook-режима"""
    global _webhook_bot
    if _webhook_bot is None:
        _webhook_bot = FlowerShopBot()
        _webhook_bot._setup()
    return _webhook_bot
