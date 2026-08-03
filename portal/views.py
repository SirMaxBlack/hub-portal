from __future__ import annotations

import csv
from datetime import timedelta
from io import StringIO
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.contrib.sessions.models import Session
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.forms.forms import NON_FIELD_ERRORS
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from .access_control import (
    build_access_restricted_response,
    consume_login_redirect_state,
    create_login_redirect_state,
    evaluate_ip_access,
    get_default_user_access_rule_values,
    get_portal_access_rule_values,
    get_request_client_ip,
    get_user_access_decision,
    get_user_access_decision_for_request,
    get_user_access_rule_values,
    prime_request_user_access_cache,
    request_user_can_access_service,
    replace_portal_access_rules,
    replace_user_access_rules,
    seed_user_access_rules_from_portal,
    validate_access_rule_list,
)
from .audit import apply_no_store, get_request_auth_method, log_audit_event
from .audit_maintenance import AuditMaintenanceBusyError, AuditMaintenanceError, clear_audit_trail
from .constants import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_TOKEN,
    LOGIN_IP_BLOCK_COOKIE_NAME,
    SESSION_AUTH_METHOD_KEY,
    SESSION_LOGIN_NEXT_URL_KEY,
    SESSION_LOGIN_REDIRECT_STATE_KEY,
)
from .forms import (
    AccountPasswordChangeForm,
    FirstPasswordChangeForm,
    ManagedServiceForm,
    ManagedUserCreateForm,
    ManagedUserUpdateForm,
    PortalAuthenticationForm,
)
from .health import get_effective_status, get_state, update_service_health
from hub_portal.release import get_release_id
from .login_protection import (
    clear_account_login_restrictions_for_user,
    clear_all_account_login_restrictions,
    clear_login_restrictions_after_success,
    format_duration_label,
    get_account_lock_decision,
    get_login_lock_summary,
    get_login_security_settings,
    register_failed_account_attempt,
)
from .models import (
    AuditEvent,
    BrowserLoginLockState,
    LoginRedirectState,
    PortalAccessRule,
    PortalSecuritySettings,
    PortalVisualSettings,
    Service,
    ServiceLayout,
    ServiceRevision,
    UserAccessRule,
    UserLoginLockState,
    UserSession,
)
from .service_admin import (
    apply_service_candidate,
    apply_service_snapshot,
    build_service_snapshot,
    create_service_revision,
    delete_service_candidate,
    disable_service_candidate,
    enable_service_candidate,
    issue_service_certificate,
    read_live_service_config,
    render_effective_service_config,
    render_managed_service_config,
    validate_service_candidate,
)
from .service_options import get_service_option_catalog, get_service_option_rows, get_service_profile_rows
from .session_tracking import ensure_session_record, revoke_session_record, revoke_user_sessions
from .visual_settings import build_particle_visual_context, get_portal_visual_settings


User = get_user_model()

USER_PAGE_SIZE_OPTIONS = (25, 50, 100)
LOG_PAGE_SIZE_OPTIONS = (25, 50, 100)
DEFAULT_USER_PAGE_SIZE = 25
DEFAULT_LOG_PAGE_SIZE = 50
DEFAULT_LOG_PERIOD = "7d"
SUPPRESS_NEXT_LOGS_AUDIT_SESSION_KEY = "suppress_next_logs_audit"
SERVICE_LAYOUT_SCOPE_GLOBAL = "global"
SERVICE_LAYOUT_RESERVE_SLOTS = 4
LOG_KIND_OPTIONS = (
    ("portal", "Портал"),
    ("service_open", "Переход на сервис"),
    ("service_gate", "Прямой доступ"),
)
MOBILE_USER_AGENT_MARKERS = (
    "android",
    "iphone",
    "ipad",
    "mobile",
    "opera mini",
    "windows phone",
)

RESULT_LABELS = {
    "success": "Успешно",
    "failure": "Ошибка",
    "denied": "Отказано",
    "blocked": "Заблокировано",
    "unauthenticated": "Не авторизован",
    "password_change_required": "Требуется смена пароля",
    "missing": "Нет данных",
    "revoked": "Отозвано",
}

LOCK_DURATION_UNIT_SECONDS = {
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}

VISUAL_PARTICLE_COUNT_LIMITS = (1, 400)
VISUAL_PARTICLE_SPEED_LIMITS = (1, 1000)
VISUAL_PARTICLE_DURATION_LIMITS = (1, 60)


def is_token_authenticated_session(request: HttpRequest) -> bool:
    return request.session.get(SESSION_AUTH_METHOD_KEY) == AUTH_METHOD_TOKEN


def is_modal_request(request: HttpRequest) -> bool:
    return request.GET.get("modal") == "1" or request.POST.get("modal") == "1"


def get_fragment_name(request: HttpRequest) -> str:
    return request.GET.get("fragment", "").strip()


def is_mobile_browser(request: HttpRequest) -> bool:
    user_agent = (request.META.get("HTTP_USER_AGENT") or "").lower()
    return any(marker in user_agent for marker in MOBILE_USER_AGENT_MARKERS)


def is_trusted_internal_request(request: HttpRequest) -> bool:
    provided_key = (request.headers.get("X-Portal-Auth-Key") or "").strip()
    expected_key = getattr(settings, "HUB_PORTAL_INTERNAL_AUTH_KEY", "").strip()
    return bool(expected_key and provided_key and constant_time_compare(provided_key, expected_key))


def sanitize_service_request_uri(raw_uri: str) -> str:
    candidate = (raw_uri or "").strip()
    if not candidate.startswith("/"):
        return "/"

    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return "/"

    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{path}{query}"


def build_post_login_target(request: HttpRequest) -> str:
    session_login_state = (request.session.pop(SESSION_LOGIN_REDIRECT_STATE_KEY, "") or "").strip()
    if session_login_state:
        redirect_target = consume_login_redirect_state(session_login_state, get_request_client_ip(request))
        if redirect_target:
            return redirect_target

    session_next_value = (request.session.pop(SESSION_LOGIN_NEXT_URL_KEY, "") or "").strip()
    if session_next_value.startswith("/"):
        return sanitize_service_request_uri(session_next_value)

    login_state = request.POST.get("state") or request.GET.get("state")
    if login_state:
        redirect_target = consume_login_redirect_state(login_state, get_request_client_ip(request))
        if redirect_target:
            return redirect_target

    next_value = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
    allowed_hosts = {settings.HUB_PORTAL_PUBLIC_HOST, request.get_host()}
    if url_has_allowed_host_and_scheme(next_value or "", allowed_hosts=allowed_hosts, require_https=not settings.DEBUG):
        return next_value
    if next_value and next_value.startswith("/"):
        return next_value
    return reverse("portal:dashboard")


def get_services_queryset():
    return Service.objects.filter(is_active=True).select_related("health_state").prefetch_related(
        Prefetch(
            "layout_entries",
            queryset=ServiceLayout.objects.filter(scope=SERVICE_LAYOUT_SCOPE_GLOBAL).only("service_id", "slot_index"),
            to_attr="global_layout_entries",
        )
    )


def get_service_status_queryset():
    return (
        Service.objects.filter(is_active=True)
        .select_related("health_state")
        .only(
            "pk",
            "slug",
            "kind",
            "is_enabled",
            "interval_seconds",
            "health_state__status",
            "health_state__last_checked_at",
        )
    )


def get_service_slot_index(service: Service) -> int:
    layout_entries = getattr(service, "global_layout_entries", [])
    if layout_entries:
        return layout_entries[0].slot_index
    return service.sort_order


def enrich_services_for_ui(services: list[Service]) -> list[Service]:
    for service in services:
        service.ui_status = get_effective_status(service, state=getattr(service, "health_state", None))
        service.layout_slot_index = get_service_slot_index(service)
    services.sort(key=lambda item: (item.layout_slot_index, item.title.casefold(), item.pk))
    return services


def get_visible_services(user) -> list[Service]:
    queryset = get_services_queryset()
    if user.is_staff or user.is_superuser:
        services = list(queryset)
    else:
        services = list(queryset.filter(users=user))
    return enrich_services_for_ui(services)


def get_visible_service_status_rows(user) -> list[Service]:
    queryset = get_service_status_queryset()
    if user.is_staff or user.is_superuser:
        return list(queryset)
    return list(queryset.filter(users=user))


def build_service_layout_slots(
    services: list[Service],
    *,
    include_reserve: bool = False,
    preserve_gaps: bool = False,
) -> list[dict]:
    ordered_services = enrich_services_for_ui(list(services))
    if not ordered_services:
        reserve_count = SERVICE_LAYOUT_RESERVE_SLOTS if include_reserve else 0
        return [{"index": slot_index, "service": None} for slot_index in range(reserve_count)]

    if not preserve_gaps:
        slots = [{"index": slot_index, "service": service} for slot_index, service in enumerate(ordered_services)]
        if include_reserve:
            start_index = len(slots)
            for offset in range(SERVICE_LAYOUT_RESERVE_SLOTS):
                slots.append({"index": start_index + offset, "service": None})
        return slots

    slots_by_index: dict[int, Service] = {}
    for service in ordered_services:
        slot_index = max(0, int(getattr(service, "layout_slot_index", 0) or 0))
        while slot_index in slots_by_index:
            slot_index += 1
        service.render_slot_index = slot_index
        slots_by_index[slot_index] = service

    last_slot_index = max(slots_by_index)
    slot_count = last_slot_index + 1 + (SERVICE_LAYOUT_RESERVE_SLOTS if include_reserve else 0)
    return [{"index": slot_index, "service": slots_by_index.get(slot_index)} for slot_index in range(slot_count)]


def render_service_layout_board(
    request: HttpRequest,
    *,
    services: list[Service],
    editable: bool,
    preserve_gaps: bool,
) -> str:
    slots = build_service_layout_slots(
        services,
        include_reserve=editable,
        preserve_gaps=preserve_gaps,
    )
    return render_to_string(
        "portal/_service_layout_board.html",
        {
            "service_layout_slots": slots,
            "service_layout_editable": editable,
            "service_layout_update_url": reverse("portal:rights_service_layout_update") if editable else "",
            "service_layout_has_services": any(slot["service"] for slot in slots),
        },
        request=request,
    )


def move_service_layout_slot(*, service: Service, target_slot_index: int, actor=None) -> bool:
    with transaction.atomic():
        ServiceLayout.ensure_global_slot(service, actor=actor)
        layouts = list(
            ServiceLayout.objects.select_for_update()
            .filter(scope=SERVICE_LAYOUT_SCOPE_GLOBAL)
            .select_related("service")
            .order_by("slot_index", "service_id")
        )
        layouts_by_service_id = {layout.service_id: layout for layout in layouts}
        moving_layout = layouts_by_service_id.get(service.pk)
        if moving_layout is None:
            return False

        target_slot_index = max(0, target_slot_index)
        if moving_layout.slot_index == target_slot_index:
            return False

        occupied_slots = {
            layout.slot_index: layout.service_id
            for layout in layouts
            if layout.service_id != service.pk
        }
        slot_updates: dict[int, int] = {}
        current_service_id = service.pk
        current_slot = target_slot_index
        while True:
            displaced_service_id = occupied_slots.get(current_slot)
            slot_updates[current_service_id] = current_slot
            if displaced_service_id is None:
                break
            current_service_id = displaced_service_id
            current_slot += 1

        timestamp = timezone.now()
        dirty_layouts = []
        for service_id, slot_index in slot_updates.items():
            layout = layouts_by_service_id.get(service_id)
            if layout is None or layout.slot_index == slot_index:
                continue
            layout.slot_index = slot_index
            layout.updated_by = actor
            layout.updated_at = timestamp
            dirty_layouts.append(layout)

        if dirty_layouts:
            ServiceLayout.objects.bulk_update(dirty_layouts, ["slot_index", "updated_by", "updated_at"])
        return bool(dirty_layouts)


