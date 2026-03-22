from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db.models import Sum, Count, Q
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.utils import timezone
from datetime import date
from .models import Loan, Payment, DeletedLoan, ActivityLog
from .forms import LoanForm, LoanUpdateForm, UserRegistrationForm
from .utils import log_activity
from .pdf_export import generate_borrower_statement, generate_loans_excel
# SMS skipped — add later with: from .sms import send_payment_confirmation_sms
from decimal import Decimal
from django.db.models import (Count, Sum, F, DecimalField,
                               ExpressionWrapper, Case, When, IntegerField)
import json


# ─── helpers ─────────────────────────────────────────────────────────────────

def is_staff(u): return u.is_staff


# ─── Auth ─────────────────────────────────────────────────────────────────────

def user_register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            login(request, user)
            log_activity(request, 'user_registered',
                         detail=f'New user registered: {user.username}')
            messages.success(request, 'Registration successful!')
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()
    return render(request, 'loans/register.html', {'form': form})


def user_login(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            log_activity(request, 'login', detail=f'{user.username} logged in')
            return redirect('dashboard')
        messages.error(request, 'Invalid credentials')
    return render(request, 'loans/login.html')


def user_logout(request):
    log_activity(request, 'logout', detail=f'{request.user.username} logged out')
    logout(request)
    return redirect('login')


# ─── Dashboard (with chart data) ──────────────────────────────────────────────

@login_required
def dashboard(request):
    if request.user.is_staff:
        loans = Loan.objects.all()
    else:
        loans = Loan.objects.filter(borrower=request.user)

    total_loaned   = loans.aggregate(Sum('amount'))['amount__sum'] or 0
    active_loans   = loans.filter(is_paid=False).count()
    total_expected = sum(loan.total_amount_due() for loan in loans)
    overdue_count  = loans.filter(status='overdue', is_paid=False).count()
    paid_count     = loans.filter(is_paid=True).count()

    # ── Chart data ────────────────────────────────────────────────────────
    # Monthly lending for the last 6 months
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    monthly_labels  = []
    monthly_amounts = []
    for i in range(5, -1, -1):
        d     = timezone.now().date() - relativedelta(months=i)
        label = d.strftime('%b %Y')
        amt   = loans.filter(
            issue_date__year=d.year,
            issue_date__month=d.month
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        monthly_labels.append(label)
        monthly_amounts.append(float(amt))

    # Status breakdown for pie/doughnut
    status_data = [
        loans.filter(is_paid=True).count(),
        loans.filter(status='active', is_paid=False).count(),
        loans.filter(status='overdue', is_paid=False).count(),
    ]

    context = {
        'total_loaned':    total_loaned,
        'total_expected':  total_expected,
        'active_loans':    active_loans,
        'overdue_count':   overdue_count,
        'paid_count':      paid_count,
        'monthly_labels':  json.dumps(monthly_labels),
        'monthly_amounts': json.dumps(monthly_amounts),
        'status_data':     json.dumps(status_data),
    }
    return render(request, 'loans/dashboard.html', context)


# ─── Loan list ────────────────────────────────────────────────────────────────

@login_required
def loan_list(request):
    if request.user.is_staff:
        loans = Loan.objects.all().select_related('borrower')
        total_expected = None
    else:
        loans = Loan.objects.filter(borrower=request.user)
        total_expected = sum(loan.total_amount_due() for loan in loans)

    # Search
    q = request.GET.get('q', '').strip()
    if q and request.user.is_staff:
        loans = loans.filter(
            Q(borrower__username__icontains=q) |
            Q(borrower__first_name__icontains=q) |
            Q(borrower__last_name__icontains=q)
        )

    # Status filter
    status_filter = request.GET.get('status', '')
    if status_filter == 'paid':
        loans = loans.filter(is_paid=True)
    elif status_filter == 'overdue':
        loans = loans.filter(status='overdue', is_paid=False)
    elif status_filter == 'active':
        loans = loans.filter(status='active', is_paid=False)

    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(loans, 25)
    page      = request.GET.get('page', 1)
    loans_page = paginator.get_page(page)

    context = {
        'loans':          loans_page,
        'total_expected': total_expected,
        'q':              q,
        'status_filter':  status_filter,
    }
    return render(request, 'loans/loan_list.html', context)


# ─── Manage loans ─────────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def manage_loans(request):
    if request.method == 'POST':
        form = LoanForm(request.POST)
        if form.is_valid():
            loan = form.save()
            log_activity(request, 'loan_created',
                         detail=f'Loan #{loan.id} created for {loan.borrower.username} — KES {loan.amount}',
                         loan_id=loan.id)
            messages.success(request, 'Loan added successfully!')
            return redirect('manage_loans')
    else:
        form = LoanForm()

    loans = Loan.objects.all().select_related('borrower')

    # Search
    q = request.GET.get('q', '').strip()
    if q:
        loans = loans.filter(
            Q(borrower__username__icontains=q) |
            Q(borrower__first_name__icontains=q) |
            Q(borrower__last_name__icontains=q)
        )

    # Pagination
    from django.core.paginator import Paginator
    paginator  = Paginator(loans, 25)
    loans_page = paginator.get_page(request.GET.get('page', 1))

    context = {'form': form, 'loans': loans_page, 'q': q}
    return render(request, 'loans/manage_loans.html', context)


# ─── Borrower summary ─────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def borrower_summary(request):
    users = User.objects.filter(loans__isnull=False).annotate(
        total_loans=Count('loans'),
        total_amount_borrowed=Sum('loans__amount'),
        total_expected=Sum(
            ExpressionWrapper(
                F('loans__amount') + (F('loans__amount') * F('loans__interest_rate') / Decimal('100')),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        ),
    ).distinct()

    # Search
    q = request.GET.get('q', '').strip()
    if q:
        users = users.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q)
        )

    for user in users:
        overdue = Loan.objects.filter(borrower=user, status='overdue', is_paid=False).count()
        active  = Loan.objects.filter(borrower=user, status='active',  is_paid=False).count()
        paid    = Loan.objects.filter(borrower=user, is_paid=True).count()
        user.status  = 'overdue' if overdue else ('active' if active else ('paid' if paid else 'n/a'))
        user.is_paid = paid > 0 and active == 0 and overdue == 0

    from django.core.paginator import Paginator
    paginator  = Paginator(list(users), 20)
    users_page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'loans/borrower_summary.html', {'users': users_page, 'q': q})


