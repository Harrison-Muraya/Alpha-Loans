from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

class Loan(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('overdue', 'Overdue'),
        ('paid', 'Paid'),
    ]
    
    borrower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='loans')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=20.00)
    penalty_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    issue_date = models.DateField(auto_now_add=True)
    due_date = models.DateField()
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Loan #{self.id} - {self.borrower.get_full_name()} - KES {self.amount}"
    
    def calculate_interest(self):
        return self.amount * (self.interest_rate / Decimal('100'))
    
    def calculate_penalty(self):
        if self.penalty_rate > 0:
            amount_with_interest = self.amount + self.calculate_interest()
            return amount_with_interest * (self.penalty_rate / Decimal('100'))
        return Decimal('0')
    
    def total_amount_due(self):
        return self.amount + self.calculate_interest() + self.calculate_penalty()


class DeletedLoan(models.Model):
    """Snapshot of a loan taken at the moment it was deleted."""
    original_loan_id    = models.IntegerField()
    borrower            = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='deleted_loans'
    )
    borrower_username   = models.CharField(max_length=150)
    borrower_full_name  = models.CharField(max_length=300)
    amount              = models.DecimalField(max_digits=10, decimal_places=2)
    interest_rate       = models.DecimalField(max_digits=5,  decimal_places=2)
    penalty_rate        = models.DecimalField(max_digits=5,  decimal_places=2)
    status              = models.CharField(max_length=10)
    is_paid             = models.BooleanField()
    issue_date          = models.DateField()
    due_date            = models.DateField()
    total_amount_due    = models.DecimalField(max_digits=12, decimal_places=2)
    deleted_by          = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='loans_deleted'
    )
    deleted_by_username = models.CharField(max_length=150)
    deleted_at          = models.DateTimeField(auto_now_add=True)
    reason              = models.TextField(blank=True)

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"[Deleted] Loan #{self.original_loan_id} — {self.borrower_username}"


class ActivityLog(models.Model):
    """Records every user action and page visit across the system."""

    ACTION_CHOICES = [
        ('login',              'Logged in'),
        ('logout',             'Logged out'),
        ('loan_created',       'Loan created'),
        ('loan_updated',       'Loan updated'),
        ('loan_deleted',       'Loan deleted'),
        ('loans_bulk_deleted', 'Loans bulk deleted'),
        ('user_registered',    'User registered'),
        ('page_visit',         'Page visited'),
    ]

    user       = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='activity_logs'
    )
    username   = models.CharField(max_length=150)
    action     = models.CharField(max_length=30, choices=ACTION_CHOICES)
    detail     = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp  = models.DateTimeField(auto_now_add=True)
    loan_id    = models.IntegerField(null=True, blank=True)
    url        = models.CharField(max_length=500, blank=True)
    method     = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.username} · {self.get_action_display()} · {self.timestamp:%Y-%m-%d %H:%M}"