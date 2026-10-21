from django.db import migrations


def sync_paid_status(apps, schema_editor):
    """Make status='paid' and is_paid describe the same loans."""
    Loan = apps.get_model('loans', 'Loan')
    Loan.objects.filter(status='paid', is_paid=False).update(is_paid=True)
    Loan.objects.filter(is_paid=True).exclude(status='paid').update(status='paid')


class Migration(migrations.Migration):

    dependencies = [
        ('loans', '0003_review_fixes'),
    ]

    operations = [
        migrations.RunPython(sync_paid_status, migrations.RunPython.noop),
    ]
