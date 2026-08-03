from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AuditEvent, Service, ServiceHealthState, User, UserSession


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Портал",
            {
                "fields": (
                    "full_name",
                    "must_change_password",
                    "login_token_hash",
                    "login_token_ciphertext",
                    "login_token_created_at",
                    "login_token_last_used_at",
                    "login_token_created_by",
                    "password_changed_at",
                    "deleted_at",
                    "services",
                )
            },
        ),
    )
    list_display = (
        "username",
        "full_name",
        "is_staff",
        "is_active",
        "must_change_password",
        "login_token_created_at",
        "password_changed_at",
        "last_login",
        "deleted_at",
    )
    filter_horizontal = ("groups", "user_permissions", "services")
    readonly_fields = (
        "login_token_hash",
        "login_token_ciphertext",
        "login_token_created_at",
        "login_token_last_used_at",
        "login_token_created_by",
        "password_changed_at",
        "deleted_at",
    )


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("title", "domain", "icon_key", "check_type", "sort_order", "is_active")
    list_filter = ("is_active", "icon_key", "check_type")
    search_fields = ("title", "domain", "description")


@admin.register(ServiceHealthState)
class ServiceHealthStateAdmin(admin.ModelAdmin):
    list_display = ("service", "status", "last_checked_at", "last_success_at", "last_failure_at")
    list_filter = ("status",)
    search_fields = ("service__title", "service__domain", "last_error")
    readonly_fields = ("last_checked_at", "last_success_at", "last_failure_at", "last_error")


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "auth_method", "ip_address", "created_at", "last_seen_at", "expires_at", "revoked_at")
    list_filter = ("auth_method", "revoked_at")
    search_fields = ("user__username", "session_key", "ip_address", "user_agent", "device_label")
    readonly_fields = (
        "session_key",
        "user",
        "auth_method",
        "ip_address",
        "user_agent",
        "device_label",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
    )


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "username_snapshot",
        "category",
        "auth_method",
        "target_label",
        "result",
        "ip_address",
    )
    list_filter = ("is_business_event", "category", "auth_method", "event_kind", "result", "occurred_at")
    search_fields = ("username_snapshot", "target_label", "ip_address", "host", "path", "user_agent")
    readonly_fields = (
        "username_snapshot",
        "user",
        "service",
        "occurred_at",
        "ip_address",
        "target_label",
        "is_business_event",
        "category",
        "action",
        "auth_method",
        "event_kind",
        "result",
        "host",
        "path",
        "http_method",
        "status_code",
        "user_agent",
        "request_id",
        "metadata",
    )
