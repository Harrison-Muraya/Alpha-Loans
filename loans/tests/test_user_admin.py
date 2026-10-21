import re

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from loans.models import ActivityLog, DeletedLoan, Loan, LoginAttempt
from loans.roles import (
    ROLE_ADMIN,
    ROLE_BORROWER,
    ROLE_OFFICER,
    ROLE_VIEWER,
    ensure_role_groups,
)
from loans.tests.helpers import make_loan, make_user

PASSWORD = 'Nairobi-Loans-2026!'


def make_superuser(username='root-admin'):
    user = make_user(username, staff=True)
    user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    return user


def make_role(username, role_name, *, staff=True):
    ensure_role_groups()
    user = make_user(username, staff=staff)
    user.groups.add(Group.objects.get(name=role_name))
    return user


class RoleGroupTests(TestCase):
    def test_groups_carry_django_permissions(self):
        ensure_role_groups()
        admin = Group.objects.get(name=ROLE_ADMIN)
        officer = Group.objects.get(name=ROLE_OFFICER)
        viewer = Group.objects.get(name=ROLE_VIEWER)
        self.assertTrue(admin.permissions.filter(codename='change_user').exists())
        self.assertTrue(admin.permissions.filter(codename='change_loan').exists())
        self.assertTrue(officer.permissions.filter(codename='change_loan').exists())
        self.assertFalse(officer.permissions.filter(codename='change_user').exists())
        self.assertTrue(viewer.permissions.filter(codename='view_loan').exists())
        self.assertFalse(viewer.permissions.filter(codename='change_loan').exists())
        self.assertFalse(viewer.permissions.filter(codename='delete_loan').exists())


