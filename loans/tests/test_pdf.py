from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from loans.pdf_export import PdfExportError, _offline_url_fetcher
from loans.tests.helpers import make_loan, make_payment, make_user


class PdfStatementTemplateTests(TestCase):
    def test_statement_template_renders_without_profile_model(self):
        borrower = make_user('pdf-borrower', password='x')
        borrower.email = 'borrower@example.com'
        borrower.save(update_fields=['email'])
        loan = make_loan(borrower, amount='1000', interest='0', penalty='0')
        make_payment(loan, '250')
        html = render_to_string(
            'loans/pdf_statement.html',
            {
                'borrower': borrower,
                'loans': [loan],
                'total_loaned': Decimal('1000'),
                'total_due': Decimal('1000'),
                'total_paid': Decimal('250'),
                'total_balance': Decimal('750'),
                'generated_at': timezone.now(),
            },
        )
        self.assertIn('LOAN STATEMENT', html)
        self.assertIn('@pdf-borrower', html)
        self.assertIn('borrower@example.com', html)
        self.assertNotIn('fonts.googleapis.com', html)
        self.assertNotIn('borrower.profile', html)


class OfflineFetcherTests(TestCase):
    def test_blocks_remote_http_urls(self):
        with self.assertRaises(PdfExportError):
            _offline_url_fetcher('https://fonts.googleapis.com/css2?family=DM+Sans')


class ExportBorrowerPdfViewTests(TestCase):
    def setUp(self):
        self.staff = make_user('pdf-staff', staff=True)
        self.borrower = make_user('pdf-view-borrower')
        make_loan(self.borrower, amount='500', interest='0', penalty='0')
        self.client.force_login(self.staff)
        self.url = reverse('export_borrower_pdf', args=[self.borrower.id])

    def test_pdf_engine_failure_redirects_with_message(self):
        with patch(
            'loans.views.generate_borrower_statement',
            side_effect=PdfExportError('PDF engine is not available on this server.'),
        ):
            response = self.client.get(self.url)
        self.assertRedirects(response, reverse('borrower_detail', args=[self.borrower.id]))
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('PDF engine' in str(m) for m in messages))
