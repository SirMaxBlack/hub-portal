import os
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .access_control import get_portal_access_rule_values, replace_portal_access_rules, replace_user_access_rules
from .audit import log_audit_event
from .audit_maintenance import AuditMaintenanceBusyError
from .constants import AUTH_METHOD_ACCOUNT, AUTH_METHOD_TOKEN, SESSION_AUTH_METHOD_KEY
from .forms import ManagedUserUpdateForm
from .models import (
    AuditEvent,
    BrowserLoginLockState,
    PortalSecuritySettings,
    PortalVisualSettings,
    Service,
    ServiceHealthState,
    ServiceLayout,
    UserLoginLockState,
    UserSession,
)
from .service_admin import ServiceOperationResult, render_managed_service_config
from .service_options import get_service_option_catalog, get_service_profile_rows


User = get_user_model()


class PortalTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.login_protection_dir = tempfile.TemporaryDirectory()
        self.login_protection_override = self.settings(
            HUB_PORTAL_LOGIN_PROTECTION_RUNTIME_DIR=self.login_protection_dir.name
        )
        self.login_protection_override.enable()
        self.addCleanup(self.login_protection_override.disable)
        self.addCleanup(self.login_protection_dir.cleanup)
        self.allowed_ip = "127.0.0.1"
        replace_portal_access_rules([f"{self.allowed_ip}/32"])
        self.service = Service.objects.create(
            title="CRM",
            slug="crm",
            description="CRM система",
            domain="crms.hub.example.com",
            upstream_url="http://192.0.2.66:80",
            icon_key="crm",
            sort_order=10,
        )
        self.user = User.objects.create_user(
            username="employee",
            password="VeryStrongPass123!",
            full_name="Иван Петров",
            must_change_password=False,
        )
        self.user.services.add(self.service)
        self.other_user = User.objects.create_user(
            username="employee2",
            password="VeryStrongPass456!",
            full_name="Павел Соколов",
            must_change_password=False,
        )
        self.other_user.services.add(self.service)
        self.admin_user = User.objects.create_superuser(
            username="admin",
            password="VeryStrongPass123!",
        )
        self.admin_user.full_name = "Главный администратор"
        self.admin_user.must_change_password = False
        self.admin_user.save()

    def establish_session(self, user, auth_method=AUTH_METHOD_ACCOUNT, client=None):
        active_client = client or self.client
        active_client.force_login(user)
        session = active_client.session
        session[SESSION_AUTH_METHOD_KEY] = auth_method
        session.save()

    def build_login_payload(self, *, username="", password="", token="", auth_method=AUTH_METHOD_ACCOUNT, fingerprint="browser-a"):
        payload = {
            "auth_method": auth_method,
            "browser_fingerprint": fingerprint,
        }
        if auth_method == AUTH_METHOD_ACCOUNT:
            payload["username"] = username
            payload["password"] = password
        else:
            payload["token"] = token
        return payload

    def build_internal_headers(self, *, service_domain=None, request_uri="/") -> dict:
        return {
            "HTTP_X_PORTAL_AUTH_KEY": settings.HUB_PORTAL_INTERNAL_AUTH_KEY,
            "HTTP_X_ORIGINAL_SERVICE": service_domain or self.service.domain,
            "HTTP_X_ORIGINAL_URI": request_uri,
            "HTTP_X_REAL_IP": self.allowed_ip,
        }

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("portal:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("portal:login"))

    def test_login_page_uses_compact_auth_layout_without_visible_heading_or_labels(self):
        response = self.client.get(reverse("portal:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>HUB</title>", html=False)
        self.assertContains(response, 'rel="icon"', html=False)
        self.assertContains(response, "favicon", html=False)
        self.assertContains(response, "data-login-particles-canvas")
        self.assertContains(response, "auth-stage-theme-toggle")
        self.assertNotContains(response, "<h2>Авторизация</h2>", html=False)
        self.assertContains(response, 'for="id_username" class="visually-hidden"', html=False)
        self.assertContains(response, 'for="id_password" class="visually-hidden"', html=False)
        self.assertContains(response, 'for="id_token" class="visually-hidden"', html=False)

    def test_legacy_login_path_redirects_to_clean_root_without_query(self):
        response = self.client.get("/login/?next=/services/", follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("portal:login"))

    def test_empty_account_login_shows_no_error_messages(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="", password="", fingerprint="empty-account-browser"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Мы вас не узнаем")
        self.assertNotContains(response, "Введите логин.")
        self.assertNotContains(response, "Введите пароль.")
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(UserLoginLockState.objects.count(), 0)
        self.assertEqual(BrowserLoginLockState.objects.count(), 0)

    def test_empty_token_login_shows_no_error_messages(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(auth_method=AUTH_METHOD_TOKEN, token="", fingerprint="empty-token-browser"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Мы вас не узнаем")
        self.assertNotContains(response, "Введите токен.")
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(UserLoginLockState.objects.count(), 0)
        self.assertEqual(BrowserLoginLockState.objects.count(), 0)

    def test_invalid_account_login_triggers_short_alert_particle_variant(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="employee", password="WrongPass123!", fingerprint="bad-account-browser"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-login-particle-base-variant="calm"', html=False)
        self.assertContains(response, 'data-login-particle-variant="alert"', html=False)
        self.assertContains(response, 'data-login-particle-revert-delay="2000"', html=False)

    def test_invalid_token_login_triggers_short_alert_particle_variant(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(auth_method=AUTH_METHOD_TOKEN, token="bad-token", fingerprint="bad-token-browser"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-login-particle-base-variant="calm"', html=False)
        self.assertContains(response, 'data-login-particle-variant="alert"', html=False)
        self.assertContains(response, 'data-login-particle-revert-delay="2000"', html=False)

    def test_ajax_invalid_account_login_returns_json_without_redirect(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="employee", password="WrongPass123!", fingerprint="ajax-bad-account"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["particle_variant"], "alert")
        self.assertEqual(payload["revert_delay_ms"], 2000)
        self.assertEqual(payload["non_field_errors"], ["Мы вас не узнаем"])

    def test_ajax_invalid_token_login_returns_json_without_redirect(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(auth_method=AUTH_METHOD_TOKEN, token="bad-token", fingerprint="ajax-bad-token"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["particle_variant"], "alert")
        self.assertEqual(payload["revert_delay_ms"], 2000)
        self.assertEqual(payload["non_field_errors"], ["Мы вас не узнаем"])

    def test_ajax_successful_login_returns_redirect_json(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="employee", password="VeryStrongPass123!", fingerprint="ajax-good-account"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["redirect_url"], reverse("portal:dashboard"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_page_exposes_visual_particle_settings(self):
        settings_row = PortalVisualSettings.get_solo()
        settings_row.login_particles_desktop_count = 177
        settings_row.login_particles_mobile_count = 61
        settings_row.calm_speed_px = 44
        settings_row.alert_speed_px = 220
        settings_row.save()

        response = self.client.get(reverse("portal:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-login-particle-desktop-count="177"', html=False)
        self.assertContains(response, 'data-login-particle-mobile-count="61"', html=False)
        self.assertContains(response, 'data-login-particle-calm-speed="44"', html=False)
        self.assertContains(response, 'data-login-particle-alert-speed="220"', html=False)

    def test_dashboard_hides_topbar_title_and_workzone_eyebrow(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Рабочая зона")
        self.assertNotContains(response, 'class="topbar-title"', html=False)
        self.assertContains(response, "Каталог сервисов")
        self.assertContains(response, 'data-service-layout-board', html=False)

    def test_security_page_uses_topbar_without_large_page_title(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:security"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="topbar-title"', html=False)
        self.assertNotContains(response, "Безопасность HUB")
        self.assertNotContains(response, "Управляйте списком разрешённых IP/CIDR")
        self.assertContains(response, "Доступ к HUB")

    def test_visual_settings_page_updates_particle_configuration(self):
        self.establish_session(self.admin_user)

        response = self.client.post(
            reverse("portal:visual_settings"),
            {
                "login_particles_desktop_count": "160",
                "blocked_particles_desktop_count": "170",
                "login_particles_mobile_count": "55",
                "blocked_particles_mobile_count": "65",
                "calm_speed_px": "32",
                "alert_speed_px": "210",
                "invalid_alert_duration_seconds": "3",
                "lock_alert_duration_seconds": "6",
                "alert_fade_duration_seconds": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("portal:visual_settings"))
        settings_row = PortalVisualSettings.get_solo()
        self.assertEqual(settings_row.login_particles_desktop_count, 160)
        self.assertEqual(settings_row.blocked_particles_desktop_count, 170)
        self.assertEqual(settings_row.login_particles_mobile_count, 55)
        self.assertEqual(settings_row.blocked_particles_mobile_count, 65)
        self.assertEqual(settings_row.calm_speed_px, 32)
        self.assertEqual(settings_row.alert_speed_px, 210)
        self.assertEqual(settings_row.invalid_alert_duration_seconds, 3)
        self.assertEqual(settings_row.lock_alert_duration_seconds, 6)
        self.assertEqual(settings_row.alert_fade_duration_seconds, 2)

    def test_service_layout_slot_is_created_automatically_for_new_service(self):
        layout = ServiceLayout.objects.filter(service=self.service, scope="global").first()

        self.assertIsNotNone(layout)
        self.assertEqual(layout.slot_index, 0)

    def test_dashboard_preserves_global_slots_for_visible_services(self):
        hidden_service = Service.objects.create(
            title="DNS",
            slug="dns",
            description="DNS система",
            domain="dns.hub.example.com",
            upstream_url="http://192.0.2.67:80",
            icon_key="dns",
            sort_order=20,
        )
        ServiceLayout.objects.filter(service=hidden_service, scope="global").update(slot_index=0)
        ServiceLayout.objects.filter(service=self.service, scope="global").update(slot_index=1)
        self.establish_session(self.user)

        response = self.client.get(reverse("portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-slot-index="0"', html=False)
        self.assertContains(response, 'data-slot-index="1"', html=False)
        self.assertContains(response, "service-slot is-empty", count=1)
        self.assertContains(response, 'data-slot-service-id="{}"'.format(self.service.pk), html=False)

    def test_password_login_sets_day_session_and_writes_audit_event(self):
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="employee", password="VeryStrongPass123!"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get_expiry_age(), 86400)
        self.assertEqual(self.client.session[SESSION_AUTH_METHOD_KEY], AUTH_METHOD_ACCOUNT)
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="employee",
                auth_method=AUTH_METHOD_ACCOUNT,
                event_kind="login",
                result="success",
            ).exists()
        )
        self.assertTrue(
            UserSession.objects.filter(
                user=self.user,
                auth_method=AUTH_METHOD_ACCOUNT,
                revoked_at__isnull=True,
            ).exists()
        )
        self.assertEqual(self.client.session.get("_hub_user_access_rules"), [])
        self.assertEqual(self.client.session.get("_hub_user_service_ids"), [self.service.pk])

    def test_account_login_with_temp_password_redirects_to_change_page(self):
        temp_user = User.objects.create_user(
            username="tempuser",
            password="VeryStrongPass123!",
            must_change_password=True,
        )
        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(username="tempuser", password="VeryStrongPass123!", fingerprint="temp-user-browser"),
        )
        self.assertRedirects(response, reverse("portal:password_change_first"))
        self.assertEqual(temp_user.username, "tempuser")

    def test_first_password_change_page_uses_login_modal_layout_without_portal_chrome(self):
        temp_user = User.objects.create_user(
            username="tempmodal",
            password="VeryStrongPass123!",
            must_change_password=True,
        )
        self.establish_session(temp_user)

        response = self.client.get(reverse("portal:password_change_first"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="login-shell"', html=False)
        self.assertContains(response, 'class="glass-panel auth-card"', html=False)
        self.assertContains(response, "Укажите новый пароль")
        self.assertContains(response, 'data-theme-toggle', html=False)
        self.assertNotContains(response, "Первый вход")
        self.assertNotContains(response, "Нужно обновить пароль")
        self.assertNotContains(response, "Текущий пароль выдан администратором как временный")
        self.assertNotContains(response, 'class="topbar glass-panel"', html=False)

    def test_first_password_change_invalid_post_keeps_modal_layout(self):
        temp_user = User.objects.create_user(
            username="tempmodalpost",
            password="VeryStrongPass123!",
            must_change_password=True,
        )
        self.establish_session(temp_user)

        response = self.client.post(
            reverse("portal:password_change_first"),
            {
                "new_password1": "NewStrongPass123!",
                "new_password2": "MismatchPass456!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="login-shell"', html=False)
        self.assertContains(response, 'class="glass-panel auth-card"', html=False)
        self.assertContains(response, "Укажите новый пароль")
        self.assertNotContains(response, 'class="topbar glass-panel"', html=False)

    def test_token_login_bypasses_password_change_and_uses_encrypted_storage(self):
        temp_user = User.objects.create_user(
            username="tokenuser",
            password="VeryStrongPass123!",
            must_change_password=True,
        )
        temp_user.services.add(self.service)
        raw_token = temp_user.rotate_login_token()
        temp_user.save(update_fields=["login_token_hash", "login_token_ciphertext", "login_token_created_at"])

        response = self.client.post(
            reverse("portal:login"),
            self.build_login_payload(auth_method=AUTH_METHOD_TOKEN, token=raw_token, fingerprint="token-browser"),
        )

        self.assertRedirects(response, reverse("portal:dashboard"))
        self.assertEqual(self.client.session[SESSION_AUTH_METHOD_KEY], AUTH_METHOD_TOKEN)
        temp_user.refresh_from_db()
        self.assertEqual(temp_user.reveal_login_token(), raw_token)
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="tokenuser",
                auth_method=AUTH_METHOD_TOKEN,
                event_kind="login",
                result="success",
            ).exists()
        )

        authz_response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(),
        )
        self.assertEqual(authz_response.status_code, 200)

    def test_account_login_lock_blocks_same_browser_but_not_another_browser_on_same_ip(self):
        login_url = reverse("portal:login")
        browser_a = "browser-a|office-1"
        browser_b = "browser-b|office-1"

        for _ in range(2):
            response = self.client.post(
                login_url,
                self.build_login_payload(username="employee", password="WrongPass123!", fingerprint=browser_a),
            )
            self.assertEqual(response.status_code, 200)

        locked_response = self.client.post(
            login_url,
            self.build_login_payload(username="employee", password="WrongPass123!", fingerprint=browser_a),
        )
        self.assertEqual(locked_response.status_code, 429)
        self.assertContains(locked_response, "Превышено количество попыток авторизоваться", status_code=429)
        self.assertContains(locked_response, 'data-login-particle-base-variant="calm"', status_code=429, html=False)
        self.assertContains(locked_response, 'data-login-particle-variant="alert"', status_code=429, html=False)
        self.assertContains(locked_response, 'data-login-particle-revert-delay="5000"', status_code=429, html=False)
        self.assertTrue(UserLoginLockState.objects.filter(user=self.user).exists())
        self.assertTrue(BrowserLoginLockState.objects.filter(user=self.user).exists())

        blocked_other_account = self.client.post(
            login_url,
            self.build_login_payload(username="employee2", password="VeryStrongPass456!", fingerprint=browser_a),
        )
        self.assertEqual(blocked_other_account.status_code, 429)

        other_browser = self.client_class()
        allowed_response = other_browser.post(
            login_url,
            self.build_login_payload(username="employee2", password="VeryStrongPass456!", fingerprint=browser_b),
        )
        self.assertEqual(allowed_response.status_code, 302)

    def test_token_login_is_not_blocked_by_account_login_lock(self):
        raw_token = self.other_user.rotate_login_token()
        self.other_user.save(
            update_fields=[
                "login_token_hash",
                "login_token_ciphertext",
                "login_token_created_at",
            ]
        )
        login_url = reverse("portal:login")
        browser_a = "browser-a|token-bypass"

        for _ in range(3):
            self.client.post(
                login_url,
                self.build_login_payload(username="employee", password="WrongPass123!", fingerprint=browser_a),
            )

        token_response = self.client.post(
            login_url,
            self.build_login_payload(auth_method=AUTH_METHOD_TOKEN, token=raw_token, fingerprint=browser_a),
        )
        self.assertEqual(token_response.status_code, 302)
        self.assertEqual(self.client.session[SESSION_AUTH_METHOD_KEY], AUTH_METHOD_TOKEN)

    def test_admin_can_reset_user_login_lock_and_delete_snapshots(self):
        login_url = reverse("portal:login")
        browser_a = "browser-a|reset-user"

        for _ in range(3):
            self.client.post(
                login_url,
                self.build_login_payload(username="employee", password="WrongPass123!", fingerprint=browser_a),
            )

        snapshot_dir = os.path.join(self.login_protection_dir.name, "browser-locks")
        self.assertTrue(os.path.isdir(snapshot_dir))
        self.assertTrue(any(name.endswith(".json") for name in os.listdir(snapshot_dir)))

        admin_client = self.client_class()
        self.establish_session(self.admin_user, client=admin_client)
        reset_response = admin_client.post(reverse("portal:rights_user_login_lock_reset", args=[self.user.pk]))
        self.assertEqual(reset_response.status_code, 200)
        self.assertJSONEqual(
            reset_response.content,
            {
                "ok": True,
                "message": f"Ограничения авторизации для {self.user.username} сброшены.",
                "removed_entries": 2,
            },
        )
        self.assertFalse(UserLoginLockState.objects.filter(user=self.user).exists())
        self.assertFalse(BrowserLoginLockState.objects.filter(user=self.user).exists())
        self.assertEqual(os.listdir(snapshot_dir), [])

        success_response = self.client.post(
            login_url,
            self.build_login_payload(username="employee", password="VeryStrongPass123!", fingerprint=browser_a),
        )
        self.assertEqual(success_response.status_code, 302)

    def test_security_settings_can_reset_all_login_locks(self):
        login_url = reverse("portal:login")
        browser_a = "browser-a|global-reset"

        for _ in range(3):
            self.client.post(
                login_url,
                self.build_login_payload(username="employee", password="WrongPass123!", fingerprint=browser_a),
            )

        PortalSecuritySettings.get_solo()
        admin_client = self.client_class()
        self.establish_session(self.admin_user, client=admin_client)
        response = admin_client.post(
            reverse("portal:security"),
            {"security_action": "reset_lockouts"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(UserLoginLockState.objects.exists())
        self.assertFalse(BrowserLoginLockState.objects.exists())
        self.assertEqual(AuditEvent.objects.filter(event_kind="login_lock_reset_all").count(), 1)

    def test_internal_authz_allows_authorized_user_without_logging_successful_gate_pass(self):
        self.establish_session(self.user)
        count_before = AuditEvent.objects.count()

        response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(request_uri="/api/operator/call-sessions/active?operator_user_id=12"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), count_before)

    def test_internal_authz_refreshes_session_access_cache_after_permissions_change(self):
        self.establish_session(self.user)

        first_response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(),
        )
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(self.client.session.get("_hub_user_service_ids"), [self.service.pk])

        self.user.services.remove(self.service)

        second_response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(),
        )
        self.assertEqual(second_response.status_code, 403)
        self.assertEqual(self.client.session.get("_hub_user_service_ids"), [])

    def test_service_open_allows_unknown_status_and_blocks_only_when_down(self):
        self.establish_session(self.user)

        allowed_response = self.client.get(reverse("portal:service_open", args=[self.service.slug]))
        self.assertRedirects(allowed_response, self.service.launch_url, fetch_redirect_response=False)

        allowed_event = AuditEvent.objects.filter(
            username_snapshot="employee",
            event_kind="service_open",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(allowed_event)
        self.assertEqual(allowed_event.target_label, "CRM · Открытие")

        ServiceHealthState.objects.update_or_create(
            service=self.service,
            defaults={"status": "down", "last_checked_at": timezone.now()},
        )

        blocked_response = self.client.get(reverse("portal:service_open", args=[self.service.slug]))
        self.assertRedirects(blocked_response, reverse("portal:dashboard"))

        blocked_event = AuditEvent.objects.filter(
            username_snapshot="employee",
            event_kind="service_open",
            result="blocked",
        ).order_by("-pk").first()
        self.assertIsNotNone(blocked_event)
        self.assertEqual(blocked_event.target_label, "CRM · Открытие")

    def test_service_open_blocks_disabled_service(self):
        self.service.is_enabled = False
        self.service.save(update_fields=["is_enabled"])
        self.establish_session(self.user)

        response = self.client.get(reverse("portal:service_open", args=[self.service.slug]))

        self.assertRedirects(response, reverse("portal:dashboard"))
        event = AuditEvent.objects.filter(
            username_snapshot="employee",
            event_kind="service_open",
            result="blocked",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "CRM · Открытие")

    def test_internal_authz_denies_unauthorized_user_and_logs_denial(self):
        other_user = User.objects.create_user(
            username="guest",
            password="VeryStrongPass123!",
            must_change_password=False,
        )
        self.establish_session(other_user)

        response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="guest",
                event_kind="service_gate",
                result="denied",
                target_label="CRM · Прямой доступ",
            ).exists()
        )

    def test_internal_authz_logs_unauthenticated_direct_access_as_service_gate(self):
        response = self.client.get(
            reverse("portal:internal_authz"),
            **self.build_internal_headers(request_uri="/records/"),
        )
        self.assertEqual(response.status_code, 401)
        event = AuditEvent.objects.filter(
            event_kind="service_gate",
            result="unauthenticated",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "CRM · Прямой доступ")
        self.assertEqual(event.category, "CRM")

    def test_global_portal_ip_restriction_returns_access_restricted_page(self):
        replace_portal_access_rules(["203.0.113.0/24"])

        response = self.client.get(reverse("portal:login"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "data-login-particles-canvas", status_code=403)
        self.assertContains(response, 'data-login-particle-base-variant="alert"', status_code=403, html=False)
        self.assertContains(response, 'data-login-particle-variant="alert"', status_code=403, html=False)
        self.assertNotContains(response, "Доступ ограничен", status_code=403)
        self.assertNotContains(response, "auth-stage-theme-toggle", status_code=403, html=False)
        self.assertContains(response, "/static/portal/particles", status_code=403, html=False)
        self.assertNotContains(response, "/static/portal/login", status_code=403, html=False)
        self.assertNotContains(response, "auth_method", status_code=403)

    def test_login_is_blocked_when_user_ip_is_not_allowed(self):
        replace_user_access_rules(self.user, ["203.0.113.0/24"])

        response = self.client.post(
            reverse("portal:login"),
            {
                "auth_method": AUTH_METHOD_ACCOUNT,
                "username": "employee",
                "password": "VeryStrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Доступ ограничен", status_code=403)
        self.assertContains(response, 'data-login-particle-base-variant="calm"', status_code=403, html=False)
        self.assertContains(response, 'data-login-particle-variant="alert"', status_code=403, html=False)
        self.assertContains(response, 'data-login-particle-revert-delay="5000"', status_code=403, html=False)
        self.assertNotContains(
            response,
            "Авторизация под этой учётной записью с текущего IP-адреса запрещена.",
            status_code=403,
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="employee",
                event_kind="login_ip_block",
                result="failure",
            ).exists()
        )

    def test_ajax_login_is_blocked_when_user_ip_is_not_allowed(self):
        replace_user_access_rules(self.user, ["203.0.113.0/24"])

        response = self.client.post(
            reverse("portal:login"),
            {
                "auth_method": AUTH_METHOD_ACCOUNT,
                "username": "employee",
                "password": "VeryStrongPass123!",
                "browser_fingerprint": "ajax-user-ip-denied",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["show_ip_access_denied"])
        self.assertEqual(payload["particle_variant"], "alert")
        self.assertEqual(payload["revert_delay_ms"], 5000)

    def test_log_audit_event_is_skipped_during_audit_maintenance(self):
        request = self.factory.get("/", HTTP_HOST="hub.example.com")

        with patch("portal.audit.is_audit_maintenance_active", return_value=True):
            result = log_audit_event(
                request,
                user=self.admin_user,
                auth_method=AUTH_METHOD_ACCOUNT,
                target_label="Портал · Проверка",
                event_kind="page_view",
                result="success",
                category="Портал",
                action="Проверка",
            )

        self.assertIsNone(result)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_staff_sees_all_services(self):
        self.establish_session(self.admin_user)
        response = self.client.get(reverse("portal:dashboard"))
        self.assertContains(response, "CRM")

    def test_management_page_has_add_service_button_only_for_admin(self):
        self.establish_session(self.admin_user)

        rights_response = self.client.get(reverse("portal:rights_home"))
        dashboard_response = self.client.get(reverse("portal:dashboard"))

        self.assertContains(rights_response, "Добавить сервис")
        self.assertContains(rights_response, reverse("portal:rights_service_create"))
        self.assertNotContains(dashboard_response, "Добавить сервис")
        self.assertNotContains(dashboard_response, reverse("portal:rights_service_create"))

    def test_service_admin_modal_is_available_for_desktop_admin(self):
        self.establish_session(self.admin_user)

        response = self.client.get(
            f"{reverse('portal:rights_service_create')}?modal=1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новый сервис")
        self.assertContains(response, "Получить сертификат")

    def test_service_admin_modal_renders_profiles_and_detail_buttons(self):
        self.establish_session(self.admin_user)

        response = self.client.get(
            f"{reverse('portal:rights_service_create')}?modal=1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Готовые профили")
        self.assertContains(response, "Применить профиль")
        self.assertContains(response, "Подробнее")
        self.assertContains(response, "Стандартный web-сервис")
        self.assertContains(response, "service-option-catalog-data", html=False)
        self.assertContains(response, "service-option-profile-data", html=False)

    def test_issue_certificate_from_create_modal_does_not_create_service_row(self):
        self.establish_session(self.admin_user)
        domain = "draft-cert.hub.example.com"

        with patch(
            "portal.views.issue_service_certificate",
            return_value=ServiceOperationResult(ok=True, message="Certificate is ready for draft-cert.hub.example.com."),
        ):
            response = self.client.post(
                f"{reverse('portal:rights_service_create')}?modal=1",
                {
                    "operation": "issue_certificate",
                    "domain": domain,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Service.objects.filter(domain=domain).exists())

    def test_create_service_draft_switches_modal_to_edit_endpoint(self):
        self.establish_session(self.admin_user)
        response = self.client.post(
            f"{reverse('portal:rights_service_create')}?modal=1",
            {
                "operation": "draft",
                "title": "Тестовый сервис",
                "domain": "draft-service.hub.example.com",
                "upstream_url": "http://192.0.2.88:8080/",
                "description": "Черновик",
                "icon_key": "default",
                "sort_order": "120",
                "is_active": "on",
                "is_enabled": "on",
                "precheck_url": "http://192.0.2.88:8080/",
                "cert_fullchain_path": "",
                "cert_privkey_path": "",
                "check_type": "http",
                "health_host": "192.0.2.88",
                "health_port": "8080",
                "health_path": "/",
                "timeout_seconds": "3",
                "allowed_status_codes": "200,204,301,302,401,403",
                "interval_seconds": "5",
                "failure_threshold": "2",
                "success_threshold": "1",
                "config_mode": "managed",
                "manual_config": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        service = Service.objects.get(domain="draft-service.hub.example.com")
        edit_action = f'action="{reverse("portal:rights_service_edit", args=[service.pk])}?modal=1"'
        self.assertIn("Редактирование сервиса", payload["html"])
        self.assertIn(edit_action, payload["html"])

    def test_service_option_catalog_exposes_examples_and_relations(self):
        catalog = get_service_option_catalog()
        streaming = catalog["streaming_mode"]
        legacy = catalog["omit_forwarded_for"]
        profiles = get_service_profile_rows()

        self.assertTrue(streaming["examples"])
        self.assertTrue(streaming["practices"])
        self.assertIn("proxy_buffering off;", streaming["directives"])
        self.assertIn("proxy_buffering off", " ".join(streaming["dependencies_titles"]))
        self.assertIn("Append X-Forwarded-For chain", legacy["conflicts_titles"])
        self.assertTrue(any(profile["key"] == "standard_web" for profile in profiles))
        self.assertTrue(any("Security headers snippet" in profile["feature_titles"] for profile in profiles))

    def test_render_managed_service_config_supports_extended_managed_features(self):
        service = Service(
            title="Docs",
            slug="docs",
            description="Docs",
            domain="docs.hub.example.com",
            upstream_url="https://192.0.2.62:8443/app/",
            icon_key="default",
            precheck_url="https://192.0.2.62:8443/app/",
            health_host="192.0.2.62",
            health_port=8443,
            health_path="/app/",
            cert_fullchain_path="/etc/letsencrypt/live/docs.hub.example.com/fullchain.pem",
            cert_privkey_path="/etc/letsencrypt/live/docs.hub.example.com/privkey.pem",
            managed_features=[
                "streaming_mode",
                "forward_authorization_header",
                "append_forwarded_for",
                "upstream_host_header",
                "proxy_redirect_off",
                "disable_next_upstream",
                "tls13_upstream_only",
            ],
        )

        config = render_managed_service_config(service)

        self.assertIn("proxy_request_buffering off;", config)
        self.assertIn('add_header X-Accel-Buffering "no" always;', config)
        self.assertIn("proxy_set_header Authorization $http_authorization;", config)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", config)
        self.assertIn("proxy_set_header Host 192.0.2.62:8443;", config)
        self.assertIn("proxy_redirect off;", config)
        self.assertIn("proxy_next_upstream off;", config)
        self.assertIn("proxy_ssl_protocols TLSv1.3;", config)

    def test_service_admin_mobile_request_is_rejected(self):
        self.establish_session(self.admin_user)

        response = self.client.get(
            f"{reverse('portal:rights_service_create')}?modal=1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14; Mobile)",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["ok"], False)
        self.assertIn("desktop", response.json()["message"].lower())

    def test_logout_clears_session_and_writes_audit_event(self):
        self.establish_session(self.admin_user)
        response = self.client.post(reverse("portal:logout"))
        self.assertRedirects(response, reverse("portal:login"))
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="admin",
                event_kind="logout",
                result="success",
            ).exists()
        )

        dashboard = self.client.get(reverse("portal:dashboard"))
        self.assertEqual(dashboard.status_code, 302)
        self.assertEqual(dashboard.url, reverse("portal:login"))

    def test_dashboard_is_never_cached(self):
        self.establish_session(self.admin_user)
        response = self.client.get(reverse("portal:dashboard"))
        self.assertIn("no-store", response["Cache-Control"])

    def test_release_state_endpoint_is_public_and_never_cached(self):
        with patch("portal.views.get_release_id", return_value="test-release"):
            response = self.client.get(reverse("portal:release_state"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["release_id"], "test-release")
        self.assertIn("no-store", response["Cache-Control"])

    def test_login_page_uses_release_bootstrap_and_loads_login_bundle_only(self):
        with patch("portal.context_processors.get_release_id", return_value="test-release"):
            response = self.client.get(reverse("portal:login"))
        self.assertContains(response, 'data-release-id="test-release"', html=False)
        self.assertContains(response, 'data-release-state-url="/api/release-state/"', html=False)
        self.assertContains(response, "/static/portal/styles", html=False)
        self.assertContains(response, "/static/portal/particles", html=False)
        self.assertContains(response, "/static/portal/login", html=False)
        self.assertNotContains(response, "/static/portal/app", html=False)
        self.assertNotContains(response, "?v=", html=False)

    def test_dashboard_redirect_keeps_clean_root_login_and_restores_target_after_auth(self):
        anonymous_client = self.client_class()

        response = anonymous_client.get(reverse("portal:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("portal:login"))
        self.assertEqual(anonymous_client.session.get("portal_login_next_url"), reverse("portal:dashboard"))

        login_response = anonymous_client.post(
            reverse("portal:login"),
            self.build_login_payload(username="employee", password="VeryStrongPass123!", fingerprint="clean-root-browser"),
        )
        self.assertRedirects(login_response, reverse("portal:dashboard"))

    def test_first_password_change_page_uses_login_bundle_only(self):
        temp_user = User.objects.create_user(
            username="bundlecheck",
            password="VeryStrongPass123!",
            must_change_password=True,
        )
        self.establish_session(temp_user)

        response = self.client.get(reverse("portal:password_change_first"))

        self.assertContains(response, "/static/portal/login", html=False)
        self.assertNotContains(response, "/static/portal/app", html=False)

    def test_forbidden_page_redirects_to_portal_dashboard(self):
        response = self.client.get(reverse("portal:forbidden"), HTTP_HOST="dns.hub.example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://hub.example.com/")

    def test_account_page_changes_password_and_logs_event(self):
        self.establish_session(self.user)

        response = self.client.post(
            reverse("portal:account"),
            {
                "old_password": "VeryStrongPass123!",
                "new_password1": "AnotherStrongPass456!",
                "new_password2": "AnotherStrongPass456!",
            },
        )

        self.assertRedirects(response, reverse("portal:account"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("AnotherStrongPass456!"))
        self.assertTrue(
            AuditEvent.objects.filter(
                username_snapshot="employee",
                event_kind="password_change",
                result="success",
            ).exists()
        )

    def test_admin_can_grant_admin_role_to_other_user_and_role_change_is_logged(self):
        self.establish_session(self.admin_user)

        response = self.client.post(
            reverse("portal:rights_user_edit", args=[self.user.pk]),
            {
                "username": self.user.username,
                "full_name": self.user.full_name,
                "is_active": "on",
                "is_staff": "on",
                "services": [str(self.service.pk)],
                "set_temporary_password": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="role_change",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Пользователи · Изменение роли")
        self.assertEqual(event.metadata.get("username"), "employee")
        self.assertTrue(event.metadata.get("is_staff"))

    def test_admin_can_create_user_with_generated_temporary_password_and_login_with_it(self):
        self.establish_session(self.admin_user)
        generated_password = "GenPass234!"

        response = self.client.post(
            reverse("portal:rights_user_create"),
            {
                "username": "generated-user",
                "full_name": "Сгенерированный Пользователь",
                "is_active": "on",
                "password1": generated_password,
                "password2": generated_password,
                "services": [str(self.service.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username="generated-user")
        self.assertTrue(created_user.check_password(generated_password))
        self.assertTrue(created_user.must_change_password)

        self.client.logout()
        login_response = self.client.post(
            reverse("portal:login"),
            {
                "auth_method": AUTH_METHOD_ACCOUNT,
                "username": "generated-user",
                "password": generated_password,
            },
        )
        self.assertRedirects(login_response, reverse("portal:password_change_first"))

    def test_admin_can_set_generated_temporary_password_for_existing_user(self):
        self.establish_session(self.admin_user)
        generated_password = "ResetPass234!"

        response = self.client.post(
            reverse("portal:rights_user_edit", args=[self.user.pk]),
            {
                "username": self.user.username,
                "full_name": self.user.full_name,
                "is_active": "on",
                "services": [str(self.service.pk)],
                "set_temporary_password": "1",
                "new_password1": generated_password,
                "new_password2": generated_password,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(generated_password))
        self.assertTrue(self.user.must_change_password)

        self.client.logout()
        login_response = self.client.post(
            reverse("portal:login"),
            {
                "auth_method": AUTH_METHOD_ACCOUNT,
                "username": self.user.username,
                "password": generated_password,
            },
        )
        self.assertRedirects(login_response, reverse("portal:password_change_first"))

    def test_non_staff_actor_cannot_change_existing_user_identity_fields_in_update_form(self):
        outsider = User.objects.create_user(
            username="guest",
            password="VeryStrongPass123!",
            full_name="Гость",
            must_change_password=False,
        )
        form = ManagedUserUpdateForm(
            data={
                "username": "rewritten",
                "full_name": "Перезаписано",
                "is_active": "on",
                "services": [str(self.service.pk)],
                "set_temporary_password": "",
            },
            instance=self.user,
            actor=outsider,
        )

        self.assertTrue(form.fields["username"].disabled)
        self.assertTrue(form.fields["full_name"].disabled)
        self.assertTrue(form.is_valid(), form.errors.as_json())

        updated_user = form.save()
        self.assertEqual(updated_user.username, "employee")
        self.assertEqual(updated_user.full_name, "Иван Петров")

    def test_edit_user_modal_no_longer_shows_username_helper_text(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:rights_user_edit", args=[self.user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "До 150 символов. Буквы, цифры и символы @/./+/-/_.")

    def test_edit_user_modal_no_longer_shows_change_password_shortcut_button(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:rights_user_edit", args=[self.user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-tab-target="security"', html=False)

    def test_admin_can_generate_and_reveal_token_for_user(self):
        self.establish_session(self.admin_user)

        generate_response = self.client.post(reverse("portal:rights_user_token_generate", args=[self.user.pk]))
        self.assertEqual(generate_response.status_code, 200)
        payload = generate_response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["has_token"])
        self.assertEqual(User.find_by_login_token(payload["token"]), self.user)

        reveal_response = self.client.post(reverse("portal:rights_user_token_reveal", args=[self.user.pk]))
        self.assertEqual(reveal_response.status_code, 200)
        reveal_payload = reveal_response.json()
        self.assertEqual(reveal_payload["token"], payload["token"])

        generate_event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="token_generate",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(generate_event)
        self.assertEqual(generate_event.target_label, "Токены · Создание")
        self.assertEqual(generate_event.metadata.get("username"), "employee")

        reveal_event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="token_reveal",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(reveal_event)
        self.assertEqual(reveal_event.target_label, "Токены · Показ")
        self.assertEqual(reveal_event.metadata.get("username"), "employee")

    def test_owner_can_reveal_own_token(self):
        raw_token = self.user.rotate_login_token()
        self.user.save(update_fields=["login_token_hash", "login_token_ciphertext", "login_token_created_at"])
        self.establish_session(self.user)

        response = self.client.post(reverse("portal:account_token_reveal"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token"], raw_token)
        event = AuditEvent.objects.filter(
            username_snapshot="employee",
            event_kind="token_reveal",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Токены · Показ")

    def test_non_staff_cannot_reveal_foreign_token(self):
        raw_token = self.user.rotate_login_token()
        self.user.save(update_fields=["login_token_hash", "login_token_ciphertext", "login_token_created_at"])
        other_user = User.objects.create_user(
            username="guest",
            password="VeryStrongPass123!",
            must_change_password=False,
        )
        self.establish_session(other_user)

        response = self.client.post(reverse("portal:rights_user_token_reveal", args=[self.user.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.find_by_login_token(raw_token), self.user)

    def test_require_password_change_endpoint_sets_flag_and_logs_event(self):
        self.establish_session(self.admin_user)
        self.user.must_change_password = False
        self.user.save(update_fields=["must_change_password"])

        response = self.client.post(reverse("portal:rights_user_require_password_change", args=[self.user.pk]))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="password_change_required",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Пользователи · Требование смены пароля")
        self.assertEqual(event.metadata.get("username"), "employee")

    def test_clear_audit_events_command_deletes_all_rows(self):
        AuditEvent.objects.create(
            username_snapshot="admin",
            target_label="Портал · Сервисы",
            event_kind="page_view",
            result="success",
        )
        AuditEvent.objects.create(
            username_snapshot="employee",
            target_label="Портал · Учётная запись",
            event_kind="page_view",
            result="success",
        )

        call_command("clear_audit_events")

        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_admin_can_restore_archived_user(self):
        self.establish_session(self.admin_user)
        self.user.archive()
        self.user.save(
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

        response = self.client.post(reverse("portal:rights_user_restore", args=[self.user.pk]))

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.deleted_at)
        self.assertTrue(self.user.is_active)
        event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="user_restore",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Пользователи · Разархивация")

    def test_journal_available_only_to_admin_and_filters_use_existing_values(self):
        self.client.post(
            reverse("portal:login"),
            {"auth_method": AUTH_METHOD_ACCOUNT, "username": "employee", "password": "VeryStrongPass123!"},
        )
        self.establish_session(self.admin_user)
        response = self.client.get(reverse("portal:rights_logs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Журнал действий")
        self.assertContains(response, "employee")
        self.assertContains(response, "Учётная запись")
        self.assertContains(response, "Все типы")
        self.assertContains(response, "Прямой доступ")

        self.client.logout()
        self.establish_session(self.user)
        denied = self.client.get(reverse("portal:rights_logs"))
        self.assertRedirects(denied, reverse("portal:dashboard"))

    def test_admin_can_clear_logs_endpoint_and_first_logs_page_stays_empty(self):
        self.establish_session(self.admin_user)

        with patch(
            "portal.views.clear_audit_trail",
            return_value={
                "ok": True,
                "deleted_events": 14,
                "cleared_files": 3,
                "removed_archives": 2,
                "message": "Журнал и project-логи HUB очищены.",
            },
        ):
            response = self.client.post(
                reverse("portal:rights_logs_clear"),
                {"confirmation_phrase": "ОЧИСТИТЬ"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["deleted_events"], 14)
        self.assertEqual(self.client.session.get("suppress_next_logs_audit"), True)

        page = self.client.get(reverse("portal:rights_logs"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Найдено событий: 0")
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_clear_logs_endpoint_rejects_wrong_phrase(self):
        self.establish_session(self.admin_user)
        response = self.client.post(
            reverse("portal:rights_logs_clear"),
            {"confirmation_phrase": "очистить"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_clear_logs_endpoint_returns_busy_error_when_cleanup_is_running(self):
        self.establish_session(self.admin_user)
        with patch("portal.views.clear_audit_trail", side_effect=AuditMaintenanceBusyError("Очистка уже выполняется.")):
            response = self.client.post(
                reverse("portal:rights_logs_clear"),
                {"confirmation_phrase": "ОЧИСТИТЬ"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["message"], "Очистка уже выполняется.")

    def test_non_staff_cannot_clear_logs(self):
        self.establish_session(self.user)
        response = self.client.post(
            reverse("portal:rights_logs_clear"),
            {"confirmation_phrase": "ОЧИСТИТЬ"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("portal:dashboard"))

    def test_user_tables_render_clickable_rows_and_service_badges(self):
        self.establish_session(self.admin_user)
        response = self.client.get(reverse("portal:rights_users"))
        self.assertContains(response, 'data-row-link="{}"'.format(reverse("portal:rights_user_edit", args=[self.user.pk])), html=False)
        self.assertContains(response, "CRM")

    def test_management_page_shows_service_catalog_with_management_navigation(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:rights_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Активные сервисы")
        self.assertContains(response, 'data-service-grid', html=False)
        self.assertContains(response, 'data-service-layout-board', html=False)
        self.assertContains(response, 'data-layout-save-url', html=False)
        self.assertContains(response, 'data-layout-pin', html=False)
        self.assertContains(response, reverse("portal:service_open", args=[self.service.slug]), html=False)
        self.assertContains(response, ">Управление<", html=False)
        self.assertNotContains(response, "<p class=\"eyebrow\">Каталог</p>", html=False)
        self.assertNotContains(response, "Здесь отображаются опубликованные сервисы и их текущий статус доступности. Позже сюда будет добавлено управление созданием сервисов.")
        self.assertNotContains(response, "Все пользователи")
        self.assertNotContains(response, "Создавайте пользователей, назначайте роли, управляйте токенами и завершайте активные сессии.")
        event = AuditEvent.objects.filter(username_snapshot="admin", event_kind="page_view").order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Портал · Управление")

    def test_users_page_hides_old_intro_and_keeps_create_button_in_table_header(self):
        self.establish_session(self.admin_user)

        response = self.client.get(reverse("portal:rights_users"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Все пользователи")
        self.assertContains(
            response,
            'data-open-modal-url="{}?modal=1"'.format(reverse("portal:rights_user_create")),
            html=False,
        )
        self.assertNotContains(response, "Список сотрудников")
        self.assertNotContains(response, "Активные сервисы")
        self.assertNotContains(response, "Создавайте пользователей, назначайте роли, управляйте токенами и завершайте активные сессии.")

    def test_admin_can_reorder_service_layout_from_management(self):
        other_service = Service.objects.create(
            title="DNS",
            slug="dns",
            description="DNS зона",
            domain="dns.hub.example.com",
            upstream_url="http://192.0.2.67:80",
            icon_key="dns",
            sort_order=20,
        )
        self.establish_session(self.admin_user)

        response = self.client.post(
            reverse("portal:rights_service_layout_update"),
            {"service_id": other_service.pk, "slot_index": 0},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("DNS", payload["board_html"])

        dns_layout = ServiceLayout.objects.get(service=other_service, scope="global")
        crm_layout = ServiceLayout.objects.get(service=self.service, scope="global")
        self.assertEqual(dns_layout.slot_index, 0)
        self.assertEqual(crm_layout.slot_index, 1)

        dashboard = self.client.get(reverse("portal:dashboard"))
        self.assertEqual([service.slug for service in dashboard.context["services"]], ["dns", "crm"])

        event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="service_layout_update",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Управление · Раскладка сервисов")
        self.assertEqual(event.metadata.get("service_slug"), "dns")
        self.assertEqual(event.metadata.get("slot_index"), 0)

    def test_admin_can_reset_service_layout_to_default_order(self):
        other_service = Service.objects.create(
            title="DNS",
            slug="dns",
            description="DNS зона",
            domain="dns.hub.example.com",
            upstream_url="http://192.0.2.67:80",
            icon_key="dns",
            sort_order=20,
        )
        ServiceLayout.objects.filter(service=self.service, scope="global").update(slot_index=3)
        ServiceLayout.objects.filter(service=other_service, scope="global").update(slot_index=0)
        self.establish_session(self.admin_user)

        response = self.client.post(
            reverse("portal:rights_service_layout_reset"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changed"])
        self.assertIn("исходному порядку", payload["message"])

        crm_layout = ServiceLayout.objects.get(service=self.service, scope="global")
        dns_layout = ServiceLayout.objects.get(service=other_service, scope="global")
        self.assertEqual(crm_layout.slot_index, 0)
        self.assertEqual(dns_layout.slot_index, 1)

        event = AuditEvent.objects.filter(
            username_snapshot="admin",
            event_kind="service_layout_reset",
            result="success",
        ).order_by("-pk").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, "Управление · Сброс раскладки сервисов")

    def test_non_staff_cannot_reorder_service_layout(self):
        self.establish_session(self.user)

        response = self.client.post(
            reverse("portal:rights_service_layout_update"),
            {"service_id": self.service.pk, "slot_index": 1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("portal:dashboard"))

    def test_prune_audit_events_deletes_entries_older_than_retention(self):
        old_event = AuditEvent.objects.create(
            username_snapshot="legacy",
            occurred_at=timezone.now() - timedelta(days=181),
            target_label="Портал: старое событие",
            event_kind="page_view",
            result="success",
        )
        fresh_event = AuditEvent.objects.create(
            username_snapshot="fresh",
            occurred_at=timezone.now() - timedelta(days=10),
            target_label="Портал: новое событие",
            event_kind="page_view",
            result="success",
        )

        call_command("prune_audit_events", "--days", "180")

        self.assertFalse(AuditEvent.objects.filter(pk=old_event.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(pk=fresh_event.pk).exists())

    def test_portal_access_rules_are_reused_from_process_cache_between_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "portal-access-rules.stamp")
            with patch.dict(os.environ, {"HUB_PORTAL_ACCESS_RULE_MARKER_FILE": marker_path}, clear=False):
                first_values = get_portal_access_rule_values()

                with CaptureQueriesContext(connection) as queries:
                    second_values = get_portal_access_rule_values()

        self.assertEqual(first_values, [f"{self.allowed_ip}/32"])
        self.assertEqual(second_values, [f"{self.allowed_ip}/32"])
        self.assertEqual(len(queries), 0)

    def test_replacing_portal_access_rules_refreshes_cached_values_without_extra_select(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "portal-access-rules.stamp")
            with patch.dict(os.environ, {"HUB_PORTAL_ACCESS_RULE_MARKER_FILE": marker_path}, clear=False):
                get_portal_access_rule_values()
                replace_portal_access_rules(["203.0.113.0/24"])

                with CaptureQueriesContext(connection) as queries:
                    refreshed_values = get_portal_access_rule_values()

        self.assertEqual(refreshed_values, ["203.0.113.0/24"])
        self.assertEqual(len(queries), 0)

