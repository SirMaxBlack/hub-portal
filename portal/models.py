import string
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.template.defaultfilters import slugify
from django.utils import timezone
from django.utils.crypto import get_random_string, salted_hmac

from .constants import AUTH_METHOD_ACCOUNT, AUTH_METHOD_TOKEN
from .service_options import normalize_service_features
from .token_crypto import decrypt_token, encrypt_token


ICON_CHOICES = [
    ("crm", "CRM"),
    ("dns", "DNS"),
    ("mqtt", "MQTT"),
    ("netbox", "NetBox"),
    ("billing", "Биллинг"),
    ("graylog", "Graylog"),
    ("frigate", "Frigate"),
    ("homeassistant", "Home Assistant"),
    ("server", "Сервер"),
    ("database", "База данных"),
    ("monitor", "Мониторинг"),
    ("camera", "Камера"),
    ("cloud", "Облако"),
    ("mail", "Почта"),
    ("users", "Пользователи"),
    ("chart", "Аналитика"),
    ("shield", "Безопасность"),
    ("default", "По умолчанию"),
]

AUTH_METHOD_CHOICES = [
    ("", "—"),
    (AUTH_METHOD_ACCOUNT, "Учётная запись"),
    (AUTH_METHOD_TOKEN, "Токен"),
]

HEALTH_CHECK_CHOICES = [
    ("http", "HTTP"),
    ("https", "HTTPS"),
    ("tcp", "TCP"),
]

SERVICE_STATUS_CHOICES = [
    ("unknown", "Неизвестно"),
    ("up", "Доступен"),
    ("down", "Недоступен"),
]

SERVICE_LAYOUT_SCOPE_CHOICES = [
    ("global", "Общая раскладка"),
]

SERVICE_CONFIG_MODE_CHOICES = [
    ("managed", "Управляемый"),
    ("manual", "Ручной"),
]

SERVICE_KIND_CHOICES = [
    ("proxy", "Защищённый reverse proxy"),
    ("external", "Внешняя ссылка"),
]