def reset_service_layout(*, actor=None) -> bool:
    services = list(Service.objects.filter(is_active=True).order_by("sort_order", "title", "pk"))
    if not services:
        return False

    service_ids = [service.pk for service in services]
    actor_id = getattr(actor, "pk", None)
    changed = False

    with transaction.atomic():
        existing_layouts = {
            layout.service_id: layout
            for layout in ServiceLayout.objects.select_for_update().filter(
                scope=SERVICE_LAYOUT_SCOPE_GLOBAL,
                service_id__in=service_ids,
            )
        }
        create_layouts: list[ServiceLayout] = []
        update_layouts: list[ServiceLayout] = []

        for slot_index, service in enumerate(services):
            layout = existing_layouts.get(service.pk)
            if layout is None:
                create_layouts.append(
                    ServiceLayout(
                        scope=SERVICE_LAYOUT_SCOPE_GLOBAL,
                        service=service,
                        slot_index=slot_index,
                        updated_by=actor,
                    )
                )
                changed = True
                continue

            if layout.slot_index == slot_index and layout.updated_by_id == actor_id:
                continue

            if layout.slot_index != slot_index:
                changed = True
            layout.slot_index = slot_index
            layout.updated_by = actor
            update_layouts.append(layout)

        if create_layouts:
            ServiceLayout.objects.bulk_create(create_layouts)
        if update_layouts:
            ServiceLayout.objects.bulk_update(update_layouts, ["slot_index", "updated_by", "updated_at"])

    return changed


