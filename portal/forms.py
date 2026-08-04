from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.forms.forms import NON_FIELD_ERRORS

from .constants import AUTH_METHOD_ACCOUNT, AUTH_METHOD_TOKEN
from .models import Service
from .service_options import get_service_option_choices, normalize_service_features


User = get_user_model()


class PasswordReuseGuardMixin:
    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        if password and self.user.check_password(password):
            raise ValidationError("Новый пароль должен отличаться от текущего.")
        return password


class PortalAuthenticationForm(forms.Form):
    error_messages = {
        "invalid_credentials": "Мы вас не узнаем",
        "inactive": "Эта учётная запись отключена.",
    }

    auth_method = forms.CharField(
        initial=AUTH_METHOD_ACCOUNT,
        widget=forms.HiddenInput(),
    )
    username = forms.CharField(
        label="Логин",
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "placeholder": "Введите логин",
            }
        ),
    )
    password = forms.CharField(
        label="Пароль",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Введите пароль",
            }
        ),
    )
    token = forms.CharField(
        label="Токен",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Вставьте токен доступа",
                "spellcheck": "false",
            }
        ),
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        self.auth_method_used = AUTH_METHOD_ACCOUNT
        super().__init__(*args, **kwargs)

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise ValidationError(
                self.error_messages["inactive"],
                code="inactive",
            )

    def clean(self):
        cleaned_data = super().clean()
        auth_method = cleaned_data.get("auth_method") or AUTH_METHOD_ACCOUNT
        self.auth_method_used = auth_method

        if auth_method == AUTH_METHOD_TOKEN:
            token = (cleaned_data.get("token") or "").strip()
            if not token:
                self.add_error("token", "Введите токен.")
                return cleaned_data
            user = User.find_by_login_token(token)
            if user is None:
                raise forms.ValidationError(self.error_messages["invalid_credentials"])
            self.confirm_login_allowed(user)
            self.user_cache = user
            return cleaned_data

        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        missing_credentials = False
        if not username:
            self.add_error("username", "Введите логин.")
            missing_credentials = True
        if not password:
            self.add_error("password", "Введите пароль.")
            missing_credentials = True
        if missing_credentials:
            return cleaned_data

        self.user_cache = authenticate(self.request, username=username, password=password)
        if self.user_cache is None:
            raise forms.ValidationError(self.error_messages["invalid_credentials"])
        self.confirm_login_allowed(self.user_cache)
        return cleaned_data

    def get_user(self):
        return self.user_cache

    def get_auth_method(self):
        return self.auth_method_used

    def has_complete_auth_input(self) -> bool:
        auth_method = (self.data.get("auth_method") or self.auth_method_used or AUTH_METHOD_ACCOUNT).strip()
        if auth_method == AUTH_METHOD_TOKEN:
            return bool((self.data.get("token") or "").strip())
        return bool((self.data.get("username") or "").strip() and (self.data.get("password") or "").strip())

    def clear_missing_input_errors(self) -> None:
        if not getattr(self, "_errors", None):
            return
        for field_name in ("username", "password", "token", NON_FIELD_ERRORS):
            self._errors.pop(field_name, None)

    def get_username_snapshot(self) -> str:
        if self.user_cache is not None:
            return self.user_cache.username
        if self.auth_method_used == AUTH_METHOD_TOKEN:
            return User.lookup_username_by_login_token((self.data.get("token") or "").strip())
        return (self.data.get("username") or "").strip()


class FirstPasswordChangeForm(PasswordReuseGuardMixin, SetPasswordForm):
    new_password1 = forms.CharField(
        label="Новый пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Придумайте новый пароль"}
        ),
    )
    new_password2 = forms.CharField(
        label="Повторите пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Повторите новый пароль"}
        ),
    )


class ManagedUserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Временный пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Повторите пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    services = forms.ModelMultipleChoiceField(
        label="Доступные сервисы",
        queryset=Service.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ["username", "full_name", "is_active", "is_staff", "services"]
        labels = {
            "username": "Логин",
            "full_name": "ФИО",
            "is_active": "Учётная запись активна",
            "is_staff": "Права администратора",
        }

    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["services"].queryset = Service.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password1")
        if cleaned_data.get("password1") != cleaned_data.get("password2"):
            raise forms.ValidationError("Пароли не совпадают.")
        if password:
            password_validation.validate_password(password, self.instance)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = self.cleaned_data["is_staff"]
        user.is_superuser = False
        user.must_change_password = True
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class ManagedUserUpdateForm(forms.ModelForm):
    set_temporary_password = forms.BooleanField(required=False, widget=forms.HiddenInput())
    new_password1 = forms.CharField(
        label="Новый временный пароль",
        strip=False,
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Повторите пароль",
        strip=False,
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    services = forms.ModelMultipleChoiceField(
        label="Доступные сервисы",
        queryset=Service.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ["username", "full_name", "is_active", "is_staff", "services"]
        labels = {
            "username": "Логин",
            "full_name": "ФИО",
            "is_active": "Учётная запись активна",
            "is_staff": "Права администратора",
        }

    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["services"].queryset = Service.objects.filter(is_active=True)
        can_edit_identity = bool(getattr(self.actor, "is_staff", False))
        if self.instance.pk and not can_edit_identity:
            self.fields["username"].disabled = True
            self.fields["full_name"].disabled = True

    def clean(self):
        cleaned_data = super().clean()
        set_temporary_password = cleaned_data.get("set_temporary_password")
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")
        if set_temporary_password or password1 or password2:
            if password1 != password2:
                raise forms.ValidationError("Новые пароли не совпадают.")
            if not password1:
                raise forms.ValidationError("Введите новый временный пароль.")
            if self.instance.check_password(password1):
                raise forms.ValidationError("Новый временный пароль должен отличаться от текущего.")
            password_validation.validate_password(password1, self.instance)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password1 = self.cleaned_data.get("new_password1")
        if password1:
            user.set_password(password1)
            user.must_change_password = True
        if commit:
            user.save()
            self.save_m2m()
        return user

    @property
    def password_change_requested(self) -> bool:
        return bool(self.data.get("set_temporary_password") or self.initial.get("set_temporary_password"))


class AccountPasswordChangeForm(PasswordReuseGuardMixin, PasswordChangeForm):
    old_password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "placeholder": "Введите текущий пароль"}
        ),
    )
    new_password1 = forms.CharField(
        label="Новый пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Придумайте новый пароль"}
        ),
    )
    new_password2 = forms.CharField(
        label="Повторите новый пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Повторите новый пароль"}
        ),
    )


