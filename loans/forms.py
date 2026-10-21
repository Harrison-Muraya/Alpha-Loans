from calendar import monthrange

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Loan


def month_end(day=None):
    day = day or timezone.localdate()
    last_day = monthrange(day.year, day.month)[1]
    return day.replace(day=last_day)


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
            self.add_error('confirm_password', "Passwords don't match")

        if password:
            user = User(
                username=cleaned_data.get('username') or '',
                email=cleaned_data.get('email') or '',
                first_name=cleaned_data.get('first_name') or '',
                last_name=cleaned_data.get('last_name') or '',
            )
            try:
                validate_password(password, user)
            except ValidationError as exc:
                self.add_error('password', exc)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['borrower', 'amount', 'issue_date', 'due_date']
        widgets = {
            'borrower': forms.Select(attrs={
                'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full'
            }),
            'amount': forms.NumberInput(attrs={
                'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full',
                'placeholder': 'Enter amount',
            }),
            'issue_date': forms.DateInput(attrs={
                'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full',
                'type': 'date',
            }),
            'due_date': forms.DateInput(attrs={
                'class': 'px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 w-full',
                'type': 'date',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['borrower'].queryset = User.objects.filter(is_staff=False)
        self.fields['issue_date'].required = False
        self.fields['issue_date'].initial = timezone.localdate()
        self.fields['due_date'].required = False
        self.fields['due_date'].initial = month_end()

    def clean_issue_date(self):
        issue_date = self.cleaned_data.get('issue_date')
        return issue_date or timezone.localdate()

    def clean_due_date(self):
        due_date = self.cleaned_data.get('due_date')
        if not due_date:
            due_date = month_end()
        return due_date


class LoanUpdateForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['interest_rate', 'penalty_rate', 'status', 'is_paid']
        widgets = {
            'interest_rate': forms.NumberInput(attrs={
                'class': 'px-2 py-1 border border-gray-300 rounded w-20', 'step': '0.01'
            }),
            'penalty_rate': forms.NumberInput(attrs={
                'class': 'px-2 py-1 border border-gray-300 rounded w-20', 'step': '0.01'
            }),
            'status': forms.Select(attrs={'class': 'px-2 py-1 border border-gray-300 rounded text-sm'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }
