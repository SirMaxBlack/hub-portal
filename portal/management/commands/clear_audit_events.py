import json

from django.core.management.base import BaseCommand

from portal.models import AuditEvent


class Command(BaseCommand):
    help = "Удаляет все audit-события портала."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Вернуть результат в JSON-формате.",
        )

    def handle(self, *args, **options):
        deleted_count, _ = AuditEvent.objects.all().delete()
        if options["json"]:
            self.stdout.write(json.dumps({"deleted_events": deleted_count}, ensure_ascii=False))
            return
        self.stdout.write(self.style.SUCCESS(f"Удалено записей аудита: {deleted_count}"))
