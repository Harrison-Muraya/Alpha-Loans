from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Loan
from datetime import datetime
from calendar import monthrange

class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
            'placeholder': '••••••••'
        })
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
            'placeholder': '••••••••'
        })
    )
    
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
                'placeholder': 'Choose a username'
            }),
            'first_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
                'placeholder': 'Enter first name'
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
                'placeholder': 'Enter last name'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent',
                'placeholder': 'your@email.com'
            }),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        
        if password and confirm_password and password != confirm_password:
            raise forms.ValidationError("Passwords don't match")
        
        return cleaned_data

class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['borrower', 'amount', 'due_date']
        widgets = {
            'borrower': forms.Select(attrs={'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full'}),
            'amount': forms.NumberInput(attrs={'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full', 'placeholder': 'Enter amount'}),
            'due_date': forms.DateInput(attrs={'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full', 'type': 'date'}),
        }
    
    def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['borrower'].queryset = User.objects.filter(is_staff=False)
            
            # Make due_date optional and set default to end of current month
            self.fields['due_date'].required = False
            
            # Set initial value to last day of current month
            today = datetime.now()
            last_day = monthrange(today.year, today.month)[1]
            end_of_month = datetime(today.year, today.month, last_day).date()
            self.fields['due_date'].initial = end_of_month
    
    def clean_due_date(self):
        due_date = self.cleaned_data.get('due_date')
        
        # If no due_date provided, set to end of current month
        if not due_date:
            today = datetime.now()
            last_day = monthrange(today.year, today.month)[1]
            due_date = datetime(today.year, today.month, last_day).date()
        
        return due_date

class LoanUpdateForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['interest_rate', 'penalty_rate', 'status', 'is_paid']
        widgets = {
            'interest_rate': forms.NumberInput(attrs={'class': 'px-2 py-1 border border-gray-300 rounded w-20', 'step': '0.01'}),
            'penalty_rate': forms.NumberInput(attrs={'class': 'px-2 py-1 border border-gray-300 rounded w-20', 'step': '0.01'}),
            'status': forms.Select(attrs={'class': 'px-2 py-1 border border-gray-300 rounded text-sm'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }