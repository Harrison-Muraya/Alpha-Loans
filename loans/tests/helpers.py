from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone

from loans.models import Loan, Payment


def make_user(username, staff=False, password='test-pass-123'):
    return User.objects.create_user(username=username, password=password, is_staff=staff)


def make_loan(
    borrower,
    amount='1000.00',
    interest='20.00',
    penalty='40.00',
    due=None,
    status='active',
    is_paid=False,
    issue_date=None,
):
    if due is None:
        due = timezone.localdate() + timedelta(days=30)
    fields = dict(
        borrower=borrower,
        amount=Decimal(amount),
        interest_rate=Decimal(interest),
        penalty_rate=Decimal(penalty),
        due_date=due,
        status=status,
        is_paid=is_paid,
    )
    if issue_date is not None:
        fields['issue_date'] = issue_date
    return Loan.objects.create(**fields)


def make_payment(loan, amount, method='cash', user=None):
    return Payment.objects.create(
        loan=loan,
        amount=Decimal(amount),
        method=method,
        recorded_by=user,
        paid_at=timezone.now(),
    )
