from django.test import TestCase
from django.urls import reverse

from loans.models import Loan, Payment
from loans.tests.helpers import make_loan, make_user


class PermissionTests(TestCase):
    def setUp(self):
        self.staff = make_user('perm-staff', staff=True)
        self.borrower = make_user('perm-borrower')
        self.loan = make_loan(self.borrower)

    def test_anonymous_users_are_sent_to_login(self):
        for url in (
            reverse('dashboard'),
            reverse('loan_list'),
            reverse('manage_loans'),
            reverse('borrower_summary'),
            reverse('borrower_detail', args=[self.borrower.id]),
            reverse('activity_log'),
            reverse('deleted_loans_archive'),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn('next=', response['Location'])

    def test_borrower_cannot_use_staff_views(self):
        self.client.force_login(self.borrower)
        urls = [
            reverse('manage_loans'),
            reverse('borrower_summary'),
            reverse('borrower_detail', args=[self.borrower.id]),
            reverse('deleted_loans_archive'),
            reverse('activity_log'),
            reverse('export_loans_excel'),
            reverse('export_borrower_pdf', args=[self.borrower.id]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn('next=', response['Location'])

        response = self.client.post(
            reverse('record_payment', args=[self.loan.id]),
            {'amount': '10', 'method': 'cash'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())

    def test_borrower_can_open_their_own_list_and_not_the_staff_total(self):
        self.client.force_login(self.borrower)
        response = self.client.get(reverse('loan_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1680.00')

    def test_staff_can_open_management_pages(self):
        self.client.force_login(self.staff)
        for url in (
            reverse('manage_loans'),
            reverse('borrower_summary'),
            reverse('borrower_detail', args=[self.borrower.id]),
            reverse('dashboard'),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_borrower_detail_url_typo_is_gone(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            reverse('borrower_detail', args=[self.borrower.id]),
            f'/manage/borrower_summary/{self.borrower.id}/',
        )
        typo = self.client.get(f'/manange/borrower_summary/{self.borrower.id}/')
        self.assertEqual(typo.status_code, 404)
        fixed = self.client.get(reverse('borrower_detail', args=[self.borrower.id]))
        self.assertEqual(fixed.status_code, 200)
