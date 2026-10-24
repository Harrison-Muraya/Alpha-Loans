from django.urls import path
from django.contrib.auth import views as auth_views
from . import reports, user_admin, views


urlpatterns = [
    path('', views.user_login, name='login'),
    path('register/', views.user_register, name='register'),
    path('logout/', views.user_logout, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('loans/', views.loan_list, name='loan_list'),
    path('manage/', views.manage_loans, name='manage_loans'),
    path('manage/borrower_summary/', views.borrower_summary, name='borrower_summary'),
    path('manage/borrower_summary/<int:user_id>/', views.borrower_detail, name='borrower_detail'),
    path('manage/payments/', views.record_borrower_payment, name='record_borrower_payment'),
    path(
        'manage/borrowers/<int:user_id>/pay/',
        views.record_borrower_payment,
        name='record_borrower_payment_for',
    ),
    path('update/<int:loan_id>/', views.update_loan, name='update_loan'),
    path('delete/<int:loan_id>/', views.delete_loan, name='delete_loan'),
    path('payment/<int:loan_id>/', views.record_payment, name='record_payment'),
 
    # Exports
    path('export/borrower/<int:user_id>/pdf/', views.export_borrower_pdf, name='export_borrower_pdf'),
    path('export/loans/excel/', views.export_loans_excel, name='export_loans_excel'),
 
    # Archive & activity
    path('manage/deleted/', views.deleted_loans_archive, name='deleted_loans_archive'),
    path('manage/activity/', views.activity_log, name='activity_log'),

    # User management (admins and superusers)
    path('manage/users/', user_admin.user_list, name='user_list'),
    path('manage/users/new/', user_admin.user_create, name='user_create'),
    path('manage/users/export/', user_admin.export_users_csv, name='export_users_csv'),
    path('manage/users/<int:user_id>/', user_admin.user_detail, name='user_detail'),
    path('manage/users/<int:user_id>/edit/', user_admin.user_edit, name='user_edit'),
    path('manage/users/<int:user_id>/password/', user_admin.user_set_password, name='user_set_password'),
    path('manage/users/<int:user_id>/deactivate/', user_admin.user_deactivate, name='user_deactivate'),
    path('manage/users/<int:user_id>/reactivate/', user_admin.user_reactivate, name='user_reactivate'),
    path('manage/users/<int:user_id>/unlock/', user_admin.user_unlock, name='user_unlock'),
    path('manage/users/<int:user_id>/delete/', user_admin.user_delete, name='user_delete'),

    path('account/', user_admin.profile, name='profile'),

    path('export/loans/csv/', reports.export_loans_csv, name='export_loans_csv'),
    path('export/payments/csv/', reports.export_payments_csv, name='export_payments_csv'),
    path('manage/activity/export/', reports.export_activity_csv, name='export_activity_csv'),
    path('manage/deleted/export/', reports.export_deleted_csv, name='export_deleted_csv'),
 
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