from django.apps import AppConfig


class LoansConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'loans'

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(_ensure_roles, sender=self)


def _ensure_roles(sender, **kwargs):
    from loans.roles import ensure_role_groups

    ensure_role_groups()