# ─── Borrower detail ──────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def borrower_detail(request, user_id):
    borrower = get_object_or_404(User, id=user_id)
    loans    = Loan.objects.filter(borrower=borrower).prefetch_related('payments')

    if request.method == 'POST':
        loan_ids = request.POST.getlist('loan_ids')
        reason   = request.POST.get('reason', '').strip()
        if not loan_ids:
            messages.error(request, 'No loans selected.')
            return redirect('borrower_detail', user_id=user_id)

        selected = Loan.objects.filter(id__in=loan_ids, borrower=borrower)
        archived = [
            DeletedLoan(
                original_loan_id=l.id, borrower=l.borrower,
                borrower_username=l.borrower.username,
                borrower_full_name=l.borrower.get_full_name() or l.borrower.username,
                amount=l.amount, interest_rate=l.interest_rate,
                penalty_rate=l.penalty_rate, status=l.status,
                is_paid=l.is_paid, issue_date=l.issue_date, due_date=l.due_date,
                total_amount_due=l.total_amount_due(),
                deleted_by=request.user, deleted_by_username=request.user.username,
                reason=reason,
            ) for l in selected
        ]
        DeletedLoan.objects.bulk_create(archived)
        count = selected.count()
        selected.delete()
        log_activity(request, 'loans_bulk_deleted',
                     detail=f'{count} loan(s) deleted for {borrower.username}. IDs: {", ".join(loan_ids)}')
        messages.success(request, f'{count} loan(s) deleted and archived.')
        return redirect('borrower_detail', user_id=user_id)

    total_loaned   = loans.aggregate(Sum('amount'))['amount__sum'] or 0
    total_expected = sum(l.total_amount_due() for l in loans)
    total_paid_sum = sum(l.total_paid() for l in loans)
    total_balance  = sum(l.balance_remaining() for l in loans)
    active_loans   = loans.filter(is_paid=False).count()

    context = {
        'borrower':      borrower,
        'loans':         loans,
        'total_loaned':  total_loaned,
        'total_expected':total_expected,
        'total_paid':    total_paid_sum,
        'total_balance': total_balance,
        'active_loans':  active_loans,
    }
    return render(request, 'loans/borrower_detail.html', context)


