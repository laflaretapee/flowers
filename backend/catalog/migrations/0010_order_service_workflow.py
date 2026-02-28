from django.db import migrations, models


def migrate_order_statuses_forward(apps, schema_editor):
    Order = apps.get_model("catalog", "Order")
    Order.objects.filter(status__in=["confirmed", "in_progress", "delivering"]).update(status="processing")


def migrate_order_statuses_backward(apps, schema_editor):
    Order = apps.get_model("catalog", "Order")
    Order.objects.filter(status="processing").update(status="in_progress")


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_sitesettings_bot_link_flowersraevka"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="is_preorder",
            field=models.BooleanField(default=False, verbose_name="Предзаказ"),
        ),
        migrations.AddField(
            model_name="order",
            name="processing_by_user_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="Обрабатывает (Telegram ID)"),
        ),
        migrations.AddField(
            model_name="order",
            name="processing_by_username",
            field=models.CharField(blank=True, max_length=100, verbose_name="Обрабатывает (username)"),
        ),
        migrations.AddField(
            model_name="order",
            name="requested_delivery",
            field=models.CharField(blank=True, max_length=120, verbose_name="Желаемая дата/время"),
        ),
        migrations.AddField(
            model_name="order",
            name="service_chat_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="Служебный чат"),
        ),
        migrations.AddField(
            model_name="order",
            name="service_message_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="ID служебного сообщения"),
        ),
        migrations.RunPython(migrate_order_statuses_forward, migrate_order_statuses_backward),
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Новый"),
                    ("processing", "В работе"),
                    ("ready", "Готов"),
                    ("completed", "Завершен"),
                    ("cancelled", "Отменен"),
                    ("expired", "Просрочен"),
                ],
                default="new",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
    ]
