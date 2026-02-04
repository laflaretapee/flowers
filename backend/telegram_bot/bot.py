"""
Telegram бот для цветочного магазина (aiogram 3.x)
"""
import asyncio
import logging
from decimal import Decimal, ROUND_HALF_UP
import os
import re
import csv
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
from aiogram.fsm.storage.memory import MemoryStorage
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

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# FSM States
class OrderStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_address = State()
    waiting_for_comment = State()


class CustomBouquetStates(StatesGroup):
    waiting_for_style = State()
    waiting_for_budget = State()
    waiting_for_deadline = State()


class AdminStates(StatesGroup):
    waiting_for_ready_photo = State()


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


# Router
router = Router()


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
        'confirmed': '✅',
        'in_progress': '🛠️',
        'ready': '📦',
        'delivering': '🚚',
        'completed': '🏁',
        'cancelled': '❌',
    }.get(status, 'ℹ️')


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

    icon = order_status_icon(order.status)
    created = timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M')
    text = f"{icon} <b>Заказ #{order.id}</b>\n"
    text += f"Статус: <b>{order.get_status_display()}</b>\n"
    text += f"Создан: {created}\n\n"
    text += f"👤 {order.customer_name}\n"
    text += f"📞 {order.phone}\n"
    text += f"📍 {order.address}\n"
    text += f"💳 Итог: {format_money(to_decimal(order.total_price))} ₽\n"
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
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_status_{order.id}_confirmed"),
        InlineKeyboardButton(text="🛠️ В работу", callback_data=f"admin_status_{order.id}_in_progress"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📦 Готов (фото)", callback_data=f"admin_ready_{order.id}"),
        InlineKeyboardButton(text="🚚 Доставка", callback_data=f"admin_status_{order.id}_delivering"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🏁 Завершить", callback_data=f"admin_status_{order.id}_completed"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_status_{order.id}_cancelled"),
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
    await callback.answer()
    parts = callback.data.split("_", 3)
    # admin_status_<id>_<status>
    order_id = int(parts[2])
    new_status = parts[3]
    if new_status == 'ready':
        # Ready requires a photo; use admin_ready_<id>
        await callback.answer("Для статуса «Готов» нужен снимок.", show_alert=True)
        return

    @sync_to_async
    def _update():
        order = Order.objects.get(pk=order_id)
        order.status = new_status
        order.save(update_fields=['status', 'updated_at', 'phone_normalized'])

    try:
        await _update()
    except Exception as exc:
        await callback.answer("Не удалось обновить статус", show_alert=True)
        logger.warning("Admin status update failed: %s", exc)
        return

    text, keyboard = await build_admin_order_detail(order_id)
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

    text = f"🛒 <b>Оформление заказа</b>\n\n"
    text += f"🌸 {product_name}\n"
    text += f"💰 Цена: {format_money(price)} ₽\n"

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
        price=price,
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent
    )

    await callback.message.answer(text, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("order_"))
async def start_order(callback: CallbackQuery, state: FSMContext):
    """Начать оформление заказа"""
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    await begin_order_flow(callback, state, product_id)


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

    text = (
        "💐 <b>Индивидуальный букет</b>\n\n"
        "Мы учтем ваши пожелания и свяжемся для подтверждения.\n\n"
    )

    if promo_enabled:
        if is_subscribed:
            text += (
                f"🎁 Скидка {discount_percent}% на первый полученный заказ "
                f"будет рассчитана после указания номера.\n\n"
            )
        else:
            text += (
                f"🎁 Скидка {discount_percent}% доступна подписчикам.\n"
                "Подпишитесь на канал, чтобы получить скидку.\n\n"
            )

    text += "👤 <b>Шаг 1/4:</b> Введите ваше имя\n\n"
    text += "<i>Или отправьте /cancel для отмены</i>"

    await state.set_state(OrderStates.waiting_for_name)
    await state.update_data(
        is_custom=True,
        product_id=None,
        product_name="Индивидуальный букет",
        price=Decimal('0'),
        is_subscribed=is_subscribed,
        promo_enabled=promo_enabled,
        discount_percent=discount_percent
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())


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
        is_custom = data.get('is_custom', False)
        custom_style = data.get('custom_style', '')
        custom_budget = data.get('custom_budget', '')
        custom_deadline = data.get('custom_deadline', '')

        name = data.get('customer_name', user.first_name)
        phone = data.get('phone', 'Не указан')
        address = data.get('address', 'Не указан')
        comment = data.get('comment', '')

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

        delivery_cost = to_decimal(delivery_info['cost'])
        product_price_raw = Decimal('0')
        product_price = Decimal('0')

        if is_custom:
            budget_value = parse_budget_value(custom_budget)
            if budget_value is not None:
                product_price_raw = budget_value
                discount_ratio = (Decimal('100') - Decimal(discount)) / Decimal('100')
                product_price = (product_price_raw * discount_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                discount = 0
        else:
            product_price_raw = to_decimal(await sync_to_async(lambda: product.price)())
            discount_ratio = (Decimal('100') - Decimal(discount)) / Decimal('100')
            product_price = (product_price_raw * discount_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

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
                    total_price=final_price,
                    discount_percent=discount,
                    has_subscription=is_subscribed
                )
                
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product_name,
                    price=product_price_raw,
                    quantity=1
                )
                return order
        
        order = await create_order_in_db()

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
            response_text += f"💰 Цена товара: {format_money(product_price_raw)} ₽\n"
            if discount > 0:
                response_text += f"🎁 Скидка: {discount}%\n"
            response_text += f"🚗 Доставка: {format_money(delivery_cost)} ₽\n"
            response_text += f"💳 <b>Итого: {format_money(final_price)} ₽</b>\n\n"
            response_text += f"⏱ Примерное время доставки: {delivery_info['duration']} минут\n\n"
            response_text += f"📞 Мы свяжемся с вами в ближайшее время для подтверждения заказа."
        
        await state.clear()
        await message.answer(response_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Ошибка создания заказа: {e}")
        await state.clear()
        await message.answer(
            "❌ Произошла ошибка при оформлении заказа. Попробуйте еще раз.",
            reply_markup=get_main_keyboard()
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
        'confirmed': '✅',
        'in_progress': '🛠️',
        'ready': '📦',
        'delivering': '🚚',
        'completed': '🏁',
        'cancelled': '❌',
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
    
    def run(self):
        """Запуск бота"""
        global bot_instance, channel_id, group_id
        
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
            return
        
        # Устанавливаем глобальные переменные
        channel_id = getattr(settings, 'TELEGRAM_CHANNEL_ID', None)
        group_id = getattr(settings, 'TELEGRAM_GROUP_ID', None)
        
        # Создаем бота с настройками по умолчанию
        self.bot = Bot(
            token=self.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        bot_instance = self.bot
        
        # Создаем диспетчер
        self.dp = Dispatcher(storage=MemoryStorage())
        
        # Регистрируем middleware для проверки подписки
        router.message.middleware(SubscriptionMiddleware())
        router.callback_query.middleware(SubscriptionMiddleware())
        
        # Регистрируем роутер
        self.dp.include_router(router)
        
        logger.info("🌸 Бот Цветочная Лавка запущен (aiogram 3.x)")
        
        # Запускаем polling
        asyncio.run(self.dp.start_polling(self.bot))
