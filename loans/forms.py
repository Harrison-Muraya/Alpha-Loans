import secrets
from calendar import monthrange

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Loan
from .roles import (
    ASSIGNABLE_ROLES,
    ROLE_BORROWER,
    UserGuardError,
    apply_role,
    assert_can_edit,
    assigned_role,
)

INPUT = 'input'
PASSWORD = {'class': INPUT, 'autocomplete': 'new-password', 'placeholder': '••••••••'}


def month_end(day=None):
    day = day or timezone.localdate()
    last_day = monthrange(day.year, day.month)[1]
    return day.replace(day=last_day)


class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs=PASSWORD))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs=PASSWORD))

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': INPUT, 'placeholder': 'Choose a username', 'autocomplete': 'username',
            }),
            'first_name': forms.TextInput(attrs={
                'class': INPUT, 'placeholder': 'First name', 'autocomplete': 'given-name',
            }),
            'last_name': forms.TextInput(attrs={
                'class': INPUT, 'placeholder': 'Last name', 'autocomplete': 'family-name',
            }),
            'email': forms.EmailInput(attrs={
                'class': INPUT, 'placeholder': 'you@example.com', 'autocomplete': 'email',
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
            'borrower': forms.Select(attrs={'class': INPUT}),
            'amount': forms.NumberInput(attrs={
                'class': INPUT, 'placeholder': 'Amount in KES', 'min': '0.01', 'step': '0.01',
            }),
            'issue_date': forms.DateInput(attrs={'class': INPUT, 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': INPUT, 'type': 'date'}),
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
            'interest_rate': forms.NumberInput(attrs={'class': INPUT, 'step': '0.01'}),
            'penalty_rate': forms.NumberInput(attrs={'class': INPUT, 'step': '0.01'}),
            'status': forms.Select(attrs={'class': INPUT}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'check'}),
        }


def generate_password():
    """A random password that satisfies Django's default validators."""
    return 'Kx' + secrets.token_urlsafe(12) + '9!'


class UserAdminForm(forms.ModelForm):
    role = forms.ChoiceField(choices=ASSIGNABLE_ROLES, widget=forms.Select(attrs={'class': INPUT}))
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs=PASSWORD),
        help_text='At least 8 characters. Cannot be entirely numeric.',
    )
    confirm_password = forms.CharField(
        required=False,
        label='Confirm password',
        widget=forms.PasswordInput(attrs=PASSWORD),
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'is_superuser']
        widgets = {
            'username': forms.TextInput(attrs={'class': INPUT, 'autocomplete': 'off'}),
            'first_name': forms.TextInput(attrs={'class': INPUT, 'autocomplete': 'off'}),
            'last_name': forms.TextInput(attrs={'class': INPUT, 'autocomplete': 'off'}),
            'email': forms.EmailInput(attrs={'class': INPUT, 'autocomplete': 'off'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'check'}),
            'is_superuser': forms.CheckboxInput(attrs={'class': 'check'}),
        }

    def __init__(self, actor, *args, instance=None, **kwargs):
        self.actor = actor
        self.creating = instance is None or instance.pk is None
        super().__init__(*args, instance=instance, **kwargs)
        if not actor.is_superuser:
            self.fields.pop('is_superuser')
        if not self.creating:
            self.fields['role'].initial = assigned_role(instance)
            self.fields['password'].help_text = 'Leave blank to keep the current password.'
        else:
            self.fields['role'].initial = ROLE_BORROWER

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        password = cleaned.get('password') or ''
        confirm = cleaned.get('confirm_password') or ''
        if self.creating and not password:
            self.add_error('password', 'Set an initial password.')
        elif password or confirm:
            if password != confirm:
                self.add_error('confirm_password', "Passwords don't match")
            else:
                candidate = self.instance or User()
                candidate.username = cleaned.get('username') or ''
                candidate.email = cleaned.get('email') or ''
                candidate.first_name = cleaned.get('first_name') or ''
                candidate.last_name = cleaned.get('last_name') or ''
                try:
                    validate_password(password, candidate)
                except ValidationError as exc:
                    self.add_error('password', exc)
        if not self.actor.is_superuser:
            cleaned['is_superuser'] = bool(self.instance.pk and self.instance.is_superuser)
        else:
            cleaned['is_superuser'] = bool(cleaned.get('is_superuser'))
        if self.instance.pk:
            try:
                assert_can_edit(
                    self.actor,
                    self.instance,
                    is_active=cleaned.get('is_active', True),
                    is_superuser=cleaned['is_superuser'],
                    role=cleaned.get('role'),
                )
            except UserGuardError as exc:
                raise ValidationError(str(exc)) from exc
        elif cleaned['is_superuser'] and not self.actor.is_superuser:
            raise ValidationError('Only a superuser can create another superuser.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        user.is_active = bool(self.cleaned_data.get('is_active', True))
        if commit:
            # Groups need a primary key, so the row is saved before apply_role.
            user.save()
            apply_role(
                user,
                self.cleaned_data['role'],
                is_superuser=self.cleaned_data['is_superuser'],
            )
            user.save(update_fields=['is_staff', 'is_superuser', 'is_active'])
        return user


class AdminPasswordForm(forms.Form):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs=PASSWORD),
    )
    confirm_password = forms.CharField(
        required=False,
        label='Confirm password',
        widget=forms.PasswordInput(attrs=PASSWORD),
    )
    generate = forms.BooleanField(
        required=False,
        label='Generate a temporary password',
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('generate'):
            cleaned['password'] = generate_password()
            cleaned['generated'] = True
            return cleaned
        password = cleaned.get('password') or ''
        confirm = cleaned.get('confirm_password') or ''
        if not password:
            self.add_error('password', 'Enter a password, or generate one.')
            return cleaned
        if password != confirm:
            self.add_error('confirm_password', "Passwords don't match")
            return cleaned
        try:
            validate_password(password, self.user)
        except ValidationError as exc:
            self.add_error('password', exc)
        cleaned['generated'] = False
        return cleaned


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': INPUT, 'autocomplete': 'given-name'}),
            'last_name': forms.TextInput(attrs={'class': INPUT, 'autocomplete': 'family-name'}),
            'email': forms.EmailInput(attrs={'class': INPUT, 'autocomplete': 'email'}),
        }
