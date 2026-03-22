from .models import ActivityLog


def get_client_ip(request):
    """Extract real client IP, handling proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_activity(request, action, detail='', loan_id=None):
    """
    Create an ActivityLog entry.
    Call this from any view after a meaningful action.
    """
    user = request.user if request.user.is_authenticated else None
    username = user.username if user else 'anonymous'

    ActivityLog.objects.create(
        user=user,
        username=username,
        action=action,
        detail=detail,
        ip_address=get_client_ip(request),
        loan_id=loan_id,
    )