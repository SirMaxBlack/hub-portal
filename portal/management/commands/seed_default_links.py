from django.core.management.base import BaseCommand

from portal.models import Service


DEFAULT_LINKS = (
    ("Cloudflare", "cloudflare", "https://dash.cloudflare.com/", "cloud", 10),
    ("GitHub", "github", "https://github.com/", "server", 20),
    ("ChatGPT", "chatgpt", "https://chatgpt.com/", "users", 30),
    ("DeepSeek", "deepseek", "https://chat.deepseek.com/", "users", 40),
)


class Command(BaseCommand):
    help = "Добавляет безопасные стартовые внешние ссылки в пустой каталог."

    def handle(self, *args, **options):
        created_count = 0
        for title, slug, url, icon_key, sort_order in DEFAULT_LINKS:
            _, created = Service.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "kind": "external",
                    "external_url": url,
                    "description": "Внешний сервис",
                    "icon_key": icon_key,
                    "sort_order": sort_order,
                    "is_active": True,
                    "is_enabled": True,
                },
            )
            created_count += int(created)
        self.stdout.write(self.style.SUCCESS(f"Стартовые ссылки готовы. Создано: {created_count}."))
