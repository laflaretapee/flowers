from django.db import models
from django.core.validators import MinValueValidator


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
        ('confirmed', 'Подтвержден'),
        ('in_progress', 'В работе'),
        ('ready', 'Готов'),
        ('delivering', 'Доставляется'),
        ('completed', 'Завершен'),
        ('cancelled', 'Отменен'),
    ]
    
    telegram_user_id = models.BigIntegerField('Telegram ID пользователя')
    telegram_username = models.CharField('Telegram username', max_length=100, blank=True)
    customer_name = models.CharField('Имя клиента', max_length=200)
    phone = models.CharField('Телефон', max_length=20)
    address = models.TextField('Адрес доставки')
    comment = models.TextField('Комментарий', blank=True)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='new')
    total_price = models.DecimalField('Итоговая цена', max_digits=10, decimal_places=2)
    discount_percent = models.IntegerField('Скидка %', default=0)
    has_subscription = models.BooleanField('Есть подписка', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлен', auto_now=True)
    
    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Заказ #{self.id} от {self.customer_name}"


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
