from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import BrowserLoginLockState, PortalSecuritySettings, User, UserLoginLockState


BROWSER_ID_COOKIE_NAME = "hub_portal_browser_id"
BROWSER_ID_COOKIE_AGE = 60 * 60 * 24 * 365
BROWSER_ID_SIGNING_SALT = "portal.browser-id"
BROWSER_LOCK_HASH_SALT = "portal.browser-lock"
FINGERPRINT_HASH_SALT = "portal.browser-fingerprint"


@dataclass(slots=True)
class LoginLockDecision:
    blocked: bool = False
    message: str = ""
    remaining_seconds: int = 0
    user: User | None = None
    browser_lock: BrowserLoginLockState | None = None
    user_lock: UserLoginLockState | None = None


def get_login_protection_runtime_dir() -> Path:
    configured = str(getattr(settings, "HUB_PORTAL_LOGIN_PROTECTION_RUNTIME_DIR", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return settings.BASE_DIR / ".runtime" / "login-protection"


def get_browser_snapshot_dir() -> Path:
    return get_login_protection_runtime_dir() / "browser-locks"


def ensure_runtime_dirs() -> None:
    get_browser_snapshot_dir().mkdir(parents=True, exist_ok=True)


def get_login_security_settings() -> PortalSecuritySettings:
    return PortalSecuritySettings.get_solo()


def is_login_attempt_limit_enabled() -> bool:
    settings_row = get_login_security_settings()
    return settings_row.login_attempt_limit_enabled and settings_row.login_attempt_limit_count > 0 and settings_row.login_attempt_lock_seconds > 0


def format_duration_label(total_seconds: int) -> str:
    seconds = max(int(total_seconds or 0), 0)
    if seconds <= 0:
        return "0 секунд"

    units = (
        ("день", "дня", "дней", 86400),
        ("час", "часа", "часов", 3600),
        ("минута", "минуты", "минут", 60),
        ("секунда", "секунды", "секунд", 1),
    )
    parts: list[str] = []
    remaining = seconds
    for singular, dual, plural, unit_seconds in units:
        if remaining < unit_seconds:
            continue
        value, remaining = divmod(remaining, unit_seconds)
        if value % 10 == 1 and value % 100 != 11:
            label = singular
        elif value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
            label = dual
        else:
            label = plural
        parts.append(f"{value} {label}")
        if len(parts) == 2:
            break
    return " ".join(parts) if parts else "0 секунд"


def build_lock_message(seconds: int) -> str:
    return f"Превышено количество попыток авторизоваться, доступ заблокирован на: {format_duration_label(seconds)}"


def _get_signer() -> signing.Signer:
    return signing.Signer(salt=BROWSER_ID_SIGNING_SALT)


def _sign_browser_id(browser_id: str) -> str:
    return _get_signer().sign(browser_id)


def _unsign_browser_id(raw_value: str) -> str:
    try:
        return _get_signer().unsign(raw_value or "")
    except signing.BadSignature:
        return ""


def generate_browser_id() -> str:
    return f"{uuid4().hex}{uuid4().hex[:16]}"


def ensure_request_browser_id(request: HttpRequest) -> str:
    cached = getattr(request, "_hub_browser_id", "")
    if cached:
        return cached

    raw_cookie = (request.COOKIES.get(BROWSER_ID_COOKIE_NAME) or "").strip()
    browser_id = _unsign_browser_id(raw_cookie)
    if browser_id:
        request._hub_browser_id = browser_id
        request._hub_browser_cookie_value = raw_cookie
        request._hub_browser_cookie_needs_set = False
        return browser_id

    browser_id = generate_browser_id()
    request._hub_browser_id = browser_id
    request._hub_browser_cookie_value = _sign_browser_id(browser_id)
    request._hub_browser_cookie_needs_set = True
    return browser_id


def attach_browser_id_cookie(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    browser_id = ensure_request_browser_id(request)
    cookie_value = getattr(request, "_hub_browser_cookie_value", "") or _sign_browser_id(browser_id)
    if not cookie_value:
        return response

    if getattr(request, "_hub_browser_cookie_needs_set", False) or request.COOKIES.get(BROWSER_ID_COOKIE_NAME) != cookie_value:
        response.set_cookie(
            BROWSER_ID_COOKIE_NAME,
            cookie_value,
            max_age=BROWSER_ID_COOKIE_AGE,
            secure=getattr(settings, "SESSION_COOKIE_SECURE", True),
            httponly=True,
            samesite=getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax"),
            domain=getattr(settings, "SESSION_COOKIE_DOMAIN", None),
        )
    return response


def get_browser_hash(browser_id: str) -> str:
    return salted_hmac(BROWSER_LOCK_HASH_SALT, browser_id).hexdigest()


def get_fingerprint_hash(raw_fingerprint: str) -> str:
    value = (raw_fingerprint or "").strip()
    if not value:
        return ""
    return salted_hmac(FINGERPRINT_HASH_SALT, value).hexdigest()


def get_request_fingerprint_hash(request: HttpRequest) -> str:
    cached = getattr(request, "_hub_browser_fingerprint_hash", None)
    if cached is not None:
        return cached
    raw_fingerprint = (request.POST.get("browser_fingerprint") or request.GET.get("browser_fingerprint") or "").strip()
    if not raw_fingerprint:
        raw_fingerprint = "|".join(
            [
                (request.META.get("HTTP_USER_AGENT") or "").strip(),
                (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").strip(),
                (request.META.get("HTTP_SEC_CH_UA") or "").strip(),
                (request.META.get("HTTP_SEC_CH_UA_PLATFORM") or "").strip(),
                (request.META.get("HTTP_SEC_CH_UA_MOBILE") or "").strip(),
            ]
        )
    fingerprint_hash = get_fingerprint_hash(raw_fingerprint)
    request._hub_browser_fingerprint_hash = fingerprint_hash
    return fingerprint_hash


def get_request_browser_hash(request: HttpRequest) -> str:
    cached = getattr(request, "_hub_browser_hash", "")
    if cached:
        return cached
    browser_hash = get_browser_hash(ensure_request_browser_id(request))
    request._hub_browser_hash = browser_hash
    return browser_hash


def get_browser_snapshot_path(browser_hash: str) -> Path:
    ensure_runtime_dirs()
    return get_browser_snapshot_dir() / f"{browser_hash}.json"


def write_browser_snapshot(lock_state: BrowserLoginLockState, *, client_ip: str = "", user_agent: str = "") -> None:
    snapshot_path = get_browser_snapshot_path(lock_state.browser_hash)
    payload = {
        "browser_hash": lock_state.browser_hash,
        "fingerprint_hash": lock_state.fingerprint_hash,
        "username_snapshot": lock_state.username_snapshot,
        "user_id": lock_state.user_id,
        "failure_count": lock_state.failure_count,
        "locked_until": lock_state.locked_until.isoformat() if lock_state.locked_until else "",
        "last_failed_at": lock_state.last_failed_at.isoformat() if lock_state.last_failed_at else "",
        "client_ip": client_ip,
        "user_agent": user_agent[:255],
    }
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_browser_snapshot(browser_hash: str) -> None:
    snapshot_path = get_browser_snapshot_path(browser_hash)
    try:
        snapshot_path.unlink()
    except FileNotFoundError:
        return


def cleanup_expired_login_locks(*, now=None) -> dict[str, int]:
    timestamp = now or timezone.now()
    deleted_user_rows = 0
    deleted_browser_rows = 0

    expired_user_states = list(
        UserLoginLockState.objects.filter(locked_until__isnull=False, locked_until__lte=timestamp)
    )
    if expired_user_states:
        deleted_user_rows = len(expired_user_states)
        UserLoginLockState.objects.filter(pk__in=[state.pk for state in expired_user_states]).delete()

    expired_browser_states = list(
        BrowserLoginLockState.objects.filter(locked_until__isnull=False, locked_until__lte=timestamp)
    )
    for state in expired_browser_states:
        remove_browser_snapshot(state.browser_hash)
    if expired_browser_states:
        deleted_browser_rows = len(expired_browser_states)
        BrowserLoginLockState.objects.filter(pk__in=[state.pk for state in expired_browser_states]).delete()

    return {
        "users": deleted_user_rows,
        "browsers": deleted_browser_rows,
    }


def get_login_lock_summary(user: User | None) -> dict:
    cleanup_expired_login_locks()
    if not user or not getattr(user, "pk", None):
        return {"is_locked": False, "locked_until": None, "remaining_seconds": 0}
    state = UserLoginLockState.objects.filter(user=user, locked_until__isnull=False).first()
    if not state or not state.locked_until:
        return {"is_locked": False, "locked_until": None, "remaining_seconds": 0}
    remaining_seconds = max(int((state.locked_until - timezone.now()).total_seconds()), 0)
    return {
        "is_locked": remaining_seconds > 0,
        "locked_until": state.locked_until,
        "remaining_seconds": remaining_seconds,
    }


def get_account_lock_decision(request: HttpRequest, username: str) -> LoginLockDecision:
    cleanup_expired_login_locks()
    settings_row = get_login_security_settings()
    if not settings_row.login_attempt_limit_enabled:
        return LoginLockDecision()

    now = timezone.now()
    browser_hash = get_request_browser_hash(request)
    fingerprint_hash = get_request_fingerprint_hash(request)
    browser_state = BrowserLoginLockState.objects.filter(browser_hash=browser_hash).select_related("user").first()
    if (not browser_state or not browser_state.locked_until or browser_state.locked_until <= now) and fingerprint_hash:
        browser_state = (
            BrowserLoginLockState.objects.filter(fingerprint_hash=fingerprint_hash)
            .select_related("user")
            .order_by("-updated_at", "-pk")
            .first()
        )
    if browser_state and browser_state.locked_until and browser_state.locked_until > now:
        remaining_seconds = max(int((browser_state.locked_until - now).total_seconds()), 0)
        return LoginLockDecision(
            blocked=True,
            message=build_lock_message(remaining_seconds),
            remaining_seconds=remaining_seconds,
            user=browser_state.user,
            browser_lock=browser_state,
        )

    normalized_username = (username or "").strip()
    if not normalized_username:
        return LoginLockDecision()

    user = User.objects.filter(username=normalized_username).first()
    if not user:
        return LoginLockDecision()

    user_state = UserLoginLockState.objects.filter(user=user, locked_until__isnull=False).first()
    if user_state and user_state.locked_until and user_state.locked_until > now:
        remaining_seconds = max(int((user_state.locked_until - now).total_seconds()), 0)
        return LoginLockDecision(
            blocked=True,
            message=build_lock_message(remaining_seconds),
            remaining_seconds=remaining_seconds,
            user=user,
            user_lock=user_state,
            browser_lock=browser_state,
        )
    return LoginLockDecision(user=user)


def register_failed_account_attempt(request: HttpRequest, username: str) -> LoginLockDecision:
    settings_row = get_login_security_settings()
    if not settings_row.login_attempt_limit_enabled:
        return LoginLockDecision()

    normalized_username = (username or "").strip()
    if not normalized_username:
        return LoginLockDecision()

    user = User.objects.filter(username=normalized_username).first()
    if not user:
        return LoginLockDecision()

    browser_hash = get_request_browser_hash(request)
    fingerprint_hash = get_request_fingerprint_hash(request)
    now = timezone.now()
    lock_duration = timedelta(seconds=settings_row.login_attempt_lock_seconds)

    with transaction.atomic():
        user_state, _ = UserLoginLockState.objects.select_for_update().get_or_create(user=user)
        browser_state, _ = BrowserLoginLockState.objects.select_for_update().get_or_create(
            browser_hash=browser_hash,
            defaults={"user": user, "username_snapshot": user.username, "fingerprint_hash": fingerprint_hash},
        )

        if user_state.locked_until and user_state.locked_until > now:
            remaining_seconds = max(int((user_state.locked_until - now).total_seconds()), 0)
            return LoginLockDecision(
                blocked=True,
                message=build_lock_message(remaining_seconds),
                remaining_seconds=remaining_seconds,
                user=user,
                browser_lock=browser_state,
                user_lock=user_state,
            )

        user_state.failure_count += 1
        user_state.last_failed_at = now
        user_state.last_browser_hash = browser_hash

        browser_state.user = user
        browser_state.username_snapshot = user.username
        browser_state.fingerprint_hash = fingerprint_hash
        browser_state.failure_count += 1
        browser_state.last_failed_at = now

        if user_state.failure_count >= settings_row.login_attempt_limit_count:
            locked_until = now + lock_duration
            user_state.locked_until = locked_until
            browser_state.locked_until = locked_until
        else:
            user_state.locked_until = None
            browser_state.locked_until = None

        user_state.save()
        browser_state.save()

    if browser_state.locked_until:
        write_browser_snapshot(
            browser_state,
            client_ip=(request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "").strip(),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "").strip(),
        )
        remaining_seconds = max(int((browser_state.locked_until - now).total_seconds()), 0)
        return LoginLockDecision(
            blocked=True,
            message=build_lock_message(remaining_seconds),
            remaining_seconds=remaining_seconds,
            user=user,
            browser_lock=browser_state,
            user_lock=user_state,
        )

    return LoginLockDecision(user=user)


def clear_account_login_restrictions_for_user(user: User | None) -> int:
    if not user or not getattr(user, "pk", None):
        return 0
    browser_hashes = list(
        BrowserLoginLockState.objects.filter(user=user).values_list("browser_hash", flat=True)
    )
    removed = 0
    for browser_hash in browser_hashes:
        remove_browser_snapshot(browser_hash)
    removed += BrowserLoginLockState.objects.filter(user=user).delete()[0]
    removed += UserLoginLockState.objects.filter(user=user).delete()[0]
    return removed


def clear_current_browser_login_restrictions(request: HttpRequest) -> int:
    browser_hash = get_request_browser_hash(request)
    remove_browser_snapshot(browser_hash)
    return BrowserLoginLockState.objects.filter(browser_hash=browser_hash).delete()[0]


def clear_login_restrictions_after_success(request: HttpRequest, user: User | None) -> None:
    if not user or not getattr(user, "pk", None):
        return
    browser_hash = get_request_browser_hash(request)
    fingerprint_hash = get_request_fingerprint_hash(request)
    remove_browser_snapshot(browser_hash)
    BrowserLoginLockState.objects.filter(browser_hash=browser_hash).delete()
    if fingerprint_hash:
        fingerprint_rows = list(
            BrowserLoginLockState.objects.filter(fingerprint_hash=fingerprint_hash).values_list("browser_hash", flat=True)
        )
        for fingerprint_browser_hash in fingerprint_rows:
            remove_browser_snapshot(fingerprint_browser_hash)
        BrowserLoginLockState.objects.filter(fingerprint_hash=fingerprint_hash).delete()
    UserLoginLockState.objects.filter(user=user).delete()


def clear_all_account_login_restrictions() -> dict[str, int]:
    browser_states = list(BrowserLoginLockState.objects.all().values_list("browser_hash", flat=True))
    for browser_hash in browser_states:
        remove_browser_snapshot(browser_hash)
    browser_snapshot_dir = get_browser_snapshot_dir()
    if browser_snapshot_dir.exists():
        for extra_file in browser_snapshot_dir.glob("*.json"):
            try:
                extra_file.unlink()
            except FileNotFoundError:
                continue
    browser_deleted = BrowserLoginLockState.objects.all().delete()[0]
    user_deleted = UserLoginLockState.objects.all().delete()[0]
    return {
        "browser_states": browser_deleted,
        "user_states": user_deleted,
    }
