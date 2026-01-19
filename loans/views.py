from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db.models import Sum, Count
from django.contrib.auth.models import User
from datetime import date
from .models import Loan
from .forms import LoanForm, LoanUpdateForm, UserRegistrationForm

def user_register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            login(request, user)
            messages.success(request, 'Registration successful! Welcome to Loan Manager.')
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'loans/register.html', {'form': form})

def user_login(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid credentials')
    
    return render(request, 'loans/login.html')

def user_logout(request):
    logout(request)
    return redirect('login')

@login_required
def dashboard(request):
    if request.user.is_staff:
        loans = Loan.objects.all()
    else:
        loans = Loan.objects.filter(borrower=request.user)
    
    total_loaned = loans.aggregate(Sum('amount'))['amount__sum'] or 0
    active_loans = loans.filter(is_paid=False).count()
    
    total_expected = sum([loan.total_amount_due() for loan in loans])
    
    context = {
        'total_loaned': total_loaned,
        'total_expected': total_expected,
        'active_loans': active_loans,
    }
    return render(request, 'loans/dashboard.html', context)

@login_required
def loan_list(request):
    if request.user.is_staff:
        loans = Loan.objects.all().select_related('borrower')
    else:
        loans = Loan.objects.filter(borrower=request.user)
        total_expected = sum([loan.total_amount_due() for loan in loans])
    
    context = {
        'loans': loans,
        'total_expected': total_expected if not request.user.is_staff else None,
        }
    return render(request, 'loans/loan_list.html', context)

@login_required
@user_passes_test(lambda u: u.is_staff)
def manage_loans(request):
    if request.method == 'POST':
        form = LoanForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Loan added successfully!')
            return redirect('manage_loans')
    else:
        form = LoanForm()
    
    loans = Loan.objects.all().select_related('borrower')
    context = {
        'form': form,
        'loans': loans,
    }
    return render(request, 'loans/manage_loans.html', context)

@login_required
@user_passes_test(lambda u: u.is_staff)
def update_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    
    if request.method == 'POST':
        form = LoanUpdateForm(request.POST, instance=loan)
        if form.is_valid():
            form.save()
            messages.success(request, 'Loan updated successfully!')
            return redirect('manage_loans')
    
    return redirect('manage_loans')

@login_required
@user_passes_test(lambda u: u.is_staff)
def delete_loan(request, loan_id):
    loan = get_object_or_404(Loan, id=loan_id)
    loan.delete()
    messages.success(request, 'Loan deleted successfully!')
    return redirect('manage_loans')
