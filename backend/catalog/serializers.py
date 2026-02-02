from rest_framework import serializers
from .models import Category, Product, ProductImage, Review


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'image', 'order']


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'image', 'order']


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'short_description',
            'price', 'image', 'category', 'is_active', 'is_popular',
            'order', 'images', 'average_rating'
        ]
    
    def get_average_rating(self, obj):
        reviews = obj.reviews.filter(is_published=True)
        if reviews.exists():
            return round(sum(r.rating for r in reviews) / reviews.count(), 1)
        return None


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id', 'name', 'text', 'rating', 'product', 'created_at']
        read_only_fields = ['created_at']
