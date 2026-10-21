from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

_UNSET = object()

MONEY = DecimalField(max_digits=14, decimal_places=2)
PRECISE = DecimalField(max_digits=24, decimal_places=8)
ZERO = Value(Decimal('0.00'), output_field=MONEY)


def _precise(number):
    return Value(Decimal(number), output_field=PRECISE)


def loan_due_expression(prefix=''):
    """SQL for Loan.total_amount_due(), including the penalty on principal + interest.

    total = amount * (1 + interest_rate/100) * (1 + penalty_rate/100)
    Rates are multiplied by 0.01 so SQLite does not integer-divide the percentage.
    A zero penalty rate contributes nothing, matching calculate_penalty().
    Build this once per query: Django mutates expression nodes.
    """
    amount = F(f'{prefix}amount')
    interest_factor = _precise('1') + F(f'{prefix}interest_rate') * _precise('0.01')
    penalty_factor = _precise('1') + F(f'{prefix}penalty_rate') * _precise('0.01')
    return ExpressionWrapper(
        amount * interest_factor * penalty_factor,
        output_field=PRECISE,
    )


def sum_money(expression):
    return Coalesce(Sum(expression), ZERO, output_field=MONEY)


class LoanQuerySet(models.QuerySet):
    def with_totals(self):
        """Annotate amount_due and paid_sum so templates do not query per loan."""
        return self.annotate(
            paid_sum=sum_money('payments__amount'),
            amount_due=loan_due_expression(),
        )

    def delete(self):
        """Archive each loan (and its payments) instead of dropping financial history."""
        with transaction.atomic():
            loans = list(self)
            for loan in loans:
                loan.archive(reason='Deleted')
        return len(loans), {'loans.Loan': len(loans)}

    def update(self, **kwargs):
        """Keep status and is_paid describing one fact, matching Loan.save()."""
        if 'status' not in kwargs and 'is_paid' not in kwargs:
            return super().update(**kwargs)

        kwargs['updated_at'] = kwargs.get('updated_at', timezone.now())
        status = kwargs.get('status', _UNSET)
        is_paid = kwargs.get('is_paid', _UNSET)

        if status is not _UNSET and is_paid is not _UNSET:
            if status == 'paid' or is_paid:
                kwargs['status'] = 'paid'
                kwargs['is_paid'] = True
            return super().update(**kwargs)

        if status == 'paid' or is_paid is True:
            kwargs['status'] = 'paid'
            kwargs['is_paid'] = True
            return super().update(**kwargs)

        with transaction.atomic(using=self.db):
            if status is not _UNSET:
                # A paid flag still on the row wins, the same way save() does.
                changed = super(LoanQuerySet, self.filter(is_paid=True)).update(
                    **{**kwargs, 'status': 'paid', 'is_paid': True}
                )
                changed += super(LoanQuerySet, self.filter(is_paid=False)).update(
                    **{**kwargs, 'status': status, 'is_paid': False}
                )
                return changed
            changed = super(LoanQuerySet, self.filter(status='paid')).update(
                **{**kwargs, 'status': 'paid', 'is_paid': True}
            )
            changed += super(LoanQuerySet, self.exclude(status='paid')).update(
                **{**kwargs, 'is_paid': False}
            )
            return changed

    def bulk_update(self, objs, fields, batch_size=None):
        field_list = list(fields)
        if 'status' in field_list or 'is_paid' in field_list:
            for obj in objs:
                if obj.status == 'paid':
                    obj.is_paid = True
                elif obj.is_paid:
                    obj.status = 'paid'
            if 'status' not in field_list:
                field_list.append('status')
            if 'is_paid' not in field_list:
                field_list.append('is_paid')
            if 'updated_at' not in field_list:
                field_list.append('updated_at')
                now = timezone.now()
                for obj in objs:
                    obj.updated_at = now
        return super().bulk_update(objs, field_list, batch_size=batch_size)