def format_timestamp(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%d.%m.%Y %H:%M")


def build_token_response(user) -> dict:
    created_by = user.login_token_created_by.display_name if user.login_token_created_by else ""
    return {
        "ok": True,
        "token": user.reveal_login_token(),
        "generated_at": format_timestamp(user.login_token_created_at),
        "last_used_at": format_timestamp(user.login_token_last_used_at),
        "created_by": created_by,
        "has_token": user.has_login_token,
        "can_reveal": user.can_reveal_login_token,
        "status_label": (
            "Активен"
            if user.can_reveal_login_token
            else ("Нужен перевыпуск" if user.has_login_token else "Не создан")
        ),
    }


def build_service_counts(services: list[Service]) -> dict:
    counts = {
        "assigned": len(services),
        "total": len(services),
        "up": 0,
        "down": 0,
        "unknown": 0,
    }
    for service in services:
        status = getattr(service, "ui_status", get_effective_status(service, state=getattr(service, "health_state", None)))
        counts[status] += 1
    return counts


def get_page_size(request: HttpRequest, *, options: tuple[int, ...], default: int) -> int:
    try:
        value = int(request.GET.get("page_size", default))
    except (TypeError, ValueError):
        return default
    return value if value in options else default


def paginate_queryset(request: HttpRequest, queryset, *, options: tuple[int, ...], default: int):
    page_size = get_page_size(request, options=options, default=default)
    paginator = Paginator(queryset, page_size)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    return page_obj, page_size


def get_user_management_queryset(request: HttpRequest):
    queryset = User.objects.prefetch_related("services").order_by("username")
    archived = request.GET.get("archived", "").strip()
    if archived == "only":
        queryset = queryset.filter(deleted_at__isnull=False)
    elif archived == "with":
        queryset = queryset
    else:
        queryset = queryset.filter(deleted_at__isnull=True)

    username_filter = request.GET.get("username", "").strip()
    full_name_filter = request.GET.get("full_name", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()
    service_filter = request.GET.get("service", "").strip()
    sort = request.GET.get("sort", "username").strip()

    if username_filter:
        queryset = queryset.filter(username__icontains=username_filter)
    if full_name_filter:
        queryset = queryset.filter(full_name__icontains=full_name_filter)
    if role_filter == "admin":
        queryset = queryset.filter(is_staff=True)
    elif role_filter == "user":
        queryset = queryset.filter(is_staff=False)
    if status_filter == "active":
        queryset = queryset.filter(is_active=True, deleted_at__isnull=True)
    elif status_filter == "inactive":
        queryset = queryset.filter(is_active=False, deleted_at__isnull=True)
    elif status_filter == "archived":
        queryset = queryset.filter(deleted_at__isnull=False)
    if service_filter:
        queryset = queryset.filter(Q(services__slug=service_filter) | Q(is_staff=True)).distinct()

    sort_map = {
        "username": ("username",),
        "full_name": ("full_name", "username"),
        "last_login": ("-last_login", "username"),
        "password_changed_at": ("-password_changed_at", "username"),
    }
    queryset = queryset.order_by(*sort_map.get(sort, ("username",)))
    return queryset, {
        "username": username_filter,
        "full_name": full_name_filter,
        "role": role_filter,
        "status": status_filter,
        "service": service_filter,
        "archived": archived,
        "sort": sort,
    }


def get_log_queryset(request: HttpRequest):
    queryset = AuditEvent.objects.select_related("user", "service").filter(is_business_event=True)
    period = request.GET.get("period", DEFAULT_LOG_PERIOD).strip() or DEFAULT_LOG_PERIOD
    user_filter = request.GET.get("user", "").strip()
    ip_filter = request.GET.get("ip", "").strip()
    category_filter = request.GET.get("category", "").strip()
    kind_filter = request.GET.get("kind", "").strip()
    auth_method_filter = request.GET.get("auth_method", "").strip()
    result_filter = request.GET.get("result", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    now = timezone.now()
    if period == "today":
        queryset = queryset.filter(occurred_at__date=timezone.localdate())
    elif period == "24h":
        queryset = queryset.filter(occurred_at__gte=now - timedelta(hours=24))
    elif period == "30d":
        queryset = queryset.filter(occurred_at__gte=now - timedelta(days=30))
    elif period == "custom":
        parsed_from = parse_date(date_from)
        parsed_to = parse_date(date_to)
        if parsed_from:
            queryset = queryset.filter(occurred_at__date__gte=parsed_from)
        if parsed_to:
            queryset = queryset.filter(occurred_at__date__lte=parsed_to)
    else:
        queryset = queryset.filter(occurred_at__gte=now - timedelta(days=7))

    if user_filter:
        queryset = queryset.filter(username_snapshot=user_filter)
    if ip_filter:
        queryset = queryset.filter(ip_address=ip_filter)
    if category_filter:
        queryset = queryset.filter(category=category_filter)
    if kind_filter == "portal":
        queryset = queryset.exclude(event_kind__in=["service_open", "service_gate"])
    elif kind_filter in {"service_open", "service_gate"}:
        queryset = queryset.filter(event_kind=kind_filter)
    if auth_method_filter:
        queryset = queryset.filter(auth_method=auth_method_filter)
    if result_filter:
        queryset = queryset.filter(result=result_filter)

    return queryset.order_by("-occurred_at", "-pk"), {
        "period": period,
        "user": user_filter,
        "ip": ip_filter,
        "category": category_filter,
        "kind": kind_filter,
        "auth_method": auth_method_filter,
        "result": result_filter,
        "date_from": date_from,
        "date_to": date_to,
    }


def get_log_filter_options():
    base_queryset = AuditEvent.objects.filter(is_business_event=True)
    return {
        "users": base_queryset.exclude(username_snapshot="").order_by("username_snapshot").values_list(
            "username_snapshot", flat=True
        ).distinct(),
        "categories": base_queryset.exclude(category="").order_by("category").values_list("category", flat=True).distinct(),
        "kinds": LOG_KIND_OPTIONS,
        "auth_methods": base_queryset.exclude(auth_method="").order_by("auth_method").values_list(
            "auth_method", flat=True
        ).distinct(),
        "results": base_queryset.exclude(result="").order_by("result").values_list("result", flat=True).distinct(),
    }


def get_active_sessions_for_user(user):
    return UserSession.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()).order_by(
        "-last_seen_at", "-created_at"
    )


def get_posted_access_rule_values(request: HttpRequest, field_name: str) -> list[str]:
    return [value for value in request.POST.getlist(field_name)]


def resolve_user_access_rule_values(request: HttpRequest, managed_user=None) -> list[str]:
    if request.method == "POST":
        return get_posted_access_rule_values(request, "user_access_rules")
    if managed_user:
        return get_user_access_rule_values(managed_user)
    return get_default_user_access_rule_values()


def split_lock_duration(seconds: int) -> tuple[int, str]:
    total_seconds = max(int(seconds or 0), 1)
    if total_seconds % LOCK_DURATION_UNIT_SECONDS["days"] == 0:
        return total_seconds // LOCK_DURATION_UNIT_SECONDS["days"], "days"
    if total_seconds % LOCK_DURATION_UNIT_SECONDS["hours"] == 0:
        return total_seconds // LOCK_DURATION_UNIT_SECONDS["hours"], "hours"
    if total_seconds % LOCK_DURATION_UNIT_SECONDS["minutes"] == 0:
        return total_seconds // LOCK_DURATION_UNIT_SECONDS["minutes"], "minutes"
    return total_seconds, "seconds"


def parse_login_attempt_settings_payload(request: HttpRequest) -> tuple[dict, list[str]]:
    errors: list[str] = []
    enabled = request.POST.get("login_attempt_limit_enabled") == "1"
    raw_limit = (request.POST.get("login_attempt_limit_count") or "").strip()
    raw_duration_value = (request.POST.get("login_attempt_lock_value") or "").strip()
    duration_unit = (request.POST.get("login_attempt_lock_unit") or "minutes").strip()

    try:
        limit = int(raw_limit or "0")
    except ValueError:
        limit = 0
    if limit < 1 or limit > 25:
        errors.append("Укажите допустимое количество ошибок от 1 до 25.")

    try:
        duration_value = int(raw_duration_value or "0")
    except ValueError:
        duration_value = 0
    if duration_value < 1 or duration_value > 365:
        errors.append("Укажите длительность блокировки от 1 до 365.")

    if duration_unit not in LOCK_DURATION_UNIT_SECONDS:
        errors.append("Некорректная единица времени для блокировки.")
        duration_unit = "minutes"

    return {
        "login_attempt_limit_enabled": enabled,
        "login_attempt_limit_count": limit,
        "login_attempt_lock_value": duration_value,
        "login_attempt_lock_unit": duration_unit,
        "login_attempt_lock_seconds": duration_value * LOCK_DURATION_UNIT_SECONDS[duration_unit],
    }, errors


def build_login_security_settings_context(request: HttpRequest) -> dict:
    if request.method == "POST":
        posted, _ = parse_login_attempt_settings_payload(request)
        return posted

    settings_row = get_login_security_settings()
    duration_value, duration_unit = split_lock_duration(settings_row.login_attempt_lock_seconds)
    return {
        "login_attempt_limit_enabled": settings_row.login_attempt_limit_enabled,
        "login_attempt_limit_count": settings_row.login_attempt_limit_count,
        "login_attempt_lock_value": duration_value,
        "login_attempt_lock_unit": duration_unit,
        "login_attempt_lock_seconds": settings_row.login_attempt_lock_seconds,
    }


def parse_visual_settings_payload(request: HttpRequest) -> tuple[dict, list[str]]:
    errors: list[str] = []
    field_specs = (
        ("login_particles_desktop_count", "Количество точек для desktop в окне авторизации"),
        ("blocked_particles_desktop_count", "Количество точек для desktop в окне блокировки по IP"),
        ("login_particles_mobile_count", "Количество точек для mobile в окне авторизации"),
        ("blocked_particles_mobile_count", "Количество точек для mobile в окне блокировки по IP"),
        ("calm_speed_px", "Скорость движения в спокойствии"),
        ("alert_speed_px", "Скорость движения в возбуждении"),
        ("invalid_alert_duration_seconds", "Время тревоги при неверном логине, пароле или токене"),
        ("lock_alert_duration_seconds", "Время тревоги при блокировке за превышение лимита"),
        ("alert_fade_duration_seconds", "Время затухания тревоги"),
    )
    payload: dict[str, int] = {}

    for field_name, label in field_specs:
        raw_value = (request.POST.get(field_name) or "").strip()
        try:
            value = int(raw_value or "0")
        except ValueError:
            value = 0
        payload[field_name] = value

        if field_name.endswith("_count"):
            min_value, max_value = VISUAL_PARTICLE_COUNT_LIMITS
            if value < min_value or value > max_value:
                errors.append(f"{label}: укажите значение от {min_value} до {max_value}.")
        elif field_name.endswith("_px"):
            min_value, max_value = VISUAL_PARTICLE_SPEED_LIMITS
            if value < min_value or value > max_value:
                errors.append(f"{label}: укажите значение от {min_value} до {max_value} пикселей в секунду.")
        else:
            min_value, max_value = VISUAL_PARTICLE_DURATION_LIMITS
            if value < min_value or value > max_value:
                errors.append(f"{label}: укажите значение от {min_value} до {max_value} секунд.")

    return payload, errors


def build_visual_settings_context(request: HttpRequest) -> dict:
    if request.method == "POST":
        posted, _ = parse_visual_settings_payload(request)
        return posted

    settings_row = get_portal_visual_settings()
    return {
        "login_particles_desktop_count": settings_row.login_particles_desktop_count,
        "blocked_particles_desktop_count": settings_row.blocked_particles_desktop_count,
        "login_particles_mobile_count": settings_row.login_particles_mobile_count,
        "blocked_particles_mobile_count": settings_row.blocked_particles_mobile_count,
        "calm_speed_px": settings_row.calm_speed_px,
        "alert_speed_px": settings_row.alert_speed_px,
        "invalid_alert_duration_seconds": settings_row.invalid_alert_duration_seconds,
        "lock_alert_duration_seconds": settings_row.lock_alert_duration_seconds,
        "alert_fade_duration_seconds": settings_row.alert_fade_duration_seconds,
    }


def build_user_form_context(
    request: HttpRequest,
    form,
    *,
    managed_user=None,
    page_heading: str,
    page_subtitle: str = "",
    active_tab: str = "basic",
    user_access_rule_errors: list[str] | None = None,
):
    sessions = get_active_sessions_for_user(managed_user) if managed_user else []
    audit_events = (
        AuditEvent.objects.filter(user=managed_user, is_business_event=True).select_related("service")[:10]
        if managed_user
        else []
    )
    login_lock_summary = get_login_lock_summary(managed_user) if managed_user else {"is_locked": False}
    return {
        "form": form,
        "managed_user": managed_user,
        "page_heading": page_heading,
        "page_subtitle": page_subtitle,
        "sessions": sessions,
        "audit_events": audit_events,
        "current_session_key": request.session.session_key or "",
        "is_modal": is_modal_request(request),
        "active_tab": active_tab,
        "user_access_rule_values": resolve_user_access_rule_values(request, managed_user),
        "user_access_rule_errors": user_access_rule_errors or [],
        "login_lock_summary": login_lock_summary,
        "login_lock_duration_label": (
            format_duration_label(login_lock_summary["remaining_seconds"])
            if login_lock_summary.get("is_locked")
            else ""
        ),
    }


def render_modal_form_response(request: HttpRequest, context: dict, *, status: int = 200) -> HttpResponse:
    template_name = "portal/rights/_user_modal_form.html" if is_modal_request(request) else "portal/rights/user_form.html"
    return render(request, template_name, context, status=status)


def render_user_table_fragment(request: HttpRequest, context: dict) -> HttpResponse:
    return render(request, "portal/rights/_users_table.html", context)


def render_logs_table_fragment(request: HttpRequest, context: dict) -> HttpResponse:
    return render(request, "portal/rights/_logs_table.html", context)


def build_security_context(request: HttpRequest, *, form_errors: list[str] | None = None) -> dict:
    if request.method == "POST":
        rule_values = get_posted_access_rule_values(request, "portal_access_rules")
    else:
        rule_values = get_portal_access_rule_values()
    security_settings_context = build_login_security_settings_context(request)
    return {
        "portal_access_rule_values": rule_values,
        "security_form_errors": form_errors or [],
        "current_client_ip": get_request_client_ip(request),
        "security_settings": security_settings_context,
        "lock_duration_units": (
            ("seconds", "Секунды"),
            ("minutes", "Минуты"),
            ("hours", "Часы"),
            ("days", "Дни"),
        ),
        "active_login_lock_counts": {
            "users": UserLoginLockState.objects.filter(locked_until__gt=timezone.now()).count(),
            "browsers": BrowserLoginLockState.objects.filter(locked_until__gt=timezone.now()).count(),
        },
        "login_lock_duration_label": format_duration_label(security_settings_context.get("login_attempt_lock_seconds", 0)),
    }


def build_service_management_context(request: HttpRequest) -> dict:
    services = enrich_services_for_ui(list(get_services_queryset()))
    return {
        "services": services,
        "service_counts": build_service_counts(services),
        "service_layout_board_html": render_service_layout_board(
            request,
            services=services,
            editable=True,
            preserve_gaps=True,
        ),
    }


def render_service_management_fragment(request: HttpRequest, context: dict) -> HttpResponse:
    return render(request, "portal/rights/_service_management_panel.html", context)


def render_service_modal_response(request: HttpRequest, context: dict, *, status: int = 200) -> HttpResponse:
    template_name = "portal/rights/_service_modal_form.html"
    if not is_modal_request(request):
        template_name = "portal/rights/service_form.html"
    return render(request, template_name, context, status=status)


def render_service_modal_html(request: HttpRequest, context: dict) -> str:
    return render_to_string("portal/rights/_service_modal_form.html", context, request=request)


def build_service_form_context(
    request: HttpRequest,
    form: ManagedServiceForm,
    *,
    service: Service | None = None,
    page_heading: str,
    page_subtitle: str = "",
    form_action_url: str = "",
    operation_message: str = "",
    revision_source=None,
) -> dict:
    def first_present(*values, default=""):
        for value in values:
            if value not in (None, ""):
                return value
        return default

    def coerce_int(value, default):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return default

    def coerce_bool(value, default=False):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no", ""}:
            return False
        return default

    resolved_service = service or form.instance
    if form.is_bound and form.is_valid():
        preview_service = form.save(commit=False)
    elif getattr(resolved_service, "pk", None):
        preview_service = resolved_service
    else:
        feature_values = form.data.getlist("managed_features") if hasattr(form.data, "getlist") else []
        preview_service = Service(
            title=str(first_present(form.data.get("title"), form.initial.get("title"), default="")).strip(),
            domain=str(first_present(form.data.get("domain"), form.initial.get("domain"), default="")).strip(),
            upstream_url=str(first_present(form.data.get("upstream_url"), form.initial.get("upstream_url"), default="http://127.0.0.1/")).strip(),
            description=str(first_present(form.data.get("description"), form.initial.get("description"), default="")).strip(),
            icon_key=str(first_present(form.data.get("icon_key"), form.initial.get("icon_key"), default="default")).strip() or "default",
            precheck_url=str(first_present(form.data.get("precheck_url"), form.initial.get("precheck_url"), default="")).strip(),
            cert_fullchain_path=str(first_present(form.data.get("cert_fullchain_path"), form.initial.get("cert_fullchain_path"), default="")).strip(),
            cert_privkey_path=str(first_present(form.data.get("cert_privkey_path"), form.initial.get("cert_privkey_path"), default="")).strip(),
            check_type=str(first_present(form.data.get("check_type"), form.initial.get("check_type"), default="http")).strip(),
            health_host=str(first_present(form.data.get("health_host"), form.initial.get("health_host"), default="")).strip(),
            health_port=coerce_int(first_present(form.data.get("health_port"), form.initial.get("health_port"), default=0), 0) or None,
            health_path=str(first_present(form.data.get("health_path"), form.initial.get("health_path"), default="/")).strip() or "/",
            timeout_seconds=coerce_int(first_present(form.data.get("timeout_seconds"), form.initial.get("timeout_seconds"), default=3), 3),
            allowed_status_codes=str(first_present(form.data.get("allowed_status_codes"), form.initial.get("allowed_status_codes"), default="200,204,301,302,401,403")).strip(),
            interval_seconds=coerce_int(first_present(form.data.get("interval_seconds"), form.initial.get("interval_seconds"), default=5), 5),
            failure_threshold=coerce_int(first_present(form.data.get("failure_threshold"), form.initial.get("failure_threshold"), default=2), 2),
            success_threshold=coerce_int(first_present(form.data.get("success_threshold"), form.initial.get("success_threshold"), default=1), 1),
            config_mode=str(first_present(form.data.get("config_mode"), form.initial.get("config_mode"), default="managed")).strip(),
            manual_config=str(first_present(form.data.get("manual_config"), form.initial.get("manual_config"), default="")),
            managed_features=feature_values or form.initial.get("managed_features", []),
            sort_order=coerce_int(first_present(form.data.get("sort_order"), form.initial.get("sort_order"), default=100), 100),
            is_enabled=coerce_bool(first_present(form.data.get("is_enabled"), form.initial.get("is_enabled"), default=False)),
            is_active=coerce_bool(first_present(form.data.get("is_active"), form.initial.get("is_active"), default=False)),
        )
    preview_service.sync_health_defaults()
    preview_service.sync_runtime_defaults()
    preview_config = render_effective_service_config(preview_service)
    live_config = read_live_service_config(preview_service.domain) if preview_service.domain else ""
    revisions = (
        ServiceRevision.objects.filter(service=resolved_service).select_related("created_by")[:12]
        if getattr(resolved_service, "pk", None)
        else []
    )
    return {
        "form": form,
        "managed_service": resolved_service if getattr(resolved_service, "pk", None) else None,
        "page_heading": page_heading,
        "page_subtitle": page_subtitle,
        "form_action_url": form_action_url,
        "operation_message": operation_message,
        "preview_config": preview_config,
        "live_config": live_config,
        "is_modal": is_modal_request(request),
        "service_option_rows": get_service_option_rows(
            preview_service.managed_features,
            upstream_url=preview_service.upstream_url,
        ),
        "service_option_catalog": get_service_option_catalog(),
        "service_option_profiles": get_service_profile_rows(),
        "service_revisions": revisions,
        "revision_source": revision_source,
    }


def compact_service_layout(actor=None) -> bool:
    changed = False
    with transaction.atomic():
        layouts = list(
            ServiceLayout.objects.select_for_update()
            .filter(scope=SERVICE_LAYOUT_SCOPE_GLOBAL, service__is_active=True)
            .select_related("service")
            .order_by("slot_index", "service__title", "service_id")
        )
        dirty_layouts = []
        timestamp = timezone.now()
        for index, layout in enumerate(layouts):
            if layout.slot_index == index:
                continue
            layout.slot_index = index
            layout.updated_by = actor
            layout.updated_at = timestamp
            dirty_layouts.append(layout)
            changed = True
        if dirty_layouts:
            ServiceLayout.objects.bulk_update(dirty_layouts, ["slot_index", "updated_by", "updated_at"])
    return changed


def build_service_target(service: Service, action: str = "Открытие") -> str:
    return f"{service.title} · {action}"


def build_portal_target(category: str, action: str) -> str:
    return f"{category} · {action}"


def log_service_access(
    request: HttpRequest,
    *,
    service: Service,
    result: str,
    status_code: int,
    auth_method: str,
    user=None,
    event_kind: str = "service_open",
    action: str = "Открытие",
) -> None:
    log_audit_event(
        request,
        user=user,
        service=service,
        auth_method=auth_method,
        target_label=build_service_target(service, action=action),
        event_kind=event_kind,
        result=result,
        category=service.title,
        action=action,
        status_code=status_code,
        metadata={"service_slug": service.slug},
    )


class StaffRequiredMixin(UserPassesTestMixin):
    login_url = reverse_lazy("portal:login")

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return redirect("portal:dashboard")
        request_path = sanitize_service_request_uri(self.request.get_full_path())
        login_path = reverse("portal:login")
        if request_path != login_path:
            self.request.session[SESSION_LOGIN_NEXT_URL_KEY] = request_path
        return redirect("portal:login")


class PortalLoginRequiredMixin(LoginRequiredMixin):
    login_url = reverse_lazy("portal:login")
    redirect_field_name = None

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return super().handle_no_permission()
        request_path = sanitize_service_request_uri(self.request.get_full_path())
        login_path = reverse("portal:login")
        if request_path != login_path:
            self.request.session[SESSION_LOGIN_NEXT_URL_KEY] = request_path
        return redirect("portal:login")


class DesktopServiceAdminMixin:
    desktop_only_message = "Управление сервисами доступно только в desktop-версии портала."

    def dispatch(self, request: HttpRequest, *args, **kwargs):
        if is_mobile_browser(request):
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or is_modal_request(request):
                return apply_no_store(JsonResponse({"ok": False, "message": self.desktop_only_message}, status=403))
            messages.error(request, self.desktop_only_message)
            return redirect("portal:rights_home")
        return super().dispatch(request, *args, **kwargs)


@method_decorator(never_cache, name="dispatch")
class DashboardView(PortalLoginRequiredMixin, TemplateView):
    template_name = "portal/dashboard.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Сервисы"),
            event_kind="page_view",
            result="success",
            category="Портал",
            action="Сервисы",
        )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        services = get_visible_services(self.request.user)
        context["services"] = services
        context["service_counts"] = build_service_counts(services)
        context["service_layout_board_html"] = render_service_layout_board(
            self.request,
            services=services,
            editable=False,
            preserve_gaps=True,
        )
        return context


