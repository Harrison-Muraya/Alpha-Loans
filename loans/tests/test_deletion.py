from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from loans.models import ArchivedPayment, DeletedLoan, Loan, Payment
from loans.tests.helpers import make_loan, make_payment, make_user


class DeleteLoanTests(TestCase):
    def setUp(self):
        self.staff = make_user('del-staff', staff=True)
        self.borrower = make_user('del-borrower')
        self.loan = make_loan(self.borrower)
        self.payment = make_payment(self.loan, '80', user=self.staff)
        self.client.force_login(self.staff)

    def test_get_does_not_delete(self):
        response = self.client.get(reverse('delete_loan', args=[self.loan.id]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())

    def test_post_archives_loan_and_payments(self):
        response = self.client.post(reverse('delete_loan', args=[self.loan.id]))
        self.assertRedirects(response, reverse('manage_loans'))
        self.assertFalse(Loan.objects.filter(pk=self.loan.pk).exists())
        self.assertFalse(Payment.objects.filter(pk=self.payment.pk).exists())
        archived = DeletedLoan.objects.get(original_loan_id=self.loan.id)
        self.assertEqual(archived.deleted_by, self.staff)
        snapshot = archived.payments.get()
        self.assertIsInstance(snapshot, ArchivedPayment)
        self.assertEqual(snapshot.amount, self.payment.amount)
        self.assertEqual(snapshot.original_payment_id, self.payment.id)
        self.assertEqual(snapshot.recorded_by_username, self.staff.username)

    def test_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.post(reverse('delete_loan', args=[self.loan.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())

    def test_borrower_cannot_delete(self):
        self.client.force_login(self.borrower)
        response = self.client.post(reverse('delete_loan', args=[self.loan.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())

    def test_bulk_delete_archives_payments(self):
        other = make_loan(self.borrower, amount='50', interest='0', penalty='0')
        make_payment(other, '10')
        response = self.client.post(
            reverse('borrower_detail', args=[self.borrower.id]),
            {'loan_ids': [self.loan.id, other.id], 'reason': 'Duplicate'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Loan.objects.filter(borrower=self.borrower).count(), 0)
        self.assertEqual(DeletedLoan.objects.count(), 2)
        self.assertEqual(ArchivedPayment.objects.count(), 2)
        self.assertEqual(
            DeletedLoan.objects.get(original_loan_id=self.loan.id).reason,
            'Duplicate',
        )

    def test_instance_delete_archives_payments(self):
        loan_id = self.loan.id
        self.loan.delete()
        self.assertFalse(Loan.objects.filter(pk=loan_id).exists())
        self.assertFalse(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertEqual(ArchivedPayment.objects.count(), 1)
        self.assertEqual(DeletedLoan.objects.get().original_loan_id, loan_id)

    def test_queryset_delete_also_archives(self):
        Loan.objects.filter(pk=self.loan.pk).delete()
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(ArchivedPayment.objects.count(), 1)

    def test_borrower_with_loans_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.borrower.delete()
        self.assertTrue(User.objects.filter(pk=self.borrower.pk).exists())
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())

    def test_borrower_can_be_deleted_after_loans_are_archived(self):
        self.loan.archive(deleted_by=self.staff, reason='Closed')
        username = self.borrower.username
        self.borrower.delete()
        archived = DeletedLoan.objects.get()
        self.assertIsNone(archived.borrower_id)
        self.assertEqual(archived.borrower_username, username)
        self.assertEqual(archived.payments.count(), 1)
