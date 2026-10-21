import ipaddress

from django.conf import settings
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .models import ActivityLog


def _valid_ip(value):
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def get_client_ip(request):
    """Return the client IP without trusting a spoofed X-Forwarded-For header.

    The header is read only when the immediate peer is a trusted proxy, or when
    REMOTE_ADDR is empty (gunicorn behind nginx on a local unix socket). The
    client is the right-most address in the chain that is not itself a trusted
    proxy. Set TRUSTED_PROXY_IPS to the addresses of your reverse proxies.
    """
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    trusted = set(getattr(settings, 'TRUSTED_PROXY_IPS', []))
    peer_is_trusted = remote in trusted or remote == ''
    if not peer_is_trusted:
        return _valid_ip(remote)

    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    parts = [part.strip() for part in forwarded.split(',') if part.strip()]
    for candidate in reversed(parts):
        if candidate not in trusted:
            return _valid_ip(candidate)
    return _valid_ip(remote)


def log_activity(request, action, detail='', loan_id=None):
    """Create an ActivityLog entry for a meaningful action."""
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


def safe_redirect(request, fallback='manage_loans'):
    """Redirect to the same-site Referer, or to a named URL.

    An untrusted Referer is an open redirect, so it is ignored.
    """
    referer = request.META.get('HTTP_REFERER', '')
    if referer and url_has_allowed_host_and_scheme(
        url=referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(fallback)