@method_decorator(never_cache, name="dispatch")
class ServiceStatusesView(PortalLoginRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        services = get_visible_service_status_rows(request.user)
        payload = []
        for service in services:
            state = getattr(service, "health_state", None)
            payload.append(
                {
                    "id": service.pk,
                    "slug": service.slug,
                    "status": get_effective_status(service, state=state),
                    "checked_at": format_timestamp(state.last_checked_at),
                }
            )
        return apply_no_store(JsonResponse({"ok": True, "services": payload}))


@method_decorator(never_cache, name="dispatch")
class ReleaseStateView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return apply_no_store(JsonResponse({"ok": True, "release_id": get_release_id()}))


@method_decorator(never_cache, name="dispatch")
class ServiceOpenView(PortalLoginRequiredMixin, View):
    def get(self, request: HttpRequest, slug: str) -> HttpResponse:
        service = get_object_or_404(Service.objects.filter(is_active=True).select_related("health_state"), slug=slug)
        auth_method = get_request_auth_method(request)
        if not request_user_can_access_service(request, request.user, service):
            log_service_access(request, service=service, result="denied", status_code=403, auth_method=auth_method, user=request.user)
            return redirect("portal:dashboard")

        if get_effective_status(service, state=getattr(service, "health_state", None)) == "down":
            log_service_access(request, service=service, result="blocked", status_code=403, auth_method=auth_method, user=request.user)
            return redirect("portal:dashboard")

        log_service_access(request, service=service, result="success", status_code=302, auth_method=auth_method, user=request.user, event_kind="service_open")
        return redirect(service.launch_url)


@method_decorator(never_cache, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PortalLoginView(LoginView):
    template_name = "portal/login.html"
    authentication_form = PortalAuthenticationForm
    redirect_authenticated_user = True

    def wants_json_response(self) -> bool:
        requested_with = (self.request.headers.get("X-Requested-With") or "").strip()
        accept_header = (self.request.headers.get("Accept") or "").lower()
        return requested_with == "XMLHttpRequest" or "application/json" in accept_header

    def finalize_login_response(self, response: HttpResponse) -> HttpResponse:
        response = apply_no_store(response)
        if self.request.COOKIES.get(LOGIN_IP_BLOCK_COOKIE_NAME):
            response.delete_cookie(LOGIN_IP_BLOCK_COOKIE_NAME, path="/")
        return response

    def build_form_error_payload(self, form) -> dict[str, object]:
        field_errors: dict[str, list[str]] = {}
        for field_name, errors in form.errors.items():
            if field_name == NON_FIELD_ERRORS:
                continue
            normalized_errors = [str(error) for error in errors]
            if normalized_errors:
                field_errors[field_name] = normalized_errors
        return {
            "ok": False,
            "auth_method": form.get_auth_method(),
            "field_errors": field_errors,
            "non_field_errors": [str(error) for error in form.non_field_errors()],
        }

    def build_json_error_response(
        self,
        form,
        *,
        status: int,
        particle_variant: str = "calm",
        revert_delay_ms: int = 0,
        show_ip_access_denied: bool = False,
        lockout: bool = False,
    ) -> JsonResponse:
        payload = self.build_form_error_payload(form)
        payload.update(
            {
                "particle_variant": particle_variant,
                "revert_delay_ms": max(0, int(revert_delay_ms or 0)),
                "show_ip_access_denied": bool(show_ip_access_denied),
                "lockout": bool(lockout),
            }
        )
        return self.finalize_login_response(JsonResponse(payload, status=status))

    def build_json_success_response(self, redirect_target: str) -> JsonResponse:
        return self.finalize_login_response(
            JsonResponse(
                {
                    "ok": True,
                    "redirect_url": redirect_target,
                }
            )
        )

    def build_lock_response(self, form, decision) -> HttpResponse:
        if getattr(form, "_errors", None):
            form._errors.pop(NON_FIELD_ERRORS, None)
        form.add_error(None, decision.message)
        if self.wants_json_response():
            particle_context = build_particle_visual_context(blocked_page=False)
            return self.build_json_error_response(
                form,
                status=429,
                particle_variant="alert",
                revert_delay_ms=particle_context["login_particle_lock_alert_duration_ms"],
                lockout=True,
            )
        context = self.get_context_data(form=form)
        return self.render_to_response(context, status=429)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get("form")
        non_field_errors = [str(error) for error in form.non_field_errors()] if form is not None else []
        context.update(build_particle_visual_context(blocked_page=False))
        context["login_state"] = (self.request.POST.get("state") or self.request.GET.get("state") or "").strip()
        context["show_ip_access_denied"] = bool(
            kwargs.get("show_ip_access_denied")
            or self.request.GET.get("blocked") == "1"
            or self.request.COOKIES.get(LOGIN_IP_BLOCK_COOKIE_NAME) == "1"
        )
        context["show_login_error_alert"] = bool(non_field_errors)
        context["show_login_lock_alert"] = any(
            "Превышено количество попыток авторизоваться" in error for error in non_field_errors
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        return self.finalize_login_response(response)

    def post(self, request: HttpRequest, *args, **kwargs):
        form = self.get_form()
        auth_method = (request.POST.get("auth_method") or AUTH_METHOD_ACCOUNT).strip() or AUTH_METHOD_ACCOUNT
        requested_username = (request.POST.get("username") or "").strip()

        if auth_method == AUTH_METHOD_ACCOUNT:
            lock_decision = get_account_lock_decision(request, requested_username)
            if lock_decision.blocked:
                log_audit_event(
                    request,
                    user=lock_decision.user,
                    username_snapshot=requested_username or getattr(lock_decision.user, "username", ""),
                    auth_method=AUTH_METHOD_ACCOUNT,
                    target_label=build_portal_target("Безопасность", "Блокировка авторизации"),
                    event_kind="login_attempt_locked",
                    result="blocked",
                    category="Безопасность",
                    action="Блокировка авторизации",
                    status_code=429,
                )
                return self.build_lock_response(form, lock_decision)

        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def render_ip_access_denied(self, form):
        log_audit_event(
            self.request,
            username_snapshot=form.get_username_snapshot(),
            auth_method=form.get_auth_method(),
            target_label=build_portal_target("Безопасность", "Ограничение IP"),
            event_kind="login_ip_block",
            result="failure",
            category="Безопасность",
            action="Ограничение IP",
            status_code=403,
        )
        if self.wants_json_response():
            particle_context = build_particle_visual_context(blocked_page=False)
            return self.build_json_error_response(
                form,
                status=403,
                particle_variant="alert",
                revert_delay_ms=particle_context["login_particle_lock_alert_duration_ms"],
                show_ip_access_denied=True,
            )
        context = self.get_context_data(form=form, show_ip_access_denied=True)
        return self.render_to_response(context, status=403)

    def form_valid(self, form):
        user = form.get_user()
        auth_method = form.get_auth_method()
        user_access_decision = get_user_access_decision(user, get_request_client_ip(self.request))
        if user_access_decision.has_rules and not user_access_decision.allowed:
            return self.render_ip_access_denied(form)
        if auth_method == AUTH_METHOD_ACCOUNT:
            clear_login_restrictions_after_success(self.request, user)
        login(self.request, user)
        self.request.session[SESSION_AUTH_METHOD_KEY] = auth_method
        self.request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        ensure_session_record(self.request, user, auth_method)
        prime_request_user_access_cache(self.request, user)
        if auth_method == AUTH_METHOD_TOKEN:
            user.mark_login_token_used()
            user.save(update_fields=["login_token_last_used_at"])
        log_audit_event(
            self.request,
            user=user,
            username_snapshot=user.username,
            auth_method=auth_method,
            target_label=build_portal_target("Авторизация", "Вход"),
            event_kind="login",
            result="success",
            category="Авторизация",
            action="Вход",
            status_code=302,
        )
        redirect_target = build_post_login_target(self.request)
        if self.request.user.must_change_password and auth_method == AUTH_METHOD_ACCOUNT:
            self.request.session["post_password_change_redirect"] = redirect_target
            redirect_target = reverse("portal:password_change_first")
        if self.wants_json_response():
            return self.build_json_success_response(redirect_target)
        return redirect(redirect_target)

    def form_invalid(self, form):
        if not form.has_complete_auth_input():
            form.clear_missing_input_errors()
            if self.wants_json_response():
                return self.build_json_error_response(form, status=400)
            return self.render_to_response(self.get_context_data(form=form))
        if form.get_auth_method() == AUTH_METHOD_ACCOUNT:
            requested_username = (self.request.POST.get("username") or "").strip()
            requested_password = (self.request.POST.get("password") or "").strip()
            if requested_username and requested_password:
                lock_decision = register_failed_account_attempt(self.request, requested_username)
                if lock_decision.blocked:
                    log_audit_event(
                        self.request,
                        user=lock_decision.user,
                        username_snapshot=requested_username,
                        auth_method=AUTH_METHOD_ACCOUNT,
                        target_label=build_portal_target("Безопасность", "Блокировка авторизации"),
                        event_kind="login_attempt_locked",
                        result="blocked",
                        category="Безопасность",
                        action="Блокировка авторизации",
                        status_code=429,
                    )
                    return self.build_lock_response(form, lock_decision)
        log_audit_event(
            self.request,
            username_snapshot=form.get_username_snapshot(),
            auth_method=form.get_auth_method(),
            target_label=build_portal_target("Авторизация", "Вход"),
            event_kind="login",
            result="failure",
            category="Авторизация",
            action="Вход",
            status_code=400,
        )
        if self.wants_json_response():
            particle_context = build_particle_visual_context(blocked_page=False)
            return self.build_json_error_response(
                form,
                status=400,
                particle_variant="alert",
                revert_delay_ms=particle_context["login_particle_invalid_alert_duration_ms"],
            )
        return super().form_invalid(form)


@method_decorator(never_cache, name="dispatch")
class PortalLogoutView(PortalLoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        current_session = UserSession.objects.filter(session_key=request.session.session_key).first()
        if current_session:
            revoke_session_record(current_session)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Авторизация", "Выход"),
            event_kind="logout",
            result="success",
            category="Авторизация",
            action="Выход",
            status_code=302,
        )
        logout(request)
        return redirect("portal:login")

    def get(self, request: HttpRequest) -> HttpResponse:
        return redirect("portal:dashboard")


@method_decorator(never_cache, name="dispatch")
class FirstPasswordChangeView(PortalLoginRequiredMixin, View):
    template_name = "portal/password_change_first.html"

    def build_context(self, form):
        return {
            "form": form,
            "hide_authenticated_chrome": True,
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Смена временного пароля"),
            event_kind="page_view",
            result="success",
            category="Портал",
            action="Смена временного пароля",
        )
        form = FirstPasswordChangeForm(request.user)
        return render(request, self.template_name, self.build_context(form))

    def post(self, request: HttpRequest) -> HttpResponse:
        form = FirstPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password", "password_changed_at"])
            update_session_auth_hash(request, user)
            request.session[SESSION_AUTH_METHOD_KEY] = AUTH_METHOD_ACCOUNT
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            log_audit_event(
                request,
                user=user,
                auth_method=AUTH_METHOD_ACCOUNT,
                target_label=build_portal_target("Портал", "Смена пароля"),
                event_kind="password_change",
                result="success",
                category="Портал",
                action="Смена пароля",
            )
            messages.success(request, "Пароль успешно обновлён.")
            redirect_target = request.session.pop("post_password_change_redirect", reverse("portal:dashboard"))
            return redirect(redirect_target)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Смена пароля"),
            event_kind="password_change",
            result="failure",
            category="Портал",
            action="Смена пароля",
            status_code=400,
        )
        return render(request, self.template_name, self.build_context(form))


class ForbiddenView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return redirect(f"{settings.HUB_PORTAL_PUBLIC_URL}/")


class InternalLoginStateView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        if not is_trusted_internal_request(request):
            return HttpResponse(status=403)

        service_domain = (request.headers.get("X-Original-Service") or "").strip().lower()
        if not service_domain:
            return HttpResponse(status=403)

        service = Service.objects.filter(domain__iexact=service_domain, is_active=True).first()
        if service is None:
            return redirect(f"{settings.HUB_PORTAL_PUBLIC_URL}/")

        target_uri = sanitize_service_request_uri(request.headers.get("X-Original-URI", "/"))
        login_state = create_login_redirect_state(
            service=service,
            target_url=f"https://{service.domain}{target_uri}",
            client_ip=get_request_client_ip(request),
        )
        request.session[SESSION_LOGIN_REDIRECT_STATE_KEY] = str(login_state.token)
        request.session.modified = True
        return apply_no_store(redirect(request.build_absolute_uri(reverse("portal:login"))))


class InternalAuthzView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        if not is_trusted_internal_request(request):
            return HttpResponse(status=403)

        host = (request.headers.get("X-Original-Service") or "").strip().lower()
        auth_method = get_request_auth_method(request)
        service = Service.objects.filter(domain__iexact=host, is_active=True).select_related("health_state").first()

        portal_access_decision = getattr(request, "portal_access_decision", None)
        if portal_access_decision is None:
            portal_access_decision = evaluate_ip_access(get_request_client_ip(request), get_portal_access_rule_values())
        if not portal_access_decision.allowed:
            return HttpResponse(status=403)

        if not request.user.is_authenticated:
            if service:
                log_service_access(
                    request,
                    service=service,
                    result="unauthenticated",
                    status_code=401,
                    auth_method=auth_method,
                    event_kind="service_gate",
                    action="Прямой доступ",
                )
            return HttpResponse(status=401)
        if not host or not service:
            return HttpResponse(status=403)

        user_access_decision = getattr(request, "user_access_decision", None)
        if user_access_decision is None:
            user_access_decision = get_user_access_decision_for_request(request, request.user)
        if user_access_decision.has_rules and not user_access_decision.allowed:
            log_service_access(
                request,
                service=service,
                result="blocked",
                status_code=403,
                auth_method=auth_method,
                user=request.user,
                event_kind="service_gate",
                action="IP-ограничение",
            )
            return HttpResponse(status=403)
        if getattr(request.user, "must_change_password", False) and not is_token_authenticated_session(request):
            log_service_access(
                request,
                service=service,
                result="password_change_required",
                status_code=403,
                auth_method=auth_method,
                user=request.user,
                event_kind="service_gate",
                action="Прямой доступ",
            )
            return HttpResponse(status=403)

        allowed = request_user_can_access_service(request, request.user, service)
        effective_status = get_effective_status(service, state=getattr(service, "health_state", None))
        if not allowed:
            log_service_access(
                request,
                service=service,
                result="denied",
                status_code=403,
                auth_method=auth_method,
                user=request.user,
                event_kind="service_gate",
                action="Прямой доступ",
            )
            return HttpResponse(status=403)
        if effective_status == "down":
            log_service_access(
                request,
                service=service,
                result="blocked",
                status_code=403,
                auth_method=auth_method,
                user=request.user,
                event_kind="service_gate",
                action="Прямой доступ",
            )
            return HttpResponse(status=403)
        return HttpResponse(status=200)


@method_decorator(never_cache, name="dispatch")
class AccountView(PortalLoginRequiredMixin, View):
    template_name = "portal/account.html"

    def build_context(self, request: HttpRequest, form: AccountPasswordChangeForm) -> dict:
        return {
            "form": form,
            "has_login_token": request.user.has_login_token,
            "can_reveal_login_token": request.user.can_reveal_login_token,
            "login_token_created_at": request.user.login_token_created_at,
            "login_token_last_used_at": request.user.login_token_last_used_at,
            "login_token_created_by": request.user.login_token_created_by,
            "sessions": get_active_sessions_for_user(request.user),
            "current_session_key": request.session.session_key or "",
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Учётная запись"),
            event_kind="page_view",
            result="success",
            category="Портал",
            action="Учётная запись",
        )
        form = AccountPasswordChangeForm(request.user)
        return render(request, self.template_name, self.build_context(request, form))

    def post(self, request: HttpRequest) -> HttpResponse:
        form = AccountPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            if user.must_change_password:
                user.must_change_password = False
                user.save(update_fields=["password", "must_change_password", "password_changed_at"])
            else:
                user.save(update_fields=["password", "password_changed_at"])
            update_session_auth_hash(request, user)
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            log_audit_event(
                request,
                user=user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Портал", "Смена пароля"),
                event_kind="password_change",
                result="success",
                category="Портал",
                action="Смена пароля",
            )
            messages.success(request, "Пароль обновлён.")
            return redirect("portal:account")
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Смена пароля"),
            event_kind="password_change",
            result="failure",
            category="Портал",
            action="Смена пароля",
            status_code=400,
        )
        return render(request, self.template_name, self.build_context(request, form))


@method_decorator(never_cache, name="dispatch")
class AccountTokenRevealView(PortalLoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        if not request.user.can_reveal_login_token:
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Токены", "Показ"),
                event_kind="token_reveal",
                result="missing",
                category="Токены",
                action="Показ",
                status_code=404,
            )
            return apply_no_store(
                JsonResponse({"ok": False, "token": "", "generated_at": "", "has_token": False, "can_reveal": False}, status=404)
            )

        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Токены", "Показ"),
            event_kind="token_reveal",
            result="success",
            category="Токены",
            action="Показ",
        )
        return apply_no_store(JsonResponse(build_token_response(request.user)))


