from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from loans.models import LoginAttempt
from loans.tests.helpers import make_user


class RegistrationTests(TestCase):
    def test_weak_password_is_rejected_by_django_validators(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'first_name': 'New',
            'last_name': 'User',
            'email': 'new@example.com',
            'password': 'password',
            'confirm_password': 'password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())
        self.assertContains(response, 'too common')

    def test_mismatched_passwords_are_rejected(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'first_name': 'New',
            'last_name': 'User',
            'email': 'new@example.com',
            'password': 'Nairobi-Loans-2026!',
            'confirm_password': 'different-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_strong_password_registers_and_hashes(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'first_name': 'New',
            'last_name': 'User',
            'email': 'new@example.com',
            'password': 'Nairobi-Loans-2026!',
            'confirm_password': 'Nairobi-Loans-2026!',
        })
        self.assertRedirects(response, reverse('dashboard'))
        user = User.objects.get(username='newuser')
        self.assertTrue(user.check_password('Nairobi-Loans-2026!'))
        self.assertFalse(user.password.startswith('Nairobi'))


class LoginLockoutTests(TestCase):
    def setUp(self):
        self.user = make_user('lock-user', password='test-pass-123')

    @override_settings(LOGIN_FAILURE_LIMIT=3, LOGIN_IP_FAILURE_LIMIT=100)
    def test_username_locks_after_repeated_failures(self):
        for _ in range(3):
            response = self.client.post(reverse('login'), {
                'username': 'lock-user',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual(LoginAttempt.objects.count(), 3)

        response = self.client.post(reverse('login'), {
            'username': 'lock-user',
            'password': 'test-pass-123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Too many failed login attempts')
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(LOGIN_FAILURE_LIMIT=3, LOGIN_LOCKOUT_SECONDS=900)
    def test_lock_expires(self):
        for _ in range(3):
            self.client.post(reverse('login'), {
                'username': 'lock-user',
                'password': 'wrong-password',
            })
        LoginAttempt.objects.update(attempted_at=timezone.now() - timedelta(hours=1))
        response = self.client.post(reverse('login'), {
            'username': 'lock-user',
            'password': 'test-pass-123',
        })
        self.assertRedirects(response, reverse('dashboard'))

    def test_successful_login_clears_failures(self):
        self.client.post(reverse('login'), {'username': 'lock-user', 'password': 'nope'})
        self.assertEqual(LoginAttempt.objects.count(), 1)
        self.client.post(reverse('login'), {
            'username': 'lock-user',
            'password': 'test-pass-123',
        })
        self.assertEqual(LoginAttempt.objects.count(), 0)


class LogoutTests(TestCase):
    def test_get_does_not_log_out(self):
        user = make_user('logout-user')
        self.client.force_login(user)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.status_code, 200)

    def test_post_logs_out(self):
        user = make_user('logout-user')
        self.client.force_login(user)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.status_code, 302)