class User(AbstractUser):
    full_name = models.CharField("ФИО", max_length=255, blank=True)
    must_change_password = models.BooleanField(
        "Требовать смену пароля",
        default=True,
    )
    login_token_hash = models.CharField("Хеш токена входа", max_length=64, blank=True, default="")
    login_token_ciphertext = models.TextField("Зашифрованный токен входа", blank=True, default="")
    login_token_created_at = models.DateTimeField("Токен обновлён", null=True, blank=True)
    login_token_last_used_at = models.DateTimeField("Токен использован", null=True, blank=True)
    login_token_created_by = models.ForeignKey(
        "self",
        verbose_name="Токен выдан пользователем",
        related_name="issued_login_tokens",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    password_changed_at = models.DateTimeField("Пароль обновлён", null=True, blank=True, db_index=True)
    access_state_updated_at = models.DateTimeField("Версия доступа", default=timezone.now, db_index=True)
    deleted_at = models.DateTimeField("Архивирован", null=True, blank=True, db_index=True)
    services = models.ManyToManyField(
        "Service",
        verbose_name="Сервисы",
        related_name="users",
        blank=True,
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    @property
    def is_deleted(self) -> bool:
        return bool(self.deleted_at)

    @property
    def access_state_token(self) -> str:
        if not self.access_state_updated_at:
            return ""
        return self.access_state_updated_at.isoformat()

    def mark_access_state_dirty(self, *, timestamp=None) -> None:
        self.access_state_updated_at = timestamp or timezone.now()

    def can_access_service(self, service: "Service") -> bool:
        if not self.is_active or self.is_deleted:
            return False
        if self.is_staff or self.is_superuser:
            return True
        return self.services.filter(pk=service.pk, is_active=True).exists()

    def can_access_host(self, host: str) -> bool:
        try:
            service = Service.objects.get(domain__iexact=host, is_active=True)
        except Service.DoesNotExist:
            return False
        return self.can_access_service(service)

    @staticmethod
    def build_login_token_hash(raw_token: str) -> str:
        return salted_hmac("portal.login_token", raw_token.strip()).hexdigest()

    @classmethod
    def generate_login_token_value(cls, length: int = 64) -> str:
        allowed_chars = string.ascii_letters + string.digits
        return get_random_string(length, allowed_chars)

    @classmethod
    def find_by_login_token(cls, raw_token: str) -> "User | None":
        candidate = (raw_token or "").strip()
        if not candidate or len(candidate) < 50 or len(candidate) > 70 or not candidate.isalnum():
            return None
        return cls.objects.filter(
            is_active=True,
            login_token_hash=cls.build_login_token_hash(candidate),
        ).first()

    @classmethod
    def lookup_username_by_login_token(cls, raw_token: str) -> str:
        candidate = (raw_token or "").strip()
        if not candidate or len(candidate) < 50 or len(candidate) > 70 or not candidate.isalnum():
            return ""
        user = cls.objects.filter(login_token_hash=cls.build_login_token_hash(candidate)).only("username").first()
        return user.username if user else ""

    @property
    def has_login_token(self) -> bool:
        return bool(self.login_token_hash)

    @property
    def can_reveal_login_token(self) -> bool:
        return bool(self.login_token_hash and self.login_token_ciphertext)

    def set_password(self, raw_password):
        super().set_password(raw_password)
        if raw_password is not None:
            self.password_changed_at = timezone.now()

    def set_login_token(self, raw_token: str, *, actor: "User | None" = None) -> None:
        self.login_token_hash = self.build_login_token_hash(raw_token)
        self.login_token_ciphertext = encrypt_token(raw_token)
        self.login_token_created_at = timezone.now()
        self.login_token_last_used_at = None
        self.login_token_created_by = actor

    def reveal_login_token(self) -> str:
        return decrypt_token(self.login_token_ciphertext)

    def rotate_login_token(self, length: int = 64, *, actor: "User | None" = None) -> str:
        raw_token = self.generate_login_token_value(length=length)
        self.set_login_token(raw_token, actor=actor)
        return raw_token

    def clear_login_token(self) -> None:
        self.login_token_hash = ""
        self.login_token_ciphertext = ""
        self.login_token_created_at = None
        self.login_token_last_used_at = None
        self.login_token_created_by = None

    def mark_login_token_used(self, *, timestamp=None) -> None:
        self.login_token_last_used_at = timestamp or timezone.now()

    def archive(self) -> None:
        self.deleted_at = timezone.now()
        self.is_active = False
        self.clear_login_token()

    def restore(self) -> None:
        self.deleted_at = None
        self.is_active = True

    def save(self, *args, **kwargs):
        tracked_fields = ("is_active", "is_staff", "is_superuser", "deleted_at")
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*tracked_fields, "access_state_updated_at")
                .first()
            )
            if previous and any(previous[field] != getattr(self, field) for field in tracked_fields):
                self.mark_access_state_dirty()
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = list(set(update_fields) | {"access_state_updated_at"})
        elif not self.access_state_updated_at:
            self.mark_access_state_dirty()
        return super().save(*args, **kwargs)


class PortalAccessRule(models.Model):
    cidr = models.CharField("Адрес или подсеть", max_length=64, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0, db_index=True)
    created_at = models.DateTimeField("Создано", default=timezone.now)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Правило доступа к HUB"
        verbose_name_plural = "Правила доступа к HUB"
        ordering = ["sort_order", "cidr", "pk"]

    def __str__(self) -> str:
        return self.cidr


class UserAccessRule(models.Model):
    user = models.ForeignKey(
        User,
        verbose_name="Пользователь",
        related_name="access_rules",
        on_delete=models.CASCADE,
    )
    cidr = models.CharField("Адрес или подсеть", max_length=64)
    sort_order = models.PositiveIntegerField("Порядок", default=0, db_index=True)
    created_at = models.DateTimeField("Создано", default=timezone.now)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Правило доступа пользователя"
        verbose_name_plural = "Правила доступа пользователей"
        ordering = ["sort_order", "cidr", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["user", "cidr"], name="unique_user_access_rule"),
        ]

    def __str__(self) -> str:
        return f"{self.user.username}: {self.cidr}"