@method_decorator(never_cache, name="dispatch")
class AccountSessionRevokeView(PortalLoginRequiredMixin, View):
    def post(self, request: HttpRequest, session_key: str) -> HttpResponse:
        session_record = get_object_or_404(UserSession, session_key=session_key, user=request.user)
        revoke_session_record(session_record)
        if session_key == request.session.session_key:
            logout(request)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Сессии", "Завершение"),
            event_kind="session_revoke",
            result="success",
            category="Сессии",
            action="Завершение",
            metadata={"session_key": session_key},
        )
        return apply_no_store(JsonResponse({"ok": True, "session_key": session_key, "logged_out": session_key == request.session.session_key}))


@method_decorator(never_cache, name="dispatch")
class AccountSessionRevokeOthersView(PortalLoginRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        count = revoke_user_sessions(request.user, exclude_session_key=request.session.session_key or "")
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Сессии", "Завершение остальных"),
            event_kind="session_revoke_all",
            result="success",
            category="Сессии",
            action="Завершение остальных",
            metadata={"count": count},
        )
        return apply_no_store(JsonResponse({"ok": True, "count": count}))


class UserManagementPageMixin(StaffRequiredMixin, View):
    template_name = "portal/rights/users.html"

    def get_page_context(self):
        managed_users, selected_filters = get_user_management_queryset(self.request)
        page_obj, page_size = paginate_queryset(
            self.request,
            managed_users,
            options=USER_PAGE_SIZE_OPTIONS,
            default=DEFAULT_USER_PAGE_SIZE,
        )
        return {
            "managed_users_page": page_obj,
            "managed_users": page_obj.object_list,
            "user_page_size": page_size,
            "user_page_size_options": USER_PAGE_SIZE_OPTIONS,
            "selected_filters": selected_filters,
            "service_filter_options": Service.objects.filter(is_active=True),
        }

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        context = self.get_page_context()
        if get_fragment_name(request) == "users-table":
            return render_user_table_fragment(request, context)
        return render(request, self.template_name, context)


@method_decorator(never_cache, name="dispatch")
class RightsHomeView(StaffRequiredMixin, TemplateView):
    template_name = "portal/rights/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_service_management_context(self.request))
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        context = self.get_context_data()
        if get_fragment_name(request) == "service-management":
            return render_service_management_fragment(request, context)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Портал", "Управление"),
            event_kind="page_view",
            result="success",
            category="Портал",
            action="Управление",
        )
        return render(request, self.template_name, context)


