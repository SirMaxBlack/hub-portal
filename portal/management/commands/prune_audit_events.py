from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.models import AuditEvent


class Command(BaseCommand):
    help = "Удаляет audit-события старше заданного количества дней."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=settings.HUB_PORTAL_AUDIT_RETENTION_DAYS,
            help="Сколько дней хранить события аудита.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        threshold = timezone.now() - timedelta(days=days)
        deleted_count, _ = AuditEvent.objects.filter(occurred_at__lt=threshold).delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено записей аудита: {deleted_count}"))
