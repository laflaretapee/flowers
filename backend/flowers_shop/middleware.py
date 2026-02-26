class AdminNoIndexMiddleware:
    """Prevent search engines from indexing Django admin pages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/admin/'):
            response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response
