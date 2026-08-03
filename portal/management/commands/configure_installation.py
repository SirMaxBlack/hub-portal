from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from portal.access_control import (
    replace_portal_access_rules,
    replace_user_access_rules,
    validate_access_rule_list,
)


User = get_user_model()


class Command(BaseCommand):
    help = "Настраивает стартовые IP/CIDR-ограничения self-hosted установки."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["unrestricted", "restricted"], required=True)
        parser.add_argument("--cidr", action="append", default=[])
        parser.add_argument("--admin-username", required=True)

    def handle(self, *args, **options):
        admin = User.objects.filter(username=options["admin_username"]).first()
        if admin is None:
            raise CommandError("Администратор не найден. Сначала выполните bootstrap_admin.")

        rules: list[str] = []
        if options["mode"] == "restricted":
            rules, errors = validate_access_rule_list(options["cidr"])
            if errors:
                raise CommandError("; ".join(errors))
            if not rules:
                raise CommandError("Для restricted-режима укажите минимум один --cidr.")

        replace_portal_access_rules(rules)
        replace_user_access_rules(admin, rules)
        mode_label = "без IP-ограничений" if not rules else ", ".join(rules)
        self.stdout.write(self.style.SUCCESS(f"Стартовый доступ настроен: {mode_label}."))
