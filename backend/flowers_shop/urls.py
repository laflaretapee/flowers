"""
URL configuration for flowers_shop project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# Customize admin site
admin.site.site_header = 'Цветочная Лавка — Админ-панель'
admin.site.site_title = 'Цветочная Лавка'
admin.site.index_title = 'Управление сайтом'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('catalog.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
