from datetime import timedelta

from django.conf import settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from .models import ActivityLog
from .utils import get_client_ip

# URL paths to skip — no point logging these, they'd flood the table
SKIP_PATHS = (
    '/static/',
    '/favicon',
    '/__reload__/',
    '/admin/jsi18n/',
    '/healthz/',
)


class ActivityLogMiddleware:
    """
    Logs page views for authenticated users.

    POST requests for login/logout/register are skipped here because the views
    log those as specific action types instead. Identical page views inside
    ACTIVITY_LOG_DEDUPE_SECONDS are not stored again. Old page-visit rows are
    removed by the purge_activity_logs command.
    """

    # View names handled explicitly in views.py — don't double-log them
    SKIP_URL_NAMES = {
        'login', 'logout', 'register',
        'activity_log',
        'password_reset',
        'password_reset_done',
        'password_reset_confirm',
        'password_reset_complete',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return response

        if response.status_code >= 400:
            return response

        path = request.path
        if any(path.startswith(prefix) for prefix in SKIP_PATHS):
            return response

        try:
            url_name = resolve(path).url_name
        except Resolver404:
            return response
        if url_name in self.SKIP_URL_NAMES:
            return response

        # POST requests are logged as specific actions in views.py
        if request.method == 'POST':
            return response

        dedupe_seconds = getattr(settings, 'ACTIVITY_LOG_DEDUPE_SECONDS', 900)
        if dedupe_seconds:
            cutoff = timezone.now() - timedelta(seconds=dedupe_seconds)
            already_logged = ActivityLog.objects.filter(
                user_id=request.user.id,
                action='page_visit',
                url=path,
                timestamp__gte=cutoff,
            ).exists()
            if already_logged:
                return response

        ActivityLog.objects.create(
            user=request.user,
            username=request.user.username,
            action='page_visit',
            detail=f'{request.method} {path}',
            ip_address=get_client_ip(request),
            url=path,
            method=request.method,
        )
        return response