class PortalSecuritySettings(models.Model):
    login_attempt_limit_enabled = models.BooleanField("Ограничение попыток входа", default=True)
    login_attempt_limit_count = models.PositiveIntegerField("Допустимое количество ошибок", default=3)
    login_attempt_lock_seconds = models.PositiveIntegerField("Длительность блокировки (сек.)", default=300)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки безопасности портала"
        verbose_name_plural = "Настройки безопасности портала"

    def __str__(self) -> str:
        return "Настройки безопасности HUB"

    @classmethod
    def get_solo(cls) -> "PortalSecuritySettings":
        instance, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "login_attempt_limit_enabled": True,
                "login_attempt_limit_count": 3,
                "login_attempt_lock_seconds": 300,
            },
        )
        return instance


class PortalVisualSettings(models.Model):
    login_particles_desktop_count = models.PositiveIntegerField("Точек для desktop авторизации", default=150)
    blocked_particles_desktop_count = models.PositiveIntegerField("Точек для desktop IP-блокировки", default=150)
    login_particles_mobile_count = models.PositiveIntegerField("Точек для mobile авторизации", default=50)
    blocked_particles_mobile_count = models.PositiveIntegerField("Точек для mobile IP-блокировки", default=50)
    calm_speed_px = models.PositiveIntegerField("Скорость спокойного режима (px/sec)", default=38)
    alert_speed_px = models.PositiveIntegerField("Скорость тревожного режима (px/sec)", default=189)
    invalid_alert_duration_seconds = models.PositiveIntegerField("Время тревоги при ошибке входа (сек.)", default=2)
    lock_alert_duration_seconds = models.PositiveIntegerField("Время тревоги при блокировке (сек.)", default=5)
    alert_fade_duration_seconds = models.PositiveIntegerField("Время затухания тревоги (сек.)", default=1)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки визуала портала"
        verbose_name_plural = "Настройки визуала портала"

    def __str__(self) -> str:
        return "Настройки визуала HUB"

    @classmethod
    def get_solo(cls) -> "PortalVisualSettings":
        instance, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "login_particles_desktop_count": 150,
                "blocked_particles_desktop_count": 150,
                "login_particles_mobile_count": 50,
                "blocked_particles_mobile_count": 50,
                "calm_speed_px": 38,
                "alert_speed_px": 189,
                "invalid_alert_duration_seconds": 2,
                "lock_alert_duration_seconds": 5,
                "alert_fade_duration_seconds": 1,
            },
        )
        return instance


class UserLoginLockState(models.Model):
    user = models.OneToOneField(
        User,
        verbose_name="Пользователь",
        related_name="login_lock_state",
        on_delete=models.CASCADE,
    )
    failure_count = models.PositiveIntegerField("Количество ошибок", default=0)
    locked_until = models.DateTimeField("Заблокирован до", null=True, blank=True, db_index=True)
    last_failed_at = models.DateTimeField("Последняя ошибка", null=True, blank=True)
    last_browser_hash = models.CharField("Хеш браузера", max_length=64, blank=True, default="")
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Состояние блокировки входа пользователя"
        verbose_name_plural = "Состояния блокировки входа пользователей"

    def __str__(self) -> str:
        return f"{self.user.username} · {self.failure_count}"


