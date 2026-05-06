from django import forms
from django.contrib.auth import get_user_model
from .models import Invite, ACCOUNT_TYPE_CHOICES

User = get_user_model()

class RegisterForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirm Password")
    account_type = forms.ChoiceField(choices=ACCOUNT_TYPE_CHOICES, initial='professional')

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'title', 'company', 'bio', 'account_type']

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user

class InviteForm(forms.ModelForm):
    class Meta:
        model = Invite
        fields = ['email']

class PostForm(forms.Form):
    content = forms.CharField(widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Share an update with your network...'}), max_length=1000)
