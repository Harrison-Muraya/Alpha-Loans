from .models import ActivityLog

# URL paths to skip — no point logging these, they'd flood the table
SKIP_PATHS = (
    '/static/',
    '/favicon',
    '/__reload__/',
    '/admin/jsi18n/',
)


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class ActivityLogMiddleware:
    """
    Logs every GET and POST request made by an authenticated user.
    POST requests for login/logout/register are skipped here because
    the views log those as specific action types instead.
    """

    # View names handled explicitly in views.py — don't double-log them
    SKIP_URL_NAMES = {
        'login', 'logout', 'register',
        'activity_log',         # don't log viewing the log itself
        'password_reset',
        'password_reset_done',
        'password_reset_confirm',
        'password_reset_complete',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only track authenticated users
        if not request.user.is_authenticated:
            return response

        # Skip static assets and internal reloader
        path = request.path
        if any(path.startswith(p) for p in SKIP_PATHS):
            return response

        # Skip explicitly managed view names
        try:
            from django.urls import resolve
            url_name = resolve(path).url_name
            if url_name in self.SKIP_URL_NAMES:
                return response
        except Exception:
            pass

        # Skip POST requests — those are logged as specific actions in views.py
        if request.method == 'POST':
            return response

        # Build a readable page label from the path
        detail = f'{request.method} {path}'

        ActivityLog.objects.create(
            user=request.user,
            username=request.user.username,
            action='page_visit',
            detail=detail,
            ip_address=get_client_ip(request),
            url=path,
            method=request.method,
        )

        return response