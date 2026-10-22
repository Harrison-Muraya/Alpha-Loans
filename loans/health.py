"""Unauthenticated liveness/readiness probe for load balancers & CI/CD.

Used by Docker HEALTHCHECK, nginx upstreams, deploy.sh and the GitHub
Actions post-deploy smoke test. Returns 200 only when Django can reach
the database; never leaks exception details.
"""

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
@never_cache
def healthz(request):
    if request.method not in ("GET", "HEAD"):
        return JsonResponse({"status": "method_not_allowed"}, status=405)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - probe must not leak internals
        return JsonResponse({"status": "unhealthy", "database": "down"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
