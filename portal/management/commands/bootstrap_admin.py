import getpass
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


User = get_user_model()


class Command(BaseCommand):
    help = "Создаёт или обновляет первоначальную админскую учётную запись."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        password_group = parser.add_mutually_exclusive_group()
        password_group.add_argument("--password")
        password_group.add_argument("--password-stdin", action="store_true")
        parser.add_argument("--full-name", default="Администратор")
        parser.add_argument("--force-password-change", action="store_true")

    def handle(self, *args, **options):
        if options["password_stdin"]:
            password = sys.stdin.readline().rstrip("\r\n")
        elif options.get("password"):
            password = options["password"]
        elif sys.stdin.isatty():
            password = getpass.getpass("Пароль администратора: ")
        else:
            raise CommandError("Передайте пароль через --password-stdin.")
        if len(password) < 8:
            raise CommandError("Пароль администратора должен содержать минимум 8 символов.")

        user, created = User.objects.get_or_create(
            username=options["username"],
            defaults={
                "full_name": options["full_name"],
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.full_name = options["full_name"]
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.must_change_password = options["force_password_change"]
        user.set_password(password)
        user.save()

        action = "создан" if created else "обновлён"
        self.stdout.write(self.style.SUCCESS(f"Администратор {user.username} {action}."))
