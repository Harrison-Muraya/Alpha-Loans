from django.urls import path
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
]