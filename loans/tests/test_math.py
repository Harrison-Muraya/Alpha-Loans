from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from loans.models import Loan, loan_due_expression, sum_money
from loans.tests.helpers import make_loan, make_payment, make_user


class LoanMathTests(TestCase):
    def test_new_loans_do_not_silently_include_a_penalty(self):
        self.assertEqual(
            Loan._meta.get_field('penalty_rate').get_default(),
            Decimal('0.00'),
        )
        borrower = make_user('default-penalty')
        loan = Loan.objects.create(
            borrower=borrower,
            amount=Decimal('1000.00'),
            due_date=timezone.localdate(),
        )
        loan.refresh_from_db()
        self.assertEqual(loan.penalty_rate, Decimal('0.00'))
        self.assertEqual(loan.interest_rate, Decimal('20.00'))
        self.assertEqual(loan.total_amount_due(), Decimal('1200.00'))

    def setUp(self):
        self.borrower = make_user('math-borrower')

    def test_interest_penalty_and_balance(self):
        loan = make_loan(self.borrower, amount='1000.00', interest='20.00', penalty='40.00')
        self.assertEqual(loan.calculate_interest(), Decimal('200'))
        self.assertEqual(loan.calculate_penalty(), Decimal('480'))
        self.assertEqual(loan.total_amount_due(), Decimal('1680'))
        self.assertEqual(loan.balance_remaining(), Decimal('1680'))
        self.assertEqual(loan.payment_percentage(), 0)

        make_payment(loan, '500.00')
        loan.refresh_from_db()
        self.assertEqual(loan.total_paid(), Decimal('500'))
        self.assertEqual(loan.balance_remaining(), Decimal('1180'))
        self.assertEqual(loan.payment_percentage(), 29)

    def test_zero_penalty_matches_interest_only(self):
        loan = make_loan(self.borrower, amount='1000.00', interest='20.00', penalty='0')
        self.assertEqual(loan.calculate_penalty(), Decimal('0'))
        self.assertEqual(loan.total_amount_due(), Decimal('1200'))

    def test_balance_never_goes_negative(self):
        loan = make_loan(self.borrower, amount='100.00', interest='0', penalty='0')
        make_payment(loan, '999.00')
        self.assertEqual(loan.balance_remaining(), Decimal('0'))

    def test_sql_due_matches_python_for_penalty_and_without(self):
        with_penalty = make_loan(self.borrower, penalty='40')
        without = make_loan(self.borrower, penalty='0', amount='250.50', interest='12.5')
        for loan in (with_penalty, without):
            total = (
                type(loan).objects.filter(pk=loan.pk)
                .aggregate(total=sum_money(loan_due_expression()))['total']
            )
            self.assertEqual(total, loan.total_amount_due())

    def test_paid_flags_stay_in_sync(self):
        loan = make_loan(self.borrower, status='active', is_paid=False)

        loan.is_paid = True
        loan.status = 'active'
        loan.save()
        loan.refresh_from_db()
        self.assertTrue(loan.is_paid)
        self.assertEqual(loan.status, 'paid')

        loan.status = 'overdue'
        loan.is_paid = False
        loan.save()
        loan.refresh_from_db()
        self.assertFalse(loan.is_paid)
        self.assertEqual(loan.status, 'overdue')

        loan.status = 'paid'
        loan.is_paid = False
        loan.save()
        loan.refresh_from_db()
        self.assertTrue(loan.is_paid)
        self.assertEqual(loan.status, 'paid')

    def test_issue_date_defaults_to_today_and_can_be_backdated(self):
        today = timezone.localdate()
        fresh = make_loan(self.borrower)
        self.assertEqual(fresh.issue_date, today)
        backdated = make_loan(self.borrower, issue_date=date(2024, 5, 1))
        backdated.refresh_from_db()
        self.assertEqual(backdated.issue_date, date(2024, 5, 1))

    def test_staff_can_set_issue_date_on_the_manage_form(self):
        staff = make_user('issue-staff', staff=True)
        self.client.force_login(staff)
        response = self.client.post(reverse('manage_loans'), {
            'borrower': self.borrower.id,
            'amount': '1000.00',
            'issue_date': '2024-05-01',
            'due_date': '2024-06-30',
        })
        self.assertRedirects(response, reverse('manage_loans'))
        loan = Loan.objects.get(borrower=self.borrower)
        self.assertEqual(loan.issue_date, date(2024, 5, 1))
        self.assertEqual(loan.due_date, date(2024, 6, 30))

    def test_blank_issue_date_on_the_form_uses_today(self):
        staff = make_user('issue-staff-blank', staff=True)
        self.client.force_login(staff)
        response = self.client.post(reverse('manage_loans'), {
            'borrower': self.borrower.id,
            'amount': '250.00',
            'issue_date': '',
            'due_date': '',
        })
        self.assertRedirects(response, reverse('manage_loans'))
        loan = Loan.objects.get(borrower=self.borrower)
        self.assertEqual(loan.issue_date, timezone.localdate())

    def test_queryset_update_keeps_paid_flags_together(self):
        loan = make_loan(self.borrower, status='active', is_paid=False)
        Loan.objects.filter(pk=loan.pk).update(status='paid')
        loan.refresh_from_db()
        self.assertEqual(loan.status, 'paid')
        self.assertTrue(loan.is_paid)

        Loan.objects.filter(pk=loan.pk).update(status='overdue', is_paid=False)
        loan.refresh_from_db()
        self.assertEqual(loan.status, 'overdue')
        self.assertFalse(loan.is_paid)

        Loan.objects.filter(pk=loan.pk).update(is_paid=True)
        loan.refresh_from_db()
        self.assertEqual(loan.status, 'paid')
        self.assertTrue(loan.is_paid)

    def test_annotated_paid_sum_is_not_multiplied_by_payment_rows(self):
        loan = make_loan(self.borrower, amount='1000', interest='20', penalty='40')
        make_payment(loan, '100')
        make_payment(loan, '50')
        annotated = type(loan).objects.with_totals().get(pk=loan.pk)
        self.assertEqual(annotated.total_amount_due(), Decimal('1680'))
        self.assertEqual(annotated.total_paid(), Decimal('150'))
        self.assertEqual(annotated.balance_remaining(), Decimal('1530'))
