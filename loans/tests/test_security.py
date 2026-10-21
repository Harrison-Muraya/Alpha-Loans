from django.conf import settings
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from loan_management.security import production_security_settings
from loans.utils import get_client_ip, safe_redirect


class ProductionSecurityTests(SimpleTestCase):
    def test_debug_does_not_force_production_flags(self):
        self.assertEqual(
            production_security_settings(debug=True, use_https=True),
            {},
        )

    def test_plain_http_deployment_skips_hsts_and_secure_cookies(self):
        configured = production_security_settings(debug=False, use_https=False)
        self.assertTrue(configured['SECURE_CONTENT_TYPE_NOSNIFF'])
        self.assertEqual(configured['X_FRAME_OPTIONS'], 'DENY')
        self.assertNotIn('SESSION_COOKIE_SECURE', configured)
        self.assertNotIn('CSRF_COOKIE_SECURE', configured)
        self.assertNotIn('SECURE_HSTS_SECONDS', configured)
        self.assertNotIn('SECURE_SSL_REDIRECT', configured)
        self.assertNotIn('SECURE_PROXY_SSL_HEADER', configured)

    def test_https_opt_in_enables_hsts_and_secure_cookies(self):
        configured = production_security_settings(
            debug=False, use_https=True, hsts_seconds=123, ssl_redirect=False
        )
        self.assertTrue(configured['SESSION_COOKIE_SECURE'])
        self.assertTrue(configured['CSRF_COOKIE_SECURE'])
        self.assertEqual(configured['SECURE_HSTS_SECONDS'], 123)
        self.assertFalse(configured['SECURE_SSL_REDIRECT'])
        self.assertEqual(
            configured['SECURE_PROXY_SSL_HEADER'],
            ('HTTP_X_FORWARDED_PROTO', 'https'),
        )

    def test_runtime_settings_keep_current_hosts_and_drop_dead_cors(self):
        self.assertNotIn('*', settings.ALLOWED_HOSTS)
        self.assertIn('197.248.201.233', settings.ALLOWED_HOSTS)
        self.assertEqual(settings.TIME_ZONE, 'Africa/Nairobi')
        self.assertFalse(hasattr(settings, 'CORS_ALLOW_ALL_ORIGINS'))
        self.assertFalse(settings.USE_HTTPS)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)


class ClientIpTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, remote, forwarded=None):
        request = self.factory.get('/')
        request.META['REMOTE_ADDR'] = remote
        if forwarded is not None:
            request.META['HTTP_X_FORWARDED_FOR'] = forwarded
        return request

    def test_untrusted_peer_cannot_spoof_forwarded_for(self):
        request = self._request('203.0.113.8', '198.51.100.20')
        self.assertEqual(get_client_ip(request), '203.0.113.8')

    def test_trusted_proxy_uses_the_forwarded_client(self):
        request = self._request('127.0.0.1', '198.51.100.20, 127.0.0.1')
        self.assertEqual(get_client_ip(request), '198.51.100.20')

    def test_unix_socket_without_remote_addr_trusts_the_proxy_header(self):
        request = self.factory.get('/')
        request.META['REMOTE_ADDR'] = ''
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.9'
        self.assertEqual(get_client_ip(request), '198.51.100.9')

    def test_garbage_forwarded_for_from_a_proxy_is_not_stored(self):
        request = self._request('127.0.0.1', 'not-an-ip')
        self.assertIsNone(get_client_ip(request))


class SafeRedirectTests(SimpleTestCase):
    def test_external_and_protocol_relative_referers_are_dropped(self):
        for referer in ('https://evil.example/phish', '//evil.example/phish'):
            request = RequestFactory().get('/payment/1/')
            request.META['HTTP_REFERER'] = referer
            response = safe_redirect(request)
            self.assertEqual(response['Location'], reverse('manage_loans'))

    def test_same_origin_referer_is_allowed(self):
        request = RequestFactory().get('/payment/1/')
        request.META['HTTP_REFERER'] = 'http://testserver/manage/'
        response = safe_redirect(request)
        self.assertEqual(response['Location'], 'http://testserver/manage/')

    def test_https_request_rejects_http_referer(self):
        request = RequestFactory().get('/payment/1/', secure=True)
        request.META['HTTP_REFERER'] = 'http://testserver/manage/'
        response = safe_redirect(request)
        self.assertEqual(response['Location'], reverse('manage_loans'))
