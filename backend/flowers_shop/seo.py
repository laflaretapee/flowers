from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone


def _base_url(request) -> str:
    base = getattr(settings, 'SITE_URL', '').rstrip('/')
    if base:
        return base
    return request.build_absolute_uri('/').rstrip('/')


def sitemap_xml(request):
    base = _base_url(request)
    today = timezone.localdate().isoformat()
    urls = [
        ('/', 'weekly', '1.0'),
        ('/index.html', 'weekly', '0.9'),
        ('/catalog.html', 'weekly', '0.9'),
        ('/privacy-policy.html', 'yearly', '0.2'),
    ]
    xml_items = []
    for path, changefreq, priority in urls:
        xml_items.append(
            f"<url><loc>{base}{path}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(xml_items) +
        '</urlset>'
    )
    return HttpResponse(xml, content_type='application/xml')


def robots_txt(request):
    base = _base_url(request)
    text = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "Disallow: /bot/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return HttpResponse(text, content_type='text/plain')
