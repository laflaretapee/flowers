from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)

from .globals import get_channel_id, get_group_id


def get_subscribe_keyboard() -> InlineKeyboardMarkup:
    channel_id = get_channel_id()
    group_id = get_group_id()

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
