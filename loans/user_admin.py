"""In-app user management for admins and superusers."""

from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import AdminPasswordForm, ProfileForm, UserAdminForm
from .lockout import clear_login_failures
from .models import ActivityLog, Loan, LoginAttempt, loan_due_expression, sum_money
from .reports import csv_response, dated_name
from .roles import (
    ROLE_ADMIN,
    ROLE_OFFICER,
    ROLE_VIEWER,
    UserGuardError,
    assert_can_deactivate,
    assert_can_delete,
    can_manage_users,
    role_for,
)
from .utils import log_activity

PAGE_SIZE = 20


def user_admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage_users(request.user):
            messages.error(request, 'Only admins can manage users.')
            return redirect('dashboard')
        return view(request, *args, **kwargs)
    return wrapped


def _window_start():
    seconds = getattr(settings, 'LOGIN_LOCKOUT_SECONDS', 900)
    return timezone.now() - timedelta(seconds=seconds)


def filtered_users(params):
    users = User.objects.all().annotate(loan_count=Count('loans', distinct=True))
    q = (params.get('q') or '').strip()
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    role = (params.get('role') or '').strip()
    if role == 'admin':
        users = users.filter(Q(is_superuser=True) | Q(groups__name=ROLE_ADMIN))
    elif role == 'loan_officer':
        users = users.filter(groups__name=ROLE_OFFICER)
    elif role == 'viewer':
        users = users.filter(groups__name=ROLE_VIEWER)
    elif role == 'borrower':
        users = users.filter(is_staff=False, is_superuser=False)
    elif role == 'staff':
        users = users.filter(is_staff=True).exclude(
            Q(is_superuser=True) | Q(groups__name__in=[ROLE_ADMIN, ROLE_OFFICER, ROLE_VIEWER])
        )

    status = (params.get('status') or '').strip()
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)
    elif status == 'locked':
        limit = getattr(settings, 'LOGIN_FAILURE_LIMIT', 5)
        locked_names = (
            LoginAttempt.objects.filter(attempted_at__gte=_window_start())
            .values('username')
            .annotate(failures=Count('id'))
            .filter(failures__gte=limit)
            .values_list('username', flat=True)
        )
        users = users.filter(username__in=list(locked_names))

    return users.distinct().order_by('-date_joined', '-pk')


def _attach_lock_state(users):
    usernames = [user.username for user in users]
    limit = getattr(settings, 'LOGIN_FAILURE_LIMIT', 5)
    counts = {}
    if usernames:
        counts = dict(
            LoginAttempt.objects.filter(
                username__in=usernames,
                attempted_at__gte=_window_start(),
            )
            .values('username')
            .annotate(failures=Count('id'))
            .values_list('username', 'failures')
        )
    for user in users:
        user.recent_failures = counts.get(user.username, 0)
        user.account_locked = user.recent_failures >= limit
    return users


def _user_stats(user):
    loans = Loan.objects.filter(borrower=user)
    principal = loans.aggregate(
        total_loaned=sum_money('amount'),
        total_expected=sum_money(loan_due_expression()),
        open_loans=Count('id', filter=Q(is_paid=False)),
        paid_loans=Count('id', filter=Q(is_paid=True)),
        overdue_loans=Count('id', filter=Q(status='overdue', is_paid=False)),
    )
    paid = loans.aggregate(total_paid=sum_money('payments__amount'))
    recorded = user.payments_recorded.count()
    return {
        'loan_count': loans.count(),
        'open_loans': principal['open_loans'],
        'paid_loans': principal['paid_loans'],
        'overdue_loans': principal['overdue_loans'],
        'total_loaned': principal['total_loaned'],
        'total_expected': principal['total_expected'],
        'total_paid': paid['total_paid'],
        'payments_recorded': recorded,
        'activity_count': ActivityLog.objects.filter(username=user.username).count(),
    }


