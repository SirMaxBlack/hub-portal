from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0011_portalvisualsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalvisualsettings",
            name="alert_fade_duration_seconds",
            field=models.PositiveIntegerField(default=1, verbose_name="Время затухания тревоги (сек.)"),
        ),
        migrations.AddField(
            model_name="portalvisualsettings",
            name="invalid_alert_duration_seconds",
            field=models.PositiveIntegerField(default=2, verbose_name="Время тревоги при ошибке входа (сек.)"),
        ),
        migrations.AddField(
            model_name="portalvisualsettings",
            name="lock_alert_duration_seconds",
            field=models.PositiveIntegerField(default=5, verbose_name="Время тревоги при блокировке (сек.)"),
        ),
    ]
