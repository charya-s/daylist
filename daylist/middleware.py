from django.conf import settings


def secure_cookies(get_response):
    def middleware(request):
        response = get_response(request)
        if request.is_secure():
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                if name in response.cookies:
                    response.cookies[name]["secure"] = True
        return response

    return middleware