@user_admin_required
def user_list(request):
    users = filtered_users(request.GET)
    paginator = Paginator(users.prefetch_related('groups'), PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page') or 1)
    _attach_lock_state(page.object_list)
    return render(request, 'loans/users/list.html', {
        'users': page,
        'q': request.GET.get('q', ''),
        'role': request.GET.get('role', ''),
        'status': request.GET.get('status', ''),
        'total': paginator.count,
    })


@user_admin_required
def export_users_csv(request):
    users = filtered_users(request.GET).prefetch_related('groups')
    rows = []
    for user in users:
        rows.append([
            user.username,
            user.email,
            user.first_name,
            user.last_name,
            role_label(user),
            'yes' if user.is_active else 'no',
            'yes' if user.is_staff else 'no',
            'yes' if user.is_superuser else 'no',
            user.loan_count,
            user.date_joined.isoformat(),
            user.last_login.isoformat() if user.last_login else '',
        ])
    return csv_response(
        dated_name('users'),
        [
            'username', 'email', 'first_name', 'last_name', 'role', 'active',
            'staff', 'superuser', 'loans', 'date_joined', 'last_login',
        ],
        rows,
    )


def role_label(user):
    return role_for(user)


@user_admin_required
@require_http_methods(['GET', 'POST'])
def user_create(request):
    if request.method == 'POST':
        form = UserAdminForm(request.user, request.POST)
        with transaction.atomic():
            if form.is_valid():
                user = form.save()
                log_activity(
                    request,
                    'user_created',
                    detail=(
                        f'Created {user.username} '
                        f'({role_label(user)}, active={user.is_active})'
                    ),
                )
                messages.success(request, f'Account created for {user.username}.')
                return redirect('user_detail', user_id=user.pk)
    else:
        form = UserAdminForm(request.user)
    return render(request, 'loans/users/form.html', {
        'form': form,
        'editing': False,
    })


@user_admin_required
def user_detail(request, user_id):
    account = get_object_or_404(
        User.objects.prefetch_related('groups'),
        pk=user_id,
    )
    attempts = list(
        LoginAttempt.objects.filter(username=account.username).order_by('-attempted_at')[:20]
    )
    activity = list(ActivityLog.objects.filter(username=account.username)[:20])
    _attach_lock_state([account])
    return render(request, 'loans/users/detail.html', {
        'account': account,
        'password_form': AdminPasswordForm(account),
        'stats': _user_stats(account),
        'attempts': attempts,
        'activity': activity,
        'role_label': role_label(account),
    })


@user_admin_required
@require_http_methods(['GET', 'POST'])
def user_edit(request, user_id):
    account = get_object_or_404(User.objects.prefetch_related('groups'), pk=user_id)
    if request.method == 'POST':
        form = UserAdminForm(request.user, request.POST, instance=account)
        with transaction.atomic():
            if form.is_valid():
                user = form.save()
                password_set = bool(form.cleaned_data.get('password'))
                log_activity(
                    request,
                    'user_updated',
                    detail=(
                        f'Updated {user.username} '
                        f'({role_label(user)}, active={user.is_active})'
                    ),
                )
                if password_set:
                    log_activity(
                        request,
                        'password_changed',
                        detail=f'Password set for {user.username}',
                    )
                messages.success(request, f'Saved changes to {user.username}.')
                return redirect('user_detail', user_id=user.pk)
    else:
        form = UserAdminForm(request.user, instance=account)
    return render(request, 'loans/users/form.html', {
        'form': form,
        'editing': True,
        'account': account,
    })


@user_admin_required
@require_POST
def user_set_password(request, user_id):
    account = get_object_or_404(User, pk=user_id)
    form = AdminPasswordForm(account, request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(
            error for errors in form.errors.values() for error in errors
        ))
        return redirect('user_detail', user_id=account.pk)
    account.set_password(form.cleaned_data['password'])
    account.save(update_fields=['password'])
    log_activity(request, 'password_changed', detail=f'Password set for {account.username}')
    if form.cleaned_data.get('generated'):
        messages.success(
            request,
            f'Temporary password for {account.username}: {form.cleaned_data["password"]}',
        )
    else:
        messages.success(request, f'Password updated for {account.username}.')
    return redirect('user_detail', user_id=account.pk)


@user_admin_required
@require_POST
def user_deactivate(request, user_id):
    account = get_object_or_404(User.objects.prefetch_related('groups'), pk=user_id)
    try:
        with transaction.atomic():
            assert_can_deactivate(request.user, account)
            account.is_active = False
            account.save(update_fields=['is_active'])
    except UserGuardError as exc:
        messages.error(request, str(exc))
        return redirect('user_detail', user_id=account.pk)
    log_activity(request, 'user_deactivated', detail=f'Deactivated {account.username}')
    messages.success(request, f'{account.username} is deactivated and cannot sign in.')
    return redirect('user_detail', user_id=account.pk)


@user_admin_required
@require_POST
def user_reactivate(request, user_id):
    account = get_object_or_404(User, pk=user_id)
    account.is_active = True
    account.save(update_fields=['is_active'])
    log_activity(request, 'user_reactivated', detail=f'Reactivated {account.username}')
    messages.success(request, f'{account.username} can sign in again.')
    return redirect('user_detail', user_id=account.pk)


@user_admin_required
@require_POST
def user_unlock(request, user_id):
    account = get_object_or_404(User, pk=user_id)
    clear_login_failures(account.username)
    log_activity(request, 'account_unlocked', detail=f'Cleared login lock for {account.username}')
    messages.success(request, f'Login lock cleared for {account.username}.')
    return redirect('user_detail', user_id=account.pk)


@user_admin_required
@require_http_methods(['GET', 'POST'])
def user_delete(request, user_id):
    account = get_object_or_404(User, pk=user_id)
    live_count = Loan.objects.filter(borrower=account).count()
    if request.method == 'GET':
        return render(request, 'loans/users/delete.html', {
            'account': account,
            'live_count': live_count,
        })

    if (request.POST.get('confirm') or '').strip() != account.username:
        messages.error(request, 'Type the username exactly to confirm deletion.')
        return redirect('user_delete', user_id=account.pk)

    archive = request.POST.get('archive_loans') == 'yes'
    if live_count and not archive:
        messages.error(
            request,
            'This person still has live loans. Archive them as part of deletion, '
            'or remove the loans from the borrower page first.',
        )
        return redirect('user_delete', user_id=account.pk)

    username = account.username
    try:
        with transaction.atomic():
            locked = get_object_or_404(User.objects.select_for_update(), pk=account.pk)
            assert_can_delete(request.user, locked)
            if archive:
                loans = list(
                    Loan.objects.filter(borrower=locked).select_related('borrower')
                )
                for loan in loans:
                    loan.archive(
                        deleted_by=request.user,
                        reason=f'Archived while deleting user {username}',
                    )
            locked.delete()
    except UserGuardError as exc:
        messages.error(request, str(exc))
        return redirect('user_detail', user_id=account.pk)
    except ProtectedError:
        messages.error(
            request,
            'This account is still linked to live loans. Archive those loans, then delete the account.',
        )
        return redirect('user_delete', user_id=account.pk)
    log_activity(request, 'user_deleted', detail=f'Deleted account {username}')
    messages.success(request, f'Account {username} was deleted.')
    return redirect('user_list')


@login_required
@require_http_methods(['GET', 'POST'])
def profile(request):
    user = request.user
    if request.method == 'POST' and request.POST.get('form') == 'password':
        profile_form = ProfileForm(instance=user)
        password_form = PasswordChangeForm(user, request.POST)
        if password_form.is_valid():
            password_form.save()
            log_activity(request, 'password_changed', detail=f'{user.username} changed their password')
            update_session_auth_hash(request, password_form.user)
            messages.success(request, 'Your password was updated.')
            return redirect('profile')
    elif request.method == 'POST':
        profile_form = ProfileForm(request.POST, instance=user)
        password_form = PasswordChangeForm(user)
        if profile_form.is_valid():
            profile_form.save()
            log_activity(request, 'profile_updated', detail=f'{user.username} updated their profile')
            messages.success(request, 'Profile saved.')
            return redirect('profile')
    else:
        profile_form = ProfileForm(instance=user)
        password_form = PasswordChangeForm(user)
    for field in password_form.fields.values():
        field.widget.attrs.setdefault('class', 'input')
        field.help_text = ''
    return render(request, 'loans/profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
    })
