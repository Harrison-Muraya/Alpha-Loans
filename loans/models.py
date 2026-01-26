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
    penalty_rate = models.DecimalField(max_digits=5, decimal_places=2, default=40.00)
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