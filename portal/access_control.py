from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address, ip_network
from ipaddress import AddressValueError, NetmaskValueError
import os
from pathlib import Path
from typing import Sequence

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone

from .audit import apply_no_store
from .models import LoginRedirectState, PortalAccessRule, User, UserAccessRule
from .visual_settings import build_particle_visual_context


DEFAULT_GLOBAL_ACCESS_RULES: list[str] = []

BASE_DIR = Path(__file__).resolve().parent.parent
PORTAL_ACCESS_RULE_MARKER_ENV = "HUB_PORTAL_ACCESS_RULE_MARKER_FILE"

USER_ACCESS_CACHE_STAMP_KEY = "_hub_user_access_stamp"
USER_ACCESS_CACHE_RULES_KEY = "_hub_user_access_rules"
USER_ACCESS_CACHE_SERVICE_IDS_KEY = "_hub_user_service_ids"

_PORTAL_ACCESS_RULE_CACHE: list[str] | None = None
_PORTAL_ACCESS_RULE_CACHE_TOKEN: tuple[int, int] | None = None


@dataclass(slots=True)
class AccessDecision:
    allowed: bool
    client_ip: str = ""
    has_rules: bool = False
    matched_rule: str = ""


def get_request_client_ip(request: HttpRequest) -> str:
    cached_value = getattr(request, "_hub_client_ip", None)
    if cached_value is not None:
        return cached_value

    real_ip = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        request._hub_client_ip = real_ip
        return real_ip

    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    request._hub_client_ip = remote_addr
    return remote_addr


def normalize_access_rule(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        raise ValidationError("Укажите IP-адрес или подсеть.")
    try:
        network = ip_network(value, strict=False)
    except (AddressValueError, NetmaskValueError, ValueError):
        raise ValidationError(f"Некорректный адрес или подсеть: {value}")
    return network.with_prefixlen


def validate_access_rule_list(raw_values: Sequence[str]) -> tuple[list[str], list[str]]:
    normalized_values: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()

    for index, raw_value in enumerate(raw_values, start=1):
        value = (raw_value or "").strip()
        if not value:
            errors.append(f"Строка {index}: укажите IP-адрес или подсеть.")
            continue
        try:
            normalized = normalize_access_rule(value)
        except ValidationError as exc:
            errors.append(f"Строка {index}: {exc.messages[0]}")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)

    return normalized_values, errors


