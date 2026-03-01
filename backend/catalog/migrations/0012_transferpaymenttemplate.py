from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_order_transfer_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransferPaymentTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="Название")),
                ("details", models.CharField(max_length=255, verbose_name="Реквизиты")),
                ("sort_order", models.IntegerField(default=0, verbose_name="Порядок")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                ("is_default", models.BooleanField(default=False, verbose_name="По умолчанию")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлен")),
            ],
            options={
                "verbose_name": "Шаблон реквизитов",
                "verbose_name_plural": "Шаблоны реквизитов",
                "ordering": ["sort_order", "name", "id"],
            },
        ),
    ]