@method_decorator(never_cache, name="dispatch")
class SecuritySettingsView(StaffRequiredMixin, TemplateView):
    template_name = "portal/security.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_security_context(self.request, form_errors=kwargs.get("form_errors")))
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Безопасность", "Настройки"),
            event_kind="page_view",
            result="success",
            category="Безопасность",
            action="Настройки",
        )
        return render(request, self.template_name, self.get_context_data())

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.POST.get("security_action") == "reset_lockouts":
            reset_result = clear_all_account_login_restrictions()
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Безопасность", "Сброс ограничений"),
                event_kind="login_lock_reset_all",
                result="success",
                category="Безопасность",
                action="Сброс ограничений",
                metadata=reset_result,
            )
            messages.success(request, "Ограничения авторизации и слепки браузеров сброшены.")
            return redirect("portal:security")

        rule_values, form_errors = validate_access_rule_list(get_posted_access_rule_values(request, "portal_access_rules"))
        login_settings_payload, login_settings_errors = parse_login_attempt_settings_payload(request)
        form_errors.extend(login_settings_errors)
        current_ip = get_request_client_ip(request)
        if not form_errors and rule_values and current_ip and not evaluate_ip_access(current_ip, rule_values).allowed:
            form_errors.append(f"Текущий IP {current_ip} должен остаться в списке, иначе вы заблокируете собственный доступ.")

        if form_errors:
            return render(request, self.template_name, self.get_context_data(form_errors=form_errors), status=400)

        previous_rules = get_portal_access_rule_values()
        security_settings_row = get_login_security_settings()
        previous_enabled = security_settings_row.login_attempt_limit_enabled
        replace_portal_access_rules(rule_values)
        security_settings_row.login_attempt_limit_enabled = login_settings_payload["login_attempt_limit_enabled"]
        security_settings_row.login_attempt_limit_count = login_settings_payload["login_attempt_limit_count"]
        security_settings_row.login_attempt_lock_seconds = login_settings_payload["login_attempt_lock_seconds"]
        security_settings_row.save(
            update_fields=[
                "login_attempt_limit_enabled",
                "login_attempt_limit_count",
                "login_attempt_lock_seconds",
                "updated_at",
            ]
        )
        if previous_enabled != security_settings_row.login_attempt_limit_enabled:
            clear_all_account_login_restrictions()
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Безопасность", "Обновление IP-правил"),
            event_kind="security_policy_update",
            result="success",
            category="Безопасность",
            action="Обновление IP-правил",
            metadata={
                "portal_access_rules_before": previous_rules,
                "portal_access_rules_after": rule_values,
                "login_attempt_limit_enabled": security_settings_row.login_attempt_limit_enabled,
                "login_attempt_limit_count": security_settings_row.login_attempt_limit_count,
                "login_attempt_lock_seconds": security_settings_row.login_attempt_lock_seconds,
            },
        )
        messages.success(request, "Настройки безопасности HUB обновлены.")
        return redirect("portal:security")


@method_decorator(never_cache, name="dispatch")
class VisualSettingsView(StaffRequiredMixin, TemplateView):
    template_name = "portal/visual.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["visual_settings"] = build_visual_settings_context(self.request)
        context["visual_form_errors"] = kwargs.get("form_errors") or []
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Визуал", "Настройки"),
            event_kind="page_view",
            result="success",
            category="Визуал",
            action="Настройки",
        )
        return render(request, self.template_name, self.get_context_data())

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        payload, form_errors = parse_visual_settings_payload(request)
        if form_errors:
            return render(request, self.template_name, self.get_context_data(form_errors=form_errors), status=400)

        settings_row = get_portal_visual_settings()
        for field_name, value in payload.items():
            setattr(settings_row, field_name, value)
        settings_row.save(
            update_fields=[
                "login_particles_desktop_count",
                "blocked_particles_desktop_count",
                "login_particles_mobile_count",
                "blocked_particles_mobile_count",
                "calm_speed_px",
                "alert_speed_px",
                "invalid_alert_duration_seconds",
                "lock_alert_duration_seconds",
                "alert_fade_duration_seconds",
                "updated_at",
            ]
        )
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Визуал", "Обновление параметров"),
            event_kind="visual_settings_update",
            result="success",
            category="Визуал",
            action="Обновление параметров",
            metadata=payload,
        )
        messages.success(request, "Параметры анимации обновлены.")
        return redirect("portal:visual_settings")


@method_decorator(never_cache, name="dispatch")
class RightsUsersView(UserManagementPageMixin):
    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Список"),
            event_kind="page_view",
            result="success",
            category="Пользователи",
            action="Список",
        )
        return super().get(request, *args, **kwargs)


@method_decorator(never_cache, name="dispatch")
class RightsServiceLayoutUpdateView(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            service_id = int(request.POST.get("service_id", "0"))
            target_slot_index = int(request.POST.get("slot_index", "0"))
        except (TypeError, ValueError):
            return apply_no_store(JsonResponse({"ok": False, "message": "Некорректные параметры раскладки."}, status=400))

        service = get_object_or_404(Service.objects.filter(is_active=True), pk=service_id)
        changed = move_service_layout_slot(
            service=service,
            target_slot_index=target_slot_index,
            actor=request.user,
        )
        services = enrich_services_for_ui(list(get_services_queryset()))

        if changed:
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Управление", "Раскладка сервисов"),
                event_kind="service_layout_update",
                result="success",
                category="Управление",
                action="Раскладка сервисов",
                metadata={"service_slug": service.slug, "slot_index": target_slot_index},
            )

        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "changed": changed,
                    "message": (
                        f"Положение сервиса {service.title} сохранено."
                        if changed
                        else f"Сервис {service.title} уже находится в выбранной ячейке."
                    ),
                    "board_html": render_service_layout_board(
                        request,
                        services=services,
                        editable=True,
                        preserve_gaps=True,
                    ),
                }
            )
        )


@method_decorator(never_cache, name="dispatch")
class RightsServiceLayoutResetView(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        changed = reset_service_layout(actor=request.user)
        services = enrich_services_for_ui(list(get_services_queryset()))

        if changed:
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Управление", "Сброс раскладки сервисов"),
                event_kind="service_layout_reset",
                result="success",
                category="Управление",
                action="Сброс раскладки сервисов",
            )

        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "changed": changed,
                    "message": (
                        "Раскладка сервисов сброшена к исходному порядку."
                        if changed
                        else "Раскладка сервисов уже находится в исходном порядке."
                    ),
                    "board_html": render_service_layout_board(
                        request,
                        services=services,
                        editable=True,
                        preserve_gaps=True,
                    ),
                }
            )
        )


class ServiceManagementFormMixin(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    form_class = ManagedServiceForm
    managed_service = None

    def get_service(self) -> Service | None:
        return self.managed_service

    def resolve_context_service(self, form: ManagedServiceForm | None = None, service: Service | None = None) -> Service | None:
        if service and getattr(service, "pk", None):
            return service
        if form is not None and getattr(form.instance, "pk", None):
            return form.instance
        candidate = self.get_service()
        if candidate and getattr(candidate, "pk", None):
            return candidate
        return None

    def get_form(self, data=None, *, initial=None, instance=None):
        return self.form_class(
            data=data,
            instance=instance if instance is not None else self.get_service(),
            initial=initial,
            actor=self.request.user,
        )

    def get_page_heading(self, service: Service | None = None) -> str:
        return "Редактирование сервиса" if self.resolve_context_service(service=service) else "Новый сервис"

    def get_page_subtitle(self, service: Service | None = None) -> str:
        service = self.resolve_context_service(service=service)
        if not service:
            return "Добавление защищённого reverse proxy или внешней ссылки."
        return service.external_url if service.is_external_link else service.domain

    def get_form_action_url(self, service: Service | None = None) -> str:
        resolved_service = self.resolve_context_service(service=service)
        if resolved_service:
            return reverse("portal:rights_service_edit", args=[resolved_service.pk])
        return reverse("portal:rights_service_create")

    def get_revision_source(self):
        service = self.get_service()
        revision_id = (self.request.GET.get("revision") or "").strip()
        if not (service and revision_id.isdigit()):
            return None
        revision = get_object_or_404(ServiceRevision, service=service, pk=int(revision_id))
        return revision

    def get_initial_from_revision(self, revision: ServiceRevision | None) -> dict | None:
        if not revision:
            return None
        snapshot = revision.snapshot or {}
        initial = {}
        for field_name in self.form_class.Meta.fields:
            if field_name in snapshot:
                initial[field_name] = snapshot[field_name]
        return initial

    def build_context(self, form, *, operation_message: str = "", revision_source=None, page_subtitle: str | None = None):
        resolved_service = self.resolve_context_service(form=form)
        return build_service_form_context(
            self.request,
            form,
            service=resolved_service,
            page_heading=self.get_page_heading(resolved_service),
            page_subtitle=page_subtitle if page_subtitle is not None else self.get_page_subtitle(resolved_service),
            form_action_url=self.get_form_action_url(resolved_service),
            operation_message=operation_message,
            revision_source=revision_source,
        )

    def render_modal_json(self, *, context: dict, message: str, refresh: bool = False, status: int = 200) -> HttpResponse:
        payload = {
            "ok": status < 400,
            "message": message,
            "keep_open": True,
            "html": render_service_modal_html(self.request, context),
            "refresh": refresh,
        }
        return apply_no_store(JsonResponse(payload, status=status))

    def save_service_form(self, form: ManagedServiceForm) -> Service:
        service = form.save(commit=False)
        service.save()
        return service


@method_decorator(never_cache, name="dispatch")
class RightsServiceCreateView(ServiceManagementFormMixin):
    def get(self, request: HttpRequest) -> HttpResponse:
        form = self.get_form(initial=self.get_initial_from_revision(None))
        return render_service_modal_response(request, self.build_context(form))

    def post(self, request: HttpRequest) -> HttpResponse:
        operation = (request.POST.get("operation") or "draft").strip()
        if operation == "issue_certificate":
            if (request.POST.get("kind") or "proxy") == "external":
                form = self.get_form(data=request.POST)
                form.add_error("kind", "Внешней ссылке сертификат портала не требуется.")
                return self.render_modal_json(
                    context=self.build_context(form),
                    message="Сертификат доступен только для reverse proxy.",
                    status=400,
                )
            domain = (request.POST.get("domain") or "").strip().lower()
            post_data = request.POST.copy()
            result = issue_service_certificate(domain) if domain else None
            if result and result.ok:
                post_data["cert_fullchain_path"] = result.details.get("cert_fullchain_path", "")
                post_data["cert_privkey_path"] = result.details.get("cert_privkey_path", "")
                form = self.get_form(data=post_data)
                context = self.build_context(form, operation_message=result.message)
                return self.render_modal_json(context=context, message="Сертификат получен.", refresh=False)
            form = self.get_form(data=post_data)
            form.add_error("domain", result.message if result else "Сначала укажите домен для сертификата.")
            context = self.build_context(form, operation_message=result.message if result else "")
            return self.render_modal_json(
                context=context,
                message=result.message if result else "Не удалось получить сертификат.",
                status=400,
            )

        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return render_service_modal_response(request, self.build_context(form), status=400)

        service = self.save_service_form(form)
        config_text = render_effective_service_config(service)
        snapshot = build_service_snapshot(service)

        if operation == "validate":
            result = validate_service_candidate(service, config_text)
            create_service_revision(
                service=service,
                actor=request.user,
                revision_kind="validate",
                summary="Проверка синтаксиса nginx",
                rendered_config=config_text,
                validated_ok=result.ok,
                snapshot=snapshot,
            )
            context = self.build_context(self.get_form(instance=service), operation_message=result.message)
            return self.render_modal_json(
                context=context,
                message="Проверка nginx завершена успешно." if result.ok else result.message,
                refresh=True,
                status=200 if result.ok else 400,
            )

        if operation == "apply":
            result = apply_service_candidate(service, config_text)
            create_service_revision(
                service=service,
                actor=request.user,
                revision_kind="apply",
                summary="Применение конфига сервиса",
                rendered_config=config_text,
                validated_ok=result.ok,
                snapshot=snapshot,
            )
            if not result.ok:
                context = self.build_context(self.get_form(instance=service), operation_message=result.message)
                return self.render_modal_json(context=context, message=result.message, refresh=True, status=400)
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Управление", "Создание сервиса"),
                event_kind="service_create",
                result="success",
                category="Управление",
                action="Создание сервиса",
                metadata={"service_slug": service.slug},
            )
            return apply_no_store(JsonResponse({"ok": True, "message": f"Сервис {service.title} опубликован.", "refresh": True}))

        create_service_revision(
            service=service,
            actor=request.user,
            revision_kind="draft",
            summary="Сохранение черновика сервиса",
            rendered_config=config_text,
            validated_ok=False,
            snapshot=snapshot,
        )
        context = self.build_context(self.get_form(instance=service), operation_message="Черновик сохранён.")
        return self.render_modal_json(context=context, message="Черновик сервиса сохранён.", refresh=True)


