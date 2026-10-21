from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import LoginAttempt


def _window_start():
    seconds = getattr(settings, 'LOGIN_LOCKOUT_SECONDS', 900)
    return timezone.now() - timedelta(seconds=seconds)


def _prune():
    LoginAttempt.objects.filter(attempted_at__lt=_window_start()).delete()


def _recent():
    return LoginAttempt.objects.filter(attempted_at__gte=_window_start())


def login_is_locked(username, ip):
    """Lock a username after repeated failures, and an IP after a higher threshold."""
    _prune()
    failures = _recent()
    user_limit = getattr(settings, 'LOGIN_FAILURE_LIMIT', 5)
    ip_limit = getattr(settings, 'LOGIN_IP_FAILURE_LIMIT', 50)
    if username and failures.filter(username=username).count() >= user_limit:
        return True
    if ip and failures.filter(ip_address=ip).count() >= ip_limit:
        return True
    return False


def record_login_failure(username, ip):
    _prune()
    LoginAttempt.objects.create(username=(username or '')[:150], ip_address=ip or None)


def clear_login_failures(username):
    if username:
        LoginAttempt.objects.filter(username=username).delete()
