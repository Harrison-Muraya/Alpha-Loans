"""
Management command: mark_overdue_loans

Flips active, unpaid loans past their due date to 'overdue'.
"Today" is the current date in TIME_ZONE (Africa/Nairobi).

Cron (daily at 8:00 server time):
    0 8 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py mark_overdue_loans
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from loans.models import Loan


class Command(BaseCommand):
    help = 'Mark loans past their due date as overdue'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']

        overdue_loans = Loan.objects.filter(
            status='active',
            is_paid=False,
            due_date__lt=today,
        ).select_related('borrower')

        count = overdue_loans.count()
        self.stdout.write(f'Found {count} loan(s) to mark overdue (today: {today})')

        if dry_run:
            for loan in overdue_loans:
                self.stdout.write(
                    f'  [DRY RUN] Loan #{loan.id} — {loan.borrower.username} '
                    f'(due {loan.due_date}, {(today - loan.due_date).days} days overdue)'
                )
            return

        updated = 0
        for loan in overdue_loans:
            loan.status = 'overdue'
            loan.save(update_fields=['status'])
            updated += 1
            self.stdout.write(self.style.WARNING(
                f'  Overdue: Loan #{loan.id} — {loan.borrower.username} '
                f'(due {loan.due_date}, balance KES {loan.balance_remaining()})'
            ))

        self.stdout.write(self.style.SUCCESS(f'\nDone. {updated} loan(s) marked overdue.'))
