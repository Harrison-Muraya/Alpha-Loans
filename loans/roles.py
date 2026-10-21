"""Staff roles backed by Django groups and their permissions.

Admin, Loan Officer, and Viewer are real auth groups. In-app checks use those
group names (and the superuser flag) so a staff account created before roles
existed keeps working. The groups still carry model permissions for Django
admin and for anything that calls ``user.has_perm``.
"""

from django.contrib.auth.models import Group, Permission, User

ROLE_ADMIN = 'Admin'
ROLE_OFFICER = 'Loan Officer'
ROLE_VIEWER = 'Viewer'
ROLE_BORROWER = 'Borrower'
ROLE_STAFF = 'Staff'

ROLE_NAMES = (ROLE_ADMIN, ROLE_OFFICER, ROLE_VIEWER)
ASSIGNABLE_ROLES = (
    (ROLE_ADMIN, 'Admin'),
    (ROLE_OFFICER, 'Loan officer'),
    (ROLE_VIEWER, 'Viewer'),
    (ROLE_BORROWER, 'Borrower'),
)

OFFICER_CODENAMES = (
    'add_loan',
    'change_loan',
    'delete_loan',
    'view_loan',
    'add_payment',
    'change_payment',
    'view_payment',
    'view_deletedloan',
    'view_archivedpayment',
    'view_activitylog',
    'view_loginattempt',
)
VIEWER_CODENAMES = (
    'view_loan',
    'view_payment',
    'view_deletedloan',
    'view_archivedpayment',
    'view_activitylog',
    'view_loginattempt',
)


class UserGuardError(Exception):
    """A user-management change that would lock the organisation out of admin."""


def ensure_role_groups():
    """Create the three staff groups and refresh the permissions they carry."""
    from django.apps import apps as django_apps
    from django.contrib.auth.management import create_permissions

    for label in ('auth', 'loans'):
        create_permissions(django_apps.get_app_config(label), verbosity=0)

    loan_perms = Permission.objects.filter(content_type__app_label='loans')
    auth_perms = Permission.objects.filter(
        content_type__app_label='auth',
        content_type__model__in=['user', 'group'],
    )

    admin, _ = Group.objects.get_or_create(name=ROLE_ADMIN)
    admin.permissions.set([*loan_perms, *auth_perms])

    officer, _ = Group.objects.get_or_create(name=ROLE_OFFICER)
    officer.permissions.set(loan_perms.filter(codename__in=OFFICER_CODENAMES))

    viewer, _ = Group.objects.get_or_create(name=ROLE_VIEWER)
    viewer.permissions.set(loan_perms.filter(codename__in=VIEWER_CODENAMES))
    return admin, officer, viewer


def _group_names(user):
    cached = getattr(user, '_role_group_names', None)
    if cached is None:
        cached = {group.name for group in user.groups.all()}
        user._role_group_names = cached
    return cached


def clear_role_cache(user):
    if hasattr(user, '_role_group_names'):
        del user._role_group_names


def role_for(user):
    """Label shown in the shell and on the user list."""
    if not getattr(user, 'is_authenticated', False):
        return ''
    names = _group_names(user)
    if user.is_superuser or ROLE_ADMIN in names:
        return ROLE_ADMIN
    if ROLE_OFFICER in names:
        return ROLE_OFFICER
    if ROLE_VIEWER in names:
        return ROLE_VIEWER
    if user.is_staff:
        return ROLE_STAFF
    return ROLE_BORROWER


def assigned_role(user):
    """Role value for the edit form. Legacy staff with no group stay officers."""
    names = _group_names(user)
    if ROLE_ADMIN in names or user.is_superuser:
        return ROLE_ADMIN
    if ROLE_OFFICER in names:
        return ROLE_OFFICER
    if ROLE_VIEWER in names:
        return ROLE_VIEWER
    if user.is_staff:
        return ROLE_OFFICER
    return ROLE_BORROWER


def can_manage_users(user):
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return bool(user.is_staff and ROLE_ADMIN in _group_names(user))


def can_write_loans(user):
    """Loan officers and admins can change the book. Viewers cannot.

    Staff accounts that predate the role groups keep write access.
    """
    if not getattr(user, 'is_authenticated', False) or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    names = _group_names(user)
    if ROLE_ADMIN in names or ROLE_OFFICER in names:
        return True
    if ROLE_VIEWER in names:
        return False
    return True


def reject_read_only(request, fallback='dashboard'):
    """Return a redirect when a viewer posts a change, otherwise None."""
    from django.contrib import messages
    from django.shortcuts import redirect

    if can_write_loans(request.user):
        return None
    messages.error(request, 'Your role can view records but cannot change them.')
    return redirect(fallback)


def apply_role(user, role, *, is_superuser):
    """Replace role groups and the staff/superuser flags. Caller saves afterwards."""
    if role not in dict(ASSIGNABLE_ROLES):
        raise UserGuardError('Choose a valid role.')
    ensure_role_groups()
    role_groups = list(Group.objects.filter(name__in=ROLE_NAMES))
    if role_groups:
        user.groups.remove(*role_groups)
    staff_by_role = {
        ROLE_ADMIN: True,
        ROLE_OFFICER: True,
        ROLE_VIEWER: True,
        ROLE_BORROWER: False,
    }
    user.is_superuser = bool(is_superuser)
    user.is_staff = staff_by_role[role] or user.is_superuser
    group_name = {
        ROLE_ADMIN: ROLE_ADMIN,
        ROLE_OFFICER: ROLE_OFFICER,
        ROLE_VIEWER: ROLE_VIEWER,
    }.get(role)
    if group_name:
        user.groups.add(Group.objects.get(name=group_name))
    clear_role_cache(user)


def _lock_superusers():
    return User.objects.select_for_update().filter(is_superuser=True)


def assert_can_edit(actor, target, *, is_active, is_superuser, role):
    """Raise UserGuardError when the edit would remove the last admin path."""
    if target.is_superuser and not actor.is_superuser and actor.pk != target.pk:
        raise UserGuardError('Only a superuser can change another superuser.')

    if target.is_superuser and not is_superuser:
        if _lock_superusers().exclude(pk=target.pk).count() == 0:
            raise UserGuardError('The last superuser cannot be demoted.')

    if target.is_superuser and target.is_active and not is_active:
        if _lock_superusers().filter(is_active=True).exclude(pk=target.pk).count() == 0:
            raise UserGuardError('The last active superuser cannot be deactivated.')

    if actor.pk == target.pk:
        if not is_active:
            raise UserGuardError('You cannot deactivate your own account.')
        if target.is_superuser and not is_superuser:
            raise UserGuardError('You cannot remove your own superuser status.')
        if role != ROLE_ADMIN:
            raise UserGuardError('You cannot remove your own admin access.')


def assert_can_delete(actor, target):
    if target.is_superuser and not actor.is_superuser:
        raise UserGuardError('Only a superuser can delete a superuser.')
    if target.is_superuser and _lock_superusers().exclude(pk=target.pk).count() == 0:
        raise UserGuardError('The last superuser cannot be deleted.')
    if actor.pk == target.pk:
        raise UserGuardError('You cannot delete your own account.')


def assert_can_deactivate(actor, target):
    assert_can_edit(
        actor,
        target,
        is_active=False,
        is_superuser=target.is_superuser,
        role=assigned_role(target) if actor.pk == target.pk else ROLE_ADMIN,
    )
