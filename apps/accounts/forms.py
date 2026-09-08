from django.contrib.auth.forms import (
    AdminUserCreationForm as DjangoAdminUserCreationForm,
)
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm

from apps.accounts.models import User


class AdminUserCreationForm(DjangoAdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name")


class UserChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
