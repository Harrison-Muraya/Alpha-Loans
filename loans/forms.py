from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Loan

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
