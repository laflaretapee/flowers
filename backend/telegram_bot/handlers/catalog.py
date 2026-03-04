import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardButton, InlineKeyboardMarkup,
    FSInputFile, InputMediaPhoto,
)
from asgiref.sync import sync_to_async

from catalog.models import Category, HeroSection, Product

from ..utils import to_decimal, format_money

logger = logging.getLogger(__name__)

router = Router()


# ── Catalog helpers ──────────────────────────────────────────────

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
            logger.error("Ошибка отправки обложки каталога: %s", e)

    await message.answer(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def edit_catalog_menu(message: Message):
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
        logger.warning("Не удалось отредактировать каталог: %s", e)
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
                logger.error("Ошибка отправки обложки каталога: %s", ex)
        await message.answer(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def send_product_confirmation(message: Message, product: Product):
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
            logger.error("Ошибка отправки фото: %s", e)

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def send_product_with_nav(
    callback: CallbackQuery,
    product: Product,
    index: int,
    total: int,
    nav_prefix: str,
    back_callback: str,
    is_first: bool = False,
):
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
                logger.error("Ошибка отправки фото: %s", e)
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    try:
        if image:
            image_path = await sync_to_async(lambda: image.path)()
            media = InputMediaPhoto(media=FSInputFile(image_path), caption=text, parse_mode=ParseMode.HTML)
            await callback.message.edit_media(media=media, reply_markup=keyboard)
        else:
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
        logger.warning("Не удалось отредактировать: %s", e)
        try:
            await callback.message.delete()
        except Exception:
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
                logger.error("Ошибка отправки фото: %s", ex)
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Handlers ─────────────────────────────────────────────────────

@router.message(Command("catalog"))
@router.message(F.text == "📋 Каталог")
async def show_catalog(message: Message):
    await send_catalog_menu(message)


@router.callback_query(F.data.startswith("cat_"))
async def show_category_products(callback: CallbackQuery):
    parts = callback.data.split("_")
    category_id = int(parts[1])
    index = int(parts[2]) if len(parts) > 2 else 0

    try:
        category = await sync_to_async(Category.objects.get)(id=category_id, is_active=True)
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
    index = max(0, min(index, total - 1))
    product = products[index]

    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix=f"cat_{category_id}",
        back_callback="back_to_catalog",
        is_first=False,
    )


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "back_to_catalog")
async def back_to_catalog(callback: CallbackQuery):
    await callback.answer()
    await edit_catalog_menu(callback.message)


@router.callback_query(F.data.startswith("all_products"))
async def show_all_products(callback: CallbackQuery):
    parts = callback.data.split("_")
    index = int(parts[2]) if len(parts) > 2 else 0

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

    await send_product_with_nav(
        callback, product, index, total,
        nav_prefix="all_products",
        back_callback="back_to_catalog",
        is_first=False,
    )


@router.callback_query(F.data == "decline_order")
async def decline_order(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_catalog_menu(callback.message)
