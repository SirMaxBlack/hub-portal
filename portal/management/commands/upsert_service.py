from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from portal.models import Service


class Command(BaseCommand):
    help = "Создаёт или обновляет запись сервиса."

    def add_arguments(self, parser):
        parser.add_argument("--domain", required=True)
        parser.add_argument("--upstream", required=True)
        parser.add_argument("--title", required=True)
        parser.add_argument("--icon", default="default")
        parser.add_argument("--description", default="")
        parser.add_argument("--sort-order", type=int, default=100)
        parser.add_argument("--inactive", action="store_true")
        parser.add_argument("--health-type", choices=["http", "https", "tcp"], default="")
        parser.add_argument("--health-host", default="")
        parser.add_argument("--health-port", type=int, default=0)
        parser.add_argument("--health-path", default="")
        parser.add_argument("--health-timeout", type=int, default=3)
        parser.add_argument("--health-codes", default="")
        parser.add_argument("--health-interval", type=int, default=5)
        parser.add_argument("--health-failure-threshold", type=int, default=2)
        parser.add_argument("--health-success-threshold", type=int, default=1)

    def handle(self, *args, **options):
        if not options["domain"].endswith(settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX):
            raise CommandError(
                f"Домен должен быть вида *{settings.HUB_PORTAL_SERVICE_DOMAIN_SUFFIX}"
            )

        parsed_upstream = urlparse(options["upstream"])
        derived_health_type = options["health_type"] or ("https" if parsed_upstream.scheme == "https" else "http")

        service, created = Service.objects.update_or_create(
            domain=options["domain"],
            defaults={
                "kind": "proxy",
                "title": options["title"],
                "description": options["description"],
                "upstream_url": options["upstream"],
                "icon_key": options["icon"],
                "check_type": derived_health_type,
                "health_host": options["health_host"],
                "health_port": options["health_port"] or None,
                "health_path": options["health_path"],
                "timeout_seconds": options["health_timeout"],
                "allowed_status_codes": options["health_codes"] or "200,204,301,302,401,403",
                "interval_seconds": options["health_interval"],
                "failure_threshold": options["health_failure_threshold"],
                "success_threshold": options["health_success_threshold"],
                "sort_order": options["sort_order"],
                "is_active": not options["inactive"],
            },
        )
        action = "создан" if created else "обновлён"
        self.stdout.write(self.style.SUCCESS(f"Сервис {service.domain} {action}."))