class BrowserLoginLockState(models.Model):
    browser_hash = models.CharField("Хеш браузера", max_length=64, unique=True, db_index=True)
    fingerprint_hash = models.CharField("Хеш отпечатка браузера", max_length=64, blank=True, default="", db_index=True)
    user = models.ForeignKey(
        User,
        verbose_name="Связанный пользователь",
        related_name="browser_login_locks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    username_snapshot = models.CharField("Логин", max_length=150, blank=True, default="")
    failure_count = models.PositiveIntegerField("Количество ошибок", default=0)
    locked_until = models.DateTimeField("Заблокирован до", null=True, blank=True, db_index=True)
    last_failed_at = models.DateTimeField("Последняя ошибка", null=True, blank=True)
    created_at = models.DateTimeField("Создан", default=timezone.now)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Состояние блокировки входа браузера"
        verbose_name_plural = "Состояния блокировки входа браузеров"
        ordering = ["-updated_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.username_snapshot or self.browser_hash[:12]}"


class UserSession(models.Model):
    session_key = models.CharField("Ключ сессии", max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        User,
        verbose_name="Пользователь",
        related_name="tracked_sessions",
        on_delete=models.CASCADE,
    )
    auth_method = models.CharField(
        "Метод авторизации",
        max_length=32,
        choices=AUTH_METHOD_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    ip_address = models.GenericIPAddressField("IP-адрес", blank=True, null=True, unpack_ipv4=True, db_index=True)
    user_agent = models.CharField("User-Agent", max_length=512, blank=True, default="")
    device_label = models.CharField("Устройство", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("Создана", default=timezone.now)
    last_seen_at = models.DateTimeField("Последняя активность", default=timezone.now, db_index=True)
    expires_at = models.DateTimeField("Истекает", db_index=True)
    revoked_at = models.DateTimeField("Завершена", null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Активная сессия"
        verbose_name_plural = "Активные сессии"
        ordering = ["-last_seen_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} · {self.session_key}"

    @property
    def is_active_session(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


class AuditEvent(models.Model):
    username_snapshot = models.CharField("Логин", max_length=150, blank=True, default="", db_index=True)
    user = models.ForeignKey(
        User,
        verbose_name="Пользователь",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    service = models.ForeignKey(
        "Service",
        verbose_name="Сервис",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    occurred_at = models.DateTimeField("Когда", default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField("IP-адрес", blank=True, null=True, unpack_ipv4=True, db_index=True)
    target_label = models.CharField("Куда", max_length=255, db_index=True)
    is_business_event = models.BooleanField("Бизнес-событие", default=True, db_index=True)
    category = models.CharField("Категория", max_length=64, blank=True, default="", db_index=True)
    action = models.CharField("Действие", max_length=64, blank=True, default="", db_index=True)
    auth_method = models.CharField(
        "Метод авторизации",
        max_length=32,
        choices=AUTH_METHOD_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    event_kind = models.CharField("Тип события", max_length=64, db_index=True)
    result = models.CharField("Результат", max_length=32, blank=True, default="")
    host = models.CharField("Host", max_length=255, blank=True, default="")
    path = models.CharField("Path", max_length=2048, blank=True, default="")
    http_method = models.CharField("HTTP-метод", max_length=16, blank=True, default="")
    status_code = models.PositiveSmallIntegerField("HTTP-статус", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=512, blank=True, default="")
    request_id = models.CharField("Request ID", max_length=128, blank=True, default="")
    metadata = models.JSONField("Дополнительные сведения", blank=True, default=dict)

    class Meta:
        verbose_name = "Событие аудита"
        verbose_name_plural = "События аудита"
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(fields=["is_business_event", "occurred_at"]),
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["category", "occurred_at"]),
            models.Index(fields=["result", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.username_snapshot or 'неизвестно'} -> {self.target_label}"


class LoginRedirectState(models.Model):
    token = models.UUIDField("Токен состояния", default=uuid4, unique=True, editable=False, db_index=True)
    service = models.ForeignKey(
        "Service",
        verbose_name="Сервис",
        related_name="login_redirect_states",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    client_ip = models.GenericIPAddressField("IP-адрес клиента", blank=True, null=True, unpack_ipv4=True, db_index=True)
    target_url = models.URLField("Итоговый адрес", max_length=1024)
    created_at = models.DateTimeField("Создано", default=timezone.now, db_index=True)
    expires_at = models.DateTimeField("Истекает", db_index=True)
    consumed_at = models.DateTimeField("Использовано", blank=True, null=True, db_index=True)

    class Meta:
        verbose_name = "Безопасное состояние перехода"
        verbose_name_plural = "Безопасные состояния перехода"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.target_url} ({self.token})"


class Service(models.Model):
    title = models.CharField("Название", max_length=255)
    slug = models.SlugField("Slug", max_length=255, unique=True)
    description = models.TextField("Описание", blank=True)
    kind = models.CharField("Тип сервиса", max_length=16, choices=SERVICE_KIND_CHOICES, default="proxy")
    domain = models.CharField("Домен", max_length=255, unique=True, null=True, blank=True)
    upstream_url = models.URLField("Upstream URL", max_length=500, blank=True, default="")
    external_url = models.URLField("Внешняя ссылка", max_length=1000, blank=True, default="")
    precheck_url = models.URLField("Precheck URL", max_length=500, blank=True, default="")
    icon_key = models.CharField(
        "Иконка",
        max_length=32,
        choices=ICON_CHOICES,
        default="default",
    )
    check_type = models.CharField("Тип проверки", max_length=16, choices=HEALTH_CHECK_CHOICES, default="http")
    health_host = models.CharField("Хост проверки", max_length=255, blank=True, default="")
    health_port = models.PositiveIntegerField("Порт проверки", null=True, blank=True)
    health_path = models.CharField("Путь проверки", max_length=255, blank=True, default="/")
    timeout_seconds = models.PositiveIntegerField("Таймаут проверки", default=3)
    allowed_status_codes = models.CharField("Допустимые HTTP-коды", max_length=255, blank=True, default="200,204,301,302,401,403")
    interval_seconds = models.PositiveIntegerField("Интервал проверки", default=5)
    failure_threshold = models.PositiveIntegerField("Порог ошибок", default=2)
    success_threshold = models.PositiveIntegerField("Порог успеха", default=1)
    cert_fullchain_path = models.CharField("Путь к fullchain.pem", max_length=500, blank=True, default="")
    cert_privkey_path = models.CharField("Путь к privkey.pem", max_length=500, blank=True, default="")
    config_mode = models.CharField("Режим конфигурации", max_length=16, choices=SERVICE_CONFIG_MODE_CHOICES, default="managed")
    managed_features = models.JSONField("Опции nginx", blank=True, default=list)
    manual_config = models.TextField("Ручной конфиг nginx", blank=True, default="")
    sort_order = models.PositiveIntegerField("Порядок", default=100)
    is_enabled = models.BooleanField("Включён в nginx", default=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Сервис"
        verbose_name_plural = "Сервисы"
        ordering = ["sort_order", "title"]

    def __str__(self) -> str:
        return self.title

    @property
    def launch_url(self) -> str:
        if self.kind == "external":
            return self.external_url
        return f"https://{self.domain}/"

    @property
    def is_external_link(self) -> bool:
        return self.kind == "external"

    @property
    def parsed_upstream(self):
        return urlparse(self.upstream_url)

    @property
    def health_status(self) -> str:
        if hasattr(self, "health_state"):
            return self.health_state.status
        return "unknown"

    def get_allowed_status_codes(self) -> list[int]:
        result = []
        for token in (self.allowed_status_codes or "").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                result.append(int(token))
            except ValueError:
                continue
        return result or [200, 204, 301, 302, 401, 403]

    def sync_health_defaults(self) -> None:
        if self.kind == "external":
            return
        parsed = self.parsed_upstream
        if not self.health_host:
            self.health_host = parsed.hostname or ""
        if not self.health_port:
            if parsed.port:
                self.health_port = parsed.port
            elif self.check_type == "https":
                self.health_port = 443
            else:
                self.health_port = 80
        if self.check_type == "tcp":
            self.health_path = ""
        elif not self.health_path:
            self.health_path = "/"

    def sync_runtime_defaults(self) -> None:
        if self.kind == "external":
            self.domain = None
            self.upstream_url = ""
            self.precheck_url = ""
            self.health_host = ""
            self.health_port = None
            self.health_path = ""
            self.cert_fullchain_path = ""
            self.cert_privkey_path = ""
            self.managed_features = []
            self.manual_config = ""
            self.config_mode = "managed"
            self.is_enabled = True
            return
        if self.domain:
            if not self.cert_fullchain_path:
                self.cert_fullchain_path = f"/etc/letsencrypt/live/{self.domain}/fullchain.pem"
            if not self.cert_privkey_path:
                self.cert_privkey_path = f"/etc/letsencrypt/live/{self.domain}/privkey.pem"
        if not self.precheck_url:
            self.precheck_url = self.upstream_url
        self.managed_features = normalize_service_features(
            self.managed_features,
            upstream_url=self.upstream_url,
        )

    def clean(self) -> None:
        self.kind = self.kind or "proxy"
        self.sync_runtime_defaults()

        if self.kind == "external":
            parsed_external = urlparse(self.external_url)
            if parsed_external.scheme not in {"http", "https"} or not parsed_external.netloc:
                raise ValidationError({"external_url": "Внешняя ссылка должна начинаться с http:// или https://."})
            return

        if self.kind != "proxy":
            raise ValidationError({"kind": "Неизвестный тип сервиса."})
        if not self.domain:
            raise ValidationError({"domain": "Укажите домен reverse proxy."})
        if self.domain == settings.HUB_PORTAL_PUBLIC_HOST:
            raise ValidationError({"domain": "Корневой портал нельзя регистрировать как сервис."})
        if not self.domain.endswith(settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX):
            raise ValidationError(
                {"domain": f"Разрешены только домены вида *{settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX}."}
            )

        parsed = urlparse(self.upstream_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError({"upstream_url": "Upstream URL должен начинаться с http:// или https://."})
        self.sync_health_defaults()
        if self.check_type not in {"http", "https", "tcp"}:
            raise ValidationError({"check_type": "Поддерживаются только HTTP, HTTPS и TCP."})
        if not self.health_host:
            raise ValidationError({"health_host": "Укажите хост проверки."})
        if not self.health_port:
            raise ValidationError({"health_port": "Укажите порт проверки."})
        if self.check_type != "tcp" and not self.health_path.startswith("/"):
            raise ValidationError({"health_path": "Путь проверки должен начинаться с /."})
        if self.timeout_seconds < 1 or self.timeout_seconds > 30:
            raise ValidationError({"timeout_seconds": "Таймаут проверки должен быть от 1 до 30 секунд."})
        if self.interval_seconds < 5:
            raise ValidationError({"interval_seconds": "Интервал проверки не может быть меньше 5 секунд."})
        if self.failure_threshold < 1 or self.success_threshold < 1:
            raise ValidationError("Порог успеха и ошибок должен быть не меньше 1.")
        if self.check_type != "tcp":
            allowed_codes = self.get_allowed_status_codes()
            if not allowed_codes:
                raise ValidationError({"allowed_status_codes": "Укажите хотя бы один допустимый HTTP-код."})
            invalid_codes = [code for code in allowed_codes if code < 100 or code > 599]
            if invalid_codes:
                raise ValidationError({"allowed_status_codes": "Допустимы только HTTP-коды в диапазоне 100-599."})
        if self.config_mode == "manual" and not self.manual_config.strip():
            raise ValidationError({"manual_config": "Для ручного режима нужно заполнить полный nginx-конфиг."})
        if self.config_mode not in {"managed", "manual"}:
            raise ValidationError({"config_mode": "Поддерживаются только управляемый и ручной режим."})

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or slugify((self.domain or "service").split(".", 1)[0])
            slug = base_slug
            index = 1
            while Service.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                index += 1
                slug = f"{base_slug}-{index}"
            self.slug = slug
        self.full_clean()
        result = super().save(*args, **kwargs)
        ServiceLayout.ensure_global_slot(self)
        return result


class ServiceRevision(models.Model):
    service = models.ForeignKey(
        Service,
        verbose_name="Сервис",
        related_name="revisions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    service_title_snapshot = models.CharField("Название сервиса", max_length=255, blank=True, default="")
    service_domain_snapshot = models.CharField("Домен сервиса", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("Создана", auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        verbose_name="Кем создана",
        related_name="service_revisions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    revision_kind = models.CharField("Тип ревизии", max_length=32, db_index=True)
    summary = models.CharField("Описание", max_length=255, blank=True, default="")
    config_mode = models.CharField("Режим конфигурации", max_length=16, choices=SERVICE_CONFIG_MODE_CHOICES, default="managed")
    rendered_config = models.TextField("Содержимое конфига", blank=True, default="")
    snapshot = models.JSONField("Снимок сервиса", blank=True, default=dict)
    validated_ok = models.BooleanField("Конфиг валиден", default=False)

    class Meta:
        verbose_name = "Ревизия сервиса"
        verbose_name_plural = "Ревизии сервисов"
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.service_title_snapshot or self.service_domain_snapshot} · {self.revision_kind}"


class ServiceLayout(models.Model):
    scope = models.CharField("Область раскладки", max_length=32, choices=SERVICE_LAYOUT_SCOPE_CHOICES, default="global")
    service = models.ForeignKey(
        Service,
        verbose_name="Сервис",
        related_name="layout_entries",
        on_delete=models.CASCADE,
    )
    slot_index = models.PositiveIntegerField("Слот", default=0, db_index=True)
    updated_by = models.ForeignKey(
        User,
        verbose_name="Кем изменено",
        related_name="service_layout_updates",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Раскладка сервиса"
        verbose_name_plural = "Раскладки сервисов"
        ordering = ["scope", "slot_index", "service__title"]
        constraints = [
            models.UniqueConstraint(fields=["scope", "service"], name="uniq_service_layout_scope_service"),
        ]
        indexes = [
            models.Index(fields=["scope", "slot_index"]),
        ]

    def __str__(self) -> str:
        return f"{self.service.title} · {self.scope} · {self.slot_index}"

    @classmethod
    def next_slot_index(cls, scope: str = "global") -> int:
        max_slot = cls.objects.filter(scope=scope).aggregate(max_slot=Max("slot_index")).get("max_slot")
        return 0 if max_slot is None else max_slot + 1

    @classmethod
    def ensure_global_slot(cls, service: Service, *, actor: User | None = None) -> "ServiceLayout":
        layout = cls.objects.filter(scope="global", service=service).first()
        if layout:
            return layout
        return cls.objects.create(
            scope="global",
            service=service,
            slot_index=cls.next_slot_index("global"),
            updated_by=actor,
        )


class ServiceHealthState(models.Model):
    service = models.OneToOneField(
        Service,
        verbose_name="Сервис",
        related_name="health_state",
        on_delete=models.CASCADE,
    )
    status = models.CharField("Статус", max_length=16, choices=SERVICE_STATUS_CHOICES, default="unknown", db_index=True)
    last_checked_at = models.DateTimeField("Проверен", null=True, blank=True, db_index=True)
    last_success_at = models.DateTimeField("Последний успех", null=True, blank=True)
    last_failure_at = models.DateTimeField("Последняя ошибка", null=True, blank=True)
    consecutive_successes = models.PositiveIntegerField("Успехов подряд", default=0)
    consecutive_failures = models.PositiveIntegerField("Ошибок подряд", default=0)
    last_error = models.TextField("Последняя ошибка", blank=True, default="")

    class Meta:
        verbose_name = "Состояние сервиса"
        verbose_name_plural = "Состояния сервисов"

    def __str__(self) -> str:
        return f"{self.service.title} · {self.status}"
