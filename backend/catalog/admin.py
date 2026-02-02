from django.contrib import admin
from .models import (
    Category, Product, ProductImage, Review, Order, OrderItem,
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
    list_display = ['name', 'order', 'is_active']
    list_editable = ['order', 'is_active']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'hide_price', 'is_active', 'is_popular', 'order']
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
    list_display = ['id', 'customer_name', 'phone', 'status', 'total_price', 'created_at']
    list_filter = ['status', 'created_at', 'has_subscription']
    search_fields = ['customer_name', 'phone', 'telegram_username']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [OrderItemInline]
    fieldsets = (
        ('Информация о клиенте', {
            'fields': ('telegram_user_id', 'telegram_username', 'customer_name', 'phone', 'address', 'comment')
        }),
        ('Заказ', {
            'fields': ('status', 'total_price', 'discount_percent', 'has_subscription')
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at')
        }),
    )


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
