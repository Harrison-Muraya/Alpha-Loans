from django.db import connection


def lock_rows(queryset):
    """Lock loan rows on PostgreSQL. SQLite has no row locks, so local dev is unchanged.

    of=('self',) avoids locking the joined borrower row.
    """
    if not connection.features.has_select_for_update:
        return queryset
    if connection.features.has_select_for_update_of:
        return queryset.select_for_update(of=('self',))
    return queryset.select_for_update()
