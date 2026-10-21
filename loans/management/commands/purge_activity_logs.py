"""
Delete old page-visit rows so the activity log does not grow without bound.

Login, payment, and loan events are kept. Cron (daily at 02:30):

    30 2 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py purge_activity_logs
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from loans.models import ActivityLog


class Command(BaseCommand):
    help = 'Delete page-visit activity rows older than the retention window'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Delete page visits older than this many days (default: ACTIVITY_LOG_RETENTION_DAYS)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report how many rows would be deleted without deleting them',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days is None:
            days = getattr(settings, 'ACTIVITY_LOG_RETENTION_DAYS', 30)
        if days < 0:
            raise CommandError('--days must be zero or greater')

        cutoff = timezone.now() - timedelta(days=days)
        stale = ActivityLog.objects.filter(action='page_visit', timestamp__lt=cutoff)
        count = stale.count()
        if options['dry_run']:
            self.stdout.write(f'Would delete {count} page-visit row(s) older than {cutoff}.')
            return

        deleted, _ = stale.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {deleted} page-visit row(s) older than {days} day(s).'
        ))