class Loan(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('overdue', 'Overdue'),
        ('paid', 'Paid'),
    ]

    borrower = models.ForeignKey(User, on_delete=models.PROTECT, related_name='loans')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('20.00')
    )
    # Flat rate staff set by hand. Left at 0 so new loans do not silently add the
    # 40% figure shown in the UI. mark_overdue_loans does not fill this in.
    penalty_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00')
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LoanQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Loan #{self.id} - {self.borrower.get_full_name()} - KES {self.amount}"

    def save(self, *args, **kwargs):
        # status='paid' and is_paid describe one fact. Either flag implies the other
        # so the columns cannot drift apart through the ORM.
        if self.status == 'paid':
            self.is_paid = True
        elif self.is_paid:
            self.status = 'paid'
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'status', 'is_paid'}
        super().save(*args, **kwargs)

    def calculate_interest(self):
        return self.amount * (self.interest_rate / Decimal('100'))

    def calculate_penalty(self):
        if self.penalty_rate > 0:
            return (self.amount + self.calculate_interest()) * (self.penalty_rate / Decimal('100'))
        return Decimal('0')

    def total_amount_due(self):
        annotated = getattr(self, 'amount_due', None)
        if annotated is not None:
            return annotated
        return self.amount + self.calculate_interest() + self.calculate_penalty()

    def total_paid(self):
        annotated = getattr(self, 'paid_sum', None)
        if annotated is not None:
            return annotated
        return self.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

    def balance_remaining(self):
        return max(self.total_amount_due() - self.total_paid(), Decimal('0'))

    def payment_percentage(self):
        total = self.total_amount_due()
        if total == 0:
            return 0
        return int((self.total_paid() / total) * 100)

    def check_and_mark_paid(self):
        """Auto-mark loan as paid when balance hits zero."""
        if self.balance_remaining() <= 0 and not self.is_paid:
            self.is_paid = True
            self.status = 'paid'
            self.save(update_fields=['is_paid', 'status'])
            return True
        return False

    def archive(self, deleted_by=None, reason=''):
        """Copy the loan and its payments into the archive, then delete the live rows."""
        with transaction.atomic():
            borrower = self.borrower
            username = borrower.username if borrower is not None else ''
            full_name = (borrower.get_full_name() or username) if borrower is not None else ''
            deleted_by_username = ''
            if deleted_by is not None and getattr(deleted_by, 'is_authenticated', False):
                deleted_by_username = deleted_by.username
            deleted = DeletedLoan.objects.create(
                original_loan_id=self.id,
                borrower=borrower,
                borrower_username=username,
                borrower_full_name=full_name,
                amount=self.amount,
                interest_rate=self.interest_rate,
                penalty_rate=self.penalty_rate,
                status=self.status,
                is_paid=self.is_paid,
                issue_date=self.issue_date,
                due_date=self.due_date,
                total_amount_due=self.total_amount_due(),
                deleted_by=deleted_by if deleted_by_username else None,
                deleted_by_username=deleted_by_username,
                reason=reason or '',
            )
            payments = self.payments.select_related('recorded_by')
            ArchivedPayment.objects.bulk_create([
                ArchivedPayment(
                    deleted_loan=deleted,
                    original_payment_id=payment.id,
                    original_loan_id=self.id,
                    amount=payment.amount,
                    method=payment.method,
                    reference=payment.reference,
                    note=payment.note,
                    recorded_by_username=(
                        payment.recorded_by.username if payment.recorded_by_id else ''
                    ),
                    paid_at=payment.paid_at,
                )
                for payment in payments
            ])
            super().delete()
            return deleted

    def delete(self, using=None, keep_parents=False):
        """Archive before the row goes, so a direct delete keeps the payment trail."""
        self.archive()


class Payment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('bank', 'Bank Transfer'),
        ('other', 'Other'),
    ]

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default='cash')
    reference = models.CharField(
        max_length=100, blank=True, help_text="M-Pesa code, bank ref, etc."
    )
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='payments_recorded'
    )
    paid_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f"Payment of KES {self.amount} on Loan #{self.loan_id}"


class DeletedLoan(models.Model):
    original_loan_id = models.IntegerField()
    borrower = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='deleted_loans'
    )
    borrower_username = models.CharField(max_length=150)
    borrower_full_name = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    penalty_rate = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=10)
    is_paid = models.BooleanField()
    issue_date = models.DateField()
    due_date = models.DateField()
    total_amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    deleted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='loans_deleted'
    )
    deleted_by_username = models.CharField(max_length=150)
    deleted_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"[Deleted] Loan #{self.original_loan_id} — {self.borrower_username}"


class ArchivedPayment(models.Model):
    """Payment snapshot kept after the live loan (and its Payment rows) is removed."""

    deleted_loan = models.ForeignKey(
        DeletedLoan, on_delete=models.CASCADE, related_name='payments'
    )
    original_payment_id = models.IntegerField()
    original_loan_id = models.IntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10)
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    recorded_by_username = models.CharField(max_length=150, blank=True)
    paid_at = models.DateTimeField()

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f"Archived payment of KES {self.amount} on Loan #{self.original_loan_id}"


class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('login', 'Logged in'),
        ('logout', 'Logged out'),
        ('loan_created', 'Loan created'),
        ('loan_updated', 'Loan updated'),
        ('loan_deleted', 'Loan deleted'),
        ('loans_bulk_deleted', 'Loans bulk deleted'),
        ('user_registered', 'User registered'),
        ('payment_recorded', 'Payment recorded'),
        ('page_visit', 'Page visited'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activity_logs'
    )
    username = models.CharField(max_length=150)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    detail = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    loan_id = models.IntegerField(null=True, blank=True)
    url = models.CharField(max_length=500, blank=True)
    method = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['action', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.username} · {self.get_action_display()} · {self.timestamp:%Y-%m-%d %H:%M}"


class LoginAttempt(models.Model):
    """Failed authentication attempts used for login lockout."""

    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['username', 'attempted_at']),
            models.Index(fields=['ip_address', 'attempted_at']),
        ]

    def __str__(self):
        return f"{self.username} @ {self.ip_address} {self.attempted_at:%Y-%m-%d %H:%M}"
