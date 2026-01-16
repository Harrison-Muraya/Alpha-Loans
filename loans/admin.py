from django.contrib import admin
from .models import Loan

@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ['id', 'borrower', 'amount', 'interest_rate', 'penalty_rate', 'status', 'due_date', 'is_paid']
    list_filter = ['status', 'is_paid', 'issue_date']
    search_fields = ['borrower__username', 'borrower__first_name', 'borrower__last_name']
    date_hierarchy = 'issue_date'