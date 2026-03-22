from django.urls import path
from django.contrib.auth import views as auth_views
from . import views


urlpatterns = [
    path('', views.user_login, name='login'),
    path('register/', views.user_register, name='register'),
    path('logout/', views.user_logout, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('loans/', views.loan_list, name='loan_list'),
    path('manage/', views.manage_loans, name='manage_loans'),
    path('manage/borrower_summary/', views.borrower_summary, name='borrower_summary'),
    path('manange/borrower_summary/<int:user_id>/', views.borrower_detail, name='borrower_detail'),
    path('update/<int:loan_id>/', views.update_loan, name='update_loan'),
    path('delete/<int:loan_id>/', views.delete_loan, name='delete_loan'),
    path('payment/<int:loan_id>/', views.record_payment, name='record_payment'),
 
    # Exports
    path('export/borrower/<int:user_id>/pdf/', views.export_borrower_pdf, name='export_borrower_pdf'),
    path('export/loans/excel/', views.export_loans_excel, name='export_loans_excel'),
 
    # Archive & activity
    path('manage/deleted/', views.deleted_loans_archive, name='deleted_loans_archive'),
    path('manage/activity/', views.activity_log, name='activity_log'),
 
    # Password reset
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='loans/password_reset.html',
        email_template_name='loans/password_reset_email.txt',
        html_email_template_name='loans/password_reset_email.html',
        subject_template_name='loans/password_reset_subject.txt',
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='loans/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='loans/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='loans/password_reset_complete.html',
    ), name='password_reset_complete'),
]