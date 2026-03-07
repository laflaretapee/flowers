from rest_framework import serializers
from .models import (
    Category, Product, ProductImage, Review,
    SiteSettings, HeroSection, PromoBanner, DeliveryInfo
)


def media_url(file_field):
    if not file_field:
        return None
    try:
        return file_field.url
    except Exception:
        return None


class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ['id', 'image', 'order']

    def get_image(self, obj):
        return media_url(getattr(obj, 'image', None))


class CategorySerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'image', 'order']

    def get_image(self, obj):
        return media_url(getattr(obj, 'image', None))


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'short_description',
            'price', 'hide_price', 'image', 'category', 'is_active', 'is_popular',
            'order', 'images', 'average_rating'
        ]

    def get_image(self, obj):
        return media_url(getattr(obj, 'image', None))
    
    def get_average_rating(self, obj):
        annotated = getattr(obj, 'average_rating', None)
        if annotated is not None:
            return round(float(annotated), 1)
        reviews = obj.reviews.filter(is_published=True).only('rating')
        if reviews.exists():
            return round(sum(r.rating for r in reviews) / reviews.count(), 1)
        return None


class ProductListSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'short_description',
            'price', 'hide_price', 'image', 'category', 'is_active', 'is_popular',
            'order'
        ]

    def get_image(self, obj):
        return media_url(getattr(obj, 'image', None))


class ReviewSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ['id', 'name', 'text', 'rating', 'product', 'product_name', 'avatar_url', 'created_at']
        read_only_fields = ['created_at']

    def get_avatar_url(self, obj):
        return media_url(getattr(obj, 'avatar', None))


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = [
            'site_name', 'phone', 'address', 'telegram_bot_link',
            'instagram_link', 'vk_link', 'telegram_channel_link', 'footer_text',
            'promo_enabled', 'promo_discount_percent',
        ]


class HeroSectionSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = HeroSection
        fields = [
            'label', 'title', 'subtitle', 'button_text', 'button_link',
            'secondary_button_text', 'secondary_button_link', 'image',
            'badge_number', 'badge_text', 'benefit_1', 'benefit_2', 'benefit_3'
        ]

    def get_image(self, obj):
        return media_url(getattr(obj, 'image', None))


class PromoBannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromoBanner
        fields = ['icon', 'title', 'text', 'button_text', 'button_link', 'is_active']


class DeliveryInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryInfo
        fields = [
            'title', 'subtitle', 'benefit_1', 'benefit_2', 'benefit_3',
            'step_1', 'step_2', 'step_3'
        ]
