import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, TelegramObject

from .keyboards import get_subscribe_keyboard
from .services import check_user_subscription, is_bot_admin

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseMiddleware):
    """Проверяет подписку пользователя перед обработкой сообщений."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
            if event.text and event.text.startswith('/start'):
                return await handler(event, data)
            if event.text and event.text.startswith('/admin'):
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            if event.data == "check_subscription":
                return await handler(event, data)

        if user_id is None:
            return await handler(event, data)

        try:
            username = None
            if isinstance(event, (Message, CallbackQuery)) and event.from_user:
                username = event.from_user.username
            if await is_bot_admin(user_id, username):
                return await handler(event, data)
        except Exception:
            pass

        is_subscribed = await check_user_subscription(user_id)

        if not is_subscribed:
            keyboard = get_subscribe_keyboard()
            text = (
                "⚠️ <b>Для использования бота необходимо подписаться на наш канал!</b>\n\n"
                "После подписки нажмите кнопку «✅ Я подписался» для проверки."
            )
            if isinstance(event, Message):
                await event.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            elif isinstance(event, CallbackQuery):
                await event.answer("Сначала подпишитесь на канал!", show_alert=True)
            return

        return await handler(event, data)
