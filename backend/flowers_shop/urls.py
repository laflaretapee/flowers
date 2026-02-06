"""
URL configuration for flowers_shop project.
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as static_serve
from django.http import FileResponse

# Customize admin site
admin.site.site_header = 'Цветочная Лавка — Админ-панель'
admin.site.site_title = 'Цветочная Лавка'
admin.site.index_title = 'Управление сайтом'


def serve_frontend(request, page='index.html'):
    """Serve frontend HTML pages from the project root."""
    file_path = settings.FRONTEND_DIR / page
    if file_path.is_file() and file_path.suffix in ('.html', '.ico', '.txt', '.xml'):
        return FileResponse(open(file_path, 'rb'))
    from django.http import Http404
    raise Http404


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('catalog.urls')),
    # Frontend pages
    path('', serve_frontend, {'page': 'index.html'}, name='home'),
    path('catalog.html', serve_frontend, {'page': 'catalog.html'}, name='catalog'),
    path('privacy-policy.html', serve_frontend, {'page': 'privacy-policy.html'}, name='privacy-policy'),
]

# Media files
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
