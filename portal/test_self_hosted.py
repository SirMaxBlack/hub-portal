import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from portal.health import get_effective_status
from portal.models import PortalAccessRule, Service, UserAccessRule


User = get_user_model()


class SelfHostedBootstrapTests(TestCase):
    def test_bootstrap_admin_reads_password_from_stdin(self):
        with patch("sys.stdin", io.StringIO("strong-password-123\n")):
            call_command(
                "bootstrap_admin",
                username="owner",
                full_name="Portal Owner",
                password_stdin=True,
                stdout=io.StringIO(),
            )

        user = User.objects.get(username="owner")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("strong-password-123"))

    def test_configure_installation_replaces_global_and_admin_rules(self):
        admin = User.objects.create_superuser(
            username="owner",
            password="strong-password-123",
            full_name="Portal Owner",
        )

        call_command(
            "configure_installation",
            mode="restricted",
            cidr=["192.0.2.10", "198.51.100.0/24"],
            admin_username=admin.username,
            stdout=io.StringIO(),
        )

        expected = ["192.0.2.10/32", "198.51.100.0/24"]
        self.assertEqual(list(PortalAccessRule.objects.values_list("cidr", flat=True)), expected)
        self.assertEqual(list(UserAccessRule.objects.values_list("cidr", flat=True)), expected)

        call_command(
            "configure_installation",
            mode="unrestricted",
            admin_username=admin.username,
            stdout=io.StringIO(),
        )
        self.assertFalse(PortalAccessRule.objects.exists())
        self.assertFalse(UserAccessRule.objects.exists())

    def test_default_external_links_are_idempotent(self):
        output = io.StringIO()
        call_command("seed_default_links", stdout=output)
        call_command("seed_default_links", stdout=output)

        services = Service.objects.order_by("sort_order")
        self.assertEqual(services.count(), 4)
        self.assertTrue(all(service.kind == "external" for service in services))
        self.assertEqual(
            list(services.values_list("slug", flat=True)),
            ["cloudflare", "github", "chatgpt", "deepseek"],
        )

    def test_external_link_needs_no_proxy_or_health_monitor(self):
        service = Service.objects.create(
            title="Documentation",
            slug="documentation",
            kind="external",
            external_url="https://example.com/docs/",
        )

        service.refresh_from_db()
        self.assertIsNone(service.domain)
        self.assertEqual(service.upstream_url, "")
        self.assertFalse(hasattr(service, "health_state"))
        self.assertEqual(service.launch_url, "https://example.com/docs/")
        self.assertEqual(get_effective_status(service), "up")
