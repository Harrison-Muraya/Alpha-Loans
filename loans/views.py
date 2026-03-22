from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db.models import Sum, Count
from django.contrib.auth.models import User
from datetime import date
from .models import Loan, DeletedLoan, ActivityLog
from .forms import LoanForm, LoanUpdateForm, UserRegistrationForm
from .utils import log_activity
from decimal import Decimal
from django.db.models import Count, Sum, F, DecimalField, ExpressionWrapper, Case, When, IntegerField


# ─── Auth ────────────────────────────────────────────────────────────────────

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
            messages.success(request, 'Registration successful! Welcome to Loan Manager.')
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
        
        if user is not None:
            login(request, user)
            log_activity(request, 'login',
                         detail=f'{user.username} logged in')
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid credentials')
    
    return render(request, 'loans/login.html')


def user_logout(request):
    log_activity(request, 'logout',
                 detail=f'{request.user.username} logged out')
    logout(request)
    return redirect('login')


# ─── Dashboard ───────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    if request.user.is_staff:
        loans = Loan.objects.all()
    else:
        loans = Loan.objects.filter(borrower=request.user)
    
    total_loaned   = loans.aggregate(Sum('amount'))['amount__sum'] or 0
    active_loans   = loans.filter(is_paid=False).count()
    total_expected = sum([loan.total_amount_due() for loan in loans])
    
    context = {
        'total_loaned':   total_loaned,
        'total_expected': total_expected,
        'active_loans':   active_loans,
    }
    return render(request, 'loans/dashboard.html', context)


# ─── Loan list ───────────────────────────────────────────────────────────────

@login_required
def loan_list(request):
    if request.user.is_staff:
        loans = Loan.objects.all().select_related('borrower')
        total_expected = None
    else:
        loans = Loan.objects.filter(borrower=request.user)
        total_expected = sum([loan.total_amount_due() for loan in loans])
    
    context = {
        'loans':          loans,
        'total_expected': total_expected,
    }
    return render(request, 'loans/loan_list.html', context)


# ─── Manage loans ────────────────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
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
    context = {'form': form, 'loans': loans}
    return render(request, 'loans/manage_loans.html', context)


# ─── Borrower summary ────────────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
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
        is_paid=Sum(
            Case(
                When(loans__is_paid=True, then=1),
                default=0,
                output_field=IntegerField()
            )
        ),
    ).distinct()

    for user in users:
        active_count  = Loan.objects.filter(borrower=user, status='active').count()
        overdue_count = Loan.objects.filter(borrower=user, status='overdue').count()
        paid_count    = Loan.objects.filter(borrower=user, status='paid').count()

        if overdue_count:
            user.status = 'overdue'
        elif active_count:
            user.status = 'active'
        elif paid_count:
            user.status = 'paid'
        else:
            user.status = 'n/a'

        user.is_paid = paid_count > 0

    return render(request, 'loans/borrower_summary.html', {'users': users})


# ─── Borrower detail (with bulk delete) ──────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
def borrower_detail(request, user_id):
    borrower = get_object_or_404(User, id=user_id)
    loans    = Loan.objects.filter(borrower=borrower)

    # ── Bulk delete POST ──────────────────────────────────────────────────────
    if request.method == 'POST':
        loan_ids = request.POST.getlist('loan_ids')  # list of selected IDs
        reason   = request.POST.get('reason', '').strip()

        if not loan_ids:
            messages.error(request, 'No loans selected.')
            return redirect('borrower_detail', user_id=user_id)

        selected_loans = Loan.objects.filter(id__in=loan_ids, borrower=borrower)

        archived = []
        for loan in selected_loans:
            archived.append(DeletedLoan(
                original_loan_id    = loan.id,
                borrower            = loan.borrower,
                borrower_username   = loan.borrower.username,
                borrower_full_name  = loan.borrower.get_full_name() or loan.borrower.username,
                amount              = loan.amount,
                interest_rate       = loan.interest_rate,
                penalty_rate        = loan.penalty_rate,
                status              = loan.status,
                is_paid             = loan.is_paid,
                issue_date          = loan.issue_date,
                due_date            = loan.due_date,
                total_amount_due    = loan.total_amount_due(),
                deleted_by          = request.user,
                deleted_by_username = request.user.username,
                reason              = reason,
            ))

        # Bulk insert snapshots then delete originals
        DeletedLoan.objects.bulk_create(archived)
        count = selected_loans.count()
        selected_loans.delete()

        log_activity(
            request, 'loans_bulk_deleted',
            detail=(
                f'{count} loan(s) deleted for {borrower.username}. '
                f'IDs: {", ".join(loan_ids)}. Reason: "{reason or "none"}"'
            ),
        )
        messages.success(request, f'{count} loan(s) deleted and archived successfully.')
        return redirect('borrower_detail', user_id=user_id)

    # ── GET ───────────────────────────────────────────────────────────────────
    total_loaned   = loans.aggregate(Sum('amount'))['amount__sum'] or 0
    total_expected = sum([loan.total_amount_due() for loan in loans])
    active_loans   = loans.filter(is_paid=False).count()

    context = {
        'borrower':      borrower,
        'loans':         loans,
        'total_loaned':  total_loaned,
        'total_expected':total_expected,
        'active_loans':  active_loans,
    }
    return render(request, 'loans/borrower_detail.html', context)


