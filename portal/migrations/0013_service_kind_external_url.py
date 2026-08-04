from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0012_portalvisualsettings_alert_durations"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="kind",
            field=models.CharField(
                choices=[
                    ("proxy", "Защищённый reverse proxy"),
                    ("external", "Внешняя ссылка"),
                ],
                default="proxy",
                max_length=16,
                verbose_name="Тип сервиса",
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="external_url",
            field=models.URLField(blank=True, default="", max_length=1000, verbose_name="Внешняя ссылка"),
        ),
        migrations.AlterField(
            model_name="service",
            name="domain",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True, verbose_name="Домен"),
        ),
        migrations.AlterField(
            model_name="service",
            name="upstream_url",
            field=models.URLField(blank=True, default="", max_length=500, verbose_name="Upstream URL"),
        ),
    ]
