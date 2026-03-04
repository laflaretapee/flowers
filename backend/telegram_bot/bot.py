"""
Telegram бот для цветочного магазина (aiogram 3.x)

This module contains only the bot setup class and the webhook singleton.
All handlers, keyboards, states, etc. are in their respective submodules.
"""
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation

from django.conf import settings

from .globals import set_bot, set_channel_id, set_group_id
from .middlewares import SubscriptionMiddleware
from .handlers import all_routers
from .fsm_storage import DjangoFSMStorage

logger = logging.getLogger(__name__)

_middleware_registered: set[int] = set()


class FlowerShopBot:
    """Main bot class: creates Bot + Dispatcher, registers routers."""

    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.bot: Bot | None = None
        self.dp: Dispatcher | None = None

    def _setup(self) -> bool:
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
            return False

        set_channel_id(getattr(settings, 'TELEGRAM_CHANNEL_ID', None))
        set_group_id(getattr(settings, 'TELEGRAM_GROUP_ID', None))

        self.bot = Bot(
            token=self.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        set_bot(self.bot)

        self.dp = Dispatcher(
            storage=DjangoFSMStorage(),
            events_isolation=SimpleEventIsolation(),
        )

        for r in all_routers:
            rid = id(r)
            if rid not in _middleware_registered:
                r.message.middleware(SubscriptionMiddleware())
                r.callback_query.middleware(SubscriptionMiddleware())
                _middleware_registered.add(rid)

            previous_parent = getattr(r, "_parent_router", None)
            if previous_parent is not None and previous_parent is not self.dp:
                try:
                    if r in previous_parent.sub_routers:
                        previous_parent.sub_routers.remove(r)
                except Exception:
                    pass
                r._parent_router = None

            self.dp.include_router(r)

        return True

    async def setup_webhook(self, webhook_url: str):
        if not self._setup():
            return
        await self.bot.set_webhook(webhook_url)
        logger.info("Бот Цветочная Лавка: webhook установлен -> %s", webhook_url)

    async def process_update(self, update_data: dict):
        from aiogram.types import Update
        update = Update.model_validate(update_data, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    async def close(self):
        if not self.bot:
            return
        try:
            await self.bot.session.close()
        except Exception:
            pass


_webhook_bot: FlowerShopBot | None = None


def get_webhook_bot() -> FlowerShopBot:
    global _webhook_bot
    if _webhook_bot is None:
        _webhook_bot = FlowerShopBot()
        _webhook_bot._setup()
    return _webhook_bot