@method_decorator(never_cache, name="dispatch")
class RightsServiceUpdateView(ServiceManagementFormMixin):
    def dispatch(self, request: HttpRequest, *args, **kwargs):
        self.managed_service = get_object_or_404(Service, pk=kwargs["service_id"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest, service_id: int) -> HttpResponse:
        revision = self.get_revision_source()
        initial = self.get_initial_from_revision(revision)
        form = self.get_form(initial=initial)
        subtitle = self.get_page_subtitle()
        if revision:
            subtitle = f"{subtitle} · ревизия {revision.created_at.strftime('%d.%m.%Y %H:%M')}"
        return render_service_modal_response(
            request,
            self.build_context(
                form,
                revision_source=revision,
                operation_message="Открыта ревизия для восстановления." if revision else "",
                page_subtitle=subtitle,
            ),
        )

    def post(self, request: HttpRequest, service_id: int) -> HttpResponse:
        operation = (request.POST.get("operation") or "draft").strip()
        if operation == "issue_certificate":
            if (request.POST.get("kind") or self.managed_service.kind) == "external":
                form = self.get_form(data=request.POST)
                form.add_error("kind", "Внешней ссылке сертификат портала не требуется.")
                return self.render_modal_json(
                    context=self.build_context(form),
                    message="Сертификат доступен только для reverse proxy.",
                    status=400,
                )
            domain = (request.POST.get("domain") or self.managed_service.domain).strip().lower()
            post_data = request.POST.copy()
            result = issue_service_certificate(domain) if domain else None
            if result and result.ok:
                post_data["cert_fullchain_path"] = result.details.get("cert_fullchain_path", "")
                post_data["cert_privkey_path"] = result.details.get("cert_privkey_path", "")
                form = self.get_form(data=post_data)
                context = self.build_context(form, operation_message=result.message)
                create_service_revision(
                    service=self.managed_service,
                    actor=request.user,
                    revision_kind="certificate",
                    summary="Получение сертификата",
                    rendered_config=read_live_service_config(self.managed_service.domain),
                    validated_ok=True,
                    snapshot=build_service_snapshot(self.managed_service),
                )
                return self.render_modal_json(context=context, message="Сертификат обновлён.", refresh=False)
            form = self.get_form(data=post_data)
            form.add_error("domain", result.message if result else "Сначала укажите домен для сертификата.")
            context = self.build_context(form, operation_message=result.message if result else "")
            return self.render_modal_json(
                context=context,
                message=result.message if result else "Не удалось получить сертификат.",
                status=400,
            )

        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return render_service_modal_response(request, self.build_context(form), status=400)

        service = self.save_service_form(form)
        config_text = render_effective_service_config(service)
        snapshot = build_service_snapshot(service)

        if operation == "validate":
            result = validate_service_candidate(service, config_text)
            create_service_revision(
                service=service,
                actor=request.user,
                revision_kind="validate",
                summary="Проверка синтаксиса nginx",
                rendered_config=config_text,
                validated_ok=result.ok,
                snapshot=snapshot,
            )
            context = self.build_context(self.get_form(instance=service), operation_message=result.message)
            return self.render_modal_json(
                context=context,
                message="Проверка nginx завершена успешно." if result.ok else result.message,
                refresh=True,
                status=200 if result.ok else 400,
            )

        if operation == "apply":
            result = apply_service_candidate(service, config_text)
            create_service_revision(
                service=service,
                actor=request.user,
                revision_kind="apply",
                summary="Применение конфига сервиса",
                rendered_config=config_text,
                validated_ok=result.ok,
                snapshot=snapshot,
            )
            if not result.ok:
                context = self.build_context(self.get_form(instance=service), operation_message=result.message)
                return self.render_modal_json(context=context, message=result.message, refresh=True, status=400)
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Управление", "Редактирование сервиса"),
                event_kind="service_update",
                result="success",
                category="Управление",
                action="Редактирование сервиса",
                metadata={"service_slug": service.slug},
            )
            return apply_no_store(JsonResponse({"ok": True, "message": f"Сервис {service.title} обновлён.", "refresh": True}))

        create_service_revision(
            service=service,
            actor=request.user,
            revision_kind="draft",
            summary="Сохранение черновика сервиса",
            rendered_config=config_text,
            validated_ok=False,
            snapshot=snapshot,
        )
        context = self.build_context(self.get_form(instance=service), operation_message="Черновик сохранён.")
        return self.render_modal_json(context=context, message="Черновик сервиса сохранён.", refresh=True)


@method_decorator(never_cache, name="dispatch")
class RightsServiceForceCheckView(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    def post(self, request: HttpRequest, service_id: int) -> HttpResponse:
        service = get_object_or_404(Service, pk=service_id, is_active=True)
        state = update_service_health(service)
        status_label = get_effective_status(service, state=state)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_service_target(service, "Принудительная проверка"),
            event_kind="service_force_check",
            result="success",
            category="Управление",
            action="Принудительная проверка",
            metadata={"service_slug": service.slug, "status": status_label},
        )
        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "message": f"Проверка {service.title}: {status_label}.",
                    "refresh": True,
                }
            )
        )


@method_decorator(never_cache, name="dispatch")
class RightsServiceToggleEnabledView(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    def post(self, request: HttpRequest, service_id: int) -> HttpResponse:
        service = get_object_or_404(Service, pk=service_id, is_active=True)
        if service.is_enabled:
            result = disable_service_candidate(service)
            next_state = False
            action = "Отключение сервиса"
            event_kind = "service_disable"
        else:
            result = enable_service_candidate(service)
            next_state = True
            action = "Включение сервиса"
            event_kind = "service_enable"
        if not result.ok:
            return apply_no_store(JsonResponse({"ok": False, "message": result.message}, status=400))
        service.is_enabled = next_state
        service.save(update_fields=["is_enabled", "updated_at"])
        create_service_revision(
            service=service,
            actor=request.user,
            revision_kind=event_kind,
            summary=action,
            rendered_config=render_effective_service_config(service),
            validated_ok=True,
            snapshot=build_service_snapshot(service),
        )
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_service_target(service, action),
            event_kind=event_kind,
            result="success",
            category="Управление",
            action=action,
            metadata={"service_slug": service.slug, "enabled": next_state},
        )
        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "message": f"Сервис {service.title} {'включён' if next_state else 'отключён'}.",
                    "refresh": True,
                }
            )
        )


@method_decorator(never_cache, name="dispatch")
class RightsServiceDeleteView(DesktopServiceAdminMixin, StaffRequiredMixin, View):
    def post(self, request: HttpRequest, service_id: int) -> HttpResponse:
        service = get_object_or_404(Service, pk=service_id)
        rendered_config = render_effective_service_config(service)
        snapshot = build_service_snapshot(service)
        result = delete_service_candidate(service)
        if not result.ok:
            return apply_no_store(JsonResponse({"ok": False, "message": result.message}, status=400))
        create_service_revision(
            service=service,
            actor=request.user,
            revision_kind="delete",
            summary="Удаление сервиса",
            rendered_config=rendered_config,
            validated_ok=True,
            snapshot=snapshot,
        )
        title = service.title
        slug = service.slug
        service.delete()
        compact_service_layout(actor=request.user)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Управление", "Удаление сервиса"),
            event_kind="service_delete",
            result="success",
            category="Управление",
            action="Удаление сервиса",
            metadata={"service_slug": slug, "service_title": title},
        )
        return apply_no_store(JsonResponse({"ok": True, "message": f"Сервис {title} удалён.", "refresh": True}))


@method_decorator(never_cache, name="dispatch")
class RightsLogsView(StaffRequiredMixin, TemplateView):
    template_name = "portal/rights/logs.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        suppress_audit = bool(request.session.pop(SUPPRESS_NEXT_LOGS_AUDIT_SESSION_KEY, False))
        if suppress_audit:
            request.session.modified = True
        else:
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Портал", "Журнал"),
                event_kind="page_view",
                result="success",
                category="Портал",
                action="Журнал",
            )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset, selected_filters = get_log_queryset(self.request)
        page_obj, page_size = paginate_queryset(
            self.request,
            queryset,
            options=LOG_PAGE_SIZE_OPTIONS,
            default=DEFAULT_LOG_PAGE_SIZE,
        )
        context.update(
            {
                "audit_page": page_obj,
                "audit_events": page_obj.object_list,
                "log_page_size": page_size,
                "log_page_size_options": LOG_PAGE_SIZE_OPTIONS,
                "selected_filters": selected_filters,
                "filter_options": get_log_filter_options(),
                "result_labels": RESULT_LABELS,
            }
        )
        return context


@method_decorator(never_cache, name="dispatch")
class RightsLogsClearView(StaffRequiredMixin, View):
    confirmation_phrase = "ОЧИСТИТЬ"

    def post(self, request: HttpRequest) -> HttpResponse:
        phrase = (request.POST.get("confirmation_phrase") or "").strip()
        if phrase != self.confirmation_phrase:
            return apply_no_store(
                JsonResponse(
                    {
                        "ok": False,
                        "message": f'Подтверждение не принято. Введите точную фразу "{self.confirmation_phrase}".',
                    },
                    status=400,
                )
            )

        try:
            payload = clear_audit_trail()
        except AuditMaintenanceBusyError as exc:
            return apply_no_store(JsonResponse({"ok": False, "message": str(exc)}, status=409))
        except AuditMaintenanceError as exc:
            return apply_no_store(JsonResponse({"ok": False, "message": str(exc)}, status=500))

        request.session[SUPPRESS_NEXT_LOGS_AUDIT_SESSION_KEY] = True
        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "deleted_events": payload.get("deleted_events", 0),
                    "cleared_files": payload.get("cleared_files", 0),
                    "removed_archives": payload.get("removed_archives", 0),
                    "message": payload.get("message") or "Журнал очищен.",
                    "redirect_url": reverse("portal:rights_logs"),
                }
            )
        )


@method_decorator(never_cache, name="dispatch")
class RightsLogsExportView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        queryset, _ = get_log_queryset(request)
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Кто", "Когда", "IP", "Куда", "Метод авторизации", "Результат"])
        for event in queryset[:5000]:
            writer.writerow(
                [
                    event.username_snapshot or "Неизвестно",
                    timezone.localtime(event.occurred_at).strftime("%d.%m.%Y %H:%M:%S"),
                    event.ip_address or "—",
                    event.target_label,
                    event.get_auth_method_display(),
                    RESULT_LABELS.get(event.result, event.result),
                ]
            )
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="hub-audit.csv"'
        return response


