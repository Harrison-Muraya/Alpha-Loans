import threading
from datetime import timedelta
from decimal import Decimal

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from loans.models import Loan, Payment
from loans.tests.helpers import make_loan, make_payment, make_user
from loans.views import parse_payment_amount


class ParseAmountTests(TestCase):
    def test_accepts_thousands_separator(self):
        self.assertEqual(parse_payment_amount('1,000'), Decimal('1000.00'))
        self.assertEqual(parse_payment_amount(' 1,000.50 '), Decimal('1000.50'))

    def test_rejects_junk_and_extra_precision(self):
        for raw in (None, '', 'abc', '1.2.3', '0', '-5', '10.555', '100000000.00'):
            with self.assertRaises(Exception):
                parse_payment_amount(raw)


class RecordPaymentTests(TestCase):
    def setUp(self):
        self.staff = make_user('pay-staff', staff=True)
        self.borrower = make_user('pay-borrower')
        self.loan = make_loan(self.borrower, amount='1000', interest='20', penalty='40')
        self.client.force_login(self.staff)
        self.url = reverse('record_payment', args=[self.loan.id])

    def test_non_numeric_amount_does_not_crash(self):
        response = self.client.post(self.url, {'amount': 'abc', 'method': 'cash'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)
        self.loan.refresh_from_db()
        self.assertFalse(self.loan.is_paid)

    def test_comma_amount_within_balance_is_recorded(self):
        response = self.client.post(self.url, {'amount': '1,000', 'method': 'mpesa'})
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get()
        self.assertEqual(payment.amount, Decimal('1000.00'))
        self.assertEqual(payment.method, 'mpesa')
        self.loan.refresh_from_db()
        self.assertFalse(self.loan.is_paid)

    def test_overpayment_is_rejected(self):
        make_payment(self.loan, '1600')
        response = self.client.post(self.url, {'amount': '2,000', 'method': 'cash'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'remaining balance')
        self.assertEqual(Payment.objects.count(), 1)
        self.loan.refresh_from_db()
        self.assertFalse(self.loan.is_paid)

    def test_invalid_method_is_rejected(self):
        response = self.client.post(self.url, {'amount': '10', 'method': 'bitcoin'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)

    def test_exact_balance_marks_loan_paid(self):
        loan = make_loan(self.borrower, amount='500', interest='0', penalty='0')
        response = self.client.post(
            reverse('record_payment', args=[loan.id]),
            {'amount': '500.00', 'method': 'bank'},
        )
        self.assertEqual(response.status_code, 302)
        loan.refresh_from_db()
        self.assertTrue(loan.is_paid)
        self.assertEqual(loan.status, 'paid')
        self.assertEqual(loan.balance_remaining(), Decimal('0'))

    def test_second_payment_cannot_exceed_what_the_first_left(self):
        self.client.post(self.url, {'amount': '1000', 'method': 'cash'})
        self.client.post(self.url, {'amount': '1000', 'method': 'cash'})
        self.assertEqual(Payment.objects.filter(loan=self.loan).count(), 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.total_paid(), Decimal('1000'))
        self.assertFalse(self.loan.is_paid)

    def test_external_referer_is_not_followed(self):
        response = self.client.post(
            self.url,
            {'amount': 'abc', 'method': 'cash'},
            HTTP_REFERER='https://evil.example/phish',
        )
        self.assertEqual(response['Location'], reverse('manage_loans'))

    def test_same_site_referer_is_kept(self):
        response = self.client.post(
            self.url,
            {'amount': '10', 'method': 'cash'},
            HTTP_REFERER='http://testserver/loans/',
        )
        self.assertEqual(response['Location'], 'http://testserver/loans/')

    def test_get_does_not_record_a_payment(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Payment.objects.count(), 0)


class BorrowerPaymentAllocationTests(TestCase):
    def setUp(self):
        self.staff = make_user('alloc-staff', staff=True)
        self.borrower = make_user('alloc-borrower')
        self.client.force_login(self.staff)

    def test_payment_splits_across_open_loans_overdue_first(self):
        older = make_loan(
            self.borrower,
            amount='1000',
            interest='0',
            penalty='0',
            due=timezone.localdate() - timedelta(days=10),
            status='overdue',
        )
        newer = make_loan(
            self.borrower,
            amount='500',
            interest='0',
            penalty='0',
            due=timezone.localdate() + timedelta(days=20),
        )
        response = self.client.post(
            reverse('record_borrower_payment_for', args=[self.borrower.id]),
            {'borrower': self.borrower.id, 'amount': '1200', 'method': 'mpesa'},
        )
        self.assertEqual(response.status_code, 302)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertTrue(older.is_paid)
        self.assertEqual(older.total_paid(), Decimal('1000'))
        self.assertFalse(newer.is_paid)
        self.assertEqual(newer.total_paid(), Decimal('200'))
        self.assertEqual(Payment.objects.filter(loan__borrower=self.borrower).count(), 2)

    def test_overpayment_against_borrower_is_rejected(self):
        make_loan(self.borrower, amount='100', interest='0', penalty='0')
        response = self.client.post(
            reverse('record_borrower_payment_for', args=[self.borrower.id]),
            {'borrower': self.borrower.id, 'amount': '500', 'method': 'cash'},
            follow=True,
        )
        self.assertContains(response, 'remaining balance')
        self.assertEqual(Payment.objects.count(), 0)

    def test_record_payment_page_lists_borrowers(self):
        make_loan(self.borrower, amount='100', interest='0', penalty='0')
        response = self.client.get(reverse('record_borrower_payment'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.borrower.username)


class ConcurrentPaymentTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_full_payments_cannot_both_succeed(self):
        staff = make_user('race-staff', staff=True)
        borrower = make_user('race-borrower')
        loan = make_loan(borrower, amount='1000', interest='0', penalty='0')
        url = reverse('record_payment', args=[loan.id])
        barrier = threading.Barrier(2)
        errors = []

        def pay():
            try:
                client = Client()
                client.force_login(staff)
                barrier.wait(timeout=5)
                client.post(url, {'amount': '1000', 'method': 'cash'})
            except Exception as exc:  # pragma: no cover - failure path for the assertion below
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=pay, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
            self.assertFalse(thread.is_alive())

        self.assertEqual(errors, [])
        self.assertEqual(Payment.objects.filter(loan=loan).count(), 1)
        loan.refresh_from_db()
        self.assertEqual(loan.total_paid(), Decimal('1000'))
        self.assertTrue(loan.is_paid)
        self.assertEqual(loan.status, 'paid')
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())
