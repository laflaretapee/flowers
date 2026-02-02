"""
Telegram бот для цветочного магазина
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from django.conf import settings
from catalog.models import Product, Category, Order, OrderItem, Review
from catalog.taxi_integration import TaxiDeliveryIntegration
from django.db import transaction

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class FlowerShopBot:
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.group_id = settings.TELEGRAM_GROUP_ID
        self.channel_id = settings.TELEGRAM_CHANNEL_ID
        self.application = None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        user = update.effective_user
        user_id = user.id
        
        # Проверяем подписку на группу
        has_subscription = await self.check_subscription(user_id, context)
        
        welcome_text = f"🌸 Добро пожаловать в Цветочная Лавка, {user.first_name}!\n\n"
        welcome_text += "Мы создаем авторские букеты из свежих цветов с доставкой по городу.\n\n"
        
        if has_subscription:
            discount = settings.PROMO_DISCOUNT_PERCENT
            welcome_text += f"🎁 У вас есть скидка {discount}% за подписку на нашу группу!\n\n"
        elif settings.PROMO_ENABLED:
            discount = settings.PROMO_DISCOUNT_PERCENT
            welcome_text += f"🎁 Подпишитесь на нашу группу и получите скидку {discount}% на первый заказ!\n\n"
        
        keyboard = [
            [KeyboardButton("📋 Каталог")],
            [KeyboardButton("🎁 Акции"), KeyboardButton("📞 Контакты")],
            [KeyboardButton("📝 Оставить отзыв")]
        ]
        
        if not has_subscription and settings.PROMO_ENABLED:
            keyboard.insert(1, [KeyboardButton("🎁 Подписаться и получить скидку")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def check_subscription(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка подписки пользователя на группу/канал"""
        if not self.group_id and not self.channel_id:
            return False
        
        try:
            if self.group_id:
                member = await context.bot.get_chat_member(self.group_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
            
            if self.channel_id:
                member = await context.bot.get_chat_member(self.channel_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
        
        return False
    
    async def handle_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка кнопки подписки"""
        user_id = update.effective_user.id
        
        if not self.group_id:
            await update.message.reply_text("Группа не настроена. Обратитесь к администратору.")
            return
        
        has_subscription = await self.check_subscription(user_id, context)
        
        if has_subscription:
            discount = settings.PROMO_DISCOUNT_PERCENT
            await update.message.reply_text(
                f"✅ Вы уже подписаны на нашу группу!\n\n"
                f"🎁 У вас действует скидка {discount}% на заказы."
            )
        else:
            group_link = f"https://t.me/{self.group_id.replace('@', '')}" if not self.group_id.startswith('-') else None
            if group_link:
                await update.message.reply_text(
                    f"🎁 Подпишитесь на нашу группу и получите скидку {settings.PROMO_DISCOUNT_PERCENT}%!\n\n"
                    f"После подписки нажмите /start для активации скидки.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Подписаться", url=group_link)
                    ]])
                )
            else:
                await update.message.reply_text(
                    f"🎁 Подпишитесь на нашу группу и получите скидку {settings.PROMO_DISCOUNT_PERCENT}%!\n\n"
                    f"После подписки нажмите /start для активации скидки."
                )
    
    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать каталог"""
        categories = Category.objects.filter(is_active=True)[:6]
        
        if not categories.exists():
            await update.message.reply_text("Каталог пока пуст. Загляните позже!")
            return
        
        keyboard = []
        for category in categories:
            keyboard.append([InlineKeyboardButton(
                category.name,
                callback_data=f"cat_{category.id}"
            )])
        
        keyboard.append([InlineKeyboardButton("📋 Все товары", callback_data="all_products")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📋 Выберите категорию или посмотрите все товары:",
            reply_markup=reply_markup
        )
    
    async def show_category_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: int):
        """Показать товары категории"""
        try:
            category = Category.objects.get(id=category_id, is_active=True)
            products = Product.objects.filter(category=category, is_active=True)[:10]
        except Category.DoesNotExist:
            await update.callback_query.answer("Категория не найдена")
            return
        
        if not products.exists():
            await update.callback_query.answer("В этой категории пока нет товаров")
            return
        
        for product in products:
            text = f"🌸 <b>{product.name}</b>\n\n"
            if product.short_description:
                text += f"{product.short_description}\n\n"
            text += f"💰 Цена: <b>{product.price} ₽</b>"
            
            keyboard = [[InlineKeyboardButton("🛒 Заказать", callback_data=f"order_{product.id}")]]
            
            if product.image:
                await update.callback_query.message.reply_photo(
                    photo=product.image.url if hasattr(product.image, 'url') else product.image,
                    caption=text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.callback_query.message.reply_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        await update.callback_query.answer()
    
    async def show_all_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все товары"""
        products = Product.objects.filter(is_active=True)[:20]
        
        if not products.exists():
            await update.callback_query.answer("Каталог пуст")
            return
        
        for product in products:
            text = f"🌸 <b>{product.name}</b>\n\n"
            if product.short_description:
                text += f"{product.short_description}\n\n"
            if product.category:
                text += f"📁 {product.category.name}\n\n"
            text += f"💰 Цена: <b>{product.price} ₽</b>"
            
            keyboard = [[InlineKeyboardButton("🛒 Заказать", callback_data=f"order_{product.id}")]]
            
            if product.image:
                await update.callback_query.message.reply_photo(
                    photo=product.image.url if hasattr(product.image, 'url') else product.image,
                    caption=text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.callback_query.message.reply_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        await update.callback_query.answer()
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback запросов"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith("cat_"):
            category_id = int(data.split("_")[1])
            await self.show_category_products(update, context, category_id)
        elif data == "all_products":
            await self.show_all_products(update, context)
        elif data.startswith("order_"):
            product_id = int(data.split("_")[1])
            await self.start_order(update, context, product_id)
    
    async def start_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        """Начать оформление заказа"""
        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            await update.callback_query.message.reply_text("Товар не найден")
            return
        
        user = update.effective_user
        has_subscription = await self.check_subscription(user.id, context)
        
        discount = settings.PROMO_DISCOUNT_PERCENT if has_subscription and settings.PROMO_ENABLED else 0
        final_price = float(product.price) * (1 - discount / 100)
        
        text = f"🛒 Оформление заказа\n\n"
        text += f"🌸 <b>{product.name}</b>\n"
        text += f"💰 Цена: {product.price} ₽\n"
        
        if discount > 0:
            text += f"🎁 Скидка: {discount}%\n"
            text += f"💰 Итого: <b>{final_price:.2f} ₽</b>\n\n"
        else:
            text += f"\n💰 Итого: <b>{final_price:.2f} ₽</b>\n\n"
        
        text += "Пожалуйста, отправьте:\n"
        text += "1. Ваше имя\n"
        text += "2. Телефон\n"
        text += "3. Адрес доставки\n\n"
        text += "Или отправьте /cancel для отмены"
        
        # Сохраняем состояние заказа
        context.user_data['ordering'] = True
        context.user_data['product_id'] = product_id
        context.user_data['discount'] = discount
        
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        text = update.message.text
        
        if context.user_data.get('ordering'):
            await self.process_order(update, context)
            return
        
        if text == "📋 Каталог":
            await self.show_catalog(update, context)
        elif text == "🎁 Акции" or text == "🎁 Подписаться и получить скидку":
            await self.handle_subscribe(update, context)
        elif text == "📞 Контакты":
            await update.message.reply_text(
                "📞 <b>Контакты</b>\n\n"
                "Телефон: +7 (999) 123‑45‑67\n"
                "Адрес: Трактовая улица, 78А, село Раевский, Альшеевский район, Республика Башкортостан, 452120\n\n"
                "Мы работаем 24/7!",
                parse_mode='HTML'
            )
        elif text == "📝 Оставить отзыв":
            await update.message.reply_text(
                "📝 Оставьте отзыв о нашем сервисе!\n\n"
                "Пожалуйста, отправьте ваш отзыв в следующем формате:\n"
                "Оценка (1-5) - Ваш отзыв\n\n"
                "Например: 5 - Отличный сервис, все понравилось!"
            )
            context.user_data['leaving_review'] = True
        elif context.user_data.get('leaving_review'):
            await self.process_review(update, context)
        else:
            await update.message.reply_text(
                "Используйте кнопки меню или команды:\n"
                "/start - Главное меню\n"
                "/catalog - Каталог"
            )
    
    async def process_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка заказа"""
        text = update.message.text
        
        if text == "/cancel":
            context.user_data.pop('ordering', None)
            await update.message.reply_text("Заказ отменен")
            return
        
        if 'order_data' not in context.user_data:
            # Первое сообщение - имя, телефон, адрес
            context.user_data['order_data'] = text
            await update.message.reply_text(
                "Спасибо! Теперь отправьте комментарий к заказу (или отправьте /skip чтобы пропустить)"
            )
        elif 'order_comment' not in context.user_data:
            if text != "/skip":
                context.user_data['order_comment'] = text
            else:
                context.user_data['order_comment'] = ""
            
            # Создаем заказ
            await self.create_order(update, context)
    
    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Создание заказа в БД"""
        try:
            user = update.effective_user
            product_id = context.user_data.get('product_id')
            discount = context.user_data.get('discount', 0)
            order_data = context.user_data.get('order_data', '')
            comment = context.user_data.get('order_comment', '')
            
            product = Product.objects.get(id=product_id)
            has_subscription = await self.check_subscription(user.id, context)
            
            # Парсим данные заказа (простой вариант)
            lines = order_data.split('\n')
            name = lines[0] if len(lines) > 0 else user.first_name
            phone = lines[1] if len(lines) > 1 else "Не указан"
            address = '\n'.join(lines[2:]) if len(lines) > 2 else "Не указан"
            
            # Рассчитываем стоимость доставки через такси
            shop_address = "Трактовая улица, 78А, село Раевский, Альшеевский район, Республика Башкортостан, 452120"  # Адрес магазина
            taxi_integration = TaxiDeliveryIntegration()
            delivery_info = taxi_integration.calculate_delivery_cost(
                from_address=shop_address,
                to_address=address,
                order_weight=1  # Примерный вес букета
            )
            
            product_price = float(product.price) * (1 - discount / 100)
            delivery_cost = float(delivery_info['cost'])
            final_price = product_price + delivery_cost
            
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
                    has_subscription=has_subscription
                )
                
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    price=product.price,
                    quantity=1
                )
            
            response_text = f"✅ Заказ оформлен!\n\n"
            response_text += f"Номер заказа: #{order.id}\n"
            response_text += f"Товар: {product.name}\n"
            response_text += f"Цена товара: {product.price} ₽\n"
            if discount > 0:
                response_text += f"Скидка: {discount}%\n"
            response_text += f"Доставка: {delivery_cost:.2f} ₽\n"
            response_text += f"Итого: {final_price:.2f} ₽\n\n"
            response_text += f"⏱ Примерное время доставки: {delivery_info['duration']} минут\n\n"
            response_text += f"Мы свяжемся с вами в ближайшее время для подтверждения заказа."
            
            await update.message.reply_text(response_text)
            
            # Очищаем данные
            context.user_data.pop('ordering', None)
            context.user_data.pop('product_id', None)
            context.user_data.pop('discount', None)
            context.user_data.pop('order_data', None)
            context.user_data.pop('order_comment', None)
            
        except Exception as e:
            logger.error(f"Ошибка создания заказа: {e}")
            await update.message.reply_text("Произошла ошибка при оформлении заказа. Попробуйте еще раз.")
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("catalog", self.show_catalog))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def process_review(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка отзыва"""
        text = update.message.text
        
        try:
            # Парсим формат: "5 - Отличный сервис"
            if ' - ' in text:
                rating_str, review_text = text.split(' - ', 1)
                rating = int(rating_str.strip())
            elif text.startswith(('1', '2', '3', '4', '5')):
                rating = int(text[0])
                review_text = text[1:].strip(' -').strip()
            else:
                # Если формат не распознан, используем оценку 5 по умолчанию
                rating = 5
                review_text = text
            
            if rating < 1 or rating > 5:
                rating = 5
            
            user = update.effective_user
            
            Review.objects.create(
                name=user.first_name or "Аноним",
                text=review_text,
                rating=rating,
                is_published=False  # Требует модерации
            )
            
            await update.message.reply_text(
                f"✅ Спасибо за ваш отзыв!\n\n"
                f"Оценка: {rating} звезд\n"
                f"Отзыв будет опубликован после модерации."
            )
            
            context.user_data.pop('leaving_review', None)
            
        except Exception as e:
            logger.error(f"Ошибка обработки отзыва: {e}")
            await update.message.reply_text(
                "Произошла ошибка при сохранении отзыва. Попробуйте еще раз в формате:\n"
                "5 - Ваш отзыв"
            )
    
    def run(self):
        """Запуск бота"""
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
            return
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        logger.info("Бот запущен")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)
