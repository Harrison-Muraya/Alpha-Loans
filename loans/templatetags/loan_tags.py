from django import template

from loans.roles import role_for

register = template.Library()


@register.filter
def role_name(user):
    return role_for(user)


@register.filter
def initials(user):
    name = (user.get_full_name() or user.username or '?').strip()
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return name[:2].upper()