@method_decorator(never_cache, name="dispatch")
class RightsLogDetailView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest, event_id: int) -> HttpResponse:
        event = get_object_or_404(AuditEvent.objects.select_related("user", "service"), pk=event_id, is_business_event=True)
        return render(request, "portal/rights/_audit_event_detail.html", {"event": event, "result_labels": RESULT_LABELS})


@method_decorator(never_cache, name="dispatch")
class RightsUserCreateView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Новый пользователь"),
            event_kind="page_view",
            result="success",
            category="Пользователи",
            action="Новый пользователь",
        )
        form = ManagedUserCreateForm(actor=request.user)
        context = build_user_form_context(request, form, page_heading="Новый пользователь")
        return render_modal_form_response(request, context)

    def post(self, request: HttpRequest) -> HttpResponse:
        form = ManagedUserCreateForm(request.POST, actor=request.user)
        user_rule_values, user_rule_errors = validate_access_rule_list(get_posted_access_rule_values(request, "user_access_rules"))
        if form.is_valid() and not user_rule_errors:
            user = form.save()
            replace_user_access_rules(user, user_rule_values)
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Пользователи", "Создание"),
                event_kind="user_create",
                result="success",
                category="Пользователи",
                action="Создание",
                metadata={"username": user.username},
            )
            if is_modal_request(request):
                return apply_no_store(JsonResponse({"ok": True, "message": f"Пользователь {user.username} создан.", "refresh": True}))
            messages.success(request, f"Пользователь {user.username} создан.")
            return redirect("portal:rights_user_edit", user_id=user.pk)

        context = build_user_form_context(
            request,
            form,
            page_heading="Новый пользователь",
            active_tab="security" if user_rule_errors else "basic",
            user_access_rule_errors=user_rule_errors,
        )
        if is_modal_request(request):
            html = render_to_string("portal/rights/_user_modal_form.html", context, request=request)
            return JsonResponse({"ok": False, "html": html}, status=400)
        return render_modal_form_response(request, context, status=400)


@method_decorator(never_cache, name="dispatch")
class RightsUserUpdateView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User.objects.prefetch_related("services", "access_rules"), pk=user_id)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Карточка"),
            event_kind="page_view",
            result="success",
            category="Пользователи",
            action="Карточка",
            metadata={"username": user.username},
        )
        form = ManagedUserUpdateForm(instance=user, actor=request.user)
        context = build_user_form_context(
            request,
            form,
            managed_user=user,
            page_heading="Редактирование пользователя",
            page_subtitle=user.display_name,
        )
        return render_modal_form_response(request, context)

    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User.objects.prefetch_related("services", "access_rules"), pk=user_id)
        before_is_staff = user.is_staff
        before_is_active = user.is_active
        before_service_titles = list(user.services.order_by("title").values_list("title", flat=True))
        before_user_rule_values = get_user_access_rule_values(user)
        had_token = user.has_login_token
        previous_password_changed_at = user.password_changed_at

        form = ManagedUserUpdateForm(request.POST, instance=user, actor=request.user)
        user_rule_values, user_rule_errors = validate_access_rule_list(get_posted_access_rule_values(request, "user_access_rules"))
        if form.is_valid() and not user_rule_errors:
            updated_user = form.save()
            replace_user_access_rules(updated_user, user_rule_values)
            after_service_titles = list(updated_user.services.order_by("title").values_list("title", flat=True))
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Пользователи", "Обновление"),
                event_kind="user_update",
                result="success",
                category="Пользователи",
                action="Обновление",
                metadata={"username": updated_user.username},
            )
            if before_is_staff != updated_user.is_staff:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Пользователи", "Изменение роли"),
                    event_kind="role_change",
                    result="success",
                    category="Пользователи",
                    action="Изменение роли",
                    metadata={"username": updated_user.username, "is_staff": updated_user.is_staff},
                )
            if before_is_active != updated_user.is_active:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Пользователи", "Статус учётной записи"),
                    event_kind="account_toggle",
                    result="success",
                    category="Пользователи",
                    action="Статус учётной записи",
                    metadata={"username": updated_user.username, "is_active": updated_user.is_active},
                )
            if before_service_titles != after_service_titles:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Пользователи", "Изменение доступов"),
                    event_kind="service_access_update",
                    result="success",
                    category="Пользователи",
                    action="Изменение доступов",
                    metadata={"username": updated_user.username, "services": after_service_titles},
                )
            if before_user_rule_values != user_rule_values:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Безопасность", "IP-доступ пользователя"),
                    event_kind="user_ip_access_update",
                    result="success",
                    category="Безопасность",
                    action="IP-доступ пользователя",
                    metadata={"username": updated_user.username, "rules": user_rule_values},
                )
            if updated_user.password_changed_at != previous_password_changed_at:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Пользователи", "Смена пароля"),
                    event_kind="password_change",
                    result="success",
                    category="Пользователи",
                    action="Смена пароля",
                    metadata={"username": updated_user.username},
                )
            if had_token and not updated_user.has_login_token:
                log_audit_event(
                    request,
                    user=request.user,
                    auth_method=get_request_auth_method(request),
                    target_label=build_portal_target("Токены", "Отзыв"),
                    event_kind="token_revoke",
                    result="success",
                    category="Токены",
                    action="Отзыв",
                    metadata={"username": updated_user.username},
                )
            if is_modal_request(request):
                return apply_no_store(JsonResponse({"ok": True, "message": f"Пользователь {updated_user.username} обновлён.", "refresh": True}))
            messages.success(request, f"Права пользователя {updated_user.username} обновлены.")
            return redirect("portal:rights_user_edit", user_id=updated_user.pk)

        context = build_user_form_context(
            request,
            form,
            managed_user=user,
            page_heading="Редактирование пользователя",
            page_subtitle=user.display_name,
            active_tab="security" if user_rule_errors else "basic",
            user_access_rule_errors=user_rule_errors,
        )
        if is_modal_request(request):
            html = render_to_string("portal/rights/_user_modal_form.html", context, request=request)
            return JsonResponse({"ok": False, "html": html}, status=400)
        return render_modal_form_response(request, context, status=400)


@method_decorator(never_cache, name="dispatch")
class RightsUserTokenGenerateView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        user.rotate_login_token(actor=request.user)
        user.save(
            update_fields=[
                "login_token_hash",
                "login_token_ciphertext",
                "login_token_created_at",
                "login_token_last_used_at",
                "login_token_created_by",
            ]
        )
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Токены", "Создание"),
            event_kind="token_generate",
            result="success",
            category="Токены",
            action="Создание",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse(build_token_response(user)))


@method_decorator(never_cache, name="dispatch")
class RightsUserTokenRotateView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        user.rotate_login_token(actor=request.user)
        user.save(
            update_fields=[
                "login_token_hash",
                "login_token_ciphertext",
                "login_token_created_at",
                "login_token_last_used_at",
                "login_token_created_by",
            ]
        )
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Токены", "Ротация"),
            event_kind="token_rotate",
            result="success",
            category="Токены",
            action="Ротация",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse(build_token_response(user)))


@method_decorator(never_cache, name="dispatch")
class RightsUserTokenRevealView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        if not user.can_reveal_login_token:
            log_audit_event(
                request,
                user=request.user,
                auth_method=get_request_auth_method(request),
                target_label=build_portal_target("Токены", "Показ"),
                event_kind="token_reveal",
                result="missing",
                category="Токены",
                action="Показ",
                status_code=404,
                metadata={"username": user.username},
            )
            return apply_no_store(
                JsonResponse({"ok": False, "token": "", "generated_at": "", "has_token": False, "can_reveal": False}, status=404)
            )

        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Токены", "Показ"),
            event_kind="token_reveal",
            result="success",
            category="Токены",
            action="Показ",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse(build_token_response(user)))


@method_decorator(never_cache, name="dispatch")
class RightsUserTokenRevokeView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        user.clear_login_token()
        user.save(
            update_fields=[
                "login_token_hash",
                "login_token_ciphertext",
                "login_token_created_at",
                "login_token_last_used_at",
                "login_token_created_by",
            ]
        )
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Токены", "Отзыв"),
            event_kind="token_revoke",
            result="success",
            category="Токены",
            action="Отзыв",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse({"ok": True, "has_token": False, "can_reveal": False, "status_label": "Не создан"}))


@method_decorator(never_cache, name="dispatch")
class RightsUserRequirePasswordChangeView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        if not user.must_change_password:
            user.must_change_password = True
            user.save(update_fields=["must_change_password"])
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Требование смены пароля"),
            event_kind="password_change_required",
            result="success",
            category="Пользователи",
            action="Требование смены пароля",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse({"ok": True, "must_change_password": True}))


@method_decorator(never_cache, name="dispatch")
class RightsUserLoginLockResetView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        removed = clear_account_login_restrictions_for_user(user)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Безопасность", "Снятие блокировки авторизации"),
            event_kind="login_lock_reset_user",
            result="success",
            category="Безопасность",
            action="Снятие блокировки авторизации",
            metadata={"username": user.username, "removed_entries": removed},
        )
        return apply_no_store(
            JsonResponse(
                {
                    "ok": True,
                    "message": f"Ограничения авторизации для {user.username} сброшены.",
                    "removed_entries": removed,
                }
            )
        )


@method_decorator(never_cache, name="dispatch")
class RightsUserSessionRevokeView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int, session_key: str) -> HttpResponse:
        session_record = get_object_or_404(UserSession, session_key=session_key, user_id=user_id)
        revoke_session_record(session_record)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Сессии", "Завершение"),
            event_kind="session_revoke",
            result="success",
            category="Сессии",
            action="Завершение",
            metadata={"username": session_record.user.username, "session_key": session_key},
        )
        return apply_no_store(JsonResponse({"ok": True, "session_key": session_key}))


@method_decorator(never_cache, name="dispatch")
class RightsUserSessionRevokeAllView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id)
        count = revoke_user_sessions(user)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Сессии", "Завершение всех"),
            event_kind="session_revoke_all",
            result="success",
            category="Сессии",
            action="Завершение всех",
            metadata={"username": user.username, "count": count},
        )
        return apply_no_store(JsonResponse({"ok": True, "count": count}))


@method_decorator(never_cache, name="dispatch")
class RightsUserSoftDeleteView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id, is_superuser=False)
        clear_account_login_restrictions_for_user(user)
        user.archive()
        user.save(
            update_fields=[
                "deleted_at",
                "is_active",
                "login_token_hash",
                "login_token_ciphertext",
                "login_token_created_at",
                "login_token_last_used_at",
                "login_token_created_by",
            ]
        )
        revoke_user_sessions(user)
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Архивация"),
            event_kind="user_archive",
            result="success",
            category="Пользователи",
            action="Архивация",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse({"ok": True}))


@method_decorator(never_cache, name="dispatch")
class RightsUserRestoreView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, user_id: int) -> HttpResponse:
        user = get_object_or_404(User, pk=user_id, is_superuser=False)
        user.restore()
        user.save(update_fields=["deleted_at", "is_active"])
        log_audit_event(
            request,
            user=request.user,
            auth_method=get_request_auth_method(request),
            target_label=build_portal_target("Пользователи", "Разархивация"),
            event_kind="user_restore",
            result="success",
            category="Пользователи",
            action="Разархивация",
            metadata={"username": user.username},
        )
        return apply_no_store(JsonResponse({"ok": True}))
