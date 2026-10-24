import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Sum, When
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .db import lock_rows
from .forms import LoanForm, LoanUpdateForm, UserRegistrationForm
from .lockout import clear_login_failures, login_is_locked, record_login_failure
from .models import (
    ActivityLog,
    Loan,
    Payment,
    loan_due_expression,
    sum_money,
)
from .pdf_export import generate_borrower_statement, generate_loans_excel
from .reports import filter_activity, filtered_deleted
from .roles import can_write_loans, reject_read_only
from .utils import get_client_ip, log_activity, safe_redirect

# SMS skipped — add later with: from .sms import send_payment_confirmation_sms


def is_staff(user):
    return user.is_staff


def parse_payment_amount(raw):
    """Parse a payment amount. Thousands separators are accepted; junk is not."""
    if raw is None:
        raise InvalidOperation
    text = str(raw).strip().replace(',', '')
    if not text:
        raise InvalidOperation
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise
    if not amount.is_finite() or amount <= 0 or amount > Decimal('99999999.99'):
        raise InvalidOperation
    quantum = Decimal('0.01')
    if amount != amount.quantize(quantum):
        raise InvalidOperation
    return amount.quantize(quantum)


def _payment_methods():
    return {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}


def _borrowers_with_open_loans():
    return (
        User.objects.filter(loans__is_paid=False)
        .distinct()
        .order_by('first_name', 'last_name', 'username')
    )