def get_portal_access_rule_marker_path() -> Path:
    configured_path = os.environ.get(PORTAL_ACCESS_RULE_MARKER_ENV, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return BASE_DIR.parent / "runtime" / "portal-access-rules.stamp"


def get_portal_access_rule_marker_token() -> tuple[int, int] | None:
    try:
        stat_result = get_portal_access_rule_marker_path().stat()
    except OSError:
        return None
    return (stat_result.st_mtime_ns, stat_result.st_size)


def touch_portal_access_rule_marker() -> tuple[int, int] | None:
    marker_path = get_portal_access_rule_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.touch()
    return get_portal_access_rule_marker_token()


def get_portal_access_rule_values() -> list[str]:
    global _PORTAL_ACCESS_RULE_CACHE, _PORTAL_ACCESS_RULE_CACHE_TOKEN

    marker_token = get_portal_access_rule_marker_token()
    if (
        marker_token is not None
        and _PORTAL_ACCESS_RULE_CACHE is not None
        and _PORTAL_ACCESS_RULE_CACHE_TOKEN == marker_token
    ):
        return list(_PORTAL_ACCESS_RULE_CACHE)

    values = list(PortalAccessRule.objects.order_by("sort_order", "cidr", "pk").values_list("cidr", flat=True))
    if marker_token is None:
        marker_token = touch_portal_access_rule_marker()

    _PORTAL_ACCESS_RULE_CACHE = list(values)
    _PORTAL_ACCESS_RULE_CACHE_TOKEN = marker_token
    return list(values)


def get_user_access_rule_values(user: User | None) -> list[str]:
    if not user or not getattr(user, "pk", None):
        return []
    return list(user.access_rules.order_by("sort_order", "cidr", "pk").values_list("cidr", flat=True))


def mark_user_access_state_updated(user: User | None) -> None:
    if not user or not getattr(user, "pk", None):
        return
    timestamp = timezone.now()
    User.objects.filter(pk=user.pk).update(access_state_updated_at=timestamp)
    user.access_state_updated_at = timestamp


def get_user_access_state_stamp(user: User | None) -> str:
    if not user or not getattr(user, "pk", None):
        return ""
    return getattr(user, "access_state_token", "")


def prime_request_user_access_cache(request: HttpRequest, user: User | None) -> None:
    if not user or not getattr(user, "pk", None):
        return
    session = getattr(request, "session", None)
    if session is None:
        return

    session[USER_ACCESS_CACHE_STAMP_KEY] = get_user_access_state_stamp(user)
    session[USER_ACCESS_CACHE_RULES_KEY] = get_user_access_rule_values(user)
    if user.is_staff or user.is_superuser or not user.is_active or user.is_deleted:
        session[USER_ACCESS_CACHE_SERVICE_IDS_KEY] = []
    else:
        session[USER_ACCESS_CACHE_SERVICE_IDS_KEY] = list(
            user.services.filter(is_active=True).order_by("pk").values_list("pk", flat=True)
        )


def get_cached_user_access_rule_values(request: HttpRequest, user: User | None) -> list[str]:
    if not user or not getattr(user, "pk", None):
        return []

    session = getattr(request, "session", None)
    if session is None:
        return get_user_access_rule_values(user)

    current_stamp = get_user_access_state_stamp(user)
    cached_stamp = session.get(USER_ACCESS_CACHE_STAMP_KEY, "")
    cached_rules = session.get(USER_ACCESS_CACHE_RULES_KEY)
    if cached_stamp == current_stamp and isinstance(cached_rules, list):
        return [str(value).strip() for value in cached_rules if str(value).strip()]

    prime_request_user_access_cache(request, user)
    refreshed_rules = session.get(USER_ACCESS_CACHE_RULES_KEY, [])
    return [str(value).strip() for value in refreshed_rules if str(value).strip()]


def request_user_can_access_service(request: HttpRequest, user: User | None, service) -> bool:
    if not user or not getattr(user, "pk", None):
        return False
    if not user.is_active or user.is_deleted:
        return False
    if user.is_staff or user.is_superuser:
        return True

    session = getattr(request, "session", None)
    if session is None:
        return user.can_access_service(service)

    current_stamp = get_user_access_state_stamp(user)
    cached_stamp = session.get(USER_ACCESS_CACHE_STAMP_KEY, "")
    cached_ids = session.get(USER_ACCESS_CACHE_SERVICE_IDS_KEY)
    if cached_stamp == current_stamp and isinstance(cached_ids, list):
        try:
            return int(service.pk) in {int(value) for value in cached_ids}
        except (TypeError, ValueError):
            return False

    prime_request_user_access_cache(request, user)
    refreshed_ids = session.get(USER_ACCESS_CACHE_SERVICE_IDS_KEY, [])
    try:
        return int(service.pk) in {int(value) for value in refreshed_ids}
    except (TypeError, ValueError):
        return False


def get_default_user_access_rule_values() -> list[str]:
    values = get_portal_access_rule_values()
    return values or list(DEFAULT_GLOBAL_ACCESS_RULES)


def evaluate_ip_access(client_ip: str, rules: Sequence[str]) -> AccessDecision:
    if not rules:
        return AccessDecision(allowed=True, client_ip=client_ip, has_rules=False)

    try:
        address = ip_address(client_ip)
    except ValueError:
        return AccessDecision(allowed=False, client_ip=client_ip, has_rules=True)

    for rule in rules:
        if address in ip_network(rule, strict=False):
            return AccessDecision(allowed=True, client_ip=client_ip, has_rules=True, matched_rule=rule)
    return AccessDecision(allowed=False, client_ip=client_ip, has_rules=True)


def get_portal_access_decision(request: HttpRequest) -> AccessDecision:
    return evaluate_ip_access(get_request_client_ip(request), get_portal_access_rule_values())


def get_user_access_decision(user: User | None, client_ip: str) -> AccessDecision:
    rule_values = get_user_access_rule_values(user)
    return evaluate_ip_access(client_ip, rule_values)


def get_user_access_decision_for_request(request: HttpRequest, user: User | None) -> AccessDecision:
    return evaluate_ip_access(get_request_client_ip(request), get_cached_user_access_rule_values(request, user))


def replace_portal_access_rules(values: Sequence[str]) -> None:
    global _PORTAL_ACCESS_RULE_CACHE, _PORTAL_ACCESS_RULE_CACHE_TOKEN

    normalized_values = list(values)
    with transaction.atomic():
        PortalAccessRule.objects.all().delete()
        PortalAccessRule.objects.bulk_create(
            [
                PortalAccessRule(cidr=value, sort_order=index)
                for index, value in enumerate(normalized_values)
            ]
        )
    _PORTAL_ACCESS_RULE_CACHE = list(normalized_values)
    _PORTAL_ACCESS_RULE_CACHE_TOKEN = touch_portal_access_rule_marker()


def replace_user_access_rules(user: User, values: Sequence[str]) -> None:
    normalized_values = list(values)
    with transaction.atomic():
        UserAccessRule.objects.filter(user=user).delete()
        UserAccessRule.objects.bulk_create(
            [
                UserAccessRule(user=user, cidr=value, sort_order=index)
                for index, value in enumerate(normalized_values)
            ]
        )
    mark_user_access_state_updated(user)


def seed_user_access_rules_from_portal(user: User) -> None:
    replace_user_access_rules(user, get_default_user_access_rule_values())


def create_login_redirect_state(*, service, target_url: str, client_ip: str, ttl_seconds: int = 600) -> LoginRedirectState:
    return LoginRedirectState.objects.create(
        service=service,
        client_ip=client_ip or None,
        target_url=target_url,
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
    )


def consume_login_redirect_state(raw_token: str, client_ip: str) -> str:
    token = (raw_token or "").strip()
    if not token:
        return ""

    with transaction.atomic():
        try:
            state = LoginRedirectState.objects.select_for_update().get(token=token)
        except LoginRedirectState.DoesNotExist:
            return ""

        now = timezone.now()
        if state.consumed_at is not None or state.expires_at <= now:
            return ""
        if state.client_ip and client_ip and state.client_ip != client_ip:
            return ""

        state.consumed_at = now
        state.save(update_fields=["consumed_at"])
        return state.target_url


def build_access_restricted_response(request: HttpRequest, *, status: int = 403):
    response = TemplateResponse(
        request,
        "portal/access_restricted.html",
        build_particle_visual_context(blocked_page=True),
        status=status,
    )
    response.render()
    return apply_no_store(response)
