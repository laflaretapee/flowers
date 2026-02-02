from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from .models import Category, Product, Review
from .serializers import CategorySerializer, ProductSerializer, ReviewSerializer
from django.db.models import Avg


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Product.objects.filter(is_active=True).select_related('category').prefetch_related('images', 'reviews')
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['price', 'order', 'created_at']
    ordering = ['order', '-is_popular']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get('category', None)
        is_popular = self.request.query_params.get('is_popular', None)
        
        if category:
            queryset = queryset.filter(category_id=category)
        if is_popular == 'true':
            queryset = queryset.filter(is_popular=True)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def popular(self, request):
        """Популярные товары"""
        products = self.queryset.filter(is_popular=True)
        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.filter(is_published=True)
    serializer_class = ReviewSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        product_id = self.request.query_params.get('product', None)
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return queryset.order_by('-created_at')
