"""CSV exports for the loan book, payments, archive, and activity log."""

import csv
from datetime import datetime

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.utils import timezone

from .models import ActivityLog, DeletedLoan, Loan, Payment


def is_staff(user):
    return user.is_staff


def parse_date(value):
    text = (value or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def csv_cell(value):
    """Keep spreadsheet formulas from running when a cell is opened."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(('=', '+', '-', '@')):
        return "'" + text
    return text


def csv_response(filename, header, rows):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_cell(value) for value in row])
    return response


def dated_name(prefix):
    return f'{prefix}_{timezone.localdate().isoformat()}.csv'


def filter_activity(params):
    logs = ActivityLog.objects.select_related('user').all()
    action = (params.get('action') or '').strip()
    username = (params.get('user') or '').strip()
    date_from = parse_date(params.get('date_from'))
    date_to = parse_date(params.get('date_to'))
    if action:
        logs = logs.filter(action=action)
    if username:
        logs = logs.filter(username__icontains=username)
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)
    return logs


def filtered_deleted(params):
    from django.db.models import Count

    deleted = (
        DeletedLoan.objects.select_related('borrower', 'deleted_by')
        .annotate(payment_count=Count('payments'))
    )
    q = (params.get('q') or '').strip()
    if q:
        deleted = deleted.filter(borrower_username__icontains=q)
    return deleted


@login_required
@user_passes_test(is_staff)
def export_loans_csv(request):
    loans = (
        Loan.objects.select_related('borrower')
        .with_totals()
        .order_by('-created_at', '-pk')
    )
    rows = []
    for loan in loans:
        balance = max(loan.amount_due - loan.paid_sum, 0)
        rows.append([
            loan.id,
            loan.borrower.username,
            loan.borrower.get_full_name(),
            loan.amount,
            loan.interest_rate,
            loan.penalty_rate,
            loan.status,
            loan.issue_date,
            loan.due_date,
            loan.amount_due,
            loan.paid_sum,
            balance,
        ])
    return csv_response(
        dated_name('loans'),
        [
            'id', 'username', 'borrower', 'principal', 'interest_rate', 'penalty_rate',
            'status', 'issue_date', 'due_date', 'total_due', 'total_paid', 'balance',
        ],
        rows,
    )


@login_required
@user_passes_test(is_staff)
def export_payments_csv(request):
    payments = (
        Payment.objects.select_related('loan__borrower', 'recorded_by')
        .order_by('-paid_at', '-pk')
    )
    rows = [
        [
            payment.id,
            payment.loan_id,
            payment.loan.borrower.username,
            payment.amount,
            payment.method,
            payment.reference,
            payment.note,
            payment.recorded_by.username if payment.recorded_by_id else '',
            payment.paid_at.isoformat(),
        ]
        for payment in payments
    ]
    return csv_response(
        dated_name('payments'),
        [
            'id', 'loan_id', 'borrower', 'amount', 'method', 'reference',
            'note', 'recorded_by', 'paid_at',
        ],
        rows,
    )


@login_required
@user_passes_test(is_staff)
def export_activity_csv(request):
    logs = filter_activity(request.GET)
    rows = [
        [
            log.timestamp.isoformat(),
            log.username,
            log.action,
            log.detail,
            log.ip_address or '',
            log.url,
            log.method,
            log.loan_id or '',
        ]
        for log in logs
    ]
    return csv_response(
        dated_name('activity'),
        ['timestamp', 'username', 'action', 'detail', 'ip', 'url', 'method', 'loan_id'],
        rows,
    )


@login_required
@user_passes_test(is_staff)
def export_deleted_csv(request):
    rows = [
        [
            row.original_loan_id,
            row.borrower_username,
            row.borrower_full_name,
            row.amount,
            row.interest_rate,
            row.penalty_rate,
            row.status,
            row.issue_date,
            row.due_date,
            row.total_amount_due,
            row.payment_count,
            row.deleted_by_username,
            row.deleted_at.isoformat(),
            row.reason,
        ]
        for row in filtered_deleted(request.GET)
    ]
    return csv_response(
        dated_name('deleted_loans'),
        [
            'original_loan_id', 'username', 'borrower', 'principal', 'interest_rate',
            'penalty_rate', 'status', 'issue_date', 'due_date', 'total_due',
            'payments', 'deleted_by', 'deleted_at', 'reason',
        ],
        rows,
    )
