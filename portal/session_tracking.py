from __future__ import annotations

from datetime import datetime

from django.contrib.sessions.models import Session
from django.utils import timezone

from .access_control import get_request_client_ip
from .models import User, UserSession

SESSION_LAST_TOUCH_KEY = "_hub_last_session_touch"
SESSION_TOUCH_INTERVAL_SECONDS = 60


def build_device_label(user_agent: str) -> str:
    agent = (user_agent or "").lower()
    platform = "Устройство"
    browser = "Браузер"

    if "iphone" in agent or "ipad" in agent or "ios" in agent:
        platform = "iPhone" if "iphone" in agent else "iPad"
    elif "android" in agent:
        platform = "Android"
    elif "windows" in agent:
        platform = "Windows"
    elif "mac os" in agent or "macintosh" in agent:
        platform = "macOS"
    elif "linux" in agent:
        platform = "Linux"

    if "edg/" in agent:
        browser = "Edge"
    elif "chrome/" in agent and "edg/" not in agent:
        browser = "Chrome"
    elif "firefox/" in agent:
        browser = "Firefox"
    elif "safari/" in agent and "chrome/" not in agent:
        browser = "Safari"

    return f"{platform} · {browser}"


def get_session_expiry(request) -> timezone.datetime:
    expiry = request.session.get_expiry_date()
    if timezone.is_naive(expiry):
        return timezone.make_aware(expiry, timezone.get_current_timezone())
    return expiry


def ensure_session_record(request, user: User, auth_method: str) -> UserSession | None:
    if not request.session.session_key:
        request.session.save()

    session_key = request.session.session_key
    if not session_key:
        return None

    timestamp = timezone.now()
    defaults = {
        "user": user,
        "auth_method": auth_method,
        "ip_address": get_request_client_ip(request) or None,
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:512],
        "device_label": build_device_label(request.META.get("HTTP_USER_AGENT") or ""),
        "last_seen_at": timestamp,
        "expires_at": get_session_expiry(request),
        "revoked_at": None,
    }
    session_record, created = UserSession.objects.update_or_create(
        session_key=session_key,
        defaults=defaults,
    )
    if created and not session_record.created_at:
        session_record.created_at = timestamp
        session_record.save(update_fields=["created_at"])
    request.session[SESSION_LAST_TOUCH_KEY] = timestamp.isoformat()
    return session_record


def touch_session_record(request) -> None:
    if not getattr(request.user, "is_authenticated", False) or not request.session.session_key:
        return

    now = timezone.now()
    last_touch_raw = request.session.get(SESSION_LAST_TOUCH_KEY)
    if last_touch_raw:
        try:
            last_touch_at = datetime.fromisoformat(str(last_touch_raw))
            if timezone.is_naive(last_touch_at):
                last_touch_at = timezone.make_aware(last_touch_at, timezone.get_current_timezone())
            if (now - last_touch_at).total_seconds() < SESSION_TOUCH_INTERVAL_SECONDS:
                return
        except ValueError:
            pass

    UserSession.objects.filter(
        session_key=request.session.session_key,
        revoked_at__isnull=True,
    ).update(
        last_seen_at=now,
        expires_at=get_session_expiry(request),
        ip_address=get_request_client_ip(request) or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        device_label=build_device_label(request.META.get("HTTP_USER_AGENT") or ""),
    )
    request.session[SESSION_LAST_TOUCH_KEY] = now.isoformat()


def revoke_session_record(session_record: UserSession, *, timestamp=None) -> None:
    if not session_record:
        return

    revoke_time = timestamp or timezone.now()
    Session.objects.filter(session_key=session_record.session_key).delete()
    if session_record.revoked_at is None:
        session_record.revoked_at = revoke_time
        session_record.save(update_fields=["revoked_at"])


def revoke_user_sessions(user: User, *, exclude_session_key: str = "") -> int:
    records = list(
        UserSession.objects.filter(user=user, revoked_at__isnull=True).exclude(session_key=exclude_session_key)
    )
    for record in records:
        revoke_session_record(record)
    return len(records)
