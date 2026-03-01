from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
import re


def normalize_phone(value: str) -> str:
    if not value:
        return ''
    digits = re.sub(r'\D+', '', value)
    # Приводим к формату 7XXXXXXXXXX для РФ, если возможно
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits


class SiteSettings(models.Model):
    """Общие настройки сайта (singleton)"""
    site_name = models.CharField('Название сайта', max_length=200, default='Цветочная Лавка')
    phone = models.CharField('Телефон', max_length=50, default='+7 (999) 123-45-67')
    address = models.TextField('Адрес', blank=True)
    telegram_bot_link = models.URLField('Ссылка на Telegram бота', blank=True, default='https://t.me/flowersraevka_bot')
    instagram_link = models.URLField('Instagram', blank=True)
    vk_link = models.URLField('VKontakte', blank=True)
    telegram_channel_link = models.URLField('Telegram канал', blank=True)
    footer_text = models.CharField('Текст в подвале', max_length=300, default='Сделано с любовью к цветам')
    
    class Meta:
        verbose_name = 'Настройки сайта'
        verbose_name_plural = 'Настройки сайта'
    
    def __str__(self):
        return 'Настройки сайта'
    
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_settings(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class HeroSection(models.Model):
    """Главный баннер (Hero секция)"""
    label = models.CharField('Метка', max_length=100, default='Доставка за 90 минут')
    title = models.CharField('Заголовок', max_length=300, default='Живые эмоции в каждом букете')
    subtitle = models.TextField('Подзаголовок', blank=True)
    button_text = models.CharField('Текст кнопки', max_length=100, default='Выбрать букет')
    button_link = models.CharField('Ссылка кнопки', max_length=200, default='catalog.html')
    secondary_button_text = models.CharField('Текст второй кнопки', max_length=100, default='Собрать свой')
    secondary_button_link = models.CharField('Ссылка второй кнопки', max_length=200, blank=True)
    image = models.ImageField('Изображение', upload_to='hero/', blank=True, null=True)
    badge_number = models.CharField('Число в бейдже', max_length=50, default='850+')
    badge_text = models.CharField('Текст бейджа', max_length=100, default='довольных клиентов')
    benefit_1 = models.CharField('Преимущество 1', max_length=200, default='Фото букета перед отправкой')
    benefit_2 = models.CharField('Преимущество 2', max_length=200, default='Оплата онлайн и при получении')
    benefit_3 = models.CharField('Преимущество 3', max_length=200, default='Работаем 24/7')
    is_active = models.BooleanField('Активен', default=True)
    
    class Meta:
        verbose_name = 'Hero секция'
        verbose_name_plural = 'Hero секция'
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_hero(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class PromoBanner(models.Model):
    """Промо баннер"""
    icon = models.CharField('Иконка (emoji)', max_length=10, default='🌷')
    title = models.CharField('Заголовок', max_length=200, default='Предзаказ тюльпанов к 8 марта 2026')
    text = models.CharField(
        'Текст',
        max_length=300,
        default='Оформите заказ заранее и получите скидку 10% на праздничные букеты.'
    )
    button_text = models.CharField('Текст кнопки', max_length=100, default='Предзаказать')
    button_link = models.CharField('Ссылка кнопки', max_length=200, blank=True, default='catalog.html')
    is_active = models.BooleanField('Активен', default=True)
    
    class Meta:
        verbose_name = 'Промо баннер'
        verbose_name_plural = 'Промо баннер'
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_promo(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class DeliveryInfo(models.Model):
    """Информация о доставке"""
    title = models.CharField('Заголовок', max_length=200, default='Доставка без задержек')
    subtitle = models.TextField('Подзаголовок', default='Привезём букет в течение 90 минут по городу или ко времени мероприятия.')
    benefit_1 = models.CharField('Преимущество 1', max_length=200, default='Бесплатная доставка от 5 000 ₽')
    benefit_2 = models.CharField('Преимущество 2', max_length=200, default='Фото готового букета в мессенджер')
    benefit_3 = models.CharField('Преимущество 3', max_length=200, default='Аккуратная упаковка и фирменная открытка')
    step_1 = models.CharField('Шаг 1', max_length=200, default='Вы выбираете букет или собираете свой.')
    step_2 = models.CharField('Шаг 2', max_length=200, default='Оставляете контакты и адрес.')
    step_3 = models.CharField('Шаг 3', max_length=200, default='Мы собираем и отправляем курьера.')
    
    class Meta:
        verbose_name = 'Информация о доставке'
        verbose_name_plural = 'Информация о доставке'
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_delivery_info(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class Category(models.Model):
    """Категория букетов"""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('URL', unique=True)
    description = models.TextField('Описание', blank=True)
    image = models.ImageField('Изображение', upload_to='categories/', blank=True, null=True)
    order = models.IntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активна', default=True)
    
    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name


class Product(models.Model):
    """Товар (букет)"""
    name = models.CharField('Название', max_length=200)
    slug = models.SlugField('URL', unique=True)
    description = models.TextField('Описание', blank=True)
    short_description = models.CharField('Краткое описание', max_length=300, blank=True)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    hide_price = models.BooleanField('Скрыть цену', default=False, help_text='Если галочка стоит, цена не будет отображаться на сайте')
    image = models.ImageField('Главное изображение', upload_to='products/', blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Категория')
    is_active = models.BooleanField('Активен', default=True)
    is_popular = models.BooleanField('Популярный', default=False)
    order = models.IntegerField('Порядок', default=0)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлен', auto_now=True)
    
    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['order', '-is_popular', 'name']
    
    def __str__(self):
        return self.name


class ProductImage(models.Model):
    """Дополнительные изображения товара"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images', verbose_name='Товар')
    image = models.ImageField('Изображение', upload_to='products/')
    order = models.IntegerField('Порядок', default=0)
    
    class Meta:
        verbose_name = 'Изображение товара'
        verbose_name_plural = 'Изображения товаров'
        ordering = ['order']
    
    def __str__(self):
        return f"{self.product.name} - изображение {self.order}"


class Review(models.Model):
    """Отзыв клиента"""
    RATING_CHOICES = [
        (5, '5 - Отлично'),
        (4, '4 - Хорошо'),
        (3, '3 - Нормально'),
        (2, '2 - Плохо'),
        (1, '1 - Очень плохо'),
    ]
    
    name = models.CharField('Имя', max_length=100)
    telegram_user_id = models.BigIntegerField('Telegram ID пользователя', blank=True, null=True)
    avatar = models.ImageField('Аватар', upload_to='reviews/avatars/', blank=True, null=True)
    text = models.TextField('Текст отзыва')
    rating = models.IntegerField('Оценка', choices=RATING_CHOICES, default=5)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True, related_name='reviews', verbose_name='Товар')
    is_published = models.BooleanField('Опубликован', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    
    class Meta:
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} - {self.rating} звезд"


class Order(models.Model):
    """Заказ через Telegram"""
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('processing', 'В работе'),
        ('ready', 'Готов'),
        ('completed', 'Завершен'),
        ('cancelled', 'Отменен'),
        ('expired', 'Просрочен'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('not_paid', 'Не оплачен'),
        ('pending', 'Ожидает оплаты'),
        ('succeeded', 'Оплачен'),
        ('canceled', 'Отменен'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('transfer', 'Перевод'),
        ('online', 'Онлайн'),
    ]
    
    telegram_user_id = models.BigIntegerField('Telegram ID пользователя')
    telegram_username = models.CharField('Telegram username', max_length=100, blank=True)
    customer_name = models.CharField('Имя клиента', max_length=200)
    phone = models.CharField('Телефон', max_length=20)
    phone_normalized = models.CharField('Телефон (нормализованный)', max_length=20, blank=True, db_index=True)
    address = models.TextField('Адрес доставки')
    comment = models.TextField('Комментарий', blank=True)
    is_preorder = models.BooleanField('Предзаказ', default=False)
    requested_delivery = models.CharField('Желаемая дата/время', max_length=120, blank=True)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='new')
    items_subtotal = models.DecimalField('Сумма товаров', max_digits=10, decimal_places=2, default=0)
    delivery_price = models.DecimalField('Стоимость доставки', max_digits=10, decimal_places=2, default=0)
    total_price = models.DecimalField('Итоговая цена', max_digits=10, decimal_places=2)
    discount_percent = models.IntegerField('Скидка %', default=0)
    has_subscription = models.BooleanField('Есть подписка', default=False)
    service_chat_id = models.CharField('Служебный чат', max_length=100, blank=True)
    service_message_id = models.BigIntegerField('ID служебного сообщения', blank=True, null=True)
    processing_by_user_id = models.BigIntegerField('Обрабатывает (Telegram ID)', blank=True, null=True)
    processing_by_username = models.CharField('Обрабатывает (username)', max_length=100, blank=True)
    ready_photo = models.ImageField('Фото готового букета', upload_to='orders/ready/', blank=True, null=True)
    payment_status = models.CharField(
        'Статус оплаты',
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='not_paid'
    )
    payment_method = models.CharField(
        'Способ оплаты',
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='transfer',
    )
    transfer_details = models.CharField('Реквизиты перевода', max_length=255, blank=True)
    payment_id = models.CharField('ID платежа YooKassa', max_length=100, blank=True)
    payment_url = models.URLField('Ссылка на оплату', blank=True)
    paid_at = models.DateTimeField('Дата оплаты', blank=True, null=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлен', auto_now=True)
    
    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Заказ #{self.id} от {self.customer_name}"

    def clean(self):
        super().clean()
        if self.status == 'ready' and not self.ready_photo:
            raise ValidationError({'ready_photo': 'Для статуса «Готов» загрузите фото готового букета.'})


class BotAdmin(models.Model):
    """Администратор бота (управляется из Django admin)."""

    username = models.CharField('Telegram username', max_length=100, blank=True, help_text='Без @')
    telegram_user_id = models.BigIntegerField('Telegram ID', blank=True, null=True)
    is_active = models.BooleanField('Активен', default=True)
    note = models.CharField('Комментарий', max_length=200, blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Админ бота'
        verbose_name_plural = 'Админы бота'
        indexes = [
            models.Index(fields=['is_active', 'telegram_user_id']),
            models.Index(fields=['is_active', 'username']),
        ]

    def __str__(self):
        if self.username:
            return self.username
        if self.telegram_user_id:
            return str(self.telegram_user_id)
        return f"Админ #{self.pk}"


class OrderItem(models.Model):
    """Элемент заказа"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, verbose_name='Товар')
    product_name = models.CharField('Название товара', max_length=200)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2)
    quantity = models.IntegerField('Количество', default=1)
    
    class Meta:
        verbose_name = 'Элемент заказа'
        verbose_name_plural = 'Элементы заказа'
    
    def __str__(self):
        return f"{self.product_name} x{self.quantity}"
