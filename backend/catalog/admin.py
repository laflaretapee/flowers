from django.contrib import admin, messages
from django.conf import settings
from django.urls import path, reverse
from django.shortcuts import redirect, get_object_or_404
from django.utils.html import format_html
from telegram_bot.sender import send_message
from .models import (
    Category, Product, ProductImage, Review, Order, OrderItem, BotAdmin,
    SiteSettings, HeroSection, PromoBanner, DeliveryInfo
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['product_name', 'price']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order', 'is_active']
    list_display_links = ['name']  # Кликабельная ссылка на редактирование
    list_editable = ['order', 'is_active']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'hide_price', 'is_active', 'is_popular', 'order']
    list_display_links = ['name']  # Кликабельная ссылка на редактирование
    list_filter = ['is_active', 'is_popular', 'hide_price', 'category']
    list_editable = ['price', 'hide_price', 'is_active', 'is_popular', 'order']
    search_fields = ['name', 'description']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline]
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'slug', 'category', 'description', 'short_description')
        }),
        ('Цена и изображение', {
            'fields': ('price', 'hide_price', 'image')
        }),
        ('Настройки', {
            'fields': ('is_active', 'is_popular', 'order')
        }),
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['name', 'rating', 'product', 'is_published', 'created_at']
    list_filter = ['rating', 'is_published', 'created_at']
    list_editable = ['is_published']
    search_fields = ['name', 'text']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'customer_name', 'phone', 'status', 'payment_status', 'total_price', 'created_at']
    list_display_links = ['id', 'customer_name']  # Кликабельные ссылки на редактирование
    list_filter = ['status', 'payment_status', 'created_at', 'has_subscription']
    search_fields = ['customer_name', 'phone', 'telegram_username']
    list_editable = ['status']  # Статус можно менять прямо в списке
    readonly_fields = ['created_at', 'updated_at', 'ready_photo_request_link']
    inlines = [OrderItemInline]
    actions = ['request_ready_photo']
    fieldsets = (
        ('Информация о клиенте', {
            'fields': ('telegram_user_id', 'telegram_username', 'customer_name', 'phone', 'address', 'comment')
        }),
        ('Заказ', {
            'fields': (
                'status',
                'total_price',
                'discount_percent',
                'has_subscription',
                'ready_photo',
                'ready_photo_request_link',
                'payment_status',
                'payment_id',
                'payment_url',
                'paid_at'
            )
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def request_ready_photo(self, request, queryset):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.message_user(request, "TELEGRAM_BOT_TOKEN не задан.", level=messages.ERROR)
            return

        admins = list(BotAdmin.objects.filter(is_active=True, telegram_user_id__isnull=False))
        if not admins:
            self.message_user(request, "Нет активных админов бота с Telegram ID.", level=messages.ERROR)
            return

        sent = 0
        for order in queryset:
            text = (
                f"📷 Запрос фото готового букета для заказа #{order.id}.\n"
                "Нажмите кнопку ниже и отправьте фото."
            )
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "📷 Загрузить фото", "callback_data": f"admin_ready_{order.id}"}]
                ]
            }
            for admin_item in admins:
                if send_message(admin_item.telegram_user_id, text, reply_markup=reply_markup, timeout=10):
                    sent += 1

        if sent:
            self.message_user(request, f"Запросы отправлены: {sent}.", level=messages.SUCCESS)
        else:
            self.message_user(request, "Не удалось отправить запросы в Telegram.", level=messages.ERROR)

    request_ready_photo.short_description = "Запросить фото готового букета в боте"

    def ready_photo_request_link(self, obj):
        if not obj or not obj.pk:
            return "Сначала сохраните заказ."
        url = reverse("admin:catalog_order_request_ready_photo", args=[obj.pk])
        return format_html('<a class="button" href="{}">Запросить фото в боте</a>', url)

    ready_photo_request_link.short_description = "Фото готового букета"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:order_id>/request-ready-photo/',
                self.admin_site.admin_view(self.request_ready_photo_view),
                name='catalog_order_request_ready_photo'
            ),
        ]
        return custom_urls + urls

    def request_ready_photo_view(self, request, order_id):
        order = get_object_or_404(Order, pk=order_id)
        self.request_ready_photo(request, Order.objects.filter(pk=order.id))
        return redirect(f'../../{order.id}/change/')


@admin.register(BotAdmin)
class BotAdminAdmin(admin.ModelAdmin):
    list_display = ['username', 'telegram_user_id', 'is_active', 'note', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['username', 'telegram_user_id', 'note']
    list_editable = ['is_active']


admin.site.register(ProductImage)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ['site_name', 'phone']
    
    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HeroSection)
class HeroSectionAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active']
    fieldsets = (
        ('Основной контент', {
            'fields': ('label', 'title', 'subtitle', 'image')
        }),
        ('Кнопки', {
            'fields': ('button_text', 'button_link', 'secondary_button_text', 'secondary_button_link')
        }),
        ('Бейдж', {
            'fields': ('badge_number', 'badge_text')
        }),
        ('Преимущества', {
            'fields': ('benefit_1', 'benefit_2', 'benefit_3')
        }),
        ('Настройки', {
            'fields': ('is_active',)
        }),
    )
    
    def has_add_permission(self, request):
        return not HeroSection.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PromoBanner)
class PromoBannerAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active']
    
    def has_add_permission(self, request):
        return not PromoBanner.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DeliveryInfo)
class DeliveryInfoAdmin(admin.ModelAdmin):
    list_display = ['title']
    fieldsets = (
        ('Заголовок', {
            'fields': ('title', 'subtitle')
        }),
        ('Преимущества', {
            'fields': ('benefit_1', 'benefit_2', 'benefit_3')
        }),
        ('Шаги', {
            'fields': ('step_1', 'step_2', 'step_3')
        }),
    )
    
    def has_add_permission(self, request):
        return not DeliveryInfo.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False