# ─── Record payment ───────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def record_payment(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)

    if request.method == 'POST':
        amount    = request.POST.get('amount')
        method    = request.POST.get('method', 'cash')
        reference = request.POST.get('reference', '').strip()
        note      = request.POST.get('note', '').strip()

        try:
            amount = Decimal(amount)
            if amount <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'Enter a valid payment amount.')
            return redirect(request.META.get('HTTP_REFERER', 'manage_loans'))

        payment = Payment.objects.create(
            loan=loan, amount=amount, method=method,
            reference=reference, note=note,
            recorded_by=request.user,
            paid_at=timezone.now(),
        )

        # Auto-mark paid if balance cleared
        was_paid = loan.check_and_mark_paid()

        log_activity(request, 'payment_recorded',
                     detail=f'KES {amount} recorded for Loan #{loan.id} ({loan.borrower.username}) via {method}',
                     loan_id=loan.id)

        msg = f'Payment of KES {amount} recorded.'
        if was_paid:
            msg += ' Loan is now fully paid! 🎉'
        messages.success(request, msg)

    return redirect(request.META.get('HTTP_REFERER', 'manage_loans'))


# ─── Update / Delete loan ─────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def update_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    if request.method == 'POST':
        form = LoanUpdateForm(request.POST, instance=loan)
        if form.is_valid():
            form.save()
            log_activity(request, 'loan_updated',
                         detail=f'Loan #{loan.id} updated', loan_id=loan.id)
            messages.success(request, 'Loan updated.')
    return redirect('manage_loans')


@login_required
@user_passes_test(is_staff)
def delete_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    DeletedLoan.objects.create(
        original_loan_id=loan.id, borrower=loan.borrower,
        borrower_username=loan.borrower.username,
        borrower_full_name=loan.borrower.get_full_name() or loan.borrower.username,
        amount=loan.amount, interest_rate=loan.interest_rate,
        penalty_rate=loan.penalty_rate, status=loan.status,
        is_paid=loan.is_paid, issue_date=loan.issue_date, due_date=loan.due_date,
        total_amount_due=loan.total_amount_due(),
        deleted_by=request.user, deleted_by_username=request.user.username,
    )
    log_activity(request, 'loan_deleted',
                 detail=f'Loan #{loan.id} deleted — {loan.borrower.username}', loan_id=loan.id)
    loan.delete()
    messages.success(request, 'Loan deleted and archived.')
    return redirect('manage_loans')


# ─── Exports ──────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def export_borrower_pdf(request, user_id):
    borrower = get_object_or_404(User, id=user_id)
    loans    = Loan.objects.filter(borrower=borrower).prefetch_related('payments')
    pdf      = generate_borrower_statement(borrower, loans)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="statement_{borrower.username}.pdf"'
    return response


@login_required
@user_passes_test(is_staff)
def export_loans_excel(request):
    loans = Loan.objects.all().select_related('borrower').prefetch_related('payments')
    xlsx  = generate_loans_excel(loans)
    response = HttpResponse(xlsx,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="loans_{date.today()}.xlsx"'
    return response


# ─── Archive & Activity ───────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def deleted_loans_archive(request):
    deleted = DeletedLoan.objects.select_related('borrower', 'deleted_by').all()
    q = request.GET.get('q', '').strip()
    if q:
        deleted = deleted.filter(borrower_username__icontains=q)

    from django.core.paginator import Paginator
    paginator    = Paginator(deleted, 25)
    deleted_page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'loans/deleted_loans_archive.html',
                  {'deleted_loans': deleted_page, 'search': q, 'total': deleted.count()})


@login_required
@user_passes_test(is_staff)
def activity_log(request):
    logs          = ActivityLog.objects.select_related('user').all()
    action_filter = request.GET.get('action', '').strip()
    user_filter   = request.GET.get('user', '').strip()
    if action_filter:
        logs = logs.filter(action=action_filter)
    if user_filter:
        logs = logs.filter(username__icontains=user_filter)

    all_logs = ActivityLog.objects.all()
    from django.core.paginator import Paginator
    paginator  = Paginator(logs, 50)
    logs_page  = paginator.get_page(request.GET.get('page', 1))

    context = {
        'logs':             logs_page,
        'action_choices':   ActivityLog.ACTION_CHOICES,
        'action_filter':    action_filter,
        'user_filter':      user_filter,
        'total':            logs.count(),
        'page_visit_count': all_logs.filter(action='page_visit').count(),
        'loan_action_count':all_logs.filter(action__in=[
            'loan_created','loan_updated','loan_deleted','loans_bulk_deleted']).count(),
        'login_count':      all_logs.filter(action='login').count(),
    }
    return render(request, 'loans/activity_log.html', context)