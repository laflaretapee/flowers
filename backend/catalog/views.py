from rest_framework import viewsets, filters, mixins
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from .models import (
    Category, Product, Review,
    SiteSettings, HeroSection, PromoBanner, DeliveryInfo
)
from .serializers import (
    CategorySerializer, ProductSerializer, ReviewSerializer,
    SiteSettingsSerializer, HeroSectionSerializer, PromoBannerSerializer, DeliveryInfoSerializer
)
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


class ReviewViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        product_id = self.request.query_params.get('product', None)
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if self.action in {'list', 'retrieve'}:
            queryset = queryset.filter(is_published=True)
        return queryset.order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(is_published=True)


@api_view(['GET'])
def site_content(request):
    """Получить весь контент сайта одним запросом"""
    settings = SiteSettings.get_settings()
    hero = HeroSection.get_hero()
    promo = PromoBanner.get_promo()
    delivery = DeliveryInfo.get_delivery_info()
    categories = Category.objects.filter(is_active=True)
    products = Product.objects.filter(is_active=True, is_popular=True).order_by('order', 'name')[:6]
    # Если популярные не отмечены, чтобы блок на главной не был пустым — покажем первые 3 активных.
    if not products.exists():
        products = Product.objects.filter(is_active=True).order_by('order', 'name')[:3]
    reviews = Review.objects.filter(is_published=True)[:6]
    
    context = {'request': request}
    
    return Response({
        'settings': SiteSettingsSerializer(settings, context=context).data,
        'hero': HeroSectionSerializer(hero, context=context).data,
        'promo': PromoBannerSerializer(promo, context=context).data if promo.is_active else None,
        'delivery': DeliveryInfoSerializer(delivery, context=context).data,
        'categories': CategorySerializer(categories, many=True, context=context).data,
        'products': ProductSerializer(products, many=True, context=context).data,
        'reviews': ReviewSerializer(reviews, many=True, context=context).data,
    })
