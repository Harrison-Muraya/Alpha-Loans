from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from loans.tests.helpers import make_loan, make_payment, make_user


@override_settings(ACTIVITY_LOG_DEDUPE_SECONDS=0)
class QueryCountTests(TestCase):
    def setUp(self):
        self.staff = make_user('query-staff', staff=True)
        self.borrower = make_user('query-borrower')
        self.client.force_login(self.staff)

    def _count(self, url):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(captured), captured

    def test_dashboard_queries_do_not_grow_with_loans(self):
        make_loan(self.borrower)
        small, _ = self._count(reverse('dashboard'))
        for index in range(6):
            make_loan(self.borrower, amount=str(100 + index))
        large, captured = self._count(reverse('dashboard'))
        self.assertEqual(
            small,
            large,
            '\n'.join(query['sql'] for query in captured.captured_queries),
        )

    def test_loan_list_queries_do_not_grow_with_loans(self):
        make_loan(self.borrower)
        small, _ = self._count(reverse('loan_list'))
        for index in range(8):
            make_loan(self.borrower, amount=str(200 + index))
        large, captured = self._count(reverse('loan_list'))
        self.assertEqual(
            small,
            large,
            '\n'.join(query['sql'] for query in captured.captured_queries),
        )

    def test_borrower_summary_queries_do_not_grow_with_users(self):
        make_loan(self.borrower, penalty='40')
        small, _ = self._count(reverse('borrower_summary'))
        for index in range(5):
            person = make_user(f'extra-{index}')
            make_loan(person, penalty='40')
        large, captured = self._count(reverse('borrower_summary'))
        self.assertEqual(
            small,
            large,
            '\n'.join(query['sql'] for query in captured.captured_queries),
        )

    def test_borrower_detail_queries_do_not_grow_with_loans_or_payments(self):
        loan = make_loan(self.borrower)
        make_payment(loan, '10', user=self.staff)
        url = reverse('borrower_detail', args=[self.borrower.id])
        small, _ = self._count(url)
        for index in range(5):
            extra = make_loan(self.borrower, amount=str(80 + index))
            make_payment(extra, '5', user=self.staff)
            make_payment(extra, '6', user=self.staff)
        large, captured = self._count(url)
        self.assertEqual(
            small,
            large,
            '\n'.join(query['sql'] for query in captured.captured_queries),
        )

    def test_borrower_summary_expected_total_includes_penalty(self):
        make_loan(self.borrower, amount='1000', interest='20', penalty='40')
        response = self.client.get(reverse('borrower_summary'))
        self.assertContains(response, '1680.00')
        self.assertEqual(response.context['users'][0].total_expected, Decimal('1680.00'))

    def test_dashboard_expected_total_includes_penalty(self):
        make_loan(self.borrower, amount='1000', interest='20', penalty='40')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '1680')
        self.assertEqual(response.context['total_expected'], Decimal('1680.00'))

    def test_borrower_detail_totals_are_not_multiplied_by_payments(self):
        loan = make_loan(self.borrower, amount='1000', interest='20', penalty='40')
        make_payment(loan, '100', user=self.staff)
        make_payment(loan, '50', user=self.staff)
        response = self.client.get(reverse('borrower_detail', args=[self.borrower.id]))
        self.assertEqual(response.context['total_loaned'], Decimal('1000.00'))
        self.assertEqual(response.context['total_expected'], Decimal('1680.00'))
        self.assertEqual(response.context['total_paid'], Decimal('150.00'))
        self.assertEqual(response.context['total_balance'], Decimal('1530.00'))
