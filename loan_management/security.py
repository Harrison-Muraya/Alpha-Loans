"""Production security settings that stay compatible with plain HTTP.

The live app is served over HTTP at 197.248.201.233:9000. Cookie-secure and
HSTS flags are applied only when DEBUG is off and USE_HTTPS is explicitly
enabled, so that deployment keeps working until TLS is configured.
"""


def production_security_settings(
    *,
    debug,
    use_https,
    hsts_seconds=31536000,
    hsts_include_subdomains=True,
    hsts_preload=False,
    ssl_redirect=True,
):
    if debug:
        return {}

    configured = {
        'SECURE_CONTENT_TYPE_NOSNIFF': True,
        'SECURE_REFERRER_POLICY': 'same-origin',
        'SESSION_COOKIE_HTTPONLY': True,
        'CSRF_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'CSRF_COOKIE_SAMESITE': 'Lax',
        'X_FRAME_OPTIONS': 'DENY',
    }
    if use_https:
        configured.update({
            'SECURE_SSL_REDIRECT': ssl_redirect,
            # Keep liveness probes on plain HTTP (unix socket / localhost)
            # working even when the public site forces HTTPS.
            'SECURE_REDIRECT_EXEMPT': [r'^healthz/?$'],
            'SESSION_COOKIE_SECURE': True,
            'CSRF_COOKIE_SECURE': True,
            'SECURE_HSTS_SECONDS': hsts_seconds,
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': hsts_include_subdomains,
            'SECURE_HSTS_PRELOAD': hsts_preload,
            'SECURE_PROXY_SSL_HEADER': ('HTTP_X_FORWARDED_PROTO', 'https'),
        })
    return configured
