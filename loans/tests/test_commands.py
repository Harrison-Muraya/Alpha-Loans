from datetime import timedelta
from io import StringIO

from django.core.management import call_command, get_commands
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from loans.models import ActivityLog, Loan
from loans.tests.helpers import make_loan, make_user


class CommandDiscoveryTests(TestCase):
    def test_overdue_command_is_registered_on_the_loans_app(self):
        commands = get_commands()
        self.assertEqual(commands['mark_overdue_loans'], 'loans')
        self.assertEqual(commands['purge_activity_logs'], 'loans')


class MarkOverdueTests(TestCase):
    def setUp(self):
        self.borrower = make_user('due-borrower')
        self.today = timezone.localdate()

    def test_past_due_active_loan_is_marked_overdue(self):
        loan = make_loan(self.borrower, due=self.today - timedelta(days=1))
        call_command('mark_overdue_loans', stdout=StringIO())
        loan.refresh_from_db()
        self.assertEqual(loan.status, 'overdue')
        self.assertFalse(loan.is_paid)

    def test_due_today_and_paid_loans_are_left_alone(self):
        due_today = make_loan(self.borrower, due=self.today)
        future = make_loan(self.borrower, due=self.today + timedelta(days=3))
        paid = make_loan(
            self.borrower,
            due=self.today - timedelta(days=10),
            status='paid',
            is_paid=True,
        )
        call_command('mark_overdue_loans', stdout=StringIO())
        due_today.refresh_from_db()
        future.refresh_from_db()
        paid.refresh_from_db()
        self.assertEqual(due_today.status, 'active')
        self.assertEqual(future.status, 'active')
        self.assertEqual(paid.status, 'paid')
        self.assertTrue(paid.is_paid)

    def test_dry_run_does_not_write(self):
        loan = make_loan(self.borrower, due=self.today - timedelta(days=2))
        call_command('mark_overdue_loans', '--dry-run', stdout=StringIO())
        loan.refresh_from_db()
        self.assertEqual(loan.status, 'active')
        self.assertEqual(Loan.objects.filter(status='overdue').count(), 0)


class PurgeActivityTests(TestCase):
    def setUp(self):
        self.user = make_user('log-user')

    def _backdate(self, entry, days):
        ActivityLog.objects.filter(pk=entry.pk).update(
            timestamp=timezone.now() - timedelta(days=days)
        )

    def test_old_page_visits_are_removed_and_other_actions_stay(self):
        old_visit = ActivityLog.objects.create(
            user=self.user, username=self.user.username, action='page_visit', url='/loans/'
        )
        recent_visit = ActivityLog.objects.create(
            user=self.user, username=self.user.username, action='page_visit', url='/dashboard/'
        )
        old_login = ActivityLog.objects.create(
            user=self.user, username=self.user.username, action='login'
        )
        self._backdate(old_visit, 40)
        self._backdate(old_login, 40)

        call_command('purge_activity_logs', '--days', '30', stdout=StringIO())

        self.assertFalse(ActivityLog.objects.filter(pk=old_visit.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=recent_visit.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=old_login.pk).exists())

    def test_dry_run_keeps_rows(self):
        visit = ActivityLog.objects.create(
            user=self.user, username=self.user.username, action='page_visit', url='/loans/'
        )
        self._backdate(visit, 90)
        call_command('purge_activity_logs', '--days', '30', '--dry-run', stdout=StringIO())
        self.assertTrue(ActivityLog.objects.filter(pk=visit.pk).exists())


class PageVisitDedupeTests(TestCase):
    def setUp(self):
        self.user = make_user('visit-user')
        self.client.force_login(self.user)

    @override_settings(ACTIVITY_LOG_DEDUPE_SECONDS=900)
    def test_repeated_get_is_logged_once(self):
        self.client.get(reverse('dashboard'))
        self.client.get(reverse('dashboard'))
        self.assertEqual(
            ActivityLog.objects.filter(action='page_visit', url='/dashboard/').count(),
            1,
        )

    @override_settings(ACTIVITY_LOG_DEDUPE_SECONDS=0)
    def test_dedupe_can_be_disabled(self):
        self.client.get(reverse('loan_list'))
        self.client.get(reverse('loan_list'))
        self.assertEqual(
            ActivityLog.objects.filter(action='page_visit', url='/loans/').count(),
            2,
        )
