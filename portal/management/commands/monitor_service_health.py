import time

from django.core.management.base import BaseCommand

from portal.health import update_all_service_health


class Command(BaseCommand):
    help = "Фоновая проверка доступности сервисов."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=int, default=5)

    def handle(self, *args, **options):
        sleep_seconds = max(options["sleep"], 5)
        while True:
            update_all_service_health()
            if options["once"]:
                return
            time.sleep(sleep_seconds)
