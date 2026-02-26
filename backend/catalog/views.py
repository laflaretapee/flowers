from rest_framework import viewsets, filters, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.utils import timezone
from django.db.models import Avg, Q
from .models import (
    Category, Product, Review, Order,
    SiteSettings, HeroSection, PromoBanner, DeliveryInfo
)
from .serializers import (
    CategorySerializer, ProductSerializer, ReviewSerializer,
    ProductListSerializer, SiteSettingsSerializer, HeroSectionSerializer, PromoBannerSerializer, DeliveryInfoSerializer
)
from .payments import yookassa_enabled, map_payment_status, notify_payment_status
class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Product.objects.filter(is_active=True).select_related('category')
    serializer_class = ProductListSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['price', 'order', 'created_at']
    ordering = ['order', '-is_popular']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ProductSerializer
        return ProductListSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get('category', None)
        is_popular = self.request.query_params.get('is_popular', None)

        if self.action == 'retrieve':
            queryset = queryset.prefetch_related('images', 'reviews')
        else:
            queryset = queryset.annotate(
                average_rating=Avg('reviews__rating', filter=Q(reviews__is_published=True))
            )
        
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


@cache_page(60)
@api_view(['GET'])
def site_content(request):
    """Получить весь контент сайта одним запросом"""
    settings = SiteSettings.get_settings()
    hero = HeroSection.get_hero()
    promo = PromoBanner.get_promo()
    delivery = DeliveryInfo.get_delivery_info()
    categories = Category.objects.filter(is_active=True)
    products = Product.objects.filter(is_active=True, is_popular=True).select_related('category').order_by('order', 'name')[:6]
    # Если популярные не отмечены, чтобы блок на главной не был пустым — покажем первые 3 активных.
    if not products.exists():
        products = Product.objects.filter(is_active=True).select_related('category').order_by('order', 'name')[:3]
    reviews = Review.objects.filter(is_published=True)[:6]
    
    context = {'request': request}
    
    return Response({
        'settings': SiteSettingsSerializer(settings, context=context).data,
        'hero': HeroSectionSerializer(hero, context=context).data,
        'promo': PromoBannerSerializer(promo, context=context).data if promo.is_active else None,
        'delivery': DeliveryInfoSerializer(delivery, context=context).data,
        'categories': CategorySerializer(categories, many=True, context=context).data,
        'products': ProductListSerializer(products, many=True, context=context).data,
        'reviews': ReviewSerializer(reviews, many=True, context=context).data,
    })


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def yookassa_webhook(request):
    """Webhook YooKassa для обновления статуса оплаты."""
    if not yookassa_enabled():
        return Response({'detail': 'YooKassa disabled'}, status=503)

    payload = request.data or {}
    obj = payload.get('object') or {}
    payment_id = obj.get('id')
    status = obj.get('status')
    metadata = obj.get('metadata') or {}
    order_id = metadata.get('order_id')

    if not payment_id:
        return Response({'detail': 'Missing payment id'}, status=400)

    order = None
    if order_id:
        order = Order.objects.filter(pk=order_id).first()
    if order is None:
        order = Order.objects.filter(payment_id=payment_id).first()
    if order is None:
        return Response({'detail': 'Order not found'}, status=200)

    prev_status = order.payment_status
    new_status = map_payment_status(status)
    order.payment_id = payment_id
    order.payment_status = new_status
    if new_status == 'succeeded' and not order.paid_at:
        order.paid_at = timezone.now()
    order.save(update_fields=['payment_id', 'payment_status', 'paid_at', 'updated_at'])

    if new_status != prev_status:
        notify_payment_status(order, new_status)

    return Response({'status': 'ok'})