def _open_loans_for_borrower(borrower):
    """Unpaid loans, overdue first, then soonest due date."""
    return (
        Loan.objects.filter(borrower=borrower, is_paid=False)
        .select_related('borrower')
        .annotate(
            _priority=Case(
                When(status='overdue', then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by('_priority', 'due_date', 'id')
        .with_totals()
    )


def _apply_payment_to_loan(loan, amount, *, method, reference, note, recorded_by):
    """Create one payment row and mark the loan paid when the balance hits zero."""
    Payment.objects.create(
        loan=loan,
        amount=amount,
        method=method,
        reference=reference,
        note=note,
        recorded_by=recorded_by,
        paid_at=timezone.now(),
    )
    return loan.check_and_mark_paid()


def allocate_borrower_payment(borrower, amount, *, method, reference, note, recorded_by):
    """Spread a payment across the borrower's open loans (overdue → oldest due).

    Returns (created_payments, loans_marked_paid). Raises ValueError if the
    amount is larger than the borrower's total outstanding balance.
    """
    with transaction.atomic():
        loans = list(
            lock_rows(
                Loan.objects.filter(borrower=borrower, is_paid=False)
                .select_related('borrower')
                .annotate(
                    _priority=Case(
                        When(status='overdue', then=0),
                        default=1,
                        output_field=IntegerField(),
                    )
                )
                .order_by('_priority', 'due_date', 'id')
            )
        )
        if not loans:
            raise ValueError('This borrower has no open loans.')

        balances = [(loan, loan.balance_remaining()) for loan in loans]
        total_balance = sum((bal for _, bal in balances), Decimal('0.00'))
        if amount > total_balance:
            raise ValueError(
                f'Amount exceeds the remaining balance (KES {total_balance}).'
            )

        created = []
        paid_count = 0
        remaining = amount
        for loan, balance in balances:
            if remaining <= 0:
                break
            if balance <= 0:
                continue
            apply = min(remaining, balance)
            was_paid = _apply_payment_to_loan(
                loan,
                apply,
                method=method,
                reference=reference,
                note=note,
                recorded_by=recorded_by,
            )
            created.append((loan, apply))
            if was_paid:
                paid_count += 1
            remaining -= apply

        return created, paid_count


def _borrower_rows(users):
    for user in users:
        overdue = user.overdue_count
        active = user.active_count
        paid = user.paid_count
        if overdue:
            user.status = 'overdue'
        elif active:
            user.status = 'active'
        elif paid:
            user.status = 'paid'
        else:
            user.status = 'n/a'
        user.is_paid = paid > 0 and active == 0 and overdue == 0
    return users


def user_register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            log_activity(
                request, 'user_registered', detail=f'New user registered: {user.username}'
            )
            messages.success(request, 'Registration successful!')
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()
    return render(request, 'loans/register.html', {'form': form})


def user_login(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        ip = get_client_ip(request)
        if login_is_locked(username, ip):
            messages.error(request, 'Too many failed login attempts. Try again later.')
            return render(request, 'loans/login.html')
        user = authenticate(request, username=username, password=password)
        if user:
            clear_login_failures(username)
            login(request, user)
            log_activity(request, 'login', detail=f'{user.username} logged in')
            return redirect('dashboard')
        record_login_failure(username, ip)
        messages.error(request, 'Invalid credentials')
    return render(request, 'loans/login.html')


@require_POST
def user_logout(request):
    if request.user.is_authenticated:
        log_activity(request, 'logout', detail=f'{request.user.username} logged out')
    logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    if request.user.is_staff:
        loans = Loan.objects.all()
    else:
        loans = Loan.objects.filter(borrower=request.user)

    totals = loans.aggregate(
        total_loaned=sum_money('amount'),
        total_expected=sum_money(loan_due_expression()),
        active_loans=Count('id', filter=Q(is_paid=False)),
        overdue_count=Count('id', filter=Q(status='overdue', is_paid=False)),
        paid_count=Count('id', filter=Q(is_paid=True)),
        active_status_count=Count('id', filter=Q(status='active', is_paid=False)),
    )

    start = timezone.localdate().replace(day=1) - relativedelta(months=5)
    grouped = (
        loans.filter(issue_date__gte=start)
        .annotate(month=TruncMonth('issue_date'))
        .values('month')
        .annotate(total=Sum('amount'))
        .order_by('month')
    )
    by_month = {}
    for row in grouped:
        month = row['month']
        if isinstance(month, datetime):
            month = month.date()
        if month is not None:
            by_month[(month.year, month.month)] = row['total'] or 0

    monthly_labels = []
    monthly_amounts = []
    cursor = start
    for _ in range(6):
        monthly_labels.append(cursor.strftime('%b %Y'))
        monthly_amounts.append(float(by_month.get((cursor.year, cursor.month), 0)))
        cursor = cursor + relativedelta(months=1)

    status_data = [
        totals['paid_count'],
        totals['active_status_count'],
        totals['overdue_count'],
    ]

    collected = loans.aggregate(total_paid=sum_money('payments__amount'))['total_paid']
    recent_loans = list(
        loans.select_related('borrower').order_by('-created_at', '-pk')[:6]
    )
    recent_payments = list(
        Payment.objects.filter(loan__in=loans)
        .select_related('loan__borrower', 'recorded_by')
        .order_by('-paid_at', '-pk')[:6]
    )
    borrower_count = None
    if request.user.is_staff:
        borrower_count = loans.values('borrower_id').distinct().count()

    context = {
        'total_loaned': totals['total_loaned'],
        'total_expected': totals['total_expected'],
        'collected': collected,
        'outstanding': totals['total_expected'] - collected,
        'active_loans': totals['active_loans'],
        'active_status_count': totals['active_status_count'],
        'overdue_count': totals['overdue_count'],
        'paid_count': totals['paid_count'],
        'borrower_count': borrower_count,
        'recent_loans': recent_loans,
        'recent_payments': recent_payments,
        'monthly_labels': json.dumps(monthly_labels),
        'monthly_amounts': json.dumps(monthly_amounts),
        'status_data': json.dumps(status_data),
    }
    return render(request, 'loans/dashboard.html', context)


@login_required
def loan_list(request):
    if request.user.is_staff:
        loans = Loan.objects.all()
        total_expected = None
    else:
        loans = Loan.objects.filter(borrower=request.user)
        total_expected = loans.aggregate(total=sum_money(loan_due_expression()))['total']

    loans = loans.select_related('borrower')

    q = request.GET.get('q', '').strip()
    if q and request.user.is_staff:
        loans = loans.filter(
            Q(borrower__username__icontains=q)
            | Q(borrower__first_name__icontains=q)
            | Q(borrower__last_name__icontains=q)
        )

    status_filter = request.GET.get('status', '')
    if status_filter == 'paid':
        loans = loans.filter(is_paid=True)
    elif status_filter == 'overdue':
        loans = loans.filter(status='overdue', is_paid=False)
    elif status_filter == 'active':
        loans = loans.filter(status='active', is_paid=False)

    paginator = Paginator(loans.with_totals().order_by('-created_at', '-pk'), 25)
    loans_page = paginator.get_page(request.GET.get('page', 1))

    context = {
        'loans': loans_page,
        'total_expected': total_expected,
        'q': q,
        'status_filter': status_filter,
    }
    return render(request, 'loans/loan_list.html', context)


@login_required
@user_passes_test(is_staff)
def manage_loans(request):
    if request.method == 'POST':
        denied = reject_read_only(request, 'manage_loans')
        if denied:
            return denied
        form = LoanForm(request.POST)
        if form.is_valid():
            loan = form.save()
            log_activity(
                request,
                'loan_created',
                detail=f'Loan #{loan.id} created for {loan.borrower.username} — KES {loan.amount}',
                loan_id=loan.id,
            )
            messages.success(request, 'Loan added successfully!')
            return redirect('manage_loans')
    else:
        form = LoanForm()

    loans = Loan.objects.all().select_related('borrower')

    q = request.GET.get('q', '').strip()
    if q:
        loans = loans.filter(
            Q(borrower__username__icontains=q)
            | Q(borrower__first_name__icontains=q)
            | Q(borrower__last_name__icontains=q)
        )

    paginator = Paginator(loans.with_totals().order_by('-created_at', '-pk'), 25)
    loans_page = paginator.get_page(request.GET.get('page', 1))

    context = {'form': form, 'loans': loans_page, 'q': q}
    return render(request, 'loans/manage_loans.html', context)


@login_required
@user_passes_test(is_staff)
def borrower_summary(request):
    users = User.objects.annotate(
        total_loans=Count('loans'),
        total_amount_borrowed=sum_money('loans__amount'),
        total_expected=sum_money(loan_due_expression('loans__')),
        overdue_count=Count(
            'loans', filter=Q(loans__status='overdue', loans__is_paid=False)
        ),
        active_count=Count(
            'loans', filter=Q(loans__status='active', loans__is_paid=False)
        ),
        paid_count=Count('loans', filter=Q(loans__is_paid=True)),
    ).filter(total_loans__gt=0)

    q = request.GET.get('q', '').strip()
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )

    paginator = Paginator(users.order_by('pk'), 20)
    users_page = paginator.get_page(request.GET.get('page', 1))
    _borrower_rows(users_page.object_list)

    return render(request, 'loans/borrower_summary.html', {'users': users_page, 'q': q})


def _borrower_loan_queryset(borrower):
    payments = Prefetch(
        'payments', queryset=Payment.objects.select_related('recorded_by')
    )
    return (
        Loan.objects.filter(borrower=borrower)
        .select_related('borrower')
        .prefetch_related(payments)
        .with_totals()
    )


def _borrower_totals(borrower):
    base = Loan.objects.filter(borrower=borrower)
    # Principal and amount-due stay on a queryset that is not joined to payments,
    # so multiple payments cannot multiply the loan figures.
    principal = base.aggregate(
        total_loaned=sum_money('amount'),
        total_expected=sum_money(loan_due_expression()),
        active_loans=Count('id', filter=Q(is_paid=False)),
    )
    paid = base.aggregate(total_paid=sum_money('payments__amount'))
    annotated = base.with_totals()
    total_balance = Decimal('0.00')
    for due, paid_sum in annotated.values_list('amount_due', 'paid_sum'):
        total_balance += max(due - paid_sum, Decimal('0'))
    return {
        'total_loaned': principal['total_loaned'],
        'total_expected': principal['total_expected'],
        'total_paid': paid['total_paid'],
        'total_balance': total_balance,
        'active_loans': principal['active_loans'],
    }


@login_required
@user_passes_test(is_staff)
def borrower_detail(request, user_id):
    borrower = get_object_or_404(User, id=user_id)

    if request.method == 'POST' and not can_write_loans(request.user):
        messages.error(request, 'Your role can view records but cannot change them.')
        return redirect('borrower_detail', user_id=user_id)

    if request.method == 'POST':
        raw_ids = request.POST.getlist('loan_ids')
        reason = request.POST.get('reason', '').strip()
        loan_ids = []
        for raw in raw_ids:
            try:
                loan_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not loan_ids:
            messages.error(request, 'No loans selected.')
            return redirect('borrower_detail', user_id=user_id)

        with transaction.atomic():
            selected = list(
                lock_rows(Loan.objects.filter(id__in=loan_ids, borrower=borrower))
                .select_related('borrower')
            )
            for loan in selected:
                loan.archive(deleted_by=request.user, reason=reason)
        if not selected:
            messages.error(request, 'No loans selected.')
            return redirect('borrower_detail', user_id=user_id)
        log_activity(
            request,
            'loans_bulk_deleted',
            detail=(
                f'{len(selected)} loan(s) deleted for {borrower.username}. '
                f'IDs: {", ".join(str(loan.id) for loan in selected)}'
            ),
        )
        messages.success(
            request,
            f'{len(selected)} loan(s) deleted and archived. Payment history was kept.',
        )
        return redirect('borrower_detail', user_id=user_id)

    loans = _borrower_loan_queryset(borrower)
    context = {
        'borrower': borrower,
        'loans': loans,
        **_borrower_totals(borrower),
    }
    return render(request, 'loans/borrower_detail.html', context)


@login_required
@user_passes_test(is_staff)
@require_http_methods(['GET', 'POST'])
def record_borrower_payment(request, user_id=None):
    """Record a payment against a borrower; amount is split across open loans."""
    denied = reject_read_only(request, 'borrower_summary')
    if denied:
        return denied

    borrowers = _borrowers_with_open_loans()
    selected = None
    if user_id is not None:
        selected = get_object_or_404(User, id=user_id)
    elif request.method == 'GET':
        raw = request.GET.get('borrower', '').strip()
        if raw.isdigit():
            selected = User.objects.filter(id=int(raw)).first()

    open_loans = list(_open_loans_for_borrower(selected)) if selected else []
    total_balance = sum((loan.balance_remaining() for loan in open_loans), Decimal('0.00'))

    if request.method == 'POST':
        method = (request.POST.get('method') or 'cash').strip()
        reference = request.POST.get('reference', '').strip()
        note = request.POST.get('note', '').strip()
        raw_borrower = request.POST.get('borrower') or user_id
        try:
            borrower = get_object_or_404(User, id=int(raw_borrower))
        except (TypeError, ValueError):
            messages.error(request, 'Choose a borrower.')
            return redirect('record_borrower_payment')

        if method not in _payment_methods():
            messages.error(request, 'Choose a valid payment method.')
            return redirect('record_borrower_payment_for', user_id=borrower.id)

        try:
            amount = parse_payment_amount(request.POST.get('amount'))
        except (InvalidOperation, ValueError, TypeError):
            messages.error(request, 'Enter a valid payment amount.')
            return redirect('record_borrower_payment_for', user_id=borrower.id)

        try:
            created, paid_count = allocate_borrower_payment(
                borrower,
                amount,
                method=method,
                reference=reference,
                note=note,
                recorded_by=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('record_borrower_payment_for', user_id=borrower.id)

        loan_bits = ', '.join(
            f'#{loan.id} KES {applied}' for loan, applied in created
        )
        log_activity(
            request,
            'payment_recorded',
            detail=(
                f'KES {amount} recorded for {borrower.username} via {method} '
                f'across {len(created)} loan(s): {loan_bits}'
            ),
            loan_id=created[0][0].id if created else None,
        )
        msg = (
            f'Payment of KES {amount} recorded for '
            f'{borrower.get_full_name() or borrower.username} '
            f'across {len(created)} loan(s).'
        )
        if paid_count:
            msg += f' {paid_count} loan(s) fully paid.'
        messages.success(request, msg)
        return redirect('borrower_detail', user_id=borrower.id)

    return render(
        request,
        'loans/record_payment.html',
        {
            'borrowers': borrowers,
            'selected': selected,
            'open_loans': open_loans,
            'total_balance': total_balance,
            'methods': Payment.PAYMENT_METHOD_CHOICES,
        },
    )


@login_required
@user_passes_test(is_staff)
@require_POST
def record_payment(request, loan_id):
    """Legacy single-loan payment endpoint (kept for existing callers/tests)."""
    denied = reject_read_only(request, 'loan_list')
    if denied:
        return denied
    method = (request.POST.get('method') or 'cash').strip()
    reference = request.POST.get('reference', '').strip()
    note = request.POST.get('note', '').strip()

    if method not in _payment_methods():
        messages.error(request, 'Choose a valid payment method.')
        return safe_redirect(request)

    try:
        amount = parse_payment_amount(request.POST.get('amount'))
    except (InvalidOperation, ValueError, TypeError):
        messages.error(request, 'Enter a valid payment amount.')
        return safe_redirect(request)

    with transaction.atomic():
        loan = get_object_or_404(
            lock_rows(Loan.objects.select_related('borrower')),
            id=loan_id,
        )
        balance = loan.balance_remaining()
        if amount > balance:
            messages.error(
                request,
                f'Amount exceeds the remaining balance (KES {balance}).',
            )
            return safe_redirect(request)

        was_paid = _apply_payment_to_loan(
            loan,
            amount,
            method=method,
            reference=reference,
            note=note,
            recorded_by=request.user,
        )
        borrower_username = loan.borrower.username
        recorded_loan_id = loan.id

    log_activity(
        request,
        'payment_recorded',
        detail=(
            f'KES {amount} recorded for Loan #{recorded_loan_id} '
            f'({borrower_username}) via {method}'
        ),
        loan_id=recorded_loan_id,
    )

    msg = f'Payment of KES {amount} recorded.'
    if was_paid:
        msg += ' Loan is now fully paid!'
    messages.success(request, msg)
    return safe_redirect(request)


@login_required
@user_passes_test(is_staff)
def update_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    if request.method == 'POST':
        denied = reject_read_only(request, 'manage_loans')
        if denied:
            return denied
        form = LoanUpdateForm(request.POST, instance=loan)
        if form.is_valid():
            form.save()
            log_activity(
                request, 'loan_updated', detail=f'Loan #{loan.id} updated', loan_id=loan.id
            )
            messages.success(request, 'Loan updated.')
    return redirect('manage_loans')


@login_required
@user_passes_test(is_staff)
@require_POST
def delete_loan(request, loan_id):
    denied = reject_read_only(request, 'manage_loans')
    if denied:
        return denied
    with transaction.atomic():
        loan = get_object_or_404(
            lock_rows(Loan.objects.select_related('borrower')),
            id=loan_id,
        )
        borrower_username = loan.borrower.username
        recorded_loan_id = loan.id
        loan.archive(deleted_by=request.user)
    log_activity(
        request,
        'loan_deleted',
        detail=f'Loan #{recorded_loan_id} deleted — {borrower_username}',
        loan_id=recorded_loan_id,
    )
    messages.success(request, 'Loan deleted and archived. Payment history was kept.')
    return redirect('manage_loans')


@login_required
@user_passes_test(is_staff)
def export_borrower_pdf(request, user_id):
    borrower = get_object_or_404(User, id=user_id)
    loans = (
        Loan.objects.filter(borrower=borrower)
        .with_totals()
        .prefetch_related('payments')
    )
    pdf = generate_borrower_statement(borrower, loans)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="statement_{borrower.username}.pdf"'
    return response


@login_required
@user_passes_test(is_staff)
def export_loans_excel(request):
    loans = (
        Loan.objects.all()
        .select_related('borrower')
        .with_totals()
        .prefetch_related('payments')
    )
    xlsx = generate_loans_excel(loans)
    response = HttpResponse(
        xlsx,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="loans_{date.today()}.xlsx"'
    return response


@login_required
@user_passes_test(is_staff)
def deleted_loans_archive(request):
    deleted = filtered_deleted(request.GET)
    q = request.GET.get('q', '').strip()

    paginator = Paginator(deleted, 25)
    deleted_page = paginator.get_page(request.GET.get('page', 1))

    return render(
        request,
        'loans/deleted_loans_archive.html',
        {'deleted_loans': deleted_page, 'search': q, 'total': deleted.count()},
    )


@login_required
@user_passes_test(is_staff)
def activity_log(request):
    logs = filter_activity(request.GET)
    action_filter = request.GET.get('action', '').strip()
    user_filter = request.GET.get('user', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    all_logs = ActivityLog.objects.all()
    paginator = Paginator(logs, 50)
    logs_page = paginator.get_page(request.GET.get('page', 1))

    context = {
        'logs': logs_page,
        'action_choices': ActivityLog.ACTION_CHOICES,
        'action_filter': action_filter,
        'user_filter': user_filter,
        'date_from': date_from,
        'date_to': date_to,
        'total': logs.count(),
        'page_visit_count': all_logs.filter(action='page_visit').count(),
        'loan_action_count': all_logs.filter(action__in=[
            'loan_created', 'loan_updated', 'loan_deleted', 'loans_bulk_deleted'
        ]).count(),
        'login_count': all_logs.filter(action='login').count(),
        'admin_action_count': all_logs.filter(action__in=[
            'user_created', 'user_updated', 'user_deactivated', 'user_reactivated',
            'user_deleted', 'password_changed', 'account_unlocked', 'profile_updated',
        ]).count(),
    }
    return render(request, 'loans/activity_log.html', context)