class UserAdminAccessTests(TestCase):
    def setUp(self):
        self.superuser = make_superuser()
        self.admin = make_role('desk-admin', ROLE_ADMIN)
        self.officer = make_role('desk-officer', ROLE_OFFICER)
        self.viewer = make_role('desk-viewer', ROLE_VIEWER)
        self.borrower = make_user('desk-borrower')

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])

    def test_borrowers_and_loan_officers_cannot_open_user_admin(self):
        for user in (self.borrower, self.officer, self.viewer):
            self.client.force_login(user)
            response = self.client.get(reverse('user_list'))
            self.assertRedirects(response, reverse('dashboard'))
            self.client.logout()

    def test_admin_group_and_superuser_can_open_the_directory(self):
        for user in (self.admin, self.superuser):
            self.client.force_login(user)
            response = self.client.get(reverse('user_list'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'User management')
            self.client.logout()


class UserAdminMutationTests(TestCase):
    def setUp(self):
        self.superuser = make_superuser()
        self.admin = make_role('desk-admin', ROLE_ADMIN)
        self.client.force_login(self.superuser)

    def _create_payload(self, **overrides):
        payload = {
            'username': 'new-officer',
            'first_name': 'New',
            'last_name': 'Officer',
            'email': 'officer@example.com',
            'role': ROLE_OFFICER,
            'is_active': 'on',
            'password': PASSWORD,
            'confirm_password': PASSWORD,
        }
        payload.update(overrides)
        return payload

    def test_create_user_assigns_role_and_logs_the_action(self):
        response = self.client.post(reverse('user_create'), self._create_payload())
        user = User.objects.get(username='new-officer')
        self.assertRedirects(response, reverse('user_detail', args=[user.pk]))
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.password.startswith('Nairobi'))
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.groups.filter(name=ROLE_OFFICER).exists())
        self.assertTrue(ActivityLog.objects.filter(action='user_created', username='root-admin').exists())

    def test_weak_password_is_rejected(self):
        response = self.client.post(reverse('user_create'), self._create_payload(
            username='weak-user', password='password', confirm_password='password',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='weak-user').exists())
        self.assertContains(response, 'too common')

    def test_search_filter_finds_the_account(self):
        make_user('find-me')
        response = self.client.get(reverse('user_list'), {'q': 'find-me', 'role': 'borrower'})
        self.assertContains(response, 'find-me')
        response = self.client.get(reverse('user_list'), {'q': 'does-not-exist'})
        self.assertNotContains(response, 'find-me')

    def test_admin_cannot_demote_or_deactivate_themselves(self):
        response = self.client.post(reverse('user_edit', args=[self.superuser.pk]), {
            'username': self.superuser.username,
            'first_name': 'Root',
            'last_name': 'Admin',
            'email': '',
            'role': ROLE_BORROWER,
            'is_active': 'on',
            'is_superuser': 'on',
            'password': '',
            'confirm_password': '',
        })
        self.assertEqual(response.status_code, 200)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_superuser)
        self.assertTrue(self.superuser.is_active)
        self.assertContains(response, 'cannot remove your own admin access')

    def test_last_superuser_cannot_be_demoted_or_deleted(self):
        other = make_superuser('second-root')
        response = self.client.post(reverse('user_edit', args=[other.pk]), {
            'username': other.username,
            'first_name': '',
            'last_name': '',
            'email': '',
            'role': ROLE_OFFICER,
            'is_active': 'on',
            'is_superuser': '',
            'password': '',
            'confirm_password': '',
        })
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)
        self.assertRedirects(response, reverse('user_detail', args=[other.pk]))

        response = self.client.post(reverse('user_delete', args=[self.superuser.pk]), {
            'confirm': self.superuser.username,
        }, follow=True)
        self.assertContains(response, 'last superuser')
        self.assertTrue(User.objects.filter(pk=self.superuser.pk, is_superuser=True).exists())

    def test_admin_cannot_delete_themselves(self):
        response = self.client.post(reverse('user_delete', args=[self.superuser.pk]), {
            'confirm': self.superuser.username,
        })
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())
        self.assertEqual(response.status_code, 302)

    def test_non_superuser_admin_cannot_delete_a_superuser(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('user_delete', args=[self.superuser.pk]), {
            'confirm': self.superuser.username,
        })
        self.assertRedirects(response, reverse('user_detail', args=[self.superuser.pk]))
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

    def test_deactivate_blocks_sign_in_and_reactivate_restores_it(self):
        target = make_user('sleepy', password=PASSWORD)
        response = self.client.post(reverse('user_deactivate', args=[target.pk]))
        self.assertRedirects(response, reverse('user_detail', args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(ActivityLog.objects.filter(action='user_deactivated').exists())
        self.client.logout()
        signed_out = self.client.post(reverse('login'), {
            'username': 'sleepy', 'password': PASSWORD,
        })
        self.assertEqual(signed_out.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.client.force_login(self.superuser)
        self.client.post(reverse('user_reactivate', args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_set_password_and_generated_password_are_not_written_to_the_log(self):
        target = make_user('reset-me')
        response = self.client.post(reverse('user_set_password', args=[target.pk]), {
            'password': PASSWORD,
            'confirm_password': PASSWORD,
        })
        self.assertRedirects(response, reverse('user_detail', args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.check_password(PASSWORD))
        self.assertTrue(ActivityLog.objects.filter(action='password_changed').exists())
        self.assertFalse(ActivityLog.objects.filter(detail__contains=PASSWORD).exists())

        response = self.client.post(reverse('user_set_password', args=[target.pk]), {
            'generate': 'on',
        }, follow=True)
        self.assertContains(response, 'Temporary password')
        match = re.search(
            r'Temporary password for reset-me: (Kx[A-Za-z0-9_-]+9!)',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        target.refresh_from_db()
        self.assertTrue(target.check_password(match.group(1)))
        self.assertFalse(ActivityLog.objects.filter(detail__contains=match.group(1)).exists())

    def test_unlock_clears_login_attempts(self):
        target = make_user('locked-person')
        for _ in range(5):
            LoginAttempt.objects.create(username=target.username, ip_address='127.0.0.1')
        self.client.post(reverse('user_unlock', args=[target.pk]))
        self.assertEqual(LoginAttempt.objects.filter(username=target.username).count(), 0)
        self.assertTrue(ActivityLog.objects.filter(action='account_unlocked').exists())

    def test_delete_requires_confirmation_and_archives_live_loans(self):
        borrower = make_user('has-loans')
        loan = make_loan(borrower)
        response = self.client.post(reverse('user_delete', args=[borrower.pk]), {
            'confirm': 'wrong-name',
        })
        self.assertTrue(User.objects.filter(pk=borrower.pk).exists())
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())

        response = self.client.post(reverse('user_delete', args=[borrower.pk]), {
            'confirm': borrower.username,
        })
        self.assertTrue(User.objects.filter(pk=borrower.pk).exists())
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('user_delete', args=[borrower.pk]), {
            'confirm': borrower.username,
            'archive_loans': 'yes',
        })
        self.assertRedirects(response, reverse('user_list'))
        self.assertFalse(User.objects.filter(pk=borrower.pk).exists())
        self.assertFalse(Loan.objects.filter(pk=loan.pk).exists())
        self.assertTrue(DeletedLoan.objects.filter(original_loan_id=loan.id).exists())
        self.assertTrue(ActivityLog.objects.filter(action='user_deleted').exists())

    def test_delete_post_requires_csrf(self):
        target = make_user('csrf-target')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.superuser)
        response = client.post(reverse('user_delete', args=[target.pk]), {
            'confirm': target.username,
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=target.pk).exists())

    def test_user_csv_is_admin_only(self):
        response = self.client.get(reverse('export_users_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertContains(response, 'root-admin')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('export_users_csv')).status_code, 200)
        self.client.force_login(make_role('export-officer', ROLE_OFFICER))
        self.assertRedirects(self.client.get(reverse('export_users_csv')), reverse('dashboard'))


class ViewerAndProfileTests(TestCase):
    def setUp(self):
        self.viewer = make_role('view-only', ROLE_VIEWER)
        self.officer = make_role('writer', ROLE_OFFICER)
        self.borrower = make_user('book-borrower')
        self.loan = make_loan(self.borrower)

    def test_viewer_can_read_but_cannot_change_loans(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('manage_loans')).status_code, 200)
        self.assertEqual(self.client.get(reverse('activity_log')).status_code, 200)
        before = Loan.objects.count()
        response = self.client.post(reverse('record_payment', args=[self.loan.id]), {
            'amount': '10.00', 'method': 'cash',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.loan.payments.count(), 0)
        response = self.client.post(reverse('manage_loans'), {
            'borrower': self.borrower.pk,
            'amount': '500.00',
        })
        self.assertEqual(Loan.objects.count(), before)

    def test_loan_officer_can_still_record_a_payment(self):
        self.client.force_login(self.officer)
        response = self.client.post(reverse('record_payment', args=[self.loan.id]), {
            'amount': '10.00', 'method': 'cash',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.loan.payments.count(), 1)

    def test_staff_without_a_role_group_can_still_record_a_payment(self):
        legacy = make_user('legacy-staff', staff=True)
        self.client.force_login(legacy)
        self.client.post(reverse('record_payment', args=[self.loan.id]), {
            'amount': '15.00', 'method': 'cash',
        })
        self.assertEqual(self.loan.payments.count(), 1)
        self.assertRedirects(self.client.get(reverse('user_list')), reverse('dashboard'))

    def test_profile_updates_and_password_change(self):
        self.client.force_login(self.borrower)
        response = self.client.post(reverse('profile'), {
            'form': 'profile',
            'first_name': 'Amina',
            'last_name': 'Otieno',
            'email': 'amina@example.com',
        })
        self.assertRedirects(response, reverse('profile'))
        self.borrower.refresh_from_db()
        self.assertEqual(self.borrower.first_name, 'Amina')
        self.assertTrue(ActivityLog.objects.filter(action='profile_updated').exists())
        response = self.client.post(reverse('profile'), {
            'form': 'password',
            'old_password': 'test-pass-123',
            'new_password1': PASSWORD,
            'new_password2': PASSWORD,
        })
        self.assertRedirects(response, reverse('profile'))
        self.borrower.refresh_from_db()
        self.assertTrue(self.borrower.check_password(PASSWORD))

    def test_loan_csv_is_staff_only(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('export_loans_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertContains(response, self.borrower.username)
        self.client.force_login(self.borrower)
        response = self.client.get(reverse('export_loans_csv'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])
        response = self.client.get(reverse('export_payments_csv'))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('export_activity_csv'))
        self.assertEqual(response.status_code, 302)
