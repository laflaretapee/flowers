"""
Telegram бот для цветочного магазина (aiogram 3.x)
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, TelegramObject,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
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
from django.db import transaction
from asgiref.sync import sync_to_async

from catalog.models import Product, Category, Order, OrderItem, Review
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


class ReviewStates(StatesGroup):
    waiting_for_review = State()


# Pagination settings
PRODUCTS_PER_PAGE = 3


# Global bot instance (will be set in FlowerShopBot)
bot_instance: Bot = None
channel_id = None
group_id = None


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
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            # Пропускаем проверку подписки callback
            if event.data == "check_subscription":
                return await handler(event, data)
        
        if user_id is None:
            return await handler(event, data)
        
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
        [KeyboardButton(text="📋 Каталог")],
        [KeyboardButton(text="🎁 Акции"), KeyboardButton(text="📞 Контакты")],
        [KeyboardButton(text="📝 Оставить отзыв")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# Router
router = Router()


# Handlers

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    await state.clear()
    user = message.from_user
    
    # Проверяем подписку
    is_subscribed = await check_user_subscription(user.id)
    
    if not is_subscribed:
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
        text += f"🎁 У вас есть скидка <b>{discount}%</b> за подписку на наш канал!\n\n"
    
    text += "Выберите действие в меню ниже 👇"
    
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    """Проверка подписки по нажатию кнопки"""
    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)
    
    if is_subscribed:
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)
        
        discount = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)
        text = (
            f"🎉 <b>Отлично!</b> Вы подписаны на наш канал!\n\n"
            f"🎁 Вам доступна скидка <b>{discount}%</b> на все заказы!\n\n"
            "Выберите действие в меню ниже 👇"
        )
        await callback.message.answer(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
        await callback.message.delete()
    else:
        await callback.answer("❌ Вы ещё не подписаны! Подпишитесь и попробуйте снова.", show_alert=True)


@router.message(Command("catalog"))
@router.message(F.text == "📋 Каталог")
async def show_catalog(message: Message):
    """Показать каталог с категориями"""
    categories = await sync_to_async(list)(
        Category.objects.filter(is_active=True).order_by('order', 'name')[:8]
    )
    
    if not categories:
        await message.answer("Каталог пока пуст. Загляните позже!")
        return
    
    keyboard = []
    for category in categories:
        # Получаем количество товаров в категории
        product_count = await sync_to_async(
            Product.objects.filter(category=category, is_active=True).count
        )()
        keyboard.append([InlineKeyboardButton(
            text=f"{category.name} ({product_count})",
            callback_data=f"cat_{category.id}_0"
        )])
    
    keyboard.append([InlineKeyboardButton(text="📋 Все товары", callback_data="all_products_0")])
    
    await message.answer(
        "📋 <b>Каталог</b>\n\nВыберите категорию цветов:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


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
    
    # Определяем, первый ли это показ (если сообщение не фото - значит первый)
    is_first = not callback.message.photo
    
    # Отправляем/редактируем карточку с навигацией
    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix=f"cat_{category_id}",
        back_callback="back_to_catalog",
        is_first=is_first
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
    price = await sync_to_async(lambda: product.price)()
    image = await sync_to_async(lambda: product.image if product.image else None)()
    
    text = f"🌸 <b>{product_name}</b>\n\n"
    if description:
        text += f"{description}\n\n"
    if category:
        category_name = await sync_to_async(lambda: category.name)()
        text += f"📁 {category_name}\n\n"
    if not hide_price:
        text += f"💰 Цена: <b>{price} ₽</b>"
    
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
                await callback.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
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
    try:
        await callback.message.delete()
    except:
        pass
    
    categories = await sync_to_async(list)(Category.objects.filter(is_active=True).order_by('order', 'name')[:8])
    
    if not categories:
        await callback.message.answer("Каталог пока пуст. Загляните позже!")
        return
    
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
    
    await callback.message.answer(
        "📋 <b>Каталог</b>\n\nВыберите категорию:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


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
    
    # Определяем, первый ли это показ
    is_first = not callback.message.photo
    
    # Отправляем/редактируем карточку с навигацией
    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix="all_products",
        back_callback="back_to_catalog",
        is_first=is_first
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
        price = await sync_to_async(lambda: product.price)()
        text += f"💰 Цена: <b>{price} ₽</b>"
    
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


@router.callback_query(F.data.startswith("order_"))
async def start_order(callback: CallbackQuery, state: FSMContext):
    """Начать оформление заказа"""
    product_id = int(callback.data.split("_")[1])
    
    try:
        product = await sync_to_async(Product.objects.get)(id=product_id, is_active=True)
    except Product.DoesNotExist:
        await callback.answer("Товар не найден")
        return
    
    await callback.answer()
    
    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)
    
    # Проверяем, есть ли у пользователя уже заказы (скидка только на первый заказ)
    has_previous_orders = await sync_to_async(
        Order.objects.filter(telegram_user_id=user_id).exists
    )()
    
    promo_enabled = getattr(settings, 'PROMO_ENABLED', True)
    discount_percent = getattr(settings, 'PROMO_DISCOUNT_PERCENT', 10)
    
    # Скидка только для подписчиков БЕЗ предыдущих заказов (первый заказ)
    discount = discount_percent if is_subscribed and promo_enabled and not has_previous_orders else 0
    
    price = await sync_to_async(lambda: float(product.price))()
    final_price = price * (1 - discount / 100)
    product_name = await sync_to_async(lambda: product.name)()
    
    text = f"🛒 <b>Оформление заказа</b>\n\n"
    text += f"🌸 {product_name}\n"
    text += f"💰 Цена: {price:.0f} ₽\n"
    
    if discount > 0:
        text += f"🎁 Скидка за первый заказ: {discount}%\n"
        text += f"💰 Итого: <b>{final_price:.0f} ₽</b>\n\n"
    elif has_previous_orders and is_subscribed:
        text += f"<i>(Скидка действует только на первый заказ)</i>\n\n"
    else:
        text += "\n"
    
    text += "� <b>Шаг 1/4:</b> Введите ваше имя\n\n"
    text += "<i>Или отправьте /cancel для отмены</i>"
    
    await state.set_state(OrderStates.waiting_for_name)
    await state.update_data(product_id=product_id, discount=discount, product_name=product_name, price=price)
    
    await callback.message.answer(text, parse_mode=ParseMode.HTML)


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
    
    # Получаем телефон из контакта или текста
    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text
    
    await state.update_data(phone=phone)
    
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
    await message.answer(
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
    
    # Получаем адрес из геолокации или текста
    if message.location:
        # Конвертируем геолокацию в реальный адрес
        taxi_integration = TaxiDeliveryIntegration()
        address_info = await sync_to_async(taxi_integration.reverse_geocode)(
            message.location.latitude, 
            message.location.longitude
        )
        
        if address_info:
            address = address_info['formatted_address']
            await message.answer(
                f"📍 <b>Адрес определен:</b>\n\n{address}\n\n"
                "Если адрес неверный, введите его вручную:",
                parse_mode=ParseMode.HTML
            )
        else:
            address = f"📍 Координаты: {message.location.latitude:.6f}, {message.location.longitude:.6f}"
            await message.answer(
                "⚠️ Не удалось определить адрес по геолокации.\n"
                "Использую координаты. Вы можете ввести адрес вручную:",
                parse_mode=ParseMode.HTML
            )
    else:
        address = message.text
    
    await state.update_data(address=address)
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
        name = data.get('customer_name', user.first_name)
        phone = data.get('phone', 'Не указан')
        address = data.get('address', 'Не указан')
        comment = data.get('comment', '')
        
        product = await sync_to_async(Product.objects.get)(id=product_id)
        is_subscribed = await check_user_subscription(user.id)
        
        # Рассчитываем стоимость доставки
        shop_address = "Трактовая улица, 78А, село Раевский, Альшеевский район, Республика Башкортостан, 452120"
        taxi_integration = TaxiDeliveryIntegration()
        delivery_info = await sync_to_async(taxi_integration.calculate_delivery_cost)(
            from_address=shop_address,
            to_address=address,
            order_weight=1
        )
        
        product_price_raw = await sync_to_async(lambda: float(product.price))()
        product_price = product_price_raw * (1 - discount / 100)
        delivery_cost = float(delivery_info['cost'])
        final_price = product_price + delivery_cost
        product_name = await sync_to_async(lambda: product.name)()
        
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
                    comment=f"{comment}\n\nДоставка через {delivery_info.get('service', 'такси')}. Примерное время: {delivery_info['duration']} мин.",
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
        
        response_text = f"✅ <b>Заказ оформлен!</b>\n\n"
        response_text += f"📦 Номер заказа: #{order.id}\n"
        response_text += f"🌸 Товар: {product_name}\n"
        response_text += f"💰 Цена товара: {product_price_raw:.0f} ₽\n"
        if discount > 0:
            response_text += f"🎁 Скидка: {discount}%\n"
        response_text += f"🚗 Доставка: {delivery_cost:.0f} ₽\n"
        response_text += f"💳 <b>Итого: {final_price:.0f} ₽</b>\n\n"
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
    
    # Проверяем, есть ли уже заказы
    has_previous_orders = await sync_to_async(
        Order.objects.filter(telegram_user_id=user_id).exists
    )()
    
    if is_subscribed and not has_previous_orders:
        text = (
            f"🎁 <b>Ваши акции</b>\n\n"
            f"✅ Скидка <b>{discount}%</b> на первый заказ за подписку на канал!\n\n"
            f"Скидка применяется автоматически при оформлении заказа."
        )
    elif is_subscribed and has_previous_orders:
        text = (
            f"🎁 <b>Акции</b>\n\n"
            f"Вы уже использовали скидку на первый заказ.\n\n"
            f"Следите за нашими новыми акциями! 🌸"
        )
    else:
        text = (
            f"🎁 <b>Акции</b>\n\n"
            f"📢 Подпишитесь на наш канал и получите скидку <b>{discount}%</b> на первый заказ!"
        )
    
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
        "Отправьте ваш отзыв в формате:\n"
        "<code>Оценка (1-5) - Ваш отзыв</code>\n\n"
        "Например:\n"
        "<code>5 - Отличный сервис, букет был свежий и красивый!</code>"
    )
    await state.set_state(ReviewStates.waiting_for_review)
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(ReviewStates.waiting_for_review)
async def process_review(message: Message, state: FSMContext):
    """Обработка отзыва"""
    text = message.text
    
    try:
        # Парсим формат: "5 - Отличный сервис"
        if ' - ' in text:
            rating_str, review_text = text.split(' - ', 1)
            rating = int(rating_str.strip())
        elif text[0].isdigit():
            rating = int(text[0])
            review_text = text[1:].strip(' -').strip()
        else:
            rating = 5
            review_text = text
        
        rating = max(1, min(5, rating))
        
        user = message.from_user
        
        @sync_to_async
        def create_review():
            return Review.objects.create(
                name=user.first_name or "Аноним",
                text=review_text,
                rating=rating,
                is_published=False
            )
        
        await create_review()
        
        stars = "⭐" * rating
        await state.clear()
        await message.answer(
            f"✅ <b>Спасибо за ваш отзыв!</b>\n\n"
            f"Оценка: {stars}\n"
            f"Отзыв будет опубликован после модерации.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Ошибка обработки отзыва: {e}")
        await message.answer(
            "❌ Произошла ошибка. Попробуйте еще раз в формате:\n"
            "<code>5 - Ваш отзыв</code>",
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
