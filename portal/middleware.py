from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .access_control import (
    build_access_restricted_response,
    get_portal_access_decision,
    get_request_client_ip,
    get_user_access_decision_for_request,
)
from .audit import apply_no_store
from .constants import AUTH_METHOD_TOKEN, LOGIN_IP_BLOCK_COOKIE_NAME, SESSION_AUTH_METHOD_KEY
from .login_protection import attach_browser_id_cookie, ensure_request_browser_id
from .session_tracking import revoke_session_record, touch_session_record


def expects_json_response(request) -> bool:
    accept_header = (request.headers.get("Accept") or "").lower()
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in accept_header


class AccessControlMiddleware:
    internal_prefixes = (
        "/internal/authz",
        "/internal/login-state",
    )
    passthrough_prefixes = (
        "/static/",
        "/internal/authz",
        "/internal/login-state",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def _revoke_current_session(self, request) -> None:
        session_key = request.session.session_key
        if not session_key:
            return
        current_session = request.user.tracked_sessions.filter(session_key=session_key).first()
        if current_session:
            revoke_session_record(current_session)

    def _build_blocked_response(self, request, *, status: int = 403):
        if any(request.path.startswith(prefix) for prefix in self.internal_prefixes):
            return HttpResponse(status=status)
        if expects_json_response(request):
            return apply_no_store(JsonResponse({"ok": False, "message": "Доступ ограничен"}, status=status))
        root_login_path = reverse("portal:login")
        if request.path != root_login_path:
            return apply_no_store(redirect(root_login_path))
        return build_access_restricted_response(request, status=status)

    def __call__(self, request):
        if any(request.path.startswith(prefix) for prefix in self.passthrough_prefixes):
            return self.get_response(request)

        portal_decision = get_portal_access_decision(request)
        request.portal_access_decision = portal_decision
        if not portal_decision.allowed:
            if getattr(request.user, "is_authenticated", False):
                self._revoke_current_session(request)
                logout(request)
            return self._build_blocked_response(request)

        if getattr(request.user, "is_authenticated", False):
            user_decision = get_user_access_decision_for_request(request, request.user)
            request.user_access_decision = user_decision
            if user_decision.has_rules and not user_decision.allowed:
                self._revoke_current_session(request)
                logout(request)
                if any(request.path.startswith(prefix) for prefix in self.internal_prefixes):
                    return HttpResponse(status=403)
                if expects_json_response(request):
                    return apply_no_store(JsonResponse({"ok": False, "message": "Доступ ограничен"}, status=403))
                response = apply_no_store(redirect(reverse("portal:login")))
                response.set_cookie(
                    LOGIN_IP_BLOCK_COOKIE_NAME,
                    "1",
                    max_age=15,
                    path="/",
                    secure=request.is_secure(),
                    httponly=True,
                    samesite="Lax",
                )
                return response

        return self.get_response(request)


class BrowserIdentityMiddleware:
    skip_prefixes = (
        "/static/",
        "/internal/authz",
        "/internal/login-state",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not any(request.path.startswith(prefix) for prefix in self.skip_prefixes):
            ensure_request_browser_id(request)
        response = self.get_response(request)
        if any(request.path.startswith(prefix) for prefix in self.skip_prefixes):
            return response
        return attach_browser_id_cookie(request, response)


class SessionTrackingMiddleware:
    skip_prefixes = (
        "/static/",
        "/internal/authz",
        "/internal/login-state",
        "/api/services/statuses/",
        "/api/release-state/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if any(request.path.startswith(prefix) for prefix in self.skip_prefixes):
            return response
        touch_session_record(request)
        return response


class MustChangePasswordMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token_authenticated = request.session.get(SESSION_AUTH_METHOD_KEY) == AUTH_METHOD_TOKEN
        if request.user.is_authenticated and getattr(request.user, "must_change_password", False) and not token_authenticated:
            path = request.path
            allowed_prefixes = (
                "/password/change-first/",
                "/logout/",
                "/internal/authz",
                "/internal/login-state",
                "/static/",
            )
            if not any(path.startswith(prefix) for prefix in allowed_prefixes):
                return redirect("portal:password_change_first")
        return self.get_response(request)