class ManagedServiceForm(forms.ModelForm):
    managed_features = forms.MultipleChoiceField(
        label="Поддерживаемые функции nginx",
        required=False,
        choices=[],
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Service
        fields = [
            "title",
            "kind",
            "domain",
            "upstream_url",
            "external_url",
            "description",
            "icon_key",
            "sort_order",
            "is_active",
            "is_enabled",
            "precheck_url",
            "cert_fullchain_path",
            "cert_privkey_path",
            "check_type",
            "health_host",
            "health_port",
            "health_path",
            "timeout_seconds",
            "allowed_status_codes",
            "interval_seconds",
            "failure_threshold",
            "success_threshold",
            "config_mode",
            "manual_config",
            "managed_features",
        ]
        labels = {
            "title": "Название сервиса",
            "kind": "Тип сервиса",
            "domain": "Домен",
            "upstream_url": "Адрес локального сервиса",
            "external_url": "Внешняя ссылка",
            "description": "Описание",
            "icon_key": "Иконка",
            "sort_order": "Порядок",
            "is_active": "Показывать сервис в каталоге",
            "is_enabled": "Включён в nginx",
            "precheck_url": "URL для preflight",
            "cert_fullchain_path": "Путь к fullchain.pem",
            "cert_privkey_path": "Путь к privkey.pem",
            "check_type": "Тип проверки",
            "health_host": "Хост проверки",
            "health_port": "Порт проверки",
            "health_path": "Путь проверки",
            "timeout_seconds": "Таймаут",
            "allowed_status_codes": "Допустимые HTTP-коды",
            "interval_seconds": "Интервал проверки",
            "failure_threshold": "Порог ошибок",
            "success_threshold": "Порог успеха",
            "config_mode": "Режим конфигурации",
            "manual_config": "Ручной nginx-конфиг",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "manual_config": forms.Textarea(attrs={"rows": 18, "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        if args and args[0] is not None and "kind" not in args[0]:
            mutable_data = args[0].copy()
            mutable_data["kind"] = "proxy"
            args = (mutable_data, *args[1:])
        elif kwargs.get("data") is not None and "kind" not in kwargs["data"]:
            mutable_data = kwargs["data"].copy()
            mutable_data["kind"] = "proxy"
            kwargs["data"] = mutable_data
        super().__init__(*args, **kwargs)
        self.fields["managed_features"].choices = get_service_option_choices()
        for field_name in ("kind", "icon_key", "config_mode", "check_type"):
            self.fields[field_name].widget.attrs["data-enhanced-select"] = "true"
        self.fields["icon_key"].widget.attrs["data-enhanced-search"] = "true"
        if not self.instance.pk:
            self.initial.setdefault("kind", "proxy")
            self.initial.setdefault("config_mode", "managed")
            self.initial.setdefault("managed_features", normalize_service_features([], upstream_url=""))
            self.initial.setdefault("is_enabled", True)
            self.initial.setdefault("is_active", True)
        elif not self.initial.get("managed_features"):
            self.initial["managed_features"] = normalize_service_features(
                self.instance.managed_features,
                upstream_url=self.instance.upstream_url,
            )

    def clean_domain(self):
        domain = (self.cleaned_data.get("domain") or "").strip().lower()
        kind = self.cleaned_data.get("kind") or self.data.get("kind") or "proxy"
        if kind == "external":
            return None
        if domain == settings.HUB_PORTAL_PUBLIC_HOST:
            raise ValidationError("Корневой домен портала нельзя использовать как сервис.")
        if domain and not domain.endswith(settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX):
            raise ValidationError(
                f"Разрешены только домены вида *{settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX}."
            )
        return domain

    def clean(self):
        cleaned_data = super().clean()
        kind = cleaned_data.get("kind") or "proxy"
        if kind == "external":
            cleaned_data["domain"] = None
            cleaned_data["upstream_url"] = ""
            cleaned_data["precheck_url"] = ""
            cleaned_data["managed_features"] = []
            cleaned_data["config_mode"] = "managed"
            cleaned_data["manual_config"] = ""
            if not (cleaned_data.get("external_url") or "").strip():
                self.add_error("external_url", "Укажите внешнюю ссылку.")
            return cleaned_data

        cleaned_data["external_url"] = ""
        upstream_url = (cleaned_data.get("upstream_url") or "").strip()
        cleaned_data["managed_features"] = normalize_service_features(
            cleaned_data.get("managed_features"),
            upstream_url=upstream_url,
        )
        config_mode = cleaned_data.get("config_mode") or "managed"
        manual_config = (cleaned_data.get("manual_config") or "").strip()
        if config_mode == "manual" and not manual_config:
            self.add_error("manual_config", "Для ручного режима заполните полный nginx-конфиг.")
        return cleaned_data
