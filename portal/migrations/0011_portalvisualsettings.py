from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0010_portalsecuritysettings_browserloginlockstate_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalVisualSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("login_particles_desktop_count", models.PositiveIntegerField(default=150, verbose_name="Точек для desktop авторизации")),
                ("blocked_particles_desktop_count", models.PositiveIntegerField(default=150, verbose_name="Точек для desktop IP-блокировки")),
                ("login_particles_mobile_count", models.PositiveIntegerField(default=50, verbose_name="Точек для mobile авторизации")),
                ("blocked_particles_mobile_count", models.PositiveIntegerField(default=50, verbose_name="Точек для mobile IP-блокировки")),
                ("calm_speed_px", models.PositiveIntegerField(default=38, verbose_name="Скорость спокойного режима (px/sec)")),
                ("alert_speed_px", models.PositiveIntegerField(default=189, verbose_name="Скорость тревожного режима (px/sec)")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Настройки визуала портала",
                "verbose_name_plural": "Настройки визуала портала",
            },
        ),
    ]

