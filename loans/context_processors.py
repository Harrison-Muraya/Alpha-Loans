from .roles import can_manage_users, can_write_loans, role_for


def shell(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {
            'nav_role': '',
            'can_manage_users': False,
            'can_write_loans': False,
        }
    return {
        'nav_role': role_for(user),
        'can_manage_users': can_manage_users(user),
        'can_write_loans': can_write_loans(user),
    }