# ─── Update loan ─────────────────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
def update_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    
    if request.method == 'POST':
        form = LoanUpdateForm(request.POST, instance=loan)
        if form.is_valid():
            form.save()
            log_activity(request, 'loan_updated',
                         detail=f'Loan #{loan.id} updated — status={loan.status}, paid={loan.is_paid}',
                         loan_id=loan.id)
            messages.success(request, 'Loan updated successfully!')
            return redirect('manage_loans')
    
    return redirect('manage_loans')


# ─── Delete single loan ───────────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
def delete_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)

    # Archive before deleting
    DeletedLoan.objects.create(
        original_loan_id    = loan.id,
        borrower            = loan.borrower,
        borrower_username   = loan.borrower.username,
        borrower_full_name  = loan.borrower.get_full_name() or loan.borrower.username,
        amount              = loan.amount,
        interest_rate       = loan.interest_rate,
        penalty_rate        = loan.penalty_rate,
        status              = loan.status,
        is_paid             = loan.is_paid,
        issue_date          = loan.issue_date,
        due_date            = loan.due_date,
        total_amount_due    = loan.total_amount_due(),
        deleted_by          = request.user,
        deleted_by_username = request.user.username,
        reason              = '',
    )

    log_activity(request, 'loan_deleted',
                 detail=f'Loan #{loan.id} deleted — borrower: {loan.borrower.username}, amount: KES {loan.amount}',
                 loan_id=loan.id)

    loan.delete()
    messages.success(request, 'Loan deleted and archived.')
    return redirect('manage_loans')


# ─── Deleted loans archive ───────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
def deleted_loans_archive(request):
    deleted = DeletedLoan.objects.select_related('borrower', 'deleted_by').all()

    # Optional filter by borrower username
    search = request.GET.get('q', '').strip()
    if search:
        deleted = deleted.filter(borrower_username__icontains=search)

    context = {
        'deleted_loans': deleted,
        'search': search,
        'total': deleted.count(),
    }
    return render(request, 'loans/deleted_loans_archive.html', context)


# ─── Activity log ────────────────────────────────────────────────────────────

@login_required
@user_passes_test(lambda u: u.is_staff)
def activity_log(request):
    logs = ActivityLog.objects.select_related('user').all()

    # Optional filters
    action_filter = request.GET.get('action', '').strip()
    user_filter   = request.GET.get('user', '').strip()

    if action_filter:
        logs = logs.filter(action=action_filter)
    if user_filter:
        logs = logs.filter(username__icontains=user_filter)

    # Stats (always on full unfiltered set)
    all_logs = ActivityLog.objects.all()

    context = {
        'logs':             logs,
        'action_choices':   ActivityLog.ACTION_CHOICES,
        'action_filter':    action_filter,
        'user_filter':      user_filter,
        'total':            logs.count(),
        'page_visit_count': all_logs.filter(action='page_visit').count(),
        'loan_action_count':all_logs.filter(
                                action__in=['loan_created','loan_updated',
                                            'loan_deleted','loans_bulk_deleted']
                            ).count(),
        'login_count':      all_logs.filter(action='login').count(),
    }
    return render(request, 'loans/activity_log.html', context)